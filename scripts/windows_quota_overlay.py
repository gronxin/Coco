"""Controller and lazy Tk view for the Windows Codex quota overlay."""

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
import os
from pathlib import Path
from queue import Empty
import time

try:
    from scripts.windows_overlay_support import (
        OverlayState,
        QueueSnapshot,
        QuotaSnapshot,
        Rect,
        capture_relative_anchor,
        clamp_rect,
        custom_panel_rect,
        default_panel_rect,
        exceeds_drag_threshold,
        load_state,
        navigation_rect,
        overlay_hit_target,
        parse_queue_payload,
        parse_quota_payload,
        save_state,
        scale_length,
    )
except ModuleNotFoundError:
    from windows_overlay_support import (
        OverlayState,
        QueueSnapshot,
        QuotaSnapshot,
        Rect,
        capture_relative_anchor,
        clamp_rect,
        custom_panel_rect,
        default_panel_rect,
        exceeds_drag_threshold,
        load_state,
        navigation_rect,
        overlay_hit_target,
        parse_queue_payload,
        parse_quota_payload,
        save_state,
        scale_length,
    )


OVERVIEW_URL = "http://127.0.0.1:8765/?view=overview"
QUEUE_URL = "http://127.0.0.1:8765/?view=night-queue"
QUOTA_URL = "http://127.0.0.1:8765/api/quota"
NIGHT_QUEUE_URL = "http://127.0.0.1:8765/api/night-queue"
POLL_INTERVAL_MS = 100
TRANSPARENT_COLOR = "#010203"


@dataclass(frozen=True)
class NavigationViewModel:
    """Describe one DPI-scaled navigation Canvas frame."""

    dpi: int
    width: int
    height: int
    overlay_visible: bool


@dataclass(frozen=True)
class PanelViewModel:
    """Describe one DPI-scaled panel Canvas frame and its bounded data."""

    dpi: int
    width: int
    height: int
    collapsed: bool
    quota: QuotaSnapshot
    queue: QueueSnapshot


@dataclass(frozen=True)
class OverlayData:
    """Carry the latest parsed quota and queue snapshots to the controller."""

    quota: QuotaSnapshot
    queue: QueueSnapshot


@dataclass(frozen=True)
class Circle:
    """Represent one physical Canvas circle."""

    center: tuple
    radius: int


@dataclass(frozen=True)
class PanelCanvasLayout:
    """Describe physical Canvas geometry shared by rendering assertions."""

    quota_rings: tuple
    arrow: Rect
    title: Rect
    count: Rect
    task_rows: tuple


class JsonStateStore:
    """Load and atomically persist overlay state through the support module."""

    def __init__(self, path):
        """Store the state-file path."""
        self.path = Path(path)

    def load(self):
        """Load validated state or support defaults."""
        return load_state(self.path)

    def save(self, state):
        """Atomically persist one validated overlay state."""
        return save_state(self.path, state)


class SystemBrowser:
    """Open overlay destinations with the user's default browser."""

    def open(self, url):
        """Delegate one URL to the standard browser integration."""
        import webbrowser

        return webbrowser.open(url)


def _rect_from_edges(edges):
    """Create a support rectangle from left, top, right and bottom edges."""
    left, top, right, bottom = edges
    return Rect(left, top, right - left, bottom - top)


def _rect_edges(rect):
    """Return a support rectangle as physical edge coordinates."""
    return (rect.x, rect.y, rect.right, rect.bottom)


def _scaled_rect(x, y, width, height, dpi):
    """Scale one logical local rectangle to physical Canvas pixels."""
    return Rect(
        scale_length(x, dpi),
        scale_length(y, dpi),
        scale_length(width, dpi),
        scale_length(height, dpi),
    )


def panel_canvas_layout(dpi, collapsed):
    """Return Canvas geometry aligned with support-module hit regions."""
    return PanelCanvasLayout(
        quota_rings=tuple(
            Circle(
                center=(scale_length(center_x, dpi), scale_length(59, dpi)),
                radius=scale_length(27, dpi),
            )
            for center_x in (68, 192)
        ),
        arrow=_scaled_rect(212, 138 if collapsed else 131, 36, 36, dpi),
        title=_scaled_rect(17, 133, 187, 18, dpi),
        count=_scaled_rect(17, 144, 187, 18, dpi),
        task_rows=tuple(
            _scaled_rect(17, 154 + index * 18, 187, 17, dpi)
            for index in range(5)
        ),
    )


