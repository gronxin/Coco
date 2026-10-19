import ast
from dataclasses import replace
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

import scripts.windows_quota_overlay as overlay
from scripts.windows_overlay_support import (
    OverlayState,
    QueueSnapshot,
    QuotaSnapshot,
    Rect,
    capture_relative_anchor,
    clamp_rect,
    custom_panel_rect,
    default_panel_rect,
    navigation_rect,
    overlay_hit_target,
    scale_length,
)


class FakeWin32:
    """Record target discovery and non-activating window operations."""

    def __init__(self, target=None):
        """Store an optional foreground target and initialize call records."""
        self.target = target
        self.calls = []

    def foreground_codex_window(self):
        """Return the configured foreground Codex target."""
        self.calls.append(("foreground_codex_window",))
        return self.target

    def configure_overlay_window(self, hwnd):
        """Record applying non-activating styles to one overlay window."""
        self.calls.append(("configure_overlay_window", hwnd))

    def root_window(self, hwnd):
        """Resolve a fake Tk client HWND to its top-level wrapper."""
        self.calls.append(("root_window", hwnd))
        return hwnd + 10_000

    def place_no_activate(self, hwnd, rect):
        """Record non-activating physical placement."""
        self.calls.append(("place_no_activate", hwnd, tuple(rect)))

    def show_no_activate(self, hwnd):
        """Record non-activating display."""
        self.calls.append(("show_no_activate", hwnd))


class FakeView:
    """Expose the controller-facing view contract without creating Tk."""

    def __init__(self):
        """Create stable fake HWNDs and rendering call records."""
        self.navigation_hwnd = 501
        self.panel_hwnd = 502
        self.navigation_models = []
        self.panel_models = []
        self.hidden = []
        self.scheduled = []
        self.cancelled = []
        self.bound_controller = None
        self.stopped = False

    def bind_controller(self, controller):
        """Record the controller bound to pointer and navigation events."""
        self.bound_controller = controller

    def render_navigation(self, model):
        """Record one navigation view model."""
        self.navigation_models.append(model)

    def render_panel(self, model):
        """Record one panel view model."""
        self.panel_models.append(model)

    def hide_navigation(self):
        """Record hiding the navigation window."""
        self.hidden.append("navigation")

    def hide_panel(self):
        """Record hiding the quota panel."""
        self.hidden.append("panel")

    def schedule(self, delay_ms, callback):
        """Record one UI-thread callback schedule."""
        token = len(self.scheduled) + 1
        self.scheduled.append((delay_ms, callback, token))
        return token

    def cancel_schedule(self, token):
        """Record cancellation of one scheduled callback."""
        self.cancelled.append(token)

    def stop(self):
        """Record a controller-requested UI shutdown."""
        self.stopped = True


class FakeStateStore:
    """Keep overlay state in memory and record persisted user changes."""

    def __init__(self, state):
        """Store the initial state."""
        self.state = state
        self.saved = []

    def load(self):
        """Return the current in-memory state."""
        return self.state

    def save(self, state):
        """Persist and record one state value."""
        self.state = state
        self.saved.append(state)


class FakeBrowser:
    """Record URLs delegated to the system browser adapter."""

    def __init__(self):
        """Initialize an empty URL record."""
        self.urls = []

    def open(self, url):
        """Record one requested URL."""
        self.urls.append(url)
        return True


class FakeCanvas:
    """Record Canvas drawing operations without a display server."""

    def __init__(self):
        """Initialize drawing and configuration records."""
        self.calls = []
        self.options = {}

    def configure(self, **kwargs):
        """Record Canvas configuration."""
        self.options.update(kwargs)
        self.calls.append(("configure", (), kwargs))

    config = configure

    def delete(self, *args):
        """Record deletion of previous Canvas items."""
        self.calls.append(("delete", args, {}))

    def create_arc(self, *args, **kwargs):
        """Record an arc item."""
        self.calls.append(("create_arc", args, kwargs))
        return len(self.calls)

    def create_line(self, *args, **kwargs):
        """Record a line item."""
        self.calls.append(("create_line", args, kwargs))
        return len(self.calls)

    def create_oval(self, *args, **kwargs):
        """Record an oval item."""
        self.calls.append(("create_oval", args, kwargs))
        return len(self.calls)

    def create_polygon(self, *args, **kwargs):
        """Record a polygon item."""
        self.calls.append(("create_polygon", args, kwargs))
        return len(self.calls)

    def create_rectangle(self, *args, **kwargs):
        """Record a rectangle item."""
        self.calls.append(("create_rectangle", args, kwargs))
        return len(self.calls)

    def create_text(self, *args, **kwargs):
        """Record a text item."""
        self.calls.append(("create_text", args, kwargs))
        return len(self.calls)


