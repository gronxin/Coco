import os

import pytest

from scripts.windows_overlay_win32 import (
    ERROR_ALREADY_EXISTS,
    INFINITE,
    SWP_NOACTIVATE,
    SW_SHOWNOACTIVATE,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TRANSPARENT,
    Win32Api,
)


class FakeNative:
    """Record Win32 adapter calls without loading platform libraries."""

    def __init__(self):
        self.foreground = 101
        self.visible = True
        self.iconic = False
        self.pid = 901
        self.process_name = "Codex.exe"
        self.title = "Codex"
        self.extended_rect = (10, 20, 810, 620)
        self.fallback_rect = (8, 18, 812, 624)
        self.work_area = (-1920, 0, 0, 1080)
        self.dpi = 120
        self.style = 0x00000100
        self.last_error = 0
        self.wait_result = WAIT_TIMEOUT
        self.process_creation_time = 123456789
        self.process_path = r"C:\Python311\pythonw.exe"
        self.closed = []
        self.calls = []

    def get_foreground_window(self):
        """Return the configured foreground HWND."""
        return self.foreground

    def is_window_visible(self, hwnd):
        """Return configured window visibility."""
        self.calls.append(("is_window_visible", hwnd))
        return self.visible

    def is_iconic(self, hwnd):
        """Return configured minimized state."""
        self.calls.append(("is_iconic", hwnd))
        return self.iconic

    def get_window_process_id(self, hwnd):
        """Return the configured process identifier."""
        self.calls.append(("get_window_process_id", hwnd))
        return self.pid

    def get_process_name(self, pid):
        """Return the configured process executable name."""
        self.calls.append(("get_process_name", pid))
        return self.process_name

    def get_window_text(self, hwnd):
        """Return the configured window title."""
        self.calls.append(("get_window_text", hwnd))
        return self.title

    def get_extended_frame_bounds(self, hwnd):
        """Return DWM bounds or None when unavailable."""
        self.calls.append(("get_extended_frame_bounds", hwnd))
        return self.extended_rect

    def get_window_rect(self, hwnd):
        """Return fallback Win32 window bounds."""
        self.calls.append(("get_window_rect", hwnd))
        return self.fallback_rect

    def get_monitor_work_area(self, hwnd):
        """Return the configured physical monitor work area."""
        self.calls.append(("get_monitor_work_area", hwnd))
        return self.work_area

    def get_dpi_for_window(self, hwnd):
        """Return the configured per-window DPI."""
        self.calls.append(("get_dpi_for_window", hwnd))
        return self.dpi

    def set_process_dpi_awareness(self):
        """Record process DPI-awareness activation."""
        self.calls.append(("set_process_dpi_awareness",))
        return True

    def get_window_ex_style(self, hwnd):
        """Return the configured extended window style."""
        self.calls.append(("get_window_ex_style", hwnd))
        return self.style

    def set_window_ex_style(self, hwnd, style):
        """Record an extended window-style update."""
        self.calls.append(("set_window_ex_style", hwnd, style))
        self.style = style

    def show_window(self, hwnd, command):
        """Record a ShowWindow call."""
        self.calls.append(("show_window", hwnd, command))

    def set_window_pos(self, hwnd, insert_after, x, y, width, height, flags):
        """Record a SetWindowPos call."""
        self.calls.append(
            ("set_window_pos", hwnd, insert_after, x, y, width, height, flags)
        )

    def create_mutex(self, initially_owned, name):
        """Return a fake mutex handle and configured last-error state."""
        self.calls.append(("create_mutex", initially_owned, name))
        return 201

    def release_mutex(self, handle):
        """Record mutex ownership release."""
        self.calls.append(("release_mutex", handle))

    def create_event(self, manual_reset, initial_state, name):
        """Return a fake named-event handle."""
        self.calls.append(("create_event", manual_reset, initial_state, name))
        return 301

    def set_event(self, handle):
        """Record setting an event."""
        self.calls.append(("set_event", handle))

    def reset_event(self, handle):
        """Record resetting an event."""
        self.calls.append(("reset_event", handle))

    def wait_for_single_object(self, handle, timeout_ms):
        """Return the configured wait result."""
        self.calls.append(("wait_for_single_object", handle, timeout_ms))
        return self.wait_result

    def open_process(self, pid):
        """Return a fake process synchronization handle."""
        self.calls.append(("open_process", pid))
        return 401

    def get_process_creation_time(self, handle):
        """Return the configured process creation timestamp."""
        self.calls.append(("get_process_creation_time", handle))
        return self.process_creation_time

    def get_process_path(self, handle):
        """Return the configured executable path."""
        self.calls.append(("get_process_path", handle))
        return self.process_path

    def close_handle(self, handle):
        """Record a closed native handle."""
        self.calls.append(("close_handle", handle))
        self.closed.append(handle)

    def get_last_error(self):
        """Return the configured native last-error code."""
        return self.last_error


def test_module_imports_but_production_adapter_rejects_non_windows():
    """Keep imports cross-platform while preventing accidental native use."""
    if os.name != "nt":
        with pytest.raises(OSError, match="Windows"):
            Win32Api()


