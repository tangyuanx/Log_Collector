"""Exercise the real Unicode-safe replacement helper and restarted native EXE."""
import base64
import os
import subprocess
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update import update_script

source = r'''
using System;
using System.IO;
using System.Threading;
class Fixture {
    static void Main(string[] args) {
        if(args.Length>0) { Thread.Sleep(1200); return; }
        File.WriteAllText(Environment.GetEnvironmentVariable("LOGCOLLECTOR_TEST_RESTART"),
            "restarted:" + (Environment.GetEnvironmentVariable("LOGCOLLECTOR_LAUNCHER") ?? "cleared"));
    }
}
'''
system = Path(os.environ['SystemRoot'])
with tempfile.TemporaryDirectory(prefix='logcollector-update-test-') as tmp:
    root = Path(tmp) / "中文 O'Brien %!"
    root.mkdir()
    fixture = root / 'fixture.cs'
    fixture.write_text(source, encoding='utf-8-sig')
    target = root / '日志采集.exe'
    new = root / '新版.exe'
    csc = system / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(csc), '/nologo', '/target:winexe', '/platform:x64', '/out:' + str(target), str(fixture)], check=True)
    old_data = target.read_bytes()
    # Deliberately differ so replacement, backup and restart can be verified.
    new.write_bytes(old_data + b'new-version-marker')
    old = subprocess.Popen([str(target), '--hold'])
    encoded = base64.b64encode(update_script(target, new, old.pid).encode('utf-16le')).decode('ascii')
    marker = root / 'restart.txt'
    env = os.environ.copy()
    env['LOGCOLLECTOR_TEST_RESTART'] = str(marker)
    env['LOGCOLLECTOR_LAUNCHER'] = 'old-launcher-path'
    ps = system / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    helper = subprocess.Popen([str(ps), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-EncodedCommand', encoded], env=env)
    try:
        helper.wait(timeout=45)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert marker.read_text() == 'restarted:cleared', 'New executable did not restart with fresh environment'
        assert target.read_bytes() == old_data + b'new-version-marker', 'New executable was not installed'
        assert Path(str(target) + '.bak').read_bytes() == old_data, 'Original executable was not retained'
        assert not new.exists(), 'Downloaded executable was not moved'
        print('Native updater passed: waits for exit, replaces EXE, preserves backup and restarts; Unicode, apostrophe, % and ! paths work.')
    finally:
        if helper.poll() is None:
            subprocess.run(['taskkill', '/PID', str(helper.pid), '/T', '/F'], capture_output=True)
        old.wait(timeout=5)
