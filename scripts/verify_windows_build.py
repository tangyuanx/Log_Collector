"""Verify the actual frozen artifacts, including clean-PATH and Unicode startup."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import pefile
from pathlib import Path

if os.name != 'nt':
    raise SystemExit('Run this verification on Windows')

single = Path('dist/LogCollector.exe').resolve()
portable = Path('dist/LogCollector-Portable').resolve()
names = {str(path.relative_to(portable / '_internal')).replace('\\', '/').lower()
         for path in (portable / '_internal').rglob('*') if path.is_file()}
python_dll = f'python{sys.version_info.major}{sys.version_info.minor}.dll'
for required in (python_dll, 'vcruntime140.dll', 'vcruntime140_1.dll'):
    if required not in names:
        raise RuntimeError('Frozen executable lacks ' + required)
for name in names:
    basename = name.rsplit('/', 1)[-1]
    if basename == 'ucrtbase.dll' or basename.startswith(('api-ms-win-', 'ext-ms-win-')):
        raise RuntimeError('OS-specific runtime must not be redistributed: ' + name)
if not any(name.endswith('/platforms/qwindows.dll') for name in names):
    raise RuntimeError('Windows Qt platform plugin missing')
if 'assets/icon.png' not in names:
    raise RuntimeError('Application icon asset missing from bundle')
pe = pefile.PE(str(single))
if not any(entry.id == pefile.RESOURCE_TYPE['RT_GROUP_ICON']
           for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries):
    raise RuntimeError('Executable lacks Windows icon resource')

results = []
with tempfile.TemporaryDirectory(prefix='logcollector-verify-') as tmp:
    root = Path(tmp)
    # Python and Qt must come from the bundle, never the build-machine PATH.
    env = os.environ.copy()
    # os.environ is case-insensitive on Windows; its plain dict copy is not.
    system_root = Path(os.environ['SystemRoot'])
    env['PATH'] = str(system_root / 'System32') + ';' + str(system_root)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        env.pop(key, None)
    env['QT_QPA_PLATFORM'] = 'windows'
    env['APPDATA'] = str(root / 'isolated-profile')
    env['LOCALAPPDATA'] = str(root / '运行缓存')
    env.pop('LOGCOLLECTOR_LAUNCHER', None)
    cases = [
        ('launcher-normal', single, root / 'temp'),
        ('launcher-unicode', root / '中文 文件夹' / 'LogCollector.exe', root / '临时 目录'),
        ('launcher-no-temp', single, root / 'blocked-temp'),
        ('launcher-repair-cache', single, root / 'repair-temp'),
        ('portable', root / '便携 目录' / 'LogCollector.exe', root / 'portable-temp'),
    ]
    cases[1][1].parent.mkdir()
    shutil.copy2(single, cases[1][1])
    shutil.copytree(portable, cases[4][1].parent)
    for label, executable, tempdir in cases:
        if label == 'launcher-no-temp':
            # TEMP is deliberately a regular file, not a usable directory.
            tempdir.write_text('blocked')
        else:
            tempdir.mkdir()
        if label == 'launcher-repair-cache':
            for dll in Path(env['LOCALAPPDATA']).rglob('vcruntime140.dll'):
                dll.unlink()
        report = root / (label + '.json')
        env['TEMP'] = env['TMP'] = str(tempdir)
        process = subprocess.Popen([str(executable), '--startup-check', str(report)], env=env, cwd=root)
        try:
            status = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False, capture_output=True)
            raise RuntimeError('Frozen executable did not start/exit: ' + label)
        if status != 0 or not report.is_file():
            diagnostic = Path(env['LOCALAPPDATA']) / 'LogCollector/startup-error.txt'
            if diagnostic.exists(): print(diagnostic.read_text(encoding='utf-8-sig'))
            raise RuntimeError(f'{label} failed to start: exit {status}, report exists={report.is_file()}')
        result = json.loads(report.read_text(encoding='utf-8'))
        if not all(result.get(key) for key in ('visible', 'help_at_top', 'update_at_top', 'icon_loaded')):
            raise RuntimeError('Window/header verification failed: ' + label)
        if result.get('border_pixel') != '#919191':
            raise RuntimeError('Window outline was not painted: ' + label)
        if result.get('version_button') != 'v' + result['version']:
            raise RuntimeError('Version entry missing: ' + label)
        if label.startswith('launcher-'):
            if not result.get('launcher_path') or '_MEI' in result.get('runtime_path', ''):
                raise RuntimeError('Launcher still depends on temporary runtime: ' + label)
            if str(Path(env['LOCALAPPDATA']) / 'LogCollector' / 'runtime').casefold() not in result['runtime_path'].casefold():
                raise RuntimeError('Runtime was not installed in user cache: ' + label)
        results.append({'case': label, **result})

Path('dist/startup-verification.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
