"""Regression cases for page-level drops without requiring MobaXterm installed."""
import ctypes
import os
import unittest
from ctypes import wintypes
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from session import SessionError, bind_cursor, bind_handle, _native, _resolve_terminal


@dataclass
class Node:
    parent: int
    bounds: tuple
    kind: str = 'Panel'
    pid: int = 20
    visible: bool = True


class WindowTree:
    """Win32-shaped model: screen/client coordinates and ancestor visibility."""
    def __init__(self):
        self.nodes = {
            1: Node(0, (0, 0, 1000, 800)),
            2: Node(1, (0, 50, 1000, 800)),
            3: Node(2, (0, 50, 200, 800), 'Navigation'),
            10: Node(2, (200, 100, 1000, 800), 'SerialPage'),
            11: Node(10, (200, 100, 1000, 700), 'Static'),
            100: Node(10, (200, 700, 1000, 800), 'CMoTTY', 10),
            101: Node(100, (200, 750, 1000, 800), 'Edit', 10),
        }
        self.point = wintypes.POINT(400, 300)

    def IsWindow(self, hwnd): return hwnd in self.nodes
    def GetParent(self, hwnd): return self.nodes[hwnd].parent
    def GetAncestor(self, hwnd, mode):
        while self.GetParent(hwnd): hwnd = self.GetParent(hwnd)
        return hwnd
    def IsChild(self, parent, child):
        while child:
            child = self.GetParent(child)
            if child == parent: return True
        return False
    def IsWindowVisible(self, hwnd):
        while hwnd:
            if not self.nodes[hwnd].visible: return False
            hwnd = self.GetParent(hwnd)
        return True
    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = self.nodes[hwnd].pid
        return 1
    def GetClassNameW(self, hwnd, buffer, size):
        buffer.value = self.nodes[hwnd].kind
        return len(buffer.value)
    def GetWindowTextW(self, hwnd, buffer, size):
        buffer.value = 'COM3' if self.nodes[hwnd].pid == 10 else 'COM4'
        return len(buffer.value)
    def GetWindowRect(self, hwnd, rect):
        rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = self.nodes[hwnd].bounds
        return True
    def GetClientRect(self, hwnd, rect):
        left, top, right, bottom = self.nodes[hwnd].bounds
        rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = 0, 0, right-left, bottom-top
        return True
    def ClientToScreen(self, hwnd, point):
        point._obj.x += self.nodes[hwnd].bounds[0]
        point._obj.y += self.nodes[hwnd].bounds[1]
        return True
    def ScreenToClient(self, hwnd, point):
        point._obj.x -= self.nodes[hwnd].bounds[0]
        point._obj.y -= self.nodes[hwnd].bounds[1]
        return True
    def ChildWindowFromPointEx(self, parent, point, flags):
        x = point.x + self.nodes[parent].bounds[0]
        y = point.y + self.nodes[parent].bounds[1]
        for hwnd, node in reversed(list(self.nodes.items())):
            left, top, right, bottom = node.bounds
            if node.parent == parent and self.IsWindowVisible(hwnd) and left <= x < right and top <= y < bottom:
                return hwnd
        return parent
    def EnumChildWindows(self, parent, callback, value):
        for hwnd in self.nodes:
            if self.IsChild(parent, hwnd) and not callback(hwnd, value): break
        return True
    def GetCursorPos(self, point):
        point._obj.x, point._obj.y = self.point.x, self.point.y
        return True
    def WindowFromPoint(self, point):
        # Read-only/static output can make WindowFromPoint return the host.
        return 1


