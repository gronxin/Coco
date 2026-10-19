"""Pure layout, parsing, hit-testing, and state logic for the Windows overlay."""

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Optional, Tuple


MAX_STATE_BYTES = 16 * 1024
MAX_QUOTA_LIMITS = 100
MAX_QUEUE_ITEMS = 1000
MAX_TITLE_LENGTH = 200
MAX_STATUS_LENGTH = 300

_HIDDEN_QUEUE_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "dispatched",
        "watching",
    }
)


@dataclass(frozen=True)
class Rect:
    """Represent a top-left-origin rectangle in physical pixels."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        """Return the rectangle's exclusive right edge."""
        return self.x + self.width

    @property
    def bottom(self):
        """Return the rectangle's exclusive bottom edge."""
        return self.y + self.height


@dataclass(frozen=True)
class OverlayState:
    """Store validated user-visible overlay preferences and its logical anchor."""

    overlay_visible: bool = False
    queue_collapsed: bool = False
    custom_position: bool = False
    relative_x: Optional[float] = None
    relative_bottom: Optional[float] = None


@dataclass(frozen=True)
class QuotaSnapshot:
    """Store the bounded quota values needed by the compact overlay."""

    five_hour_remaining: Optional[float] = None
    five_hour_reset: Optional[float] = None
    weekly_remaining: Optional[float] = None
    weekly_reset: Optional[float] = None
    fetched_at: Optional[str] = None
    stale: bool = True
    message: str = ""


@dataclass(frozen=True)
class QueueSnapshot:
    """Store the bounded actionable queue summary needed by the overlay."""

    count: int = 0
    titles: Tuple[str, ...] = field(default_factory=tuple)
    state: str = ""
    error: str = ""


def _is_finite_number(value):
    """Return whether a value is a finite non-boolean integer or float."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _point(value, name):
    """Validate and return a two-number physical point."""
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or not all(_is_finite_number(item) for item in value)
    ):
        raise ValueError("{} must contain two finite numbers".format(name))
    return float(value[0]), float(value[1])


def scale_length(value, dpi):
    """Scale one logical value to physical pixels with half-up rounding."""
    if not _is_finite_number(value):
        raise ValueError("logical value must be finite")
    if not _is_finite_number(dpi) or dpi <= 0:
        raise ValueError("dpi must be a positive finite number")
    return math.floor(float(value) * float(dpi) / 96.0 + 0.5)


def navigation_rect(window, dpi):
    """Place the scaled navigation control inside a physical Codex window."""
    size = scale_length(36, dpi)
    return Rect(
        window.x + scale_length(6, dpi),
        window.bottom - scale_length(54, dpi) - size,
        size,
        size,
    )


def _panel_size(dpi, collapsed):
    """Return the scaled physical panel width and selected height."""
    return (
        scale_length(260, dpi),
        scale_length(194 if collapsed else 270, dpi),
    )


def default_panel_rect(navigation, dpi, collapsed=False):
    """Place the panel eight logical units right and ninety-six units up."""
    width, height = _panel_size(dpi, collapsed)
    bottom = navigation.bottom - scale_length(96, dpi)
    return Rect(
        navigation.right + scale_length(8, dpi),
        bottom - height,
        width,
        height,
    )


def custom_panel_rect(window, state, dpi, collapsed=False):
    """Restore a custom panel rectangle from its logical left and bottom anchor."""
    if state.relative_x is None or state.relative_bottom is None:
        raise ValueError("custom panel position requires paired coordinates")
    if not _valid_coordinate(state.relative_x) or not _valid_coordinate(
        state.relative_bottom
    ):
        raise ValueError("custom panel coordinates are invalid")
    width, height = _panel_size(dpi, collapsed)
    bottom = window.y + scale_length(state.relative_bottom, dpi)
    return Rect(
        window.x + scale_length(state.relative_x, dpi),
        bottom - height,
        width,
        height,
    )


def capture_relative_anchor(window, panel, dpi):
    """Capture logical panel-left and panel-bottom offsets from a Codex window."""
    if not _is_finite_number(dpi) or dpi <= 0:
        raise ValueError("dpi must be a positive finite number")
    logical_per_pixel = 96.0 / float(dpi)
    return (
        (panel.x - window.x) * logical_per_pixel,
        (panel.bottom - window.y) * logical_per_pixel,
    )


def clamp_rect(rect, work_area):
    """Clamp a physical rectangle into a possibly negative monitor work area."""
    maximum_x = max(work_area.x, work_area.right - rect.width)
    maximum_y = max(work_area.y, work_area.bottom - rect.height)
    return Rect(
        min(max(rect.x, work_area.x), maximum_x),
        min(max(rect.y, work_area.y), maximum_y),
        rect.width,
        rect.height,
    )


def _contains(rect, point):
    """Return whether a local physical point lies inside a rectangle."""
    x, y = point
    return rect.x <= x < rect.right and rect.y <= y < rect.bottom


def _scaled_rect(x, y, width, height, dpi):
    """Scale one logical local rectangle into physical pixels."""
    return Rect(
        scale_length(x, dpi),
        scale_length(y, dpi),
        scale_length(width, dpi),
        scale_length(height, dpi),
    )


def overlay_hit_target(
    point,
    collapsed,
    visible_task_count,
    dpi,
    dragged=False,
):
    """Resolve one local physical point using arrow, task, quota priority."""
    local_point = _point(point, "point")
    if dragged:
        return "none"
    if (
        not isinstance(visible_task_count, int)
        or isinstance(visible_task_count, bool)
    ):
        raise ValueError("visible_task_count must be an integer")

    arrow = _scaled_rect(212, 138 if collapsed else 131, 36, 36, dpi)
    if _contains(arrow, local_point):
        return "arrow"

    if not collapsed:
        count = min(max(visible_task_count, 0), 5)
        for index in range(count):
            task = _scaled_rect(17, 154 + index * 18, 187, 17, dpi)
            if _contains(task, local_point):
                return "task:{}".format(index)

    for center_x in (68, 192):
        center_y = scale_length(59, dpi)
        physical_x = scale_length(center_x, dpi)
        radius = scale_length(27, dpi)
        delta_x = local_point[0] - physical_x
        delta_y = local_point[1] - center_y
        if delta_x * delta_x + delta_y * delta_y <= radius * radius:
            return "quota"
    return "none"


def exceeds_drag_threshold(start, current, dpi):
    """Return whether physical movement reaches four scaled logical units."""
    start_x, start_y = _point(start, "start")
    current_x, current_y = _point(current, "current")
    threshold = scale_length(4, dpi)
    delta_x = current_x - start_x
    delta_y = current_y - start_y
    return delta_x * delta_x + delta_y * delta_y >= threshold * threshold


def _bounded_text(value, name, limit, allow_none=False):
    """Validate a string field and truncate it to a bounded character count."""
    if value is None and allow_none:
        return ""
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(name))
    return value[:limit]


def _quota_window(value, name):
    """Validate one quota window and return its normalized numeric fields."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("{} must be an object or null".format(name))
    duration = value.get("windowDurationMins")
    used = value.get("usedPercent")
    reset = value.get("resetsAt")
    if (
        not isinstance(duration, int)
        or isinstance(duration, bool)
        or not _is_finite_number(used)
        or not _is_finite_number(reset)
    ):
        raise ValueError("{} contains invalid quota fields".format(name))
    return duration, float(used), float(reset)