class FakeTkWidget:
    """Provide the minimal widget surface used during lazy view construction."""

    _next_hwnd = 700

    def __init__(self, *_args, **kwargs):
        """Allocate a fake HWND and remember constructor options."""
        type(self)._next_hwnd += 1
        self.hwnd = type(self)._next_hwnd
        self.kwargs = kwargs
        self.calls = []

    def withdraw(self):
        """Record an initially hidden window."""
        self.calls.append(("withdraw",))

    def overrideredirect(self, value):
        """Record borderless-window configuration."""
        self.calls.append(("overrideredirect", value))

    def attributes(self, *args):
        """Record window attribute configuration."""
        self.calls.append(("attributes",) + args)

    def configure(self, **kwargs):
        """Record widget configuration."""
        self.calls.append(("configure", kwargs))

    config = configure

    def bind(self, event, callback):
        """Record one event binding."""
        self.calls.append(("bind", event, callback))

    def pack(self, **kwargs):
        """Record Canvas packing."""
        self.calls.append(("pack", kwargs))

    def update_idletasks(self):
        """Record processing pending geometry work."""
        self.calls.append(("update_idletasks",))

    def update(self):
        """Record one Tk event-cycle update."""
        self.calls.append(("update",))

    def winfo_id(self):
        """Return the stable fake HWND."""
        return self.hwnd

    def after(self, delay_ms, callback):
        """Return a fake schedule token."""
        self.calls.append(("after", delay_ms, callback))
        return f"after-{delay_ms}"

    def after_cancel(self, token):
        """Record cancelling a fake schedule token."""
        self.calls.append(("after_cancel", token))

    def mainloop(self):
        """Record entering the Tk main loop."""
        self.calls.append(("mainloop",))

    def quit(self):
        """Record leaving the Tk main loop."""
        self.calls.append(("quit",))

    def destroy(self):
        """Record destroying a widget."""
        self.calls.append(("destroy",))


class FakeTkRoot(FakeTkWidget):
    """Represent the hidden Tk root."""


class FakeTkToplevel(FakeTkWidget):
    """Represent one borderless overlay Toplevel."""

    instances = []

    def __init__(self, *args, **kwargs):
        """Record every created overlay Toplevel."""
        super().__init__(*args, **kwargs)
        self.instances.append(self)


class FakeTkCanvas(FakeTkWidget):
    """Represent one Tk Canvas during view construction."""


def make_target(
    rect=(100, 100, 1100, 900),
    work_area=(0, 0, 1920, 1080),
    dpi=96,
):
    """Create a foreground target shaped like the Win32 adapter result."""
    return SimpleNamespace(
        hwnd=101,
        pid=901,
        process_name="Codex.exe",
        title="Codex",
        rect=rect,
        work_area=work_area,
        dpi=dpi,
    )


def rect_from_edges(left, top, right, bottom):
    """Create a support rectangle from physical edge coordinates."""
    return Rect(left, top, right - left, bottom - top)


def rect_edges(rect):
    """Return physical edge coordinates for one support rectangle."""
    return (rect.x, rect.y, rect.right, rect.bottom)


def rect_center(rect):
    """Return the center point of one support rectangle."""
    return (rect.x + rect.width / 2, rect.y + rect.height / 2)


def make_state(**changes):
    """Create a complete overlay state with selected field changes."""
    state = OverlayState(
        overlay_visible=True,
        queue_collapsed=False,
        custom_position=False,
        relative_x=None,
        relative_bottom=None,
    )
    return replace(state, **changes)


def make_quota():
    """Create representative quota data for rendering tests."""
    return QuotaSnapshot(
        five_hour_remaining=75.0,
        five_hour_reset=1_798_000_000.0,
        weekly_remaining=60.0,
        weekly_reset=1_798_600_000.0,
        fetched_at="2026-10-19T06:30:00+00:00",
        stale=False,
        message="",
    )


def make_queue(titles=("任务 A",)):
    """Create representative queue data for hit and rendering tests."""
    return QueueSnapshot(
        count=len(titles),
        titles=tuple(titles),
        state="待执行",
        error="",
    )


