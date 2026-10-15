"""GitHub Release checks and verified native-launcher updates."""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPOSITORY = 'tangyuanx/Log_Collector'
VERSION = '0.1.6'
API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'


def _request(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Log-Collector/' + VERSION})
    return urllib.request.urlopen(req, timeout=25)


def version_tuple(value):
    if not re.fullmatch(r'[vV]?\d+\.\d+\.\d+', value):
        raise RuntimeError('发布版本号格式无效')
    return tuple(int(x) for x in value.lstrip('vV').split('.'))


def check_update():
    with _request(API) as response:
        release = json.load(response)
    latest = release.get('tag_name', '')
    if version_tuple(latest) <= version_tuple(VERSION):
        return None
    assets = {x['name']: x for x in release.get('assets', [])}
    if 'LogCollector.exe' not in assets or 'SHA256SUMS.txt' not in assets:
        raise RuntimeError('新版本缺少程序或 SHA256 校验文件')
    return {'version': latest, 'exe': assets['LogCollector.exe']['browser_download_url'],
            'sha': assets['SHA256SUMS.txt']['browser_download_url'],
            'size': assets['LogCollector.exe'].get('size', 0)}


def download_verified(info, folder, progress=lambda done, total, stage: None):
    """Download byte progress; return only after length and SHA256 validation."""
    new = Path(folder) / 'LogCollector.exe'
    with _request(info['sha']) as response:
        sums = response.read(128000).decode('utf-8')
    checksums = []
    for line in sums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == 'LogCollector.exe':
            checksums.append(fields[0].lower())
    if len(checksums) != 1 or not re.fullmatch('[0-9a-f]{64}', checksums[0]):
        raise RuntimeError('发布文件中的 SHA256 校验值无效')
    sha = hashlib.sha256()
    done = 0
    try:
        with _request(info['exe']) as response, new.open('wb') as out:
            expected = int(info.get('size') or response.headers.get('Content-Length') or 0)
            progress(0, expected, '下载中')
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                out.write(block)
                done += len(block)
                sha.update(block)
                progress(done, expected, '下载中')
        if expected and done != expected:
            raise RuntimeError('下载未完成，文件大小与发布记录不符')
        progress(done, expected, '正在校验 SHA256')
        if sha.hexdigest() != checksums[0]:
            raise RuntimeError('下载文件校验失败，请重试')
        progress(done, expected, '校验完成，准备重启')
        return new
    except Exception:
        new.unlink(missing_ok=True)
        raise


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def update_script(exe, new, pid):
    """Unicode-safe helper; bounded retries and restore on replacement failure."""
    return f"""$ErrorActionPreference = 'Stop'
$target = {_ps_quote(exe)}
$new = {_ps_quote(new)}
$backup = $target + '.bak'
$log = {_ps_quote(Path(new).parent / 'update-error.txt')}
try {{
    $parent = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue
    if ($parent) {{ $parent.WaitForExit() }}
    $moved = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {{
        try {{ Move-Item -LiteralPath $target -Destination $backup -Force; $moved = $true; break }}
        catch {{ Start-Sleep -Milliseconds 1000 }}
    }}
    if (-not $moved) {{ throw '无法替换程序，请检查目录写权限。' }}
    try {{ Move-Item -LiteralPath $new -Destination $target -Force }}
    catch {{ Move-Item -LiteralPath $backup -Destination $target -Force; throw }}
    Remove-Item Env:LOGCOLLECTOR_LAUNCHER -ErrorAction SilentlyContinue
    Start-Process -FilePath $target
}} catch {{
    $_ | Out-String | Set-Content -LiteralPath $log -Encoding UTF8
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('升级未完成。原程序已保留。诊断文件：' + $log, '日志采集 · 升级失败') | Out-Null
}}
"""


def download_and_install(info, progress=lambda done, total, stage: None):
    if not getattr(sys, 'frozen', False) or os.name != 'nt':
        raise RuntimeError('请使用发布的 Windows EXE 进行软件内升级')
    launcher = os.environ.get('LOGCOLLECTOR_LAUNCHER')
    if not launcher:
        raise RuntimeError('目录版请下载完整便携包；软件内升级请使用发布页的单文件 LogCollector.exe')
    exe = Path(launcher).resolve()
    if not exe.is_file():
        raise RuntimeError('原启动器不存在，请重新下载单文件程序')
    tempdir = Path(tempfile.mkdtemp(prefix='logcollector-update-'))
    try:
        new = download_verified(info, tempdir, progress)
        script = update_script(exe, new, os.getpid())
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                          '-EncodedCommand', encoded], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                         close_fds=True)
    except Exception:
        shutil.rmtree(tempdir, ignore_errors=True)
        raise

