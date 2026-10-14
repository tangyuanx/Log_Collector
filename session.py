"""Bind a specific MobaXterm MoTTY surface; never resolve from the shared host."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil


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


def bind_handle(hwnd):
    user = _native()
    # The native CMoTTY child owns a session's stream. The MobaXterm root
    # owns multiple sessions and cannot be used as a capture lookup root.
    for _ in range(16):
        if not hwnd:
            break
        class_name = _text(user, hwnd, 'GetClassNameW')
        if class_name.casefold() in ('cmotty', 'putty'):
            break
        hwnd = user.GetParent(hwnd)
    else:
        hwnd = None
    if not hwnd or _text(user, hwnd, 'GetClassNameW').casefold() not in ('cmotty', 'putty'):
        raise SessionError('请拖到 MobaXterm 会话的终端文字区域，不要选择标题栏或标签页。')
    pid = _pid(user, hwnd)
    host = user.GetAncestor(hwnd, 2)
    host_pid = _pid(user, host)
    try:
        process, owner = psutil.Process(pid), psutil.Process(host_pid)
        if process.name().casefold() != 'motty.exe' or not owner.name().casefold().startswith('mobaxterm'):
            raise SessionError('目前支持 MobaXterm 会话，请选择它的终端文字区域。')
        if pid == host_pid:
            raise SessionError('这个窗口不能区分独立会话，请选择具体的终端文字区域。')
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
    return bind_handle(user.WindowFromPoint(point))


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