def make_controller(state=None, target=None, updates=None):
    """Create a controller and all of its injectable fake adapters."""
    actual_state = state or make_state()
    win32 = FakeWin32(target or make_target())
    view = FakeView()
    store = FakeStateStore(actual_state)
    browser = FakeBrowser()
    controller = overlay.OverlayController(
        win32=win32,
        view=view,
        state_store=store,
        browser=browser,
        updates=updates,
    )
    return controller, win32, view, store, browser


def last_placement(win32, hwnd):
    """Return the most recent placed rectangle for one fake HWND."""
    return next(
        call[2]
        for call in reversed(win32.calls)
        if call[0] == "place_no_activate" and call[1] == hwnd
    )


def test_foreground_codex_shows_both_windows_and_background_hides_them():
    """Preserve user intent while applying the foreground visibility matrix."""
    controller, win32, view, store, _browser = make_controller()

    controller.poll_once()

    assert ("show_no_activate", view.navigation_hwnd) in win32.calls
    assert ("show_no_activate", view.panel_hwnd) in win32.calls
    win32.target = None
    controller.poll_once()
    assert view.hidden[-2:] == ["navigation", "panel"]
    assert store.saved == []
    assert controller.state.overlay_visible is True


def test_navigation_click_toggles_persisted_intent_through_noactivate_calls():
    """Toggle only persisted visibility and never use activating display APIs."""
    controller, win32, view, store, _browser = make_controller()
    controller.poll_once()

    controller.toggle_overlay()

    assert store.saved[-1].overlay_visible is False
    assert view.hidden[-1] == "panel"
    assert ("show_no_activate", view.navigation_hwnd) in win32.calls
    assert not hasattr(view, "show_navigation")
    assert not hasattr(view, "move_navigation")


def test_default_and_custom_placement_reuse_support_geometry():
    """Use shared geometry functions for both default and persisted anchors."""
    target = make_target(dpi=120)
    controller, win32, view, _store, _browser = make_controller(target=target)

    controller.poll_once()

    window = rect_from_edges(*target.rect)
    work_area = rect_from_edges(*target.work_area)
    navigation = clamp_rect(navigation_rect(window, 120), work_area)
    expected_default = clamp_rect(
        default_panel_rect(navigation, 120, collapsed=False),
        work_area,
    )
    assert last_placement(win32, view.navigation_hwnd) == rect_edges(navigation)
    assert last_placement(win32, view.panel_hwnd) == rect_edges(expected_default)

    controller.state = make_state(
        custom_position=True,
        relative_x=80,
        relative_bottom=600,
    )
    controller.poll_once()
    expected_custom = clamp_rect(
        custom_panel_rect(window, controller.state, 120, collapsed=False),
        work_area,
    )
    assert last_placement(win32, view.panel_hwnd) == rect_edges(expected_custom)


def test_move_resize_and_dpi_changes_recompute_all_geometry():
    """Follow changes to physical bounds, work area and target DPI on every poll."""
    controller, win32, view, _store, _browser = make_controller()
    controller.poll_once()
    first_navigation = last_placement(win32, view.navigation_hwnd)
    first_panel = last_placement(win32, view.panel_hwnd)

    win32.target = make_target(
        rect=(-1800, 50, -700, 850),
        work_area=(-1920, 0, 0, 1080),
        dpi=144,
    )
    controller.poll_once()

    assert last_placement(win32, view.navigation_hwnd) != first_navigation
    assert last_placement(win32, view.panel_hwnd) != first_panel
    assert view.navigation_models[-1].width == scale_length(36, 144)
    assert view.navigation_models[-1].height == scale_length(36, 144)
    assert view.panel_models[-1].width == scale_length(260, 144)
    assert view.panel_models[-1].height == scale_length(270, 144)


def test_arrow_only_collapse_preserves_custom_bottom_anchor():
    """Persist collapse intent while keeping the saved and displayed bottom edge."""
    state = make_state(
        custom_position=True,
        relative_x=200,
        relative_bottom=700,
    )
    controller, win32, view, store, _browser = make_controller(state=state)
    controller.poll_once()
    expanded = rect_from_edges(*last_placement(win32, view.panel_hwnd))

    controller.handle_panel_click((230, 149))

    collapsed = rect_from_edges(*last_placement(win32, view.panel_hwnd))
    assert store.saved[-1].queue_collapsed is True
    assert store.saved[-1].relative_x == 200
    assert store.saved[-1].relative_bottom == 700
    assert expanded.bottom == collapsed.bottom
    assert expanded.height == 270
    assert collapsed.height == 194


