"""Capture only bytes appended to an existing MobaXterm log after Start."""
from __future__ import annotations
import os
import re
from pathlib import Path
from datetime import datetime

class CaptureError(RuntimeError):
    pass

def safe_name(s):
    return (re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', s).strip(' ._')[:65] or 'session')

class FileCapture:
    def __init__(self):
        self.src = self.dst = None
        self.source = self.target = None
        self.offset = self.count = 0
        self.started = None
        self.identity = None
    @property
    def active(self):
        return self.src is not None
    def start(self, source, output_dir, label):
        if self.active: raise CaptureError('Please stop the current capture first')
        p = Path(source).expanduser().resolve(strict=True)
        if not p.is_file(): raise CaptureError('Source must be a log file')
        folder = Path(output_dir).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        fh = open(p, 'rb', buffering=0)
        try:
            fh.seek(0, os.SEEK_END)
            offset = fh.tell()
            ident = os.fstat(fh.fileno())
            prefix = safe_name(label) + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            for i in range(1000):
                target = folder / (prefix + (f'_{i}' if i else '') + '.log')
                try:
                    out = open(target, 'xb', buffering=0)
                    break
                except FileExistsError: continue
            else: raise CaptureError('Unable to create unique output file')
        except Exception:
            fh.close()
            raise
        self.src, self.dst = fh, out
        self.source, self.target = p, target
        self.identity = (ident.st_dev, ident.st_ino)
        self.offset, self.count = offset, 0
        self.started = datetime.now()
        return target
    def poll(self, limit=1048576):
        if not self.active: return 0
        try:
            path_stat = self.source.stat()
            opened_stat = os.fstat(self.src.fileno())
            if self.identity != (path_stat.st_dev, path_stat.st_ino):
                raise CaptureError('Source file was replaced; stopped to avoid mixing sessions')
            if min(path_stat.st_size, opened_stat.st_size) < self.offset:
                raise CaptureError('Source log was truncated; capture stopped')
            pending = min(path_stat.st_size - self.offset, limit)
            read_bytes = 0
            while pending:
                data = self.src.read(min(pending, 65536))
                if not data: break
                n = self.dst.write(data)
                if n != len(data): raise CaptureError('Incomplete write')
                pending -= n
                read_bytes += n
                self.offset += n
                self.count += n
            return read_bytes
        except OSError as e:
            raise CaptureError(str(e)) from e
    def stop(self):
        if not self.active: return None
        try:
            # Fix the boundary when Stop is clicked; a streaming source must
            # not keep extending the capture while the tail is being drained.
            end = os.fstat(self.src.fileno()).st_size
            self.poll(limit=0)  # Validate replacement/truncation even with no tail.
            while self.offset < end:
                if self.poll(limit=min(1048576, end - self.offset)) == 0:
                    raise CaptureError('Source changed before pending bytes could be saved')
            return self.target
        finally:
            self.abort()
    def abort(self):
        for handle in (self.src, self.dst):
            if handle:
                try: handle.close()
                except OSError: pass
        self.src = self.dst = None

def scan_logs(folder, limit=300):
    root = Path(folder).expanduser()
    if not root.is_dir(): return []
    entries = []
    visited = 0
    for here, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > 150: break
        if len(Path(here).relative_to(root).parts) >= 3: dirs[:] = []
        for name in files:
            if not name.lower().endswith(('.log', '.txt', '.out')): continue
            p = Path(here) / name
            try:
                entries.append((p.stat().st_mtime, p))
            except OSError: pass
    entries.sort(reverse=True)
    return [p for _, p in entries[:limit]]
