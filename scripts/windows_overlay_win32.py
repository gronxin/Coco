"""Lazy Win32 adapter for the Windows quota overlay."""

from dataclasses import dataclass
import os
from pathlib import PureWindowsPath


ERROR_ALREADY_EXISTS = 183
GWL_EXSTYLE = -20
HWND_TOPMOST = -1
INFINITE = 0xFFFFFFFF
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4
WAIT_FAILED = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020


@dataclass(frozen=True)
class TargetWindow:
    """Describe one accepted foreground Codex window in physical pixels."""

    hwnd: int
    pid: int
    process_name: str
    title: str
    rect: tuple
    work_area: tuple
    dpi: int


class OwnedMutex:
    """Own a Win32 mutex handle and release ownership before closing."""

    def __init__(self, native, handle, owned):
        """Store the native handle and whether this process owns the mutex."""
        self._native = native
        self.handle = handle
        self.owned = owned
        self._closed = False

    def close(self):
        """Release owned mutex state and close the handle once."""
        if self._closed:
            return
        if self.owned:
            self._native.release_mutex(self.handle)
        self._native.close_handle(self.handle)
        self._closed = True

    def __enter__(self):
        """Return the mutex for context-manager use."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close the mutex at context-manager exit."""
        self.close()


class ManualResetEvent:
    """Wrap the named manual-reset stop event."""

    def __init__(self, native, handle):
        """Store one native event handle."""
        self._native = native
        self.handle = handle
        self._closed = False

    def set(self):
        """Signal the event."""
        self._native.set_event(self.handle)

    def reset(self):
        """Return the event to its unsignaled state."""
        self._native.reset_event(self.handle)

    def is_set(self):
        """Poll the event without blocking."""
        result = self._native.wait_for_single_object(self.handle, 0)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise OSError("等待 Windows 事件失败")

    def close(self):
        """Close the event handle once."""
        if not self._closed:
            self._native.close_handle(self.handle)
            self._closed = True

    def __enter__(self):
        """Return the event for context-manager use."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close the event at context-manager exit."""
        self.close()


class ProcessHandle:
    """Wrap a process synchronization and identity handle."""

    def __init__(self, native, handle, pid):
        """Store the process handle and requested PID."""
        self._native = native
        self.handle = handle
        self.pid = pid
        self._closed = False

    @property
    def creation_time(self):
        """Return the process creation timestamp from the kernel."""
        return self._native.get_process_creation_time(self.handle)

    @property
    def executable_path(self):
        """Return the process executable's absolute path."""
        return self._native.get_process_path(self.handle)

    def is_alive(self):
        """Return whether the process is still running."""
        result = self._native.wait_for_single_object(self.handle, 0)
        if result == WAIT_TIMEOUT:
            return True
        if result == WAIT_OBJECT_0:
            return False
        raise OSError("查询 Windows 进程状态失败")

    def wait(self, timeout):
        """Wait for process exit and return whether it exited in time."""
        milliseconds = (
            INFINITE if timeout is None else max(0, int(float(timeout) * 1000 + 0.5))
        )
        result = self._native.wait_for_single_object(self.handle, milliseconds)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise OSError("等待 Windows 进程退出失败")

    def close(self):
        """Close the process handle once."""
        if not self._closed:
            self._native.close_handle(self.handle)
            self._closed = True

    def __enter__(self):
        """Return the process wrapper for context-manager use."""
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        """Close the process handle at context-manager exit."""
        self.close()