def _remaining_window(windows, duration, now):
    """Return bounded remaining quota and reset time for one official duration."""
    match = next((value for value in windows if value[0] == duration), None)
    if match is None:
        return None, None
    _, used, reset = match
    remaining = None if reset <= now else max(0.0, min(100.0, 100.0 - used))
    return remaining, reset


def parse_quota_payload(payload, now):
    """Parse a bounded quota payload by the official five-hour and weekly durations."""
    if not isinstance(payload, dict):
        raise ValueError("quota payload must be an object")
    status = payload.get("status")
    if not isinstance(status, str) or status not in ("ok", "unavailable"):
        raise ValueError("quota status is invalid")
    message = _bounded_text(
        payload.get("message", ""),
        "quota message",
        MAX_STATUS_LENGTH,
    )

    stale = status != "ok"
    if stale:
        source = payload.get("last_success")
        if source is None:
            return QuotaSnapshot(stale=True, message=message)
        if not isinstance(source, dict):
            raise ValueError("last_success must be an object")
    else:
        source = payload

    fetched_at = source.get("fetched_at")
    if fetched_at is not None:
        fetched_at = _bounded_text(
            fetched_at,
            "fetched_at",
            MAX_STATUS_LENGTH,
        )
    limits = source.get("limits", [])
    if not isinstance(limits, list):
        raise ValueError("limits must be an array")
    if len(limits) > MAX_QUOTA_LIMITS:
        raise ValueError("limits collection is too large")

    normalized = []
    for index, limit in enumerate(limits):
        if not isinstance(limit, dict):
            raise ValueError("limit {} must be an object".format(index))
        limit_id = limit.get("limit_id")
        if limit_id is not None and not isinstance(limit_id, str):
            raise ValueError("limit_id must be a string or null")
        windows = []
        for key in ("primary", "secondary"):
            window = _quota_window(limit.get(key), "{}.{}".format(index, key))
            if window is not None:
                windows.append(window)
        normalized.append((limit_id, windows))

    selected = next(
        (windows for limit_id, windows in normalized if limit_id == "codex"),
        normalized[0][1] if normalized else [],
    )
    five_remaining, five_reset = _remaining_window(selected, 300, float(now))
    week_remaining, week_reset = _remaining_window(selected, 10080, float(now))
    return QuotaSnapshot(
        five_hour_remaining=five_remaining,
        five_hour_reset=five_reset,
        weekly_remaining=week_remaining,
        weekly_reset=week_reset,
        fetched_at=fetched_at,
        stale=stale,
        message=message,
    )


