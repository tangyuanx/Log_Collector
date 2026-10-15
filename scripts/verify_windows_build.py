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
    env['LOGCOLLECTOR_TEST_FONT'] = str(Path('build/typography-fixture/simhei.ttf').resolve())
    env['APPDATA'] = str(root / 'isolated-profile')
    env['LOCALAPPDATA'] = str(root / '运行缓存')
    env.pop('LOGCOLLECTOR_LAUNCHER', None)
    cases = [
        ('launcher-normal', single, root / 'temp'),
        ('launcher-unicode', root / '中文 文件夹' / 'LogCollector.exe', root / '临时 目录'),
        ('launcher-no-temp', single, root / 'blocked-temp'),
        ('launcher-repair-cache', single, root / 'repair-temp'),
        ('portable', root / '便携 目录' / 'LogCollector.exe', root / 'portable-temp'),
        ('launcher-dpi125', single, root / 'dpi125-temp'),
        ('launcher-dpi150', single, root / 'dpi150-temp'),
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
        env['QT_SCALE_FACTOR'] = '1.25' if label.endswith('dpi125') else '1.5' if label.endswith('dpi150') else '1'
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
            if diagnostic.exists(): print(json.dumps(diagnostic.read_text(encoding='utf-8-sig'), ensure_ascii=True))
            raise RuntimeError(f'{label} failed to start: exit {status}, report exists={report.is_file()}')
        result = json.loads(report.read_text(encoding='utf-8'))
        if not all(result.get(key) for key in ('visible', 'help_at_top', 'update_at_top', 'icon_loaded')):
            raise RuntimeError('Window/header verification failed: ' + label)
        if result.get('border_pixel') != '#d8d8d8':
            raise RuntimeError('Window outline was not painted: ' + label)
        if not all(result.get(key) for key in ('pin_native_enabled', 'pin_native_disabled', 'pin_preserved_window', 'ui_icons_loaded')):
            raise RuntimeError('Native pin or glyph verification failed: ' + label + ' ' + json.dumps(result))
        frame = result.get('native_frame', {})
        if frame.get('window_size') != frame.get('client_size'):
            raise RuntimeError('Unwanted native frame/caption is visible: ' + label + ' ' + json.dumps(frame))
        typography = result.get('typography', {})
        for text in typography.get('labels', []):
            if not text['no_missing_glyphs'] or not text['not_clipped'] or text['colored_pixels'] or text['weight'] != 400:
                raise RuntimeError('Text rendering verification failed: ' + label + ' ' + json.dumps(text))
        if len(typography.get('labels', [])) != 2:
            raise RuntimeError('Missing text render probes: ' + label)
        preview = typography.pop('preview_png', '')
        if label in ('launcher-normal', 'launcher-dpi125', 'launcher-dpi150'):
            import base64
            Path('dist/text-' + label + '.png').write_bytes(base64.b64decode(preview))
            print('TEXT_PREVIEW ' + label + ' ' + preview)
        if result.get('version_button') != 'v' + result['version']:
            raise RuntimeError('Version entry missing: ' + label)
        if label.startswith('launcher-'):
            if not result.get('launcher_path') or '_MEI' in result.get('runtime_path', ''):
                raise RuntimeError('Launcher still depends on temporary runtime: ' + label)
            if not Path(result['runtime_path']).resolve().is_relative_to((Path(env['LOCALAPPDATA']) / 'LogCollector' / 'runtime').resolve()):
                raise RuntimeError('Runtime was not installed in user cache: ' + label + ' ' + json.dumps(result))
        results.append({'case': label, **result})

Path('dist/startup-verification.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))