class Win32Api:
    """Expose the overlay's bounded Win32 surface through an injectable backend."""

    def __init__(self, native=None):
        """Create a lazy production backend or use a test double."""
        if native is None:
            if os.name != "nt":
                raise OSError("Windows Win32 API 只能在 Windows 上使用")
            native = _CtypesNative()
        self._native = native

    def enable_per_monitor_dpi(self):
        """Enable per-monitor DPI awareness before Tk creates any windows."""
        return self._native.set_process_dpi_awareness()

    def foreground_codex_window(self):
        """Return the valid foreground Codex target or None."""
        hwnd = self._native.get_foreground_window()
        if not hwnd:
            return None
        if not self._native.is_window_visible(hwnd) or self._native.is_iconic(hwnd):
            return None
        pid = self._native.get_window_process_id(hwnd)
        process_name = self._native.get_process_name(pid)
        title = self._native.get_window_text(hwnd)
        executable = PureWindowsPath(process_name).name.lower()
        accepted = executable == "codex.exe" or (
            executable == "chatgpt.exe" and "codex" in title.lower()
        )
        if not accepted:
            return None
        rect = self._native.get_extended_frame_bounds(hwnd)
        if rect is None:
            rect = self._native.get_window_rect(hwnd)
        dpi = int(self._native.get_dpi_for_window(hwnd) or 96)
        minimum = int(120 * dpi / 96 + 0.5)
        if rect[2] - rect[0] < minimum or rect[3] - rect[1] < minimum:
            return None
        return TargetWindow(
            hwnd=hwnd,
            pid=pid,
            process_name=process_name,
            title=title,
            rect=tuple(rect),
            work_area=tuple(self._native.get_monitor_work_area(hwnd)),
            dpi=dpi,
        )

    def configure_overlay_window(self, hwnd):
        """Merge tool-window and non-activating styles into a Tk HWND."""
        style = self._native.get_window_ex_style(hwnd)
        configured = style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self._native.set_window_ex_style(hwnd, configured)
        return configured

    def show_no_activate(self, hwnd):
        """Show an overlay HWND without taking foreground activation."""
        self._native.show_window(hwnd, SW_SHOWNOACTIVATE)

    def place_no_activate(self, hwnd, rect):
        """Move, size and raise an overlay HWND without activation."""
        left, top, right, bottom = rect
        self._native.set_window_pos(
            hwnd,
            HWND_TOPMOST,
            left,
            top,
            right - left,
            bottom - top,
            SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW,
        )

    def create_owned_mutex(self, name):
        """Create an initially owned named mutex and report real ownership."""
        handle = self._native.create_mutex(True, name)
        if not handle:
            raise OSError("创建 Windows 命名互斥量失败")
        owned = self._native.get_last_error() != ERROR_ALREADY_EXISTS
        return OwnedMutex(self._native, handle, owned)

    def open_stop_event(self, name):
        """Create or open the overlay's manual-reset named stop event."""
        handle = self._native.create_event(True, False, name)
        if not handle:
            raise OSError("创建 Windows 停止事件失败")
        return ManualResetEvent(self._native, handle)

    def open_process(self, pid):
        """Open one process for synchronization and identity checks."""
        handle = self._native.open_process(pid)
        if not handle:
            raise OSError("无法打开 Windows 进程")
        return ProcessHandle(self._native, handle, pid)