def test_real_pointer_arrow_release_resizes_without_a_poll_delay():
    """Clear pointer capture before an arrow click recomputes panel height."""
    controller, win32, view, _store, _browser = make_controller()
    controller.poll_once()
    controller.handle_panel_press((400, 400))

    controller.handle_panel_release((400, 400), (230, 149))

    collapsed = rect_from_edges(*last_placement(win32, view.panel_hwnd))
    assert controller.state.queue_collapsed is True
    assert collapsed.height == 194


def test_quota_and_task_clicks_open_urls_while_blank_regions_do_nothing():
    """Route only shared quota and visible task hit targets to browser URLs."""
    controller, _win32, _view, _store, browser = make_controller()
    controller.apply_update(overlay.OverlayData(make_quota(), make_queue()))
    controller.poll_once()

    controller.handle_panel_click((68, 59))
    controller.handle_panel_click((100, 162))
    controller.handle_panel_click((100, 140))

    assert browser.urls == [overlay.OVERVIEW_URL, overlay.QUEUE_URL]


def test_subthreshold_jitter_clicks_but_threshold_drag_saves_logical_anchor():
    """Treat jitter as a click and save only a completed thresholded drag."""
    controller, win32, view, store, browser = make_controller()
    controller.apply_update(overlay.OverlayData(make_quota(), make_queue()))
    controller.poll_once()

    controller.handle_panel_press((300, 300))
    controller.handle_panel_release((303, 300), (68, 59))
    assert browser.urls == [overlay.OVERVIEW_URL]
    assert store.saved == []

    original = rect_from_edges(*last_placement(win32, view.panel_hwnd))
    controller.handle_panel_press((300, 300))
    controller.handle_panel_motion((320, 310))
    moved = rect_from_edges(*last_placement(win32, view.panel_hwnd))
    controller.handle_panel_release((320, 310), (68, 59))

    expected_anchor = capture_relative_anchor(
        rect_from_edges(*win32.target.rect),
        moved,
        win32.target.dpi,
    )
    assert moved.x == original.x + 20
    assert moved.y == original.y + 10
    assert store.saved[-1].custom_position is True
    assert (
        store.saved[-1].relative_x,
        store.saved[-1].relative_bottom,
    ) == expected_anchor
    assert browser.urls == [overlay.OVERVIEW_URL]


def test_scheduled_poll_does_not_reset_an_active_drag():
    """Keep the pointer-controlled panel stable across 100-millisecond polls."""
    controller, win32, view, _store, _browser = make_controller()
    controller.poll_once()
    controller.handle_panel_press((300, 300))
    controller.handle_panel_motion((340, 320))
    dragged = last_placement(win32, view.panel_hwnd)

    controller.poll_once()

    assert last_placement(win32, view.panel_hwnd) == dragged


def test_foreground_loss_cancels_drag_before_a_late_pointer_release():
    """Discard drag capture safely when Codex leaves the foreground."""
    controller, win32, view, store, _browser = make_controller()
    controller.poll_once()
    controller.handle_panel_press((300, 300))
    controller.handle_panel_motion((340, 320))
    win32.target = None
    controller.poll_once()

    controller.handle_panel_release((340, 320), (68, 59))

    assert view.hidden[-2:] == ["navigation", "panel"]
    assert store.saved == []


def test_edge_clamping_never_rewrites_the_saved_anchor():
    """Apply temporary work-area clamping without persisting display coordinates."""
    state = make_state(
        custom_position=True,
        relative_x=-900,
        relative_bottom=50,
    )
    original = replace(state)
    controller, win32, view, store, _browser = make_controller(
        state=state,
        target=make_target(
            rect=(0, 0, 1000, 800),
            work_area=(0, 0, 1000, 800),
        ),
    )

    controller.poll_once()

    displayed = rect_from_edges(*last_placement(win32, view.panel_hwnd))
    requested = custom_panel_rect(
        rect_from_edges(*win32.target.rect),
        state,
        96,
        collapsed=False,
    )
    assert displayed != requested
    assert displayed == clamp_rect(
        requested,
        rect_from_edges(*win32.target.work_area),
    )
    assert controller.state == original
    assert store.saved == []


