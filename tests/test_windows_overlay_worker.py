import copy
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.windows_overlay_worker import (
    JOIN_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    BoundedApiWorker,
    InstanceLifecycle,
    open_verified_process,
)


class FakeClock:
    """Provide a controllable monotonic clock."""

    def __init__(self):
        """Start the fake clock at zero."""
        self.value = 0.0

    def __call__(self):
        """Return the current fake monotonic time."""
        return self.value

    def advance(self, seconds):
        """Advance fake monotonic time by the requested duration."""
        self.value += seconds


class FakeSocket:
    """Record timeout updates made on an open connection socket."""

    def __init__(self):
        """Create an empty timeout log."""
        self.timeouts = []

    def settimeout(self, timeout):
        """Record one socket timeout update."""
        self.timeouts.append(timeout)


class FakeResponse:
    """Serve deterministic response bytes through bounded read calls."""

    def __init__(self, payload, *, status=200, clock=None, read_delay=0.0):
        """Store response bytes and optional fake read latency."""
        self.payload = payload
        self.status = status
        self.clock = clock
        self.read_delay = read_delay
        self.offset = 0
        self.read_requests = []

    def read1(self, amount):
        """Return at most the requested bytes and record the bound."""
        self.read_requests.append(amount)
        if self.clock is not None:
            self.clock.advance(self.read_delay)
        chunk = self.payload[self.offset : self.offset + amount]
        self.offset += len(chunk)
        return chunk


