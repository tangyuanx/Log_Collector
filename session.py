"""Bind a specific MobaXterm MoTTY surface; never resolve from the shared host."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil

WINDOW_ENUM_PROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
TERMINAL_CLASSES = ('cmotty', 'putty')


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    hwnd: int
    pid: int
    created: float
    class_name: str
    host: int
    host_pid: int
    host_created: float
    title: str


def _native():
    if os.name != 'nt':
        raise SessionError('窗口绑定仅支持 Windows。')
    user = ctypes.WinDLL('user32', use_last_error=True)
    signatures = {
        'WindowFromPoint': ([wintypes.POINT], wintypes.HWND),
        'GetAncestor': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        'GetWindowTextW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        'GetParent': ([wintypes.HWND], wintypes.HWND),
        'GetCursorPos': ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        'IsWindow': ([wintypes.HWND], wintypes.BOOL),
        'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
        'IsChild': ([wintypes.HWND, wintypes.HWND], wintypes.BOOL),
        'GetWindowRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        'GetClientRect': ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        'ClientToScreen': ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        'ScreenToClient': ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        'ChildWindowFromPointEx': ([wintypes.HWND, wintypes.POINT, wintypes.UINT], wintypes.HWND),
        'EnumChildWindows': ([wintypes.HWND, WINDOW_ENUM_PROC, wintypes.LPARAM], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(user, name)
        fn.argtypes, fn.restype = args, result
    return user


def _pid(user, hwnd):
    value = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
    return value.value


def _text(user, hwnd, name):
    buf = ctypes.create_unicode_buffer(2048)
    getattr(user, name)(hwnd, buf, len(buf))
    return buf.value


def _ancestors(user, hwnd, host):
    result = []
    while hwnd and hwnd not in result and len(result) < 64:
        result.append(hwnd)
        if hwnd == host:
            break
        hwnd = user.GetParent(hwnd)
    return result


def _client_bounds(user, hwnd):
    rect = wintypes.RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    start, end = wintypes.POINT(rect.left, rect.top), wintypes.POINT(rect.right, rect.bottom)
    if not (user.ClientToScreen(hwnd, ctypes.byref(start)) and user.ClientToScreen(hwnd, ctypes.byref(end))):
        return None
    return start.x, start.y, end.x, end.y


def _contains(bounds, point):
    return bounds and bounds[0] <= point.x < bounds[2] and bounds[1] <= point.y < bounds[3]


def _visible_bounds(user, hwnd, host):
    if not user.IsWindowVisible(hwnd):
        return None
    rect = wintypes.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    bounds = rect.left, rect.top, rect.right, rect.bottom
    # A visible flag alone does not exclude off-page/clipped terminal controls.
    for parent in _ancestors(user, user.GetParent(hwnd), host):
        clip = _client_bounds(user, parent)
        if not clip:
            return None
        bounds = max(bounds[0], clip[0]), max(bounds[1], clip[1]), min(bounds[2], clip[2]), min(bounds[3], clip[3])
    return bounds if bounds[0] < bounds[2] and bounds[1] < bounds[3] else None


def _resolve_terminal(user, hit, host, point):
    # Keep the direct terminal/child path. Never change an existing binding
    # when another tab becomes visible or acquires keyboard focus.
    for hwnd in _ancestors(user, hit, host):
        if _text(user, hwnd, 'GetClassNameW').casefold() in TERMINAL_CLASSES:
            return hwnd
    if point is None or not _contains(_client_bounds(user, host), point):
        raise SessionError('请拖到要采集的 MobaXterm 会话页面（显示区或输入区）。')

    # WindowFromPoint can skip static/disabled read-only output controls.
    # Descend the actual page under this same screen point, including those
    # controls; skip hidden tabs and mouse-transparent overlays only.
    path = [host]
    for _ in range(64):
        local = wintypes.POINT(point.x, point.y)
        if not user.ScreenToClient(path[-1], ctypes.byref(local)):
            break
        child = user.ChildWindowFromPointEx(path[-1], local, 0x0001 | 0x0004)
        if not child or child in path:
            break
        path.append(child)
    for hwnd in reversed(path):
        if _text(user, hwnd, 'GetClassNameW').casefold() in TERMINAL_CLASSES:
            return hwnd

    terminals = {}
    @WINDOW_ENUM_PROC
    def collect(hwnd, _):
        if _text(user, hwnd, 'GetClassNameW').casefold() in TERMINAL_CLASSES:
            bounds = _visible_bounds(user, hwnd, host)
            if bounds:
                terminals[hwnd] = bounds
        return True
    user.EnumChildWindows(host, collect, 0)

    # Find a terminal in the most specific page/pane containing the drop.
    # Serial output and the terminal input may be sibling controls. A unique
    # visible terminal in their common page is the stable session identity.
    # Do not promote a sidebar/title-bar drop to the entire MobaXterm host.
    for page in reversed(path[1:]):
        candidates = [hwnd for hwnd in terminals if user.IsChild(page, hwnd)]
        if not candidates:
            continue
        under_point = [hwnd for hwnd in candidates if _contains(terminals[hwnd], point)]
        if len(under_point) == 1:
            return under_point[0]
        if len(candidates) == 1 and not under_point:
            hwnd = candidates[0]
            bounds = terminals[hwnd]
            # Exclude adjacent shared navigation/side panels. Vertical space
            # above a serial input is the intended expanded drop target.
            if _contains(_client_bounds(user, page), point) and bounds[0] <= point.x < bounds[2]:
                return hwnd
            break
        raise SessionError('此区域包含多个会话，请拖到要采集的具体会话页面内。')
    raise SessionError('未在此页面识别到独立终端，请拖到要采集的 MobaXterm 会话显示区或输入区。')


def bind_handle(hwnd, point=None):
    user = _native()
    if not hwnd or not user.IsWindow(hwnd):
        raise SessionError('未识别到目标窗口，请重新拖选会话页面。')
    host = user.GetAncestor(hwnd, 2)
    host_pid = _pid(user, host)
    try:
        owner = psutil.Process(host_pid)
        if not owner.name().casefold().startswith('mobaxterm'):
            raise SessionError('目前支持 MobaXterm 会话，请拖到它的会话页面。')
        hwnd = _resolve_terminal(user, hwnd, host, point)
        pid = _pid(user, hwnd)
        class_name = _text(user, hwnd, 'GetClassNameW')
        process = psutil.Process(pid)
        if process.name().casefold() != 'motty.exe':
            raise SessionError('未识别到独立的 MobaXterm 终端，请重新拖选会话页面。')
        if pid == host_pid:
            raise SessionError('这个窗口不能区分独立会话，请选择具体的会话页面。')
        title = _text(user, hwnd, 'GetWindowTextW').strip()
        # Optional display metadata only; IP is never a session identifier.
        peers = set()
        try:
            peers = {(c.raddr.ip, c.raddr.port) for c in process.net_connections('tcp')
                     if c.status == psutil.CONN_ESTABLISHED and c.raddr}
        except (psutil.Error, OSError):
            pass
        if len(peers) == 1:
            address, port = peers.pop()
            title = f'{address}:{port}'
        session = Session(int(hwnd), pid, process.create_time(), class_name,
                          int(host), host_pid, owner.create_time(), title or f'会话 {pid}')
        validate(session)
        return session
    except psutil.Error as exc:
        raise SessionError('无法读取会话身份，请以与 MobaXterm 相同的权限运行工具。') from exc


def bind_cursor():
    user = _native()
    point = wintypes.POINT()
    if not user.GetCursorPos(ctypes.byref(point)):
        raise SessionError('无法读取指针位置，请重新选择。')
    return bind_handle(user.WindowFromPoint(point), point)


def validate(session):
    user = _native()
    try:
        valid = (user.IsWindow(session.hwnd) and user.IsWindow(session.host)
                 and _pid(user, session.hwnd) == session.pid
                 and _pid(user, session.host) == session.host_pid
                 and _text(user, session.hwnd, 'GetClassNameW') == session.class_name
                 and user.GetAncestor(session.hwnd, 2) == session.host
                 and psutil.Process(session.pid).create_time() == session.created
                 and psutil.Process(session.host_pid).create_time() == session.host_created)
    except psutil.Error:
        valid = False
    # Hidden tabs remain valid. Never bind whichever tab is currently visible.
    if not valid:
        raise SessionError('目标会话已关闭或被替换，请重新选择。')


def log_candidates(paths):
    """Only regular log files held by the selected MoTTY, excluding diagnostics."""
    result = {}
    for raw in paths:
        path = Path(raw)
        if path.suffix.casefold() not in ('.log', '.txt', '.out'):
            continue
        if any(word in path.name.casefold() for word in ('debug', 'eventlog', 'event_log')):
            continue
        try:
            if path.is_file():
                resolved = path.resolve()
                result[os.path.normcase(str(resolved))] = resolved
        except OSError:
            continue
    return sorted(result.values(), key=lambda p: str(p).casefold())


def discover_logs(session):
    validate(session)
    try:
        # Do not walk parent processes, sibling sessions, saved settings or
        # arbitrary directories. Windows may omit file handles, so a miss
        # is an explicit setup/error state, never permission to guess a file.
        paths = [file.path for file in psutil.Process(session.pid).open_files()]
    except psutil.AccessDenied as exc:
        raise SessionError('无法读取会话日志，请以与 MobaXterm 相同的权限运行工具。') from exc
    except psutil.Error as exc:
        raise SessionError('会话已失效，请重新选择目标。') from exc
    validate(session)
    candidates = log_candidates(paths)
    if not candidates:
        raise SessionError('未识别到该会话的日志。请先在 MobaXterm 开启日志输出，重新连接会话后再绑定；已开启仍失败时，当前版本无法自动关联此会话。')
    if len(candidates) > 1:
        raise SessionError('该会话同时打开了多个候选日志，无法确定采集来源。当前版本不会自动猜选，请只保留一个会话输出日志后重试。')
    return candidates[0]

