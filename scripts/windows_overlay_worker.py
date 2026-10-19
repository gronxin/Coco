"""Bounded HTTP polling and instance lifecycle for the Windows overlay."""

from dataclasses import dataclass
import http.client
import json
import ntpath
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
import threading
import time
from urllib.parse import urlsplit


JOIN_TIMEOUT_SECONDS = 3.5
MAX_RESPONSE_BYTES = 1024 * 1024
POLL_INTERVAL_SECONDS = 10.0
READ_CHUNK_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 3.0
RUNTIME_MUTEX_NAME = r"Local\CocoCodexManagerOverlay"
STOP_EVENT_NAME = r"Local\CocoCodexManagerOverlayStop"


@dataclass(frozen=True)
class EndpointSnapshot:
    """Describe one endpoint value and its current freshness state."""

    value: object
    state: str
    error: object = None


@dataclass(frozen=True)
class WorkerUpdate:
    """Group the quota and queue results from one serial polling round."""

    quota: EndpointSnapshot
    queue: EndpointSnapshot


def _default_http_opener(parsed_url, timeout):
    """Create an HTTP connection for one already-validated URL."""
    connection_type = (
        http.client.HTTPSConnection
        if parsed_url.scheme == "https"
        else http.client.HTTPConnection
    )
    return connection_type(parsed_url.hostname, parsed_url.port, timeout=timeout)


def _remaining_seconds(clock, deadline):
    """Return positive deadline time or raise a bounded timeout."""
    remaining = deadline - clock()
    if remaining <= 0:
        raise TimeoutError("HTTP request exceeded total deadline")
    return remaining


def _normalized_windows_path(path):
    """Normalize a Windows executable path for identity comparison."""
    return ntpath.normcase(ntpath.normpath(path))


def _valid_identity_metadata(metadata):
    """Return whether metadata contains strict process identity fields."""
    if not isinstance(metadata, dict):
        return False
    pid = metadata.get("pid")
    creation_time = metadata.get("process_creation_time")
    pythonw_path = metadata.get("pythonw_path")
    return (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and pid > 0
        and isinstance(creation_time, int)
        and not isinstance(creation_time, bool)
        and creation_time >= 0
        and isinstance(pythonw_path, str)
        and bool(pythonw_path)
    )


def open_verified_process(kernel, metadata):
    """Open a live process only when all persisted identity fields match."""
    if not _valid_identity_metadata(metadata):
        return None
    try:
        process = kernel.open_process(metadata["pid"])
    except OSError:
        return None
    try:
        matches = (
            process.is_alive()
            and process.creation_time == metadata["process_creation_time"]
            and _normalized_windows_path(process.executable_path)
            == _normalized_windows_path(metadata["pythonw_path"])
        )
    except (OSError, TypeError, ValueError):
        process.close()
        return None
    if not matches:
        process.close()
        return None
    return process