class FakeConnection:
    """Model one HTTP connection while tracking serial access."""

    def __init__(self, opener, path, response, timeout):
        """Store connection state and register one active connection."""
        self.opener = opener
        self.path = path
        self.response = response
        self.timeout = timeout
        self.sock = None
        self.closed = False
        self.opener.active += 1
        self.opener.max_active = max(self.opener.max_active, self.opener.active)

    def connect(self):
        """Create the fake socket or raise the configured connect error."""
        self.opener.calls.append(("connect", self.path, self.timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        self.sock = FakeSocket()

    def request(self, method, target, headers=None):
        """Record the outbound request."""
        self.opener.calls.append(("request", self.path, method, target, headers))

    def getresponse(self):
        """Return the configured fake response."""
        self.opener.calls.append(("getresponse", self.path))
        return self.response

    def close(self):
        """Close the connection and update concurrency accounting once."""
        if not self.closed:
            self.closed = True
            self.opener.active -= 1
            self.opener.calls.append(("close", self.path))


class FakeOpener:
    """Create path-specific fake HTTP connections."""

    def __init__(self, responses):
        """Store endpoint response queues and initialize call tracking."""
        self.responses = {
            path: list(values) for path, values in responses.items()
        }
        self.calls = []
        self.open_timeouts = []
        self.active = 0
        self.max_active = 0

    def __call__(self, parsed_url, timeout):
        """Open the next configured response for a parsed URL."""
        path = parsed_url.path
        self.open_timeouts.append((path, timeout))
        response = self.responses[path].pop(0)
        return FakeConnection(self, path, response, timeout)


class FakeThread:
    """Record daemon-thread lifecycle without running its target."""

    def __init__(self, *, target, name, daemon):
        """Capture thread construction arguments."""
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_timeouts = []

    def start(self):
        """Record that the worker requested thread startup."""
        self.started = True

    def join(self, timeout):
        """Record the bounded join timeout."""
        self.join_timeouts.append(timeout)

    def is_alive(self):
        """Return whether the fake thread was started."""
        return self.started


class FakeMutex:
    """Represent an owned or contended runtime mutex."""

    def __init__(self, owned):
        """Store ownership and open state."""
        self.owned = owned
        self.closed = False

    def close(self):
        """Record mutex release and handle closure."""
        self.closed = True


class FakeNamedEvent:
    """Represent the named manual-reset stop event."""

    def __init__(self):
        """Start with an unsignaled, open event."""
        self.signaled = False
        self.closed = False

    def is_set(self):
        """Return the configured signal state."""
        return self.signaled

    def close(self):
        """Record event-handle closure."""
        self.closed = True


class FakeProcess:
    """Expose process identity and liveness through a fake handle."""

    def __init__(
        self,
        pid,
        *,
        creation_time=123456789,
        executable_path=r"C:\Python311\pythonw.exe",
        alive=True,
    ):
        """Store fake process identity and liveness."""
        self.pid = pid
        self.creation_time = creation_time
        self.executable_path = executable_path
        self.alive = alive
        self.closed = False

    def is_alive(self):
        """Return configured process liveness."""
        return self.alive

    def close(self):
        """Record process-handle closure."""
        self.closed = True


class FakeKernel:
    """Provide injectable mutex, event and process operations."""

    def __init__(self, *, mutex_owned=True):
        """Initialize configured runtime objects and call tracking."""
        self.mutex = FakeMutex(mutex_owned)
        self.event = FakeNamedEvent()
        self.processes = {}
        self.calls = []

    def create_owned_mutex(self, name):
        """Return the configured runtime mutex."""
        self.calls.append(("create_owned_mutex", name))
        return self.mutex

    def open_stop_event(self, name):
        """Return the configured named stop event."""
        self.calls.append(("open_stop_event", name))
        return self.event

    def open_process(self, pid):
        """Return a configured process handle for one PID."""
        self.calls.append(("open_process", pid))
        process = self.processes.get(pid)
        if process is None:
            raise OSError("process unavailable")
        return process


class FakeFiles:
    """Store JSON files in memory while recording atomic operations."""

    def __init__(self):
        """Create an empty in-memory file map."""
        self.payloads = {}
        self.writes = []
        self.removes = []
        self.read_error = None

    def write_json(self, path, payload):
        """Store a deep copy as one atomic JSON write."""
        key = str(path)
        copied = copy.deepcopy(payload)
        self.payloads[key] = copied
        self.writes.append((key, copied))

    def read_json(self, path):
        """Return a deep copy of a stored JSON payload."""
        if self.read_error is not None:
            raise self.read_error
        return copy.deepcopy(self.payloads.get(str(path)))

    def remove(self, path):
        """Remove an in-memory file if present."""
        key = str(path)
        self.payloads.pop(key, None)
        self.removes.append(key)


class FakeStoppableWorker:
    """Record lifecycle-driven worker shutdown."""

    def __init__(self):
        """Start with no stop request."""
        self.stopped = False

    def stop(self):
        """Record the worker stop request."""
        self.stopped = True


def parse_quota(payload):
    """Extract the quota marker used by worker tests."""
    return payload["quota"]


def parse_queue(payload):
    """Extract the queue marker used by worker tests."""
    return payload["queue"]


def json_response(payload, **kwargs):
    """Create a fake response containing compact JSON bytes."""
    return FakeResponse(json.dumps(payload).encode("utf-8"), **kwargs)


def make_worker(opener, **kwargs):
    """Create a worker with deterministic endpoints and parsers."""
    return BoundedApiWorker(
        quota_url="http://127.0.0.1:8765/api/quota",
        queue_url="http://127.0.0.1:8765/api/night-queue",
        quota_parser=parse_quota,
        queue_parser=parse_queue,
        opener=opener,
        **kwargs,
    )


def make_lifecycle(tmp_path, *, mutex_owned=True, files=None):
    """Create an active-ready lifecycle fixture and its adapters."""
    kernel = FakeKernel(mutex_owned=mutex_owned)
    kernel.processes[42] = FakeProcess(42)
    files = files or FakeFiles()
    lifecycle = InstanceLifecycle(
        kernel=kernel,
        files=files,
        overlay_dir=tmp_path,
        version="abc123",
        nonce="nonce-7",
        pid=42,
        wall_clock=lambda: 1700000000.25,
    )
    return lifecycle, kernel, files


def test_poll_once_fetches_both_apis_serially_with_total_deadlines():
    """Fetch quota then queue on one thread with independent deadlines."""
    clock = FakeClock()
    opener = FakeOpener(
        {
            "/api/quota": [json_response({"quota": "q"}, clock=clock)],
            "/api/night-queue": [json_response({"queue": "n"}, clock=clock)],
        }
    )
    worker = make_worker(opener, clock=clock)

    update = worker.poll_once()

    assert update.quota.value == "q"
    assert update.queue.value == "n"
    assert update.quota.state == "fresh"
    assert update.queue.state == "fresh"
    assert [call[:2] for call in opener.calls if call[0] == "connect"] == [
        ("connect", "/api/quota"),
        ("connect", "/api/night-queue"),
    ]
    assert opener.max_active == 1
    assert opener.active == 0
    assert [timeout for _, timeout in opener.open_timeouts] == pytest.approx(
        [REQUEST_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS]
    )


def test_response_reads_no_more_than_one_mebibyte_plus_one_byte():
    """Detect an oversized body after reading only the sentinel byte."""
    oversized = FakeResponse(b"x" * (MAX_RESPONSE_BYTES + 500))
    opener = FakeOpener(
        {
            "/api/quota": [oversized],
            "/api/night-queue": [json_response({"queue": "ok"})],
        }
    )

    update = make_worker(opener).poll_once()

    assert oversized.offset == MAX_RESPONSE_BYTES + 1
    assert sum(oversized.read_requests) == MAX_RESPONSE_BYTES + 1
    assert update.quota.state == "unavailable"
    assert "large" in update.quota.error.lower()
    assert update.queue.value == "ok"


class SlowDripHandler(BaseHTTPRequestHandler):
    """Serve a body slowly enough to test the total monotonic deadline."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        """Serve a slow quota response and a fast queue response."""
        if self.path == "/api/quota":
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        body = b'{"queue": "ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        """Suppress local HTTP server diagnostics during tests."""


def test_slow_drip_cannot_extend_connect_and_read_past_total_deadline():
    """Apply one three-second deadline across connection and complete read."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowDripHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    worker = BoundedApiWorker(
        quota_url=f"http://127.0.0.1:{port}/api/quota",
        queue_url=f"http://127.0.0.1:{port}/api/night-queue",
        quota_parser=parse_quota,
        queue_parser=parse_queue,
    )

    started = time.monotonic()
    try:
        update = worker.poll_once()
    finally:
        elapsed = time.monotonic() - started
        server.shutdown()
        server.server_close()

    assert elapsed < REQUEST_TIMEOUT_SECONDS + 0.8
    assert update.quota.state == "unavailable"
    assert update.queue.value == "ok"


@pytest.mark.parametrize(
    "bad_response",
    [
        FakeResponse(b"{not-json"),
        json_response({"wrong": "shape"}),
    ],
)
def test_malformed_payload_fails_only_the_affected_snapshot(bad_response):
    """Keep polling the second endpoint after JSON or parser failure."""
    opener = FakeOpener(
        {
            "/api/quota": [bad_response],
            "/api/night-queue": [json_response({"queue": "valid"})],
        }
    )

    update = make_worker(opener).poll_once()

    assert update.quota.value is None
    assert update.quota.state == "unavailable"
    assert update.quota.error
    assert update.queue.value == "valid"
    assert update.queue.state == "fresh"


def test_consecutive_failures_preserve_last_successful_snapshots():
    """Retain prior values while marking quota stale and queue unavailable."""
    opener = FakeOpener(
        {
            "/api/quota": [
                json_response({"quota": "q1"}),
                OSError("quota down"),
                OSError("quota still down"),
            ],
            "/api/night-queue": [
                json_response({"queue": "n1"}),
                OSError("queue down"),
                OSError("queue still down"),
            ],
        }
    )
    worker = make_worker(opener)

    first = worker.poll_once()
    second = worker.poll_once()
    third = worker.poll_once()

    assert first.quota.state == first.queue.state == "fresh"
    assert second.quota.value == third.quota.value == "q1"
    assert second.queue.value == third.queue.value == "n1"
    assert second.quota.state == third.quota.state == "stale"
    assert second.queue.state == third.queue.state == "unavailable"
    assert "still down" in third.quota.error
    assert "still down" in third.queue.error


def test_latest_update_replaces_the_previous_pending_item():
    """Keep only the newest worker update in the capacity-one queue."""
    opener = FakeOpener(
        {
            "/api/quota": [
                json_response({"quota": "q1"}),
                json_response({"quota": "q2"}),
            ],
            "/api/night-queue": [
                json_response({"queue": "n1"}),
                json_response({"queue": "n2"}),
            ],
        }
    )
    worker = make_worker(opener)

    worker.poll_once()
    latest = worker.poll_once()

    assert worker.updates.qsize() == 1
    assert worker.updates.get_nowait() == latest
    assert latest.quota.value == "q2"
    assert latest.queue.value == "n2"


def test_worker_uses_one_daemon_thread_and_caps_join_at_three_point_five_seconds():
    """Run polling on one daemon thread and bound every shutdown join."""
    created = []

    def thread_factory(**kwargs):
        """Create and retain one fake worker thread."""
        thread = FakeThread(**kwargs)
        created.append(thread)
        return thread

    opener = FakeOpener(
        {
            "/api/quota": [json_response({"quota": "q"})],
            "/api/night-queue": [json_response({"queue": "n"})],
        }
    )
    worker = make_worker(opener, thread_factory=thread_factory)

    worker.start()
    worker.stop()
    worker.join(99.0)

    assert len(created) == 1
    assert created[0].daemon is True
    assert created[0].started is True
    assert created[0].join_timeouts == [JOIN_TIMEOUT_SECONDS]


def test_external_stop_callback_ends_worker_wait_without_another_poll():
    """Observe an external stop condition while waiting between rounds."""
    stop_requested = threading.Event()
    opened = threading.Event()

    class SignalingOpener(FakeOpener):
        """Signal after both endpoints in the first round have opened."""

        def __call__(self, parsed_url, timeout):
            """Open a fake connection and signal after the queue endpoint."""
            connection = super().__call__(parsed_url, timeout)
            if parsed_url.path == "/api/night-queue":
                opened.set()
            return connection

    opener = SignalingOpener(
        {
            "/api/quota": [json_response({"quota": "q"})],
            "/api/night-queue": [json_response({"queue": "n"})],
        }
    )
    worker = make_worker(
        opener,
        poll_interval=60.0,
        should_stop=stop_requested.is_set,
    )

    worker.start()
    assert opened.wait(1.0)
    stop_requested.set()
    worker.join()

    assert not worker.is_alive()
    assert len(opener.open_timeouts) == 2


def test_stop_during_quota_fetch_prevents_a_new_queue_request():
    """Avoid starting another bounded request after external shutdown."""
    stop_requested = threading.Event()
    opener = FakeOpener(
        {
            "/api/quota": [json_response({"quota": "q"})],
            "/api/night-queue": [json_response({"queue": "n"})],
        }
    )

    def stopping_quota_parser(payload):
        """Signal external shutdown after parsing the quota response."""
        stop_requested.set()
        return parse_quota(payload)

    worker = BoundedApiWorker(
        quota_url="http://127.0.0.1:8765/api/quota",
        queue_url="http://127.0.0.1:8765/api/night-queue",
        quota_parser=stopping_quota_parser,
        queue_parser=parse_queue,
        opener=opener,
        should_stop=stop_requested.is_set,
    )

    update = worker.poll_once()

    assert update.quota.value == "q"
    assert update.queue.state == "unavailable"
    assert [path for path, _timeout in opener.open_timeouts] == ["/api/quota"]


def test_runtime_mutex_and_stop_event_are_held_for_lifecycle():
    """Hold owned kernel objects until lifecycle shutdown."""
    lifecycle, kernel, files = make_lifecycle(Path("overlay"))

    assert lifecycle.start() is True
    assert kernel.mutex.closed is False
    assert kernel.event.closed is False
    assert str(Path("overlay") / "instance.json") in files.payloads

    lifecycle.close()

    assert kernel.event.closed is True
    assert kernel.mutex.closed is True


def test_already_running_instance_exits_before_ui_or_instance_creation(tmp_path):
    """Reject mutex contention before creating UI or writing metadata."""
    lifecycle, kernel, files = make_lifecycle(tmp_path, mutex_owned=False)
    ui_created = []

    if lifecycle.start():
        ui_created.append(True)

    assert ui_created == []
    assert kernel.mutex.closed is True
    assert ("open_stop_event", lifecycle.stop_event_name) not in kernel.calls
    assert files.writes == []


def test_named_stop_event_stops_worker_and_invokes_ui_callback(tmp_path):
    """Fan one named stop request out to worker and UI shutdown."""
    lifecycle, kernel, _files = make_lifecycle(tmp_path)
    worker = FakeStoppableWorker()
    ui_callbacks = []
    assert lifecycle.start() is True
    kernel.event.signaled = True

    stopped = lifecycle.shutdown_if_requested(
        worker, lambda: ui_callbacks.append("stopped")
    )

    assert stopped is True
    assert worker.stopped is True
    assert ui_callbacks == ["stopped"]
    lifecycle.close()


def test_ready_and_instance_files_include_exact_runtime_identity(tmp_path):
    """Persist complete instance identity and exact candidate ready fields."""
    lifecycle, kernel, files = make_lifecycle(tmp_path)

    assert lifecycle.start() is True
    ready_path = lifecycle.mark_ready()

    instance = files.payloads[str(tmp_path / "instance.json")]
    assert instance == {
        "pid": 42,
        "process_creation_time": 123456789,
        "pythonw_path": r"C:\Python311\pythonw.exe",
        "version": "abc123",
        "nonce": "nonce-7",
        "time": 1700000000.25,
    }
    assert ready_path == tmp_path / "ready-nonce-7.json"
    assert files.payloads[str(ready_path)] == {
        "pid": 42,
        "version": "abc123",
        "nonce": "nonce-7",
    }
    assert kernel.processes[42].closed is True
    lifecycle.close()


@pytest.mark.parametrize(
    ("replacement", "deleted"),
    [
        (None, True),
        ({"pid": 43}, False),
        ({"process_creation_time": 999}, False),
    ],
)
def test_exit_deletes_instance_only_when_pid_and_creation_time_match(
    tmp_path, replacement, deleted
):
    """Avoid deleting metadata owned by a replacement process."""
    lifecycle, _kernel, files = make_lifecycle(tmp_path)
    assert lifecycle.start() is True
    instance_path = str(tmp_path / "instance.json")
    if replacement is not None:
        files.payloads[instance_path].update(replacement)

    lifecycle.close()

    assert (instance_path not in files.payloads) is deleted
    assert (instance_path in files.removes) is deleted


def test_lifecycle_closes_kernel_handles_when_identity_cleanup_fails(tmp_path):
    """Close event and mutex handles even when instance reading fails."""
    files = FakeFiles()
    lifecycle, kernel, _files = make_lifecycle(tmp_path, files=files)
    assert lifecycle.start() is True
    files.read_error = OSError("disk unavailable")

    lifecycle.close()

    assert kernel.event.closed is True
    assert kernel.mutex.closed is True


@pytest.mark.parametrize(
    ("creation_time", "path", "alive", "verified"),
    [
        (123456789, r"c:\python311\PYTHONW.EXE", True, True),
        (999, r"C:\Python311\pythonw.exe", True, False),
        (123456789, r"C:\Other\pythonw.exe", True, False),
        (123456789, r"C:\Python311\pythonw.exe", False, False),
    ],
)
def test_process_identity_rejects_pid_reuse_or_executable_mismatch(
    creation_time, path, alive, verified
):
    """Require PID, creation time, executable path and liveness together."""
    kernel = FakeKernel()
    process = FakeProcess(
        42,
        creation_time=creation_time,
        executable_path=path,
        alive=alive,
    )
    kernel.processes[42] = process
    metadata = {
        "pid": 42,
        "process_creation_time": 123456789,
        "pythonw_path": r"C:\Python311\pythonw.exe",
        "version": "abc123",
        "nonce": "nonce-7",
        "time": 1700000000.25,
    }

    handle = open_verified_process(kernel, metadata)

    assert (handle is not None) is verified
    if handle is not None:
        handle.close()
    assert process.closed is True
