"""Windows on a Windows desktop: listing, focusing, moving. Uses the system's own libraries directly."""
import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath

from .window import Window

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
WM_CLOSE = 0x0010
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PER_MONITOR_AWARE_V2 = -4
# The desktop itself and the taskbar are windows too, but never something the user means.
NOT_APPS = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
ENUM_WINDOWS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _Mouse(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mouse", _Mouse)]



user32.EnumWindows.argtypes = [ENUM_WINDOWS, wintypes.LPARAM]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT


def _see_real_pixels() -> None:
    """At 125% display scaling Windows would otherwise lie about positions, and every click would miss."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


_see_real_pixels()


def window_pid(window_id: int) -> int | None:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window_id, ctypes.byref(pid))
    return pid.value or None


def _program(window_id: int) -> str:
    """The lower-case name of the program a window belongs to, like 'chrome'."""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, window_pid(window_id) or 0)
    if not process:  # another user's, or run as administrator
        return ""
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if not kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
            return ""
        return PureWindowsPath(path.value).stem.lower()
    finally:
        kernel32.CloseHandle(process)


def _is_cloaked(window_id: int) -> bool:
    """Suspended Store apps and other virtual desktops count as visible, but nobody can see them."""
    cloaked = wintypes.DWORD()
    try:
        dwmapi.DwmGetWindowAttribute(window_id, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    except OSError:
        return False
    return bool(cloaked.value)


def _describe(window_id: int) -> Window | None:
    if not user32.IsWindowVisible(window_id) or user32.GetWindowLongW(window_id, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return None
    title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(window_id) + 1)
    user32.GetWindowTextW(window_id, title, len(title))
    kind = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(window_id, kind, len(kind))
    if not title.value or kind.value in NOT_APPS or _is_cloaked(window_id):
        return None
    return Window(window_id, title.value, f"{_program(window_id)} {kind.value}".strip())


def list_windows() -> list[Window]:
    """Application windows and their dialogs, topmost first."""
    found = []

    def visit(window_id, _):
        window = _describe(window_id)
        if window:
            found.append(window)
        return True

    user32.EnumWindows(ENUM_WINDOWS(visit), 0)
    return found


def active_window() -> Window | None:
    window_id = user32.GetForegroundWindow()
    return _describe(window_id) if window_id else None


def activate(window_id: int) -> None:
    """Brings a window forward, restoring it if minimized."""
    if user32.IsIconic(window_id):
        user32.ShowWindow(window_id, SW_RESTORE)
    # Windows only lets a background program take the front just after it has had input, so this gives it some.
    still = _Input(0, _Mouse())  # a mouse move of nothing at all
    user32.SendInput(1, ctypes.byref(still), ctypes.sizeof(still))
    if not user32.SetForegroundWindow(window_id):
        user32.SwitchToThisWindow(window_id, True)


def window_rect(window_id: int) -> tuple[int, int, int, int]:
    """Left, top, width and height of what's visible on screen."""
    rect = wintypes.RECT()
    try:  # this one leaves out the invisible resize border that overlaps whatever is next to it
        dwmapi.DwmGetWindowAttribute(window_id, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    except OSError:
        user32.GetWindowRect(window_id, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def close(window_id: int) -> None:
    user32.PostMessageW(window_id, WM_CLOSE, 0, 0)


def maximize(window_id: int) -> None:
    user32.ShowWindow(window_id, SW_MAXIMIZE)


def minimize(window_id: int) -> None:
    user32.ShowWindow(window_id, SW_MINIMIZE)