class BoundedApiWorker:
    """Poll both local APIs serially and publish capacity-one updates."""

    def __init__(
        self,
        *,
        quota_url,
        queue_url,
        quota_parser,
        queue_parser,
        opener=None,
        clock=None,
        poll_interval=POLL_INTERVAL_SECONDS,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
        should_stop=None,
        thread_factory=None,
    ):
        """Store injectable I/O dependencies and initialize worker state."""
        self.quota_url = quota_url
        self.queue_url = queue_url
        self.quota_parser = quota_parser
        self.queue_parser = queue_parser
        self._opener = opener or _default_http_opener
        self._clock = clock or time.monotonic
        self._poll_interval = float(poll_interval)
        self._request_timeout = float(request_timeout)
        self._external_should_stop = should_stop or (lambda: False)
        self._thread_factory = thread_factory or threading.Thread
        self._local_stop = threading.Event()
        self._thread = None
        self._last_quota = None
        self._last_queue = None
        self.updates = Queue(maxsize=1)

    def start(self):
        """Start exactly one daemon polling thread."""
        if self._thread is not None:
            if self._thread.is_alive():
                return
            raise RuntimeError("worker cannot be restarted")
        self._thread = self._thread_factory(
            target=self._run,
            name="windows-overlay-api-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Request local worker shutdown."""
        self._local_stop.set()

    def join(self, timeout=JOIN_TIMEOUT_SECONDS):
        """Join the worker for no more than 3.5 seconds."""
        if self._thread is None:
            return
        if timeout is None:
            bounded_timeout = JOIN_TIMEOUT_SECONDS
        else:
            bounded_timeout = min(
                JOIN_TIMEOUT_SECONDS, max(0.0, float(timeout))
            )
        self._thread.join(bounded_timeout)

    def is_alive(self):
        """Return whether the polling thread is still alive."""
        return self._thread is not None and self._thread.is_alive()

    def poll_once(self):
        """Fetch both endpoints serially and publish their combined update."""
        quota = self._poll_endpoint(
            self.quota_url,
            self.quota_parser,
            previous=self._last_quota,
            failed_with_value_state="stale",
        )
        if quota.state == "fresh":
            self._last_quota = quota.value

        if self._stop_requested():
            queue = EndpointSnapshot(
                value=self._last_queue,
                state="unavailable",
                error="worker stopped",
            )
        else:
            queue = self._poll_endpoint(
                self.queue_url,
                self.queue_parser,
                previous=self._last_queue,
                failed_with_value_state="unavailable",
            )
            if queue.state == "fresh":
                self._last_queue = queue.value

        update = WorkerUpdate(quota=quota, queue=queue)
        self._publish_latest(update)
        return update

    def _run(self):
        """Poll immediately, then wait without overlapping polling rounds."""
        while not self._stop_requested():
            self.poll_once()
            if self._wait_for_stop(self._poll_interval):
                break

    def _stop_requested(self):
        """Return whether local or external shutdown has been requested."""
        return self._local_stop.is_set() or bool(self._external_should_stop())

    def _wait_for_stop(self, timeout):
        """Wait in short increments so a named event stops polling promptly."""
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._stop_requested():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._local_stop.wait(min(0.05, remaining)):
                return True
        return True

    def _poll_endpoint(
        self,
        url,
        parser,
        *,
        previous,
        failed_with_value_state,
    ):
        """Fetch and parse one endpoint while retaining prior good data."""
        try:
            value = self._fetch_json(url, parser)
        except Exception as error:
            state = (
                failed_with_value_state
                if previous is not None
                else "unavailable"
            )
            message = str(error) or error.__class__.__name__
            return EndpointSnapshot(
                value=previous,
                state=state,
                error=message[:300],
            )
        return EndpointSnapshot(value=value, state="fresh")

    def _fetch_json(self, url, parser):
        """Read and parse one response under a single monotonic deadline."""
        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme not in ("http", "https")
            or not parsed_url.hostname
        ):
            raise ValueError("unsupported API URL")

        deadline = self._clock() + self._request_timeout
        connection = self._opener(
            parsed_url, _remaining_seconds(self._clock, deadline)
        )
        try:
            self._set_connection_timeout(connection, deadline)
            connection.connect()

            target = parsed_url.path or "/"
            if parsed_url.query:
                target = "{}?{}".format(target, parsed_url.query)
            self._set_connection_timeout(connection, deadline)
            connection.request(
                "GET",
                target,
                headers={"Accept": "application/json", "Connection": "close"},
            )

            self._set_connection_timeout(connection, deadline)
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError("HTTP status {}".format(response.status))

            body = self._read_bounded(connection, response, deadline)
        finally:
            connection.close()

        payload = json.loads(body.decode("utf-8"))
        return parser(payload)

    def _set_connection_timeout(self, connection, deadline):
        """Set connection and live-socket timeouts to deadline remainder."""
        remaining = _remaining_seconds(self._clock, deadline)
        connection.timeout = remaining
        socket = getattr(connection, "sock", None)
        if socket is not None:
            socket.settimeout(remaining)

    def _read_bounded(self, connection, response, deadline):
        """Read at most one mebibyte plus one overflow sentinel byte."""
        body = bytearray()
        maximum_read = MAX_RESPONSE_BYTES + 1
        reader = getattr(response, "read1", None)
        if reader is None:
            reader = response.read
        while len(body) < maximum_read:
            self._set_connection_timeout(connection, deadline)
            amount = min(READ_CHUNK_BYTES, maximum_read - len(body))
            chunk = reader(amount)
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("HTTP response too large")
        return bytes(body)

    def _publish_latest(self, update):
        """Replace any pending update with the newest completed round."""
        try:
            self.updates.put_nowait(update)
            return
        except Full:
            pass
        try:
            self.updates.get_nowait()
        except Empty:
            pass
        self.updates.put_nowait(update)


