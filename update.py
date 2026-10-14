"""Opt-in GitHub Release update for a frozen Windows .exe."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPOSITORY = 'tangyuanx/Log_Collector'
VERSION = '0.1.1'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'

def _request(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Log-Collector/' + VERSION, 'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(req, timeout=25)

def version_tuple(value):
    return tuple(int(x) for x in value.lstrip('vV').split('.') if x.isdecimal())

def check_update():
    with _request(API) as response:
        release = json.load(response)
    latest = release.get('tag_name', '')
    if not latest or version_tuple(latest) <= version_tuple(VERSION): return None
    assets = {x['name']: x['browser_download_url'] for x in release.get('assets', [])}
    if 'LogCollector.exe' not in assets or 'SHA256SUMS.txt' not in assets:
        raise RuntimeError('Release must include LogCollector.exe and SHA256SUMS.txt')
    return {'version': latest, 'exe': assets['LogCollector.exe'], 'sha': assets['SHA256SUMS.txt']}

def download_and_install(info):
    if not getattr(sys, 'frozen', False) or os.name != 'nt':
        raise RuntimeError('In-app replacement works only in packaged Windows executable')
    exe = Path(sys.executable).resolve()
    # Avoid silently overwriting programs from other locations.
    tempdir = Path(tempfile.mkdtemp(prefix='logcollector-update-'))
    new = tempdir / 'LogCollector.exe'
    with _request(info['sha']) as r: sums = r.read(128000).decode('utf-8')
    matching = [line.split()[0].lower() for line in sums.splitlines() if line.strip().split()[-1].lstrip('*') == 'LogCollector.exe']
    if len(matching) != 1 or len(matching[0]) != 64:
        raise RuntimeError('Missing SHA256 checksum in release')
    sha = hashlib.sha256()
    with _request(info['exe']) as r, open(new, 'wb') as out:
        while True:
            block = r.read(256 * 1024)
            if not block: break
            out.write(block)
            sha.update(block)
    if sha.hexdigest() != matching[0]:
        new.unlink(missing_ok=True)
        raise RuntimeError('Downloaded executable checksum mismatch')
    # Helper lives outside the exe and waits for the parent to exit. The old
    # binary is retained as .bak for recovery; errors are recorded to a log.
    script = tempdir / 'apply_update.cmd'
    # Using cmd arguments avoids PowerShell policy and paths are escaped.
    # Percent signs in Windows paths can expand in .cmd, so reject them.
    if any('%' in str(p) or '!' in str(p) for p in (exe, new)):
        raise RuntimeError('Paths containing % or ! are unsupported for updater')
    script.write_text('@echo off\r\nsetlocal DisableDelayedExpansion\r\n'
        f':retry\r\ntimeout /t 2 /nobreak >nul\r\n'
        f'move /Y "{exe}" "{exe}.bak" >nul 2>&1\r\n'
        f'if errorlevel 1 goto retry\r\n'
        f'move /Y "{new}" "{exe}" >nul 2>&1\r\n'
        f'if errorlevel 1 (move /Y "{exe}.bak" "{exe}" >nul & exit /b 1)\r\n'
        f'start "" "{exe}"\r\n', encoding='utf-8')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.Popen(['cmd.exe', '/d', '/c', str(script)], creationflags=flags, close_fds=True)
