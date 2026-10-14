import tempfile
import unittest
from pathlib import Path
from capture import FileCapture, CaptureError, safe_name, LineCounter

class FileCaptureTests(unittest.TestCase):
    def test_line_counts_across_every_split_of_mixed_newlines(self):
        data = b'first\r\nsecond\rthird\n\nlast'
        for cut in range(len(data) + 1):
            counter = LineCounter()
            counter.feed(data[:cut])
            counter.feed(data[cut:])
            self.assertEqual(counter.count, 5, f'Incorrect at split {cut}')
            self.assertTrue(counter.pending)
        counter = LineCounter()
        for value in data:
            counter.feed(bytes([value]))
        self.assertEqual(counter.count, 5)

    def test_completing_partial_line_does_not_count_it_twice(self):
        counter = LineCounter()
        for chunk in (b'first', b' part', b'\r', b'\n'):
            counter.feed(chunk)
            self.assertEqual(counter.count, 1)
        self.assertFalse(counter.pending)
        counter.feed(b'next')
        self.assertEqual(counter.count, 2)

    def test_capture_line_count_activity_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'session.log'
            source.write_bytes(b'old\r\nhistory\n')
            capture = FileCapture()
            capture.start(source, root / 'out', 'test')
            self.assertEqual(capture.line_count, 0)
            self.assertIsNone(capture.last_write_at)
            for chunk, expected in ((b'first', 1), (b'\r', 1), (b'\nsecond\n', 2)):
                with source.open('ab') as stream: stream.write(chunk)
                capture.poll()
                self.assertEqual(capture.line_count, expected)
            stamp = capture.last_write_at
            self.assertEqual(capture.poll(), 0)
            self.assertEqual(capture.last_write_at, stamp)
            capture.stop()
            self.assertEqual(capture.line_count, 2)
            capture.start(source, root / 'out', 'test')
            self.assertEqual(capture.line_count, 0)
            self.assertIsNone(capture.last_write_at)
            capture.stop()

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
    def test_stop_does_not_include_bytes_added_after_stop_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'session.log'
            source.write_bytes(b'old')
            capture = FileCapture()
            target = capture.start(source, root / 'out', 'test')
            with source.open('ab') as stream: stream.write(b'within')
            poll = capture.poll
            def concurrent_append(limit=1048576):
                with source.open('ab') as stream: stream.write(b'after-stop')
                return poll(limit)
            capture.poll = concurrent_append
            capture.stop()
            self.assertEqual(target.read_bytes(), b'within')
    def test_stop_validates_source_even_when_no_tail_remains(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / 'session.log'
            source.write_bytes(b'old-history')
            capture = FileCapture()
            capture.start(source, root / 'out', 'test')
            source.write_bytes(b'x')
            with self.assertRaises(CaptureError): capture.stop()
            self.assertFalse(capture.active)

if __name__ == '__main__': unittest.main()