class JsonFileStore:
    """Read bounded JSON and write it through same-directory replacement."""

    def write_json(self, path, payload):
        """Atomically replace one JSON file with a durable temporary file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".{}.".format(path.name),
            suffix=".tmp",
            dir=str(path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary_path), str(path))
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def read_json(self, path):
        """Read one bounded JSON object or return None when absent."""
        path = Path(path)
        try:
            with path.open("rb") as handle:
                body = handle.read(16 * 1024 + 1)
        except FileNotFoundError:
            return None
        if len(body) > 16 * 1024:
            raise ValueError("JSON identity file too large")
        return json.loads(body.decode("utf-8"))

    def remove(self, path):
        """Remove one file while tolerating an already-absent path."""
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


class InstanceLifecycle:
    """Own runtime kernel objects and persistent process identity files."""

    def __init__(
        self,
        *,
        kernel,
        overlay_dir,
        version,
        nonce=None,
        files=None,
        pid=None,
        wall_clock=None,
        runtime_mutex_name=RUNTIME_MUTEX_NAME,
        stop_event_name=STOP_EVENT_NAME,
    ):
        """Store lifecycle dependencies without touching runtime resources."""
        self.kernel = kernel
        self.files = files or JsonFileStore()
        self.overlay_dir = Path(overlay_dir)
        self.version = version
        self.nonce = nonce
        self.pid = os.getpid() if pid is None else pid
        self._wall_clock = wall_clock or time.time
        self.runtime_mutex_name = runtime_mutex_name
        self.stop_event_name = stop_event_name
        self.instance_path = self.overlay_dir / "instance.json"
        self._mutex = None
        self._stop_event = None
        self._creation_time = None
        self._pythonw_path = None
        self._active = False
        self._validate_inputs()

    def _validate_inputs(self):
        """Reject unsafe or incomplete runtime identity inputs."""
        if (
            not isinstance(self.pid, int)
            or isinstance(self.pid, bool)
            or self.pid <= 0
        ):
            raise ValueError("pid must be a positive integer")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be a non-empty string")
        if self.nonce is not None:
            if (
                not isinstance(self.nonce, str)
                or not self.nonce
                or len(self.nonce) > 128
                or Path(self.nonce).name != self.nonce
                or "\\" in self.nonce
            ):
                raise ValueError("nonce is not a safe file component")

    def start(self):
        """Acquire singleton ownership and persist this process identity."""
        if self._active:
            return True
        mutex = self.kernel.create_owned_mutex(self.runtime_mutex_name)
        if not mutex.owned:
            mutex.close()
            return False

        stop_event = None
        try:
            stop_event = self.kernel.open_stop_event(self.stop_event_name)
            process = self.kernel.open_process(self.pid)
            try:
                creation_time = process.creation_time
                pythonw_path = process.executable_path
            finally:
                process.close()

            metadata = {
                "pid": self.pid,
                "process_creation_time": creation_time,
                "pythonw_path": pythonw_path,
                "version": self.version,
                "nonce": self.nonce,
                "time": self._wall_clock(),
            }
            self.files.write_json(self.instance_path, metadata)
        except Exception:
            if stop_event is not None:
                stop_event.close()
            mutex.close()
            raise

        self._mutex = mutex
        self._stop_event = stop_event
        self._creation_time = creation_time
        self._pythonw_path = pythonw_path
        self._active = True
        return True

    def mark_ready(self):
        """Atomically publish exact candidate identity for the installer."""
        if not self._active:
            raise RuntimeError("lifecycle has not started")
        if self.nonce is None:
            return None
        ready_path = self.overlay_dir / "ready-{}.json".format(self.nonce)
        self.files.write_json(
            ready_path,
            {
                "pid": self.pid,
                "version": self.version,
                "nonce": self.nonce,
            },
        )
        return ready_path

    def should_stop(self):
        """Return whether the named manual-reset event is signaled."""
        return self._active and self._stop_event.is_set()

    def shutdown_if_requested(self, worker, ui_callback):
        """Stop worker and UI once when the named event is observed."""
        if not self.should_stop():
            return False
        worker.stop()
        ui_callback()
        return True

    def close(self):
        """Delete only owned metadata and close every held kernel handle."""
        if not self._active:
            return
        try:
            self._delete_instance_if_self()
        finally:
            try:
                self._stop_event.close()
            finally:
                self._mutex.close()
                self._active = False

    def _delete_instance_if_self(self):
        """Remove instance metadata only when PID and creation time match."""
        try:
            metadata = self.files.read_json(self.instance_path)
            owns_file = (
                isinstance(metadata, dict)
                and metadata.get("pid") == self.pid
                and metadata.get("process_creation_time")
                == self._creation_time
            )
            if owns_file:
                self.files.remove(self.instance_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return

    def __enter__(self):
        """Start lifecycle ownership for context-manager use."""
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close lifecycle ownership at context-manager exit."""
        self.close()