@pytest.mark.parametrize(
    ("process_name", "title", "accepted"),
    [
        ("Codex.exe", "Anything", True),
        ("codex.EXE", "", True),
        ("ChatGPT.exe", "Codex - project", True),
        ("ChatGPT.exe", "ChatGPT", False),
        ("python.exe", "Codex", False),
    ],
)
def test_foreground_window_process_and_title_selection(process_name, title, accepted):
    """Accept only native Codex or a Codex-titled ChatGPT window."""
    native = FakeNative()
    native.process_name = process_name
    native.title = title

    target = Win32Api(native).foreground_codex_window()

    assert (target is not None) is accepted


@pytest.mark.parametrize(
    ("visible", "iconic", "rect"),
    [
        (False, False, (10, 20, 810, 620)),
        (True, True, (10, 20, 810, 620)),
        (True, False, (10, 20, 109, 620)),
        (True, False, (10, 20, 810, 139)),
    ],
)
def test_foreground_window_rejects_hidden_minimized_or_undersized(
    visible, iconic, rect
):
    """Reject windows that cannot host the logical overlay."""
    native = FakeNative()
    native.visible = visible
    native.iconic = iconic
    native.extended_rect = rect

    assert Win32Api(native).foreground_codex_window() is None


def test_window_selection_prefers_dwm_bounds_and_returns_physical_monitor_data():
    """Expose DWM geometry, work area and DPI without coordinate scaling."""
    native = FakeNative()

    target = Win32Api(native).foreground_codex_window()

    assert target.hwnd == 101
    assert target.pid == 901
    assert target.rect == (10, 20, 810, 620)
    assert target.work_area == (-1920, 0, 0, 1080)
    assert target.dpi == 120
    assert ("get_window_rect", 101) not in native.calls


def test_window_selection_falls_back_to_get_window_rect():
    """Use GetWindowRect when DWM extended bounds are unavailable."""
    native = FakeNative()
    native.extended_rect = None

    target = Win32Api(native).foreground_codex_window()

    assert target.rect == native.fallback_rect
    assert ("get_window_rect", 101) in native.calls


def test_nonactivating_styles_are_ored_without_mouse_transparency():
    """Preserve Tk styles while adding tool-window and no-activate behavior."""
    native = FakeNative()
    original = native.style

    actual = Win32Api(native).configure_overlay_window(501)

    assert actual == original | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    assert actual & WS_EX_TRANSPARENT == 0
    assert native.calls[-1] == ("set_window_ex_style", 501, actual)


def test_show_and_place_never_activate_the_overlay():
    """Route visibility and geometry through non-activating Win32 calls."""
    native = FakeNative()
    api = Win32Api(native)

    api.show_no_activate(501)
    api.place_no_activate(501, (10, 20, 270, 290))

    assert ("show_window", 501, SW_SHOWNOACTIVATE) in native.calls
    placement = next(call for call in native.calls if call[0] == "set_window_pos")
    assert placement[3:7] == (10, 20, 260, 270)
    assert placement[-1] & SWP_NOACTIVATE


def test_runtime_and_installer_mutex_ownership():
    """Track initial mutex ownership and avoid releasing unowned handles."""
    native = FakeNative()
    api = Win32Api(native)

    runtime = api.create_owned_mutex(r"Local\CocoCodexManagerOverlay")
    assert runtime.owned is True
    runtime.close()
    assert ("release_mutex", 201) in native.calls
    assert 201 in native.closed

    native.last_error = ERROR_ALREADY_EXISTS
    installer = api.create_owned_mutex(r"Local\CocoCodexManagerOverlayInstall")
    assert installer.owned is False
    installer.close()
    assert native.calls.count(("release_mutex", 201)) == 1


def test_manual_reset_stop_event_operations_and_waits():
    """Create one manual-reset event and expose set, reset and polling."""
    native = FakeNative()
    api = Win32Api(native)
    event = api.open_stop_event(r"Local\CocoCodexManagerOverlayStop")

    assert ("create_event", True, False, r"Local\CocoCodexManagerOverlayStop") in native.calls
    assert event.is_set() is False
    native.wait_result = WAIT_OBJECT_0
    assert event.is_set() is True
    event.set()
    event.reset()
    assert ("set_event", 301) in native.calls
    assert ("reset_event", 301) in native.calls
    event.close()
    assert 301 in native.closed


def test_process_handle_wait_liveness_and_identity():
    """Expose bounded waits plus creation-time and executable identity."""
    native = FakeNative()
    process = Win32Api(native).open_process(901)

    assert process.pid == 901
    assert process.creation_time == 123456789
    assert process.executable_path == r"C:\Python311\pythonw.exe"
    assert process.is_alive() is True
    assert process.wait(0.25) is False
    assert ("wait_for_single_object", 401, 250) in native.calls

    native.wait_result = WAIT_OBJECT_0
    assert process.is_alive() is False
    assert process.wait(None) is True
    assert ("wait_for_single_object", 401, INFINITE) in native.calls
    process.close()
    assert 401 in native.closed