def parse_queue_payload(payload):
    """Parse, filter, sort, and bound one real-shaped queue payload."""
    if not isinstance(payload, dict):
        raise ValueError("queue payload must be an object")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("items must be an array")
    if len(items) > MAX_QUEUE_ITEMS:
        raise ValueError("items collection is too large")

    state = _bounded_text(
        payload.get("state", ""),
        "queue state",
        MAX_STATUS_LENGTH,
    )
    discovery = payload.get("discovery")
    if discovery is None:
        error = ""
    else:
        if not isinstance(discovery, dict):
            raise ValueError("discovery must be an object")
        error = _bounded_text(
            discovery.get("error"),
            "discovery error",
            MAX_STATUS_LENGTH,
            allow_none=True,
        )

    actionable = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("queue item {} must be an object".format(index))
        title = item.get("title")
        status = item.get("status")
        position = item.get("position")
        if not isinstance(title, str):
            raise ValueError("queue item title must be a string")
        if not isinstance(status, str):
            raise ValueError("queue item status must be a string")
        if not isinstance(position, int) or isinstance(position, bool):
            raise ValueError("queue item position must be an integer")
        if status not in _HIDDEN_QUEUE_STATUSES:
            actionable.append((position, index, title[:MAX_TITLE_LENGTH]))

    actionable.sort(key=lambda row: (row[0], row[1]))
    return QueueSnapshot(
        count=len(actionable),
        titles=tuple(row[2] for row in actionable[:5]),
        state=state,
        error=error,
    )


def _valid_coordinate(value):
    """Return whether a saved logical coordinate meets the state contract."""
    return _is_finite_number(value) and abs(float(value)) <= 100000


def _parse_state_payload(payload):
    """Validate a JSON-compatible state object and return canonical state."""
    if not isinstance(payload, dict):
        raise ValueError("state must be an object")
    booleans = {}
    for key in ("overlay_visible", "queue_collapsed", "custom_position"):
        value = payload.get(key, False)
        if not isinstance(value, bool):
            raise ValueError("{} must be a boolean".format(key))
        booleans[key] = value

    relative_x = payload.get("relative_x")
    relative_bottom = payload.get("relative_bottom")
    if (relative_x is None) != (relative_bottom is None):
        raise ValueError("state coordinates must be paired")
    if relative_x is not None:
        if not _valid_coordinate(relative_x) or not _valid_coordinate(
            relative_bottom
        ):
            raise ValueError("state coordinates are invalid")
        relative_x = float(relative_x)
        relative_bottom = float(relative_bottom)
    elif booleans["custom_position"]:
        raise ValueError("custom position requires coordinates")

    return OverlayState(
        overlay_visible=booleans["overlay_visible"],
        queue_collapsed=booleans["queue_collapsed"],
        custom_position=booleans["custom_position"],
        relative_x=relative_x,
        relative_bottom=relative_bottom,
    )


def _state_payload(state):
    """Convert and validate an in-memory state before persistence."""
    if not isinstance(state, OverlayState):
        raise ValueError("state must be an OverlayState")
    payload = {
        "overlay_visible": state.overlay_visible,
        "queue_collapsed": state.queue_collapsed,
        "custom_position": state.custom_position,
        "relative_x": state.relative_x,
        "relative_bottom": state.relative_bottom,
    }
    _parse_state_payload(payload)
    return payload


def load_state(path):
    """Load a state file within 16 KiB or return defaults without rewriting it."""
    state_path = Path(path)
    try:
        if state_path.stat().st_size > MAX_STATE_BYTES:
            return OverlayState()
        raw = state_path.read_bytes()
        if len(raw) > MAX_STATE_BYTES:
            return OverlayState()
        return _parse_state_payload(json.loads(raw.decode("utf-8")))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return OverlayState()


def save_state(path, state):
    """Atomically save valid state while preserving the prior file on failure."""
    state_path = Path(path)
    temporary_path = None
    try:
        payload = _state_payload(state)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            return False
        state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".{}.".format(state_path.name),
            suffix=".tmp",
            dir=str(state_path.parent),
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary_path), str(state_path))
        temporary_path = None
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
