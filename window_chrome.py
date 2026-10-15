"""Windows custom frame and topmost state without recreating the capture window."""
import os

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    dwmapi = ctypes.WinDLL('dwmapi')
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    hooks = {}  # Keep callbacks alive for the lifetime of their native windows.
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    class Margins(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int) for name in ('left', 'right', 'top', 'bottom')]

    dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(Margins)]
    dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long


def apply_frame(hwnd):
    if os.name != 'nt':
        return
    # Qt consumes WM_NCCALCSIZE before QWidget.nativeEvent on Windows. Hook the
    # native procedure so the standard caption is removed before Qt sees it.
    if hwnd not in hooks:
        previous = user32.GetWindowLongPtrW(hwnd, -4)
        @WNDPROC
        def procedure(window, message, wparam, lparam):
            result = handle_native(window, message, wparam, lparam)
            if result is not None:
                return result
            if message == 0x0082:  # WM_NCDESTROY: stop intercepting before teardown
                user32.SetWindowLongPtrW(window, -4, previous)
            return user32.CallWindowProcW(previous, window, message, wparam, lparam)
        hooks[hwnd] = (previous, procedure)
        user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(procedure, ctypes.c_void_p).value)
    # Retain native resize / maximize / shadow behavior while Qt owns the title.
    style = user32.GetWindowLongPtrW(hwnd, -16)
    user32.SetWindowLongPtrW(hwnd, -16, style | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00030000)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)
    dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(1, 1, 1, 1)))
    # Windows 11 rounds and tints the native perimeter. Older Windows retains
    # the application's light 1px fallback; unsupported attributes are harmless.
    for attribute, value in ((33, 2), (34, 0x00D8D8D8)):
        data = ctypes.c_int(value)
        dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))


def set_topmost(hwnd, enabled):
    if os.name != 'nt':
        return False
    if not user32.SetWindowPos(hwnd, -1 if enabled else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def is_topmost(hwnd):
    return bool(user32.GetWindowLongPtrW(hwnd, -20) & 0x00000008) if os.name == 'nt' else None


def frame_report(hwnd):
    if os.name != 'nt':
        return {}
    rect, client = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.GetClientRect(hwnd, ctypes.byref(client))
    return {'window_size': [rect.right - rect.left, rect.bottom - rect.top],
            'client_size': [client.right, client.bottom], 'topmost': is_topmost(hwnd)}


def handle_native(hwnd, message, wparam, lparam):
    if os.name != 'nt':
        return None
    if message == 0x0083 and wparam:  # WM_NCCALCSIZE: no standard caption / thick rim
        if user32.IsZoomed(hwnd):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info)):
                rect = wintypes.RECT.from_address(lparam)
                rect.left, rect.top, rect.right, rect.bottom = (info.rcWork.left, info.rcWork.top,
                                                              info.rcWork.right, info.rcWork.bottom)
        return 0
    if message == 0x0084 and not user32.IsZoomed(hwnd):  # WM_NCHITTEST: all eight resize edges
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        x = ctypes.c_short(lparam & 0xffff).value
        y = ctypes.c_short((lparam >> 16) & 0xffff).value
        border = 6
        left, right = x < rect.left + border, x >= rect.right - border
        top, bottom = y < rect.top + border, y >= rect.bottom - border
        if top:
            return 13 if left else 14 if right else 12
        if bottom:
            return 16 if left else 17 if right else 15
        if left or right:
            return 10 if left else 11
    return None