class PageBindingTests(unittest.TestCase):
    def setUp(self):
        self.user = WindowTree()
        self.native = patch('session._native', return_value=self.user)
        self.native.start()
        def process(pid):
            return SimpleNamespace(name=lambda: 'MobaXterm.exe' if pid == 20 else 'motty.exe',
                create_time=lambda: float(pid), net_connections=lambda kind: [])
        self.process = patch('session.psutil.Process', side_effect=process)
        self.process.start()
        self.addCleanup(self.native.stop)
        self.addCleanup(self.process.stop)

    def bind(self, hit=11, x=400, y=300):
        return bind_handle(hit, wintypes.POINT(x, y))

    def test_output_and_input_bind_the_same_serial_session(self):
        output = self.bind()
        self.assertEqual(output, self.bind(101, 400, 760))
        self.assertEqual((output.hwnd, output.pid, output.title), (100, 10, 'COM3'))

    def test_empty_space_in_serial_page_is_bindable(self):
        self.user.nodes[11].bounds = (200, 100, 800, 600)
        self.assertEqual(self.bind(10, 950, 650).hwnd, 100)

    def test_cursor_drop_recovers_page_when_system_hit_is_host(self):
        self.assertEqual(bind_cursor().hwnd, 100)

    def test_hidden_overlapping_tab_is_not_selected(self):
        self.user.nodes[20] = Node(2, (200, 100, 1000, 800), visible=False)
        self.user.nodes[200] = Node(20, (200, 100, 1000, 800), 'CMoTTY', 11)
        self.assertEqual(self.bind().hwnd, 100)

    def split(self):
        for hwnd in (10, 11, 100, 101):
            left, top, right, bottom = self.user.nodes[hwnd].bounds
            self.user.nodes[hwnd].bounds = left, top, 600, bottom
        self.user.nodes[20] = Node(2, (600, 100, 1000, 800), 'SerialPage')
        self.user.nodes[21] = Node(20, (600, 100, 1000, 700), 'Static')
        self.user.nodes[200] = Node(20, (600, 700, 1000, 800), 'CMoTTY', 11)

    def test_split_panes_bind_page_under_drop(self):
        self.split()
        self.assertEqual(self.bind().hwnd, 100)
        self.assertEqual(self.bind(21, 800, 300).hwnd, 200)

    def test_shared_space_with_two_sessions_is_not_guessed(self):
        self.split()
        with self.assertRaisesRegex(SessionError, '多个会话'):
            self.bind(2, 400, 70)

    def test_sidebar_does_not_select_the_only_visible_session(self):
        with self.assertRaises(SessionError): self.bind(3, 100, 300)

    def test_native_title_bar_is_not_a_page(self):
        # Model a point above the client rectangle.
        with self.assertRaises(SessionError): self.bind(1, 400, -10)

    def test_empty_page_does_not_take_another_pages_session(self):
        self.split()
        del self.user.nodes[100]
        del self.user.nodes[101]
        with self.assertRaises(SessionError): self.bind()

    def test_terminal_clipped_outside_its_page_is_ignored(self):
        self.user.nodes[100].bounds = (200, 850, 1000, 950)
        self.user.nodes[101].bounds = (200, 850, 1000, 950)
        with self.assertRaises(SessionError): self.bind()

    def test_non_mobaxterm_window_is_rejected(self):
        with patch('session.psutil.Process', return_value=SimpleNamespace(name=lambda:'other.exe')):
            with self.assertRaisesRegex(SessionError, 'MobaXterm'): self.bind()

    def test_negative_monitor_coordinates(self):
        for node in self.user.nodes.values():
            left, top, right, bottom = node.bounds
            node.bounds = left-1600, top-900, right-1600, bottom-900
        self.assertEqual(self.bind(11, -1200, -600).hwnd, 100)


@unittest.skipUnless(os.name == 'nt', 'Requires real Win32 child windows')
class NativePageBindingTests(unittest.TestCase):
    def test_readonly_output_and_input_siblings_using_real_windows(self):
        """Real HWND traversal, clipping, visibility and point conversions."""
        user = _native()
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        class WNDCLASS(ctypes.Structure):
            _fields_ = [('style', wintypes.UINT), ('proc', proc_type), ('cls_extra', ctypes.c_int),
                ('wnd_extra', ctypes.c_int), ('instance', wintypes.HINSTANCE), ('icon', wintypes.HICON),
                ('cursor', wintypes.HANDLE), ('background', wintypes.HBRUSH),
                ('menu', wintypes.LPCWSTR), ('name', wintypes.LPCWSTR)]
        user.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user.RegisterClassW.restype = wintypes.WORD
        user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
            wintypes.HINSTANCE, wintypes.LPVOID]
        user.CreateWindowExW.restype = wintypes.HWND
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.DestroyWindow.restype = wintypes.BOOL
        user.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user.UnregisterClassW.restype = wintypes.BOOL
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.ShowWindow.restype = wintypes.BOOL
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        instance = kernel.GetModuleHandleW(None)
        callback = proc_type(user.DefWindowProcW)
        names = ['LogCollectorBindingHost', 'CMoTTY']
        registered = []
        host = None
        try:
            for name in names:
                record = WNDCLASS(); record.proc = callback; record.name = name; record.instance = instance
                self.assertTrue(user.RegisterClassW(ctypes.byref(record)))
                registered.append(name)
            def create(kind, parent, x, y, width, height, extra=0):
                handle = user.CreateWindowExW(0, kind, '', 0x10000000 | (0x40000000 if parent else 0x80000000) | extra,
                    x, y, width, height, parent, None, instance, None)
                self.assertTrue(handle)
                return handle
            host = create(names[0], None, 80, 80, 600, 500)
            page = create('Static', host, 100, 40, 480, 440)
            output = create('Static', page, 0, 0, 480, 360, 0x08000000)  # WS_DISABLED
            terminal = create('CMoTTY', page, 0, 360, 480, 80)
            hidden_page = create('Static', host, 100, 40, 480, 440)
            create('CMoTTY', hidden_page, 0, 0, 480, 440)
            user.ShowWindow(hidden_page, 0)
            point = wintypes.POINT(200, 150)
            self.assertTrue(user.ClientToScreen(host, ctypes.byref(point)))
            self.assertEqual(_resolve_terminal(user, output, host, point), terminal)
            self.assertEqual(_resolve_terminal(user, host, host, point), terminal)
            self.assertEqual(_resolve_terminal(user, terminal, host, point), terminal)
            # No visible terminal means failure, not a hidden-tab selection.
            user.ShowWindow(terminal, 0)
            with self.assertRaises(SessionError): _resolve_terminal(user, host, host, point)
        finally:
            if host: user.DestroyWindow(host)
            for name in reversed(registered): user.UnregisterClassW(name, instance)
