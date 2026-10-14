import tempfile
import unittest
from pathlib import Path
from capture import FileCapture, CaptureError, safe_name

class FileCaptureTests(unittest.TestCase):
    def test_only_future_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / 'session.log'
            src.write_bytes(b'old\r\n')
            c = FileCapture()
            target = c.start(src, root / 'out', '192.168.1.1')
            with src.open('ab') as f: f.write(b'new\r\n\xff\x00')
            self.assertEqual(c.poll(), 7)
            self.assertEqual(c.poll(), 0)
            c.stop()
            self.assertEqual(target.read_bytes(), b'new\r\n\xff\x00')
    def test_truncation_does_not_silently_mix(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            src=root/'session.log'
            src.write_bytes(b'old data that is long')
            c=FileCapture()
            c.start(src,root/'out','test')
            src.write_bytes(b'x')
            with self.assertRaises(CaptureError): c.poll()
            c.abort()
    def test_safe_filename(self):
        self.assertEqual(safe_name('10.0.0.1:22 / abc'), '10.0.0.1_22 _ abc')

if __name__ == '__main__': unittest.main()
