import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update

class Response(io.BytesIO):
    headers = {}

class UpdateTests(unittest.TestCase):
    def responses(self, data, checksum=None):
        checksum = checksum or hashlib.sha256(data).hexdigest()
        return [Response((checksum + '  LogCollector.exe\n').encode()), Response(data)]

    def test_download_bytes_and_sha_before_ready(self):
        data = b'new executable' * 30000
        progress = []
        with tempfile.TemporaryDirectory() as folder, patch('update._request', side_effect=self.responses(data)):
            result = update.download_verified({'sha': 'sha', 'exe': 'exe', 'size': len(data)}, folder,
                                              lambda *values: progress.append(values))
            self.assertEqual(result.read_bytes(), data)
        self.assertEqual(progress[0], (0, len(data), '下载中'))
        self.assertEqual(progress[-1], (len(data), len(data), '校验完成，准备重启'))
        self.assertIn('正在校验 SHA256', [x[2] for x in progress])

    def test_wrong_checksum_and_partial_download_are_removed(self):
        data = b'bad download'
        for size, checksum in ((len(data), '0' * 64), (len(data) + 1, None)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as folder:
                with patch('update._request', side_effect=self.responses(data, checksum)), self.assertRaises(RuntimeError):
                    update.download_verified({'sha': 'sha', 'exe': 'exe', 'size': size}, folder)
                self.assertFalse((Path(folder) / 'LogCollector.exe').exists())

    def test_missing_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch('update._request', return_value=Response(b'\n')):
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                update.download_verified({'sha': 'sha', 'exe': 'exe'}, folder)

    def test_helper_handles_unicode_and_literal_special_characters(self):
        target = Path("C:/用户/O'Brien %!/日志采集.exe")
        script = update.update_script(target, Path('C:/临时/new.exe'), 123)
        self.assertIn("O''Brien %!", script)
        self.assertIn('Get-Process -Id 123', script)
        self.assertIn('$attempt -lt 30', script)
        self.assertIn('Move-Item -LiteralPath $backup -Destination $target', script)
        self.assertEqual(script, script.encode('utf-16le').decode('utf-16le'))