def test_controller_starts_100ms_schedule_and_drains_latest_worker_update():
    """Schedule foreground polling and consume worker data on the UI thread."""
    updates = Queue(maxsize=1)
    updates.put(overlay.OverlayData(make_quota(), make_queue(("A", "B"))))
    controller, _win32, view, _store, _browser = make_controller(updates=updates)

    controller.start()

    assert view.bound_controller is controller
    assert view.scheduled[-1][0] == 100
    assert view.panel_models[-1].queue.titles == ("A", "B")


def test_view_models_use_documented_logical_sizes_for_both_panel_states():
    """Scale 36×36 navigation and 260×270/194 panel dimensions exactly once."""
    controller, _win32, view, _store, _browser = make_controller()
    controller.poll_once()

    assert (view.navigation_models[-1].width, view.navigation_models[-1].height) == (
        36,
        36,
    )
    assert (view.panel_models[-1].width, view.panel_models[-1].height) == (
        260,
        270,
    )

    controller.handle_panel_click((230, 149))
    assert (view.panel_models[-1].width, view.panel_models[-1].height) == (
        260,
        194,
    )


def test_canvas_layout_matches_shared_hit_geometry():
    """Keep rendered ring, arrow and task geometry aligned with shared hit routing."""
    expanded = overlay.panel_canvas_layout(96, collapsed=False)
    collapsed = overlay.panel_canvas_layout(96, collapsed=True)

    for ring in expanded.quota_rings:
        assert (
            overlay_hit_target(
                ring.center,
                collapsed=False,
                visible_task_count=1,
                dpi=96,
            )
            == "quota"
        )
    assert (
        overlay_hit_target(
            rect_center(expanded.arrow),
            collapsed=False,
            visible_task_count=1,
            dpi=96,
        )
        == "arrow"
    )
    assert (
        overlay_hit_target(
            rect_center(collapsed.arrow),
            collapsed=True,
            visible_task_count=1,
            dpi=96,
        )
        == "arrow"
    )
    assert (
        overlay_hit_target(
            rect_center(expanded.task_rows[0]),
            collapsed=False,
            visible_task_count=1,
            dpi=96,
        )
        == "task:0"
    )


def test_canvas_renderer_uses_only_explicit_negative_pixel_fonts():
    """Render every text item with a DPI-scaled negative pixel font size."""
    view = object.__new__(overlay.TkOverlayView)
    navigation_canvas = FakeCanvas()
    panel_canvas = FakeCanvas()
    navigation_model = overlay.NavigationViewModel(
        dpi=120,
        width=45,
        height=45,
        overlay_visible=True,
    )
    panel_model = overlay.PanelViewModel(
        dpi=120,
        width=325,
        height=338,
        collapsed=False,
        quota=make_quota(),
        queue=make_queue(),
    )

    view.draw_navigation(navigation_canvas, navigation_model)
    view.draw_panel(panel_canvas, panel_model)

    text_calls = [
        call
        for call in navigation_canvas.calls + panel_canvas.calls
        if call[0] == "create_text"
    ]
    assert text_calls
    assert all(call[2]["font"][1] < 0 for call in text_calls)


def test_lazy_view_creates_two_hidden_toplevels_and_styles_both(monkeypatch):
    """Import Tk only at construction and configure both HWNDs through Win32."""
    FakeTkToplevel.instances = []
    fake_tk = SimpleNamespace(
        Tk=FakeTkRoot,
        Toplevel=FakeTkToplevel,
        Canvas=FakeTkCanvas,
    )
    monkeypatch.setitem(__import__("sys").modules, "tkinter", fake_tk)
    win32 = FakeWin32()

    view = overlay.TkOverlayView(win32)

    assert len(FakeTkToplevel.instances) == 2
    assert all(("withdraw",) in item.calls for item in FakeTkToplevel.instances)
    client_hwnds = [item.hwnd for item in FakeTkToplevel.instances]
    assert ("root_window", client_hwnds[0]) in win32.calls
    assert ("root_window", client_hwnds[1]) in win32.calls
    assert view.navigation_hwnd == client_hwnds[0] + 10_000
    assert view.panel_hwnd == client_hwnds[1] + 10_000
    assert ("configure_overlay_window", view.navigation_hwnd) in win32.calls
    assert ("configure_overlay_window", view.panel_hwnd) in win32.calls


class OrderedComponent:
    """Append lifecycle operations to one shared ordering log."""

    def __init__(self, name, events):
        """Store the component name and shared event list."""
        self.name = name
        self.events = events

    def start(self):
        """Record component startup."""
        self.events.append(f"{self.name}.start")


