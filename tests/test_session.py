import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from session import Session, SessionError, discover_logs, log_candidates, validate, _native


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(100, 10, 123.0, 'CMoTTY', 200, 20, 100.0, 'server')

    def test_discovery_reads_only_bound_process(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'session.log'
            path.write_bytes(b'')
            process = SimpleNamespace(open_files=lambda: [SimpleNamespace(path=str(path))])
            with patch('session.validate'), patch('session.psutil.Process', return_value=process) as lookup:
                self.assertEqual(discover_logs(self.session), path)
                lookup.assert_called_once_with(10)

    def test_multiple_logs_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d) / name for name in ('first.log', 'second.log')]
            for path in paths:
                path.write_bytes(b'')
            process = SimpleNamespace(open_files=lambda: [SimpleNamespace(path=str(p)) for p in paths])
            with patch('session.validate'), patch('session.psutil.Process', return_value=process):
                with self.assertRaisesRegex(SessionError, '多个'):
                    discover_logs(self.session)

    def test_missing_handle_does_not_search_other_sessions(self):
        process = SimpleNamespace(open_files=lambda: [])
        with patch('session.validate'), patch('session.psutil.Process', return_value=process) as lookup:
            with self.assertRaisesRegex(SessionError, '未识别'):
                discover_logs(self.session)
            lookup.assert_called_once_with(10)

    def test_diagnostic_files_and_duplicates_are_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            paths = [Path(d) / name for name in ('session.log', 'debug.log', 'event_log.txt', 'settings.ini')]
            for path in paths:
                path.write_bytes(b'')
            self.assertEqual(log_candidates(paths + [paths[0]]), [paths[0]])

    def test_recycled_pid_invalidates_binding(self):
        class Native:
            def IsWindow(self, handle): return True
            def GetAncestor(self, handle, mode): return 200
        with patch('session._native', return_value=Native()), patch('session._pid', side_effect=[10, 20]), \
             patch('session._text', return_value='CMoTTY'), \
             patch('session.psutil.Process', return_value=SimpleNamespace(create_time=lambda: 124.0)):
            with self.assertRaisesRegex(SessionError, '已关闭'):
                validate(self.session)

    def test_hidden_tab_is_still_same_session(self):
        class Native:
            def IsWindow(self, handle): return True
            def GetAncestor(self, handle, mode): return 200
        with patch('session._native', return_value=Native()), patch('session._pid', side_effect=[10, 20]), \
             patch('session._text', return_value='CMoTTY'), \
             patch('session.psutil.Process', side_effect=[SimpleNamespace(create_time=lambda: 123.0),
                                                       SimpleNamespace(create_time=lambda: 100.0)]):
            validate(self.session)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows native API smoke test')
    def test_native_windows_signatures(self):
        import ctypes
        from ctypes import wintypes
        user = _native()
        self.assertFalse(user.IsWindow(0))
        self.assertEqual(ctypes.sizeof(user.WindowFromPoint.restype), ctypes.sizeof(ctypes.c_void_p))