class _CtypesNative:
    """Bind the concrete Win32 functions only after production construction."""

    def __init__(self):
        """Load Windows DLLs and configure the required function signatures."""
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        try:
            self.shcore = ctypes.WinDLL("shcore", use_last_error=True)
        except OSError:
            self.shcore = None
        self._configure_signatures()

    def _configure_signatures(self):
        """Declare pointer-sized Win32 signatures to prevent 64-bit handle truncation."""
        ctypes = self.ctypes
        wintypes = self.wintypes
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self.user32.GetMonitorInfoW.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self._configure_optional_user32_signatures()

        self.kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel32.ReleaseMutex.restype = wintypes.BOOL
        self.kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel32.SetEvent.restype = wintypes.BOOL
        self.kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
        self.kernel32.ResetEvent.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

        self.dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.dwmapi.DwmGetWindowAttribute.restype = wintypes.LONG

    def _configure_optional_user32_signatures(self):
        """Configure Win32 functions whose availability varies by Windows release."""
        ctypes = self.ctypes
        wintypes = self.wintypes
        get_dpi = getattr(self.user32, "GetDpiForWindow", None)
        if get_dpi is not None:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
        set_context = getattr(self.user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = wintypes.BOOL
        get_long = getattr(self.user32, "GetWindowLongPtrW", None)
        set_long = getattr(self.user32, "SetWindowLongPtrW", None)
        if get_long is not None and set_long is not None:
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
        else:
            self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            self.user32.GetWindowLongW.restype = wintypes.LONG
            self.user32.SetWindowLongW.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.LONG,
            ]
            self.user32.SetWindowLongW.restype = wintypes.LONG

    def get_foreground_window(self):
        """Return the foreground window handle."""
        return self.user32.GetForegroundWindow()

    def is_window_visible(self, hwnd):
        """Return whether a window is visible."""
        return bool(self.user32.IsWindowVisible(hwnd))

    def is_iconic(self, hwnd):
        """Return whether a window is minimized."""
        return bool(self.user32.IsIconic(hwnd))

    def get_window_process_id(self, hwnd):
        """Return the process identifier that owns a window."""
        pid = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        return int(pid.value)

    def get_process_name(self, pid):
        """Return the executable path for a window-owning process."""
        handle = self.open_process(pid)
        if not handle:
            return ""
        try:
            return self.get_process_path(handle)
        finally:
            self.close_handle(handle)

    def get_window_text(self, hwnd):
        """Return one top-level window title."""
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = self.ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def get_extended_frame_bounds(self, hwnd):
        """Return DWM extended frame bounds when available."""
        rect = self.wintypes.RECT()
        result = self.dwmapi.DwmGetWindowAttribute(
            hwnd, 9, self.ctypes.byref(rect), self.ctypes.sizeof(rect)
        )
        if result != 0:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def get_window_rect(self, hwnd):
        """Return fallback physical window bounds."""
        rect = self.wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, self.ctypes.byref(rect)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return (rect.left, rect.top, rect.right, rect.bottom)

    def get_monitor_work_area(self, hwnd):
        """Return the nearest monitor's physical work area."""
        class MONITORINFO(self.ctypes.Structure):
            _fields_ = [
                ("cbSize", self.wintypes.DWORD),
                ("rcMonitor", self.wintypes.RECT),
                ("rcWork", self.wintypes.RECT),
                ("dwFlags", self.wintypes.DWORD),
            ]

        monitor = self.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = self.ctypes.sizeof(info)
        if not self.user32.GetMonitorInfoW(monitor, self.ctypes.byref(info)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        rect = info.rcWork
        return (rect.left, rect.top, rect.right, rect.bottom)

    def get_dpi_for_window(self, hwnd):
        """Return per-window DPI with a system-DPI fallback."""
        function = getattr(self.user32, "GetDpiForWindow", None)
        if function is not None:
            value = function(hwnd)
            if value:
                return int(value)
        device_context = self.user32.GetDC(hwnd)
        if not device_context:
            return 96
        try:
            gdi32 = self.ctypes.WinDLL("gdi32", use_last_error=True)
            return int(gdi32.GetDeviceCaps(device_context, 88) or 96)
        finally:
            self.user32.ReleaseDC(hwnd, device_context)

    def set_process_dpi_awareness(self):
        """Enable Per-Monitor V2 awareness with legacy fallbacks."""
        context = self.ctypes.c_void_p(-4)
        function = getattr(self.user32, "SetProcessDpiAwarenessContext", None)
        if function is not None and function(context):
            return True
        if self.shcore is not None:
            result = self.shcore.SetProcessDpiAwareness(2)
            if result in (0, -2147024891):
                return True
        return bool(self.user32.SetProcessDPIAware())

    def get_window_ex_style(self, hwnd):
        """Read the pointer-sized extended window style."""
        getter = getattr(self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW)
        self.ctypes.set_last_error(0)
        value = getter(hwnd, GWL_EXSTYLE)
        if value == 0 and self.ctypes.get_last_error():
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return int(value)

    def set_window_ex_style(self, hwnd, style):
        """Write the pointer-sized extended window style."""
        setter = getattr(self.user32, "SetWindowLongPtrW", self.user32.SetWindowLongW)
        self.ctypes.set_last_error(0)
        value = setter(hwnd, GWL_EXSTYLE, style)
        if value == 0 and self.ctypes.get_last_error():
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def show_window(self, hwnd, command):
        """Invoke ShowWindow with the supplied command."""
        self.user32.ShowWindow(hwnd, command)

    def set_window_pos(self, hwnd, insert_after, x, y, width, height, flags):
        """Invoke SetWindowPos and raise on failure."""
        if not self.user32.SetWindowPos(
            hwnd, insert_after, x, y, width, height, flags
        ):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def create_mutex(self, initially_owned, name):
        """Create or open a named mutex."""
        return self.kernel32.CreateMutexW(None, initially_owned, name)

    def release_mutex(self, handle):
        """Release ownership of a mutex."""
        if not self.kernel32.ReleaseMutex(handle):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def create_event(self, manual_reset, initial_state, name):
        """Create or open a named event."""
        return self.kernel32.CreateEventW(None, manual_reset, initial_state, name)

    def set_event(self, handle):
        """Signal an event."""
        if not self.kernel32.SetEvent(handle):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def reset_event(self, handle):
        """Reset an event."""
        if not self.kernel32.ResetEvent(handle):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def wait_for_single_object(self, handle, timeout_ms):
        """Wait on one kernel object."""
        return int(self.kernel32.WaitForSingleObject(handle, timeout_ms))

    def open_process(self, pid):
        """Open a process for synchronization and limited query access."""
        return self.kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )

    def get_process_creation_time(self, handle):
        """Return the raw 100-nanosecond Windows process creation time."""
        creation = self.wintypes.FILETIME()
        exit_time = self.wintypes.FILETIME()
        kernel = self.wintypes.FILETIME()
        user = self.wintypes.FILETIME()
        if not self.kernel32.GetProcessTimes(
            handle,
            self.ctypes.byref(creation),
            self.ctypes.byref(exit_time),
            self.ctypes.byref(kernel),
            self.ctypes.byref(user),
        ):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)

    def get_process_path(self, handle):
        """Return the normalized executable path for a process handle."""
        size = self.wintypes.DWORD(32768)
        buffer = self.ctypes.create_unicode_buffer(size.value)
        if not self.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, self.ctypes.byref(size)
        ):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return buffer.value

    def close_handle(self, handle):
        """Close one kernel object handle."""
        if handle:
            self.kernel32.CloseHandle(handle)

    def get_last_error(self):
        """Return the calling thread's last Win32 error."""
        return self.ctypes.get_last_error()