class OrderedReadyLifecycle:
    """Record ready publication for startup-order assertions."""

    def __init__(self, events):
        """Store the shared event list."""
        self.events = events

    def write_ready(self):
        """Record publishing candidate ready metadata."""
        self.events.append("lifecycle.ready")


class OrderedStartLifecycle:
    """Record durable instance startup for install-lock handoff tests."""

    def __init__(self, events, started=True):
        """Store shared events and the configured singleton result."""
        self.events = events
        self.started = started

    def start(self):
        """Record instance persistence before returning startup state."""
        self.events.append("lifecycle.start")
        return self.started


class OrderedIdleView:
    """Record the first completed Tk idle/event cycle."""

    def __init__(self, events):
        """Store the shared event list."""
        self.events = events

    def process_idle_cycle(self):
        """Record one completed idle/event cycle."""
        self.events.append("view.idle")


def test_ready_is_written_only_after_worker_controller_and_idle_cycle():
    """Enforce every documented ready precondition in order."""
    events = []

    ready = overlay.activate_runtime(
        worker=OrderedComponent("worker", events),
        controller=OrderedComponent("controller", events),
        view=OrderedIdleView(events),
        lifecycle=OrderedReadyLifecycle(events),
        environ={},
    )

    assert ready is True
    assert events == [
        "worker.start",
        "controller.start",
        "view.idle",
        "lifecycle.ready",
    ]


def test_install_lock_handoff_follows_durable_instance_start():
    """Release the launcher lock only after lifecycle identity is established."""
    events = []
    lifecycle = OrderedStartLifecycle(events)

    started = overlay.start_lifecycle_with_handoff(
        lifecycle,
        lambda: events.append("install-lock.release"),
    )

    assert started is True
    assert events == ["lifecycle.start", "install-lock.release"]


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"COCO_OVERLAY_TEST_MODE": "1"}, False),
        ({"COCO_OVERLAY_FAIL_BEFORE_READY": "1"}, False),
        (
            {
                "COCO_OVERLAY_TEST_MODE": "true",
                "COCO_OVERLAY_FAIL_BEFORE_READY": "1",
            },
            False,
        ),
        (
            {
                "COCO_OVERLAY_TEST_MODE": "1",
                "COCO_OVERLAY_FAIL_BEFORE_READY": "true",
            },
            False,
        ),
        (
            {
                "COCO_OVERLAY_TEST_MODE": "1",
                "COCO_OVERLAY_FAIL_BEFORE_READY": "1",
            },
            True,
        ),
    ],
)
def test_failure_injection_requires_both_exact_environment_values(environ, expected):
    """Ignore partial or inexact rollback hooks and require both exact values."""
    assert overlay.should_fail_before_ready(environ) is expected


def test_injected_failure_exits_before_ready_publication():
    """Start schedulers and complete idle work but withhold candidate ready."""
    events = []
    environ = {
        "COCO_OVERLAY_TEST_MODE": "1",
        "COCO_OVERLAY_FAIL_BEFORE_READY": "1",
    }

    ready = overlay.activate_runtime(
        worker=OrderedComponent("worker", events),
        controller=OrderedComponent("controller", events),
        view=OrderedIdleView(events),
        lifecycle=OrderedReadyLifecycle(events),
        environ=environ,
    )

    assert ready is False
    assert events == ["worker.start", "controller.start", "view.idle"]


def test_runtime_parser_accepts_launcher_contract_and_optional_nonce(tmp_path):
    """Accept trusted install-root/version arguments with an absent nonce."""
    arguments = overlay._argument_parser().parse_args(
        [
            "--install-root",
            str(tmp_path),
            "--version",
            "0123456789abcdef",
        ]
    )

    assert arguments.install_root == tmp_path
    assert arguments.version == "0123456789abcdef"
    assert arguments.nonce is None


def test_source_contract_is_lazy_nonactivating_and_fully_documented():
    """Reject eager GUI/native imports, focus grabs and undocumented callables."""
    source_path = Path(overlay.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_imports = {
        alias.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "tkinter" not in top_level_imports
    assert "ctypes" not in top_level_imports
    assert "focus_force" not in source
    assert "focus_set" not in source
    assert "WS_EX_TRANSPARENT" not in source
    assert ".geometry(" not in source
    assert ".deiconify(" not in source
    assert ".lift(" not in source
    assert source.count("Toplevel(") == 2
    assert "place_no_activate" in source
    assert "show_no_activate" in source

    undocumented = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.get_docstring(node) is None
    ]
    assert undocumented == []