def _pixel_font(logical_size, dpi, bold=False):
    """Return an explicit negative-pixel Segoe UI font tuple."""
    weight = "bold" if bold else "normal"
    return ("Segoe UI", -max(1, scale_length(logical_size, dpi)), weight)


def _format_percent(value):
    """Format an optional remaining percentage for a compact ring."""
    if value is None:
        return "—"
    return "{}%".format(int(float(value) + 0.5))


def _format_reset(value, weekly):
    """Format an optional reset timestamp for one quota ring."""
    if value is None:
        return "重置时间待更新"
    try:
        rendered = datetime.fromtimestamp(float(value)).strftime("%m月%d日 %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "重置时间待更新"
    return "{}重置 {}".format("下周" if weekly else "下次", rendered)


def _format_updated(value):
    """Extract a compact update time from an ISO-shaped timestamp."""
    if not value:
        return "—"
    text = str(value)
    if "T" in text:
        clock = text.split("T", 1)[1]
        if len(clock) >= 5:
            return clock[:5]
    return text[:5]


def _normalize_worker_update(update, current):
    """Convert worker endpoint results or direct snapshots into overlay data."""
    if isinstance(update, OverlayData):
        return update
    quota_result = getattr(update, "quota", None)
    queue_result = getattr(update, "queue", None)
    quota = getattr(quota_result, "value", quota_result)
    queue = getattr(queue_result, "value", queue_result)
    if not isinstance(quota, QuotaSnapshot):
        quota = current.quota
    if not isinstance(queue, QueueSnapshot):
        queue = current.queue

    quota_state = getattr(quota_result, "state", "fresh")
    quota_error = str(getattr(quota_result, "error", "") or "")
    if quota_state != "fresh":
        quota = replace(quota, stale=True, message=quota_error[:300])
    queue_state = getattr(queue_result, "state", "fresh")
    queue_error = str(getattr(queue_result, "error", "") or "")
    if queue_state != "fresh":
        queue = replace(queue, error=queue_error[:300] or queue.error)
    return OverlayData(quota, queue)


class OverlayController:
    """Coordinate pure overlay state without importing Tk or native libraries."""

    def __init__(
        self,
        *,
        win32,
        view,
        state_store,
        browser,
        updates=None,
        should_stop=None,
    ):
        """Store adapters and load the initial validated user state."""
        self.win32 = win32
        self.view = view
        self.state_store = state_store
        self.browser = browser
        self.updates = updates
        self.should_stop = should_stop or (lambda: False)
        self.state = state_store.load()
        self.data = OverlayData(QuotaSnapshot(), QueueSnapshot())
        self._active = False
        self._schedule_token = None
        self._target = None
        self._window_rect = None
        self._work_area = None
        self._panel_rect = None
        self._dpi = 96
        self._press_point = None
        self._press_panel = None
        self._dragged = False

    def start(self):
        """Bind the view, render once, and activate 100-millisecond polling."""
        if self._active:
            return
        self._active = True
        self.view.bind_controller(self)
        self.poll_once()
        self._schedule_next()

    def stop(self):
        """Cancel the active UI schedule without touching worker ownership."""
        self._active = False
        if self._schedule_token is not None:
            self.view.cancel_schedule(self._schedule_token)
            self._schedule_token = None

    def _schedule_next(self):
        """Schedule the next controller poll while active."""
        if self._active:
            self._schedule_token = self.view.schedule(
                POLL_INTERVAL_MS,
                self._scheduled_poll,
            )

    def _scheduled_poll(self):
        """Run one scheduled poll or stop the UI for a lifecycle request."""
        self._schedule_token = None
        if not self._active:
            return
        if self.should_stop():
            self._active = False
            self.view.stop()
            return
        self.poll_once()
        self._schedule_next()

    def _drain_updates(self):
        """Drain the latest pending worker result without blocking the UI."""
        if self.updates is None:
            return
        latest = None
        while True:
            try:
                latest = self.updates.get_nowait()
            except Empty:
                break
        if latest is not None:
            self.apply_update(latest)

    def apply_update(self, update):
        """Apply one direct or worker-shaped data update."""
        self.data = _normalize_worker_update(update, self.data)

    def _persist(self, state):
        """Replace in-memory state and request an atomic persistence attempt."""
        self.state = state
        self.state_store.save(state)

    def _hide_all(self):
        """Hide both windows without changing persisted visibility intent."""
        self.view.hide_navigation()
        self.view.hide_panel()
        self._cancel_pointer_interaction()
        self._target = None
        self._window_rect = None
        self._work_area = None
        self._panel_rect = None

    def _cancel_pointer_interaction(self):
        """Discard pointer capture when its panel can no longer interact."""
        self._press_point = None
        self._press_panel = None
        self._dragged = False

    def poll_once(self):
        """Drain data, discover Codex, and emit current placement and view models."""
        self._drain_updates()
        target = self.win32.foreground_codex_window()
        if target is None:
            self._hide_all()
            return

        window = _rect_from_edges(target.rect)
        work_area = _rect_from_edges(target.work_area)
        dpi = int(target.dpi or 96)
        navigation = clamp_rect(navigation_rect(window, dpi), work_area)
        navigation_model = NavigationViewModel(
            dpi=dpi,
            width=int(navigation.width),
            height=int(navigation.height),
            overlay_visible=self.state.overlay_visible,
        )
        self.view.render_navigation(navigation_model)
        self.win32.place_no_activate(
            self.view.navigation_hwnd,
            _rect_edges(navigation),
        )
        self.win32.show_no_activate(self.view.navigation_hwnd)

        self._target = target
        self._window_rect = window
        self._work_area = work_area
        self._dpi = dpi
        if not self.state.overlay_visible:
            self.view.hide_panel()
            self._cancel_pointer_interaction()
            self._panel_rect = None
            return

        dragging = self._press_point is not None and self._panel_rect is not None
        if dragging:
            panel = self._panel_rect
        else:
            if self.state.custom_position:
                requested = custom_panel_rect(
                    window,
                    self.state,
                    dpi,
                    collapsed=self.state.queue_collapsed,
                )
            else:
                requested = default_panel_rect(
                    navigation,
                    dpi,
                    collapsed=self.state.queue_collapsed,
                )
            panel = clamp_rect(requested, work_area)
        self._panel_rect = panel
        panel_model = PanelViewModel(
            dpi=dpi,
            width=int(panel.width),
            height=int(panel.height),
            collapsed=self.state.queue_collapsed,
            quota=self.data.quota,
            queue=self.data.queue,
        )
        self.view.render_panel(panel_model)
        if not dragging:
            self.win32.place_no_activate(
                self.view.panel_hwnd,
                _rect_edges(panel),
            )
            self.win32.show_no_activate(self.view.panel_hwnd)

    def toggle_overlay(self):
        """Toggle and persist the user's panel visibility intent."""
        self._persist(
            replace(
                self.state,
                overlay_visible=not self.state.overlay_visible,
            )
        )
        self.poll_once()

    def toggle_queue(self):
        """Toggle and persist queue collapse while retaining the saved anchor."""
        self._persist(
            replace(
                self.state,
                queue_collapsed=not self.state.queue_collapsed,
            )
        )
        self.poll_once()

    def handle_panel_click(self, local_point):
        """Route one non-drag panel click through shared support hit testing."""
        target = overlay_hit_target(
            local_point,
            collapsed=self.state.queue_collapsed,
            visible_task_count=len(self.data.queue.titles),
            dpi=self._dpi,
        )
        if target == "arrow":
            self.toggle_queue()
        elif target == "quota":
            self.browser.open(OVERVIEW_URL)
        elif target.startswith("task:"):
            self.browser.open(QUEUE_URL)

    def handle_panel_press(self, screen_point):
        """Capture pointer and panel origins for thresholded dragging."""
        if self._panel_rect is None:
            return
        self._press_point = tuple(screen_point)
        self._press_panel = self._panel_rect
        self._dragged = False

    def handle_panel_motion(self, screen_point):
        """Move the panel only after the shared logical threshold is reached."""
        if self._press_point is None or self._press_panel is None:
            return
        current = tuple(screen_point)
        if not self._dragged:
            if not exceeds_drag_threshold(
                self._press_point,
                current,
                self._dpi,
            ):
                return
            self._dragged = True
        delta_x = current[0] - self._press_point[0]
        delta_y = current[1] - self._press_point[1]
        requested = Rect(
            self._press_panel.x + delta_x,
            self._press_panel.y + delta_y,
            self._press_panel.width,
            self._press_panel.height,
        )
        self._panel_rect = clamp_rect(requested, self._work_area)
        self.win32.place_no_activate(
            self.view.panel_hwnd,
            _rect_edges(self._panel_rect),
        )
        self.win32.show_no_activate(self.view.panel_hwnd)

    def handle_panel_release(self, screen_point, local_point):
        """Persist a completed drag or route sub-threshold jitter as a click."""
        if self._press_point is None:
            return
        self.handle_panel_motion(screen_point)
        try:
            if self._dragged:
                relative_x, relative_bottom = capture_relative_anchor(
                    self._window_rect,
                    self._panel_rect,
                    self._dpi,
                )
                self._persist(
                    replace(
                        self.state,
                        custom_position=True,
                        relative_x=relative_x,
                        relative_bottom=relative_bottom,
                    )
                )
            else:
                self._cancel_pointer_interaction()
                self.handle_panel_click(local_point)
        finally:
            self._cancel_pointer_interaction()


class TkOverlayView:
    """Create and draw two lazy, borderless Tk overlay windows."""

    def __init__(self, win32):
        """Create hidden root and two styled overlay windows on demand."""
        import tkinter as tk

        self._tk = tk
        self._win32 = win32
        self._controller = None
        self._stopped = False
        self.root = tk.Tk()
        self.root.withdraw()
        self.navigation_window = tk.Toplevel(self.root)
        self.panel_window = tk.Toplevel(self.root)
        for window in (self.navigation_window, self.panel_window):
            window.withdraw()
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.attributes("-transparentcolor", TRANSPARENT_COLOR)
            window.configure(bg=TRANSPARENT_COLOR)
        self.navigation_canvas = tk.Canvas(
            self.navigation_window,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.panel_canvas = tk.Canvas(
            self.panel_window,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        self.navigation_canvas.pack(fill="both", expand=True)
        self.panel_canvas.pack(fill="both", expand=True)
        self.root.update_idletasks()
        navigation_client = int(self.navigation_window.winfo_id())
        panel_client = int(self.panel_window.winfo_id())
        self.navigation_hwnd = self._win32.root_window(navigation_client)
        self.panel_hwnd = self._win32.root_window(panel_client)
        self._win32.configure_overlay_window(self.navigation_hwnd)
        self._win32.configure_overlay_window(self.panel_hwnd)

    def bind_controller(self, controller):
        """Bind Canvas pointer events to one controller instance."""
        self._controller = controller
        self.navigation_canvas.bind("<ButtonRelease-1>", self._on_navigation_release)
        self.panel_canvas.bind("<ButtonPress-1>", self._on_panel_press)
        self.panel_canvas.bind("<B1-Motion>", self._on_panel_motion)
        self.panel_canvas.bind("<ButtonRelease-1>", self._on_panel_release)

    def _on_navigation_release(self, _event):
        """Forward a navigation click without activating a window."""
        self._controller.toggle_overlay()

    def _on_panel_press(self, event):
        """Forward a physical screen pointer-down coordinate."""
        self._controller.handle_panel_press((event.x_root, event.y_root))

    def _on_panel_motion(self, event):
        """Forward a physical screen drag coordinate."""
        self._controller.handle_panel_motion((event.x_root, event.y_root))

    def _on_panel_release(self, event):
        """Forward screen and local coordinates for drag-or-click routing."""
        self._controller.handle_panel_release(
            (event.x_root, event.y_root),
            (event.x, event.y),
        )

    def render_navigation(self, model):
        """Resize and redraw the navigation Canvas from one view model."""
        self.draw_navigation(self.navigation_canvas, model)

    def render_panel(self, model):
        """Resize and redraw the quota Canvas from one view model."""
        self.draw_panel(self.panel_canvas, model)

    def hide_navigation(self):
        """Hide the navigation window without changing controller state."""
        self.navigation_window.withdraw()

    def hide_panel(self):
        """Hide the panel window without changing controller state."""
        self.panel_window.withdraw()

    def schedule(self, delay_ms, callback):
        """Schedule one callback on the Tk UI thread."""
        return self.root.after(delay_ms, callback)

    def cancel_schedule(self, token):
        """Cancel one pending Tk callback if it still exists."""
        try:
            self.root.after_cancel(token)
        except Exception:
            return

    def process_idle_cycle(self):
        """Complete pending layout and one event cycle before ready."""
        self.root.update_idletasks()
        self.root.update()

    def run(self):
        """Enter the Tk main event loop."""
        self.root.mainloop()

    def stop(self):
        """Stop and destroy the lazy Tk hierarchy once."""
        if self._stopped:
            return
        self._stopped = True
        try:
            self.root.quit()
        finally:
            self.root.destroy()

    def draw_navigation(self, canvas, model):
        """Draw the compact quota-and-queue navigation glyph."""
        canvas.configure(width=model.width, height=model.height)
        canvas.delete("all")
        inset = scale_length(1, model.dpi)
        canvas.create_rectangle(
            inset,
            inset,
            model.width - inset,
            model.height - inset,
            fill="#273244" if model.overlay_visible else "#242832",
            outline="#596477" if model.overlay_visible else "#242832",
            width=max(1, scale_length(1, model.dpi)),
        )
        radius = scale_length(4, model.dpi)
        ring_y = scale_length(13, model.dpi)
        for center_x, color in ((12, "#22d3ee"), (24, "#4ade80")):
            physical_x = scale_length(center_x, model.dpi)
            canvas.create_oval(
                physical_x - radius,
                ring_y - radius,
                physical_x + radius,
                ring_y + radius,
                outline=color,
                width=max(1, scale_length(2, model.dpi)),
            )
        for logical_y in (21, 25, 29):
            physical_y = scale_length(logical_y, model.dpi)
            canvas.create_line(
                scale_length(11, model.dpi),
                physical_y,
                scale_length(25, model.dpi),
                physical_y,
                fill="#dedfe3",
                width=max(1, scale_length(1.4, model.dpi)),
            )

    def draw_panel(self, canvas, model):
        """Draw quota rings, queue rows, disclosure control and update state."""
        canvas.configure(width=model.width, height=model.height)
        canvas.delete("all")
        canvas.create_rectangle(
            scale_length(1, model.dpi),
            scale_length(1, model.dpi),
            model.width - scale_length(1, model.dpi),
            model.height - scale_length(1, model.dpi),
            fill="#111827",
            outline="#4a5262",
            width=max(1, scale_length(1, model.dpi)),
        )
        layout = panel_canvas_layout(model.dpi, model.collapsed)
        values = (
            (
                model.quota.five_hour_remaining,
                model.quota.five_hour_reset,
                "5 小时",
                False,
                "#22d3ee",
            ),
            (
                model.quota.weekly_remaining,
                model.quota.weekly_reset,
                "本周",
                True,
                "#4ade80",
            ),
        )
        for ring, value in zip(layout.quota_rings, values):
            self._draw_quota_ring(canvas, model, ring, value)

        divider_y = scale_length(127, model.dpi)
        canvas.create_line(
            scale_length(14, model.dpi),
            divider_y,
            model.width - scale_length(14, model.dpi),
            divider_y,
            fill="#353d4c",
            width=max(1, scale_length(1, model.dpi)),
        )
        queue_color = "#f59e0b" if model.queue.error else "#d1d5db"
        if model.collapsed:
            canvas.create_text(
                layout.count.x,
                layout.count.y,
                anchor="nw",
                text="{} 个任务".format(model.queue.count),
                fill=queue_color,
                font=_pixel_font(12, model.dpi, bold=True),
            )
        else:
            canvas.create_text(
                layout.title.x,
                layout.title.y,
                anchor="nw",
                text="任务队列 · {} 个".format(model.queue.count),
                fill=queue_color,
                font=_pixel_font(10, model.dpi, bold=True),
            )
            self._draw_task_rows(canvas, model, layout)
        self._draw_arrow(canvas, model, layout.arrow)
        updated = _format_updated(model.quota.fetched_at)
        canvas.create_text(
            model.width / 2,
            model.height - scale_length(12, model.dpi),
            anchor="center",
            text=(
                "旧数据 · {}".format(updated)
                if model.quota.stale
                else "更新于 {}".format(updated)
            ),
            fill="#f59e0b" if model.quota.stale else "#a3a3a3",
            font=_pixel_font(9, model.dpi),
        )

    def _draw_quota_ring(self, canvas, model, ring, value):
        """Draw one bounded quota value and its reset caption."""
        remaining, reset, title, weekly, color = value
        x, y = ring.center
        radius = ring.radius
        width = max(1, scale_length(5, model.dpi))
        bounds = (x - radius, y - radius, x + radius, y + radius)
        canvas.create_oval(*bounds, outline="#374151", width=width)
        if remaining is not None:
            canvas.create_arc(
                *bounds,
                start=90,
                extent=-float(remaining) * 3.6,
                style="arc",
                outline="#64748b" if model.quota.stale else color,
                width=width,
            )
        canvas.create_text(
            x,
            y,
            anchor="center",
            text=_format_percent(remaining),
            fill="#ffffff",
            font=_pixel_font(17, model.dpi, bold=True),
        )
        canvas.create_text(
            x,
            scale_length(95, model.dpi),
            anchor="center",
            text=title,
            fill="#d4d4d8",
            font=_pixel_font(11, model.dpi),
        )
        canvas.create_text(
            x,
            scale_length(113, model.dpi),
            anchor="center",
            text=_format_reset(reset, weekly),
            fill="#a3a3a3",
            font=_pixel_font(9, model.dpi),
        )

    def _draw_task_rows(self, canvas, model, layout):
        """Draw at most five bounded task titles inside shared row geometry."""
        if not model.queue.titles:
            text = "队列暂不可用" if model.queue.error else "暂无待执行任务"
            canvas.create_text(
                layout.task_rows[0].x,
                layout.task_rows[0].y,
                anchor="nw",
                text=text,
                fill="#9499a3",
                font=_pixel_font(10, model.dpi),
            )
            return
        for index, title in enumerate(model.queue.titles[:5]):
            row = layout.task_rows[index]
            canvas.create_text(
                row.x,
                row.y,
                anchor="nw",
                text="{}. {}".format(index + 1, title),
                fill="#ffffff",
                font=_pixel_font(11, model.dpi),
            )

    def _draw_arrow(self, canvas, model, rect):
        """Draw the queue's only collapse or expand affordance."""
        canvas.create_rectangle(
            rect.x,
            rect.y,
            rect.right,
            rect.bottom,
            fill="#263042",
            outline="#4b5563",
            width=max(1, scale_length(1, model.dpi)),
        )
        center_x = rect.x + rect.width / 2
        center_y = rect.y + rect.height / 2
        direction = -1 if model.collapsed else 1
        horizontal = scale_length(5, model.dpi)
        vertical = scale_length(3, model.dpi)
        canvas.create_line(
            center_x - horizontal,
            center_y - direction * vertical,
            center_x,
            center_y + direction * scale_length(2, model.dpi),
            center_x + horizontal,
            center_y - direction * vertical,
            fill="#e5e7eb",
            width=max(1, scale_length(2, model.dpi)),
        )


def should_fail_before_ready(environ):
    """Return whether both exact native rollback injection flags are enabled."""
    return (
        environ.get("COCO_OVERLAY_TEST_MODE") == "1"
        and environ.get("COCO_OVERLAY_FAIL_BEFORE_READY") == "1"
    )


def _publish_ready(lifecycle):
    """Publish candidate ready through the lifecycle's supported method."""
    marker = getattr(lifecycle, "mark_ready", None)
    if marker is None:
        marker = lifecycle.write_ready
    marker()


def activate_runtime(*, worker, controller, view, lifecycle, environ):
    """Start schedulers, complete one UI cycle, then publish candidate ready."""
    worker.start()
    controller.start()
    view.process_idle_cycle()
    if should_fail_before_ready(environ):
        return False
    _publish_ready(lifecycle)
    return True


def _default_overlay_dir():
    """Return the installed overlay directory for the current user."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CocoCodexManager" / "overlay"
    return Path(__file__).resolve().parent


def _quota_parser(payload):
    """Parse quota payloads using the current wall-clock reset boundary."""
    return parse_quota_payload(payload, now=time.time())


def _load_runtime_types():
    """Lazily import Windows and worker implementations for production startup."""
    try:
        from scripts.windows_overlay_win32 import Win32Api
        from scripts.windows_overlay_worker import (
            BoundedApiWorker,
            InstanceLifecycle,
        )
    except ModuleNotFoundError:
        from windows_overlay_win32 import Win32Api
        from windows_overlay_worker import (
            BoundedApiWorker,
            InstanceLifecycle,
        )
    return Win32Api, BoundedApiWorker, InstanceLifecycle


def _argument_parser():
    """Build the runtime's installed-launch argument parser."""
    parser = argparse.ArgumentParser(description="Windows Codex quota overlay")
    parser.add_argument(
        "--install-root",
        type=Path,
        default=_default_overlay_dir(),
    )
    parser.add_argument("--version", default="development")
    parser.add_argument("--nonce")
    return parser


def start_lifecycle_with_handoff(lifecycle, install_lock_handoff):
    """Persist runtime identity before releasing an ordinary launcher lock."""
    started = lifecycle.start()
    if install_lock_handoff is not None:
        install_lock_handoff()
    return started


def main(argv=None, environ=None, install_lock_handoff=None):
    """Compose adapters, enforce ready ordering, and run the Windows UI."""
    environment = os.environ if environ is None else environ
    arguments = _argument_parser().parse_args(argv)
    Win32Api, BoundedApiWorker, InstanceLifecycle = _load_runtime_types()
    win32 = Win32Api()
    win32.enable_per_monitor_dpi()
    lifecycle = InstanceLifecycle(
        kernel=win32,
        overlay_dir=arguments.install_root,
        version=arguments.version,
        nonce=arguments.nonce,
        pid=os.getpid(),
    )
    lifecycle_started = start_lifecycle_with_handoff(
        lifecycle,
        install_lock_handoff,
    )
    if not lifecycle_started:
        return 0

    view = None
    controller = None
    worker = None
    try:
        worker = BoundedApiWorker(
            quota_url=QUOTA_URL,
            queue_url=NIGHT_QUEUE_URL,
            quota_parser=_quota_parser,
            queue_parser=parse_queue_payload,
            should_stop=lifecycle.should_stop,
        )
        view = TkOverlayView(win32)
        controller = OverlayController(
            win32=win32,
            view=view,
            state_store=JsonStateStore(
                arguments.install_root.parent / "overlay-state.json"
            ),
            browser=SystemBrowser(),
            updates=worker.updates,
            should_stop=lifecycle.should_stop,
        )
        if not activate_runtime(
            worker=worker,
            controller=controller,
            view=view,
            lifecycle=lifecycle,
            environ=environment,
        ):
            return 1
        view.run()
        return 0
    finally:
        if controller is not None:
            controller.stop()
        if worker is not None:
            worker.stop()
            worker.join()
        if view is not None:
            view.stop()
        lifecycle.close()


if __name__ == "__main__":
    raise SystemExit(
        main(
            install_lock_handoff=globals().get(
                "_COCO_INSTALL_LOCK_HANDOFF"
            )
        )
    )
