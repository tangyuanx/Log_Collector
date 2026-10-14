# Windows 10/11 x64. Keep application runtimes; use the OS's own UCRT/API sets.
import sys
from pathlib import Path
import PySide6

base = Path(sys.base_prefix)
qt = Path(PySide6.__file__).parent
runtime_binaries = []
for name in ('vcruntime140.dll', 'vcruntime140_1.dll'):
    candidate = next((folder / name for folder in (base, qt) if (folder / name).is_file()), None)
    if candidate is None:
        raise RuntimeError('Required application runtime is missing: ' + name)
    runtime_binaries.append((str(candidate), '.'))

a = Analysis(['app.py'], pathex=[SPECPATH], binaries=runtime_binaries,
             datas=[('assets/icon.png', 'assets')], hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False)

def system_runtime(entry):
    name = entry[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
    return name == 'ucrtbase.dll' or name.startswith(('api-ms-win-', 'ext-ms-win-'))

a.binaries = [entry for entry in a.binaries if not system_runtime(entry)]
pyz = PYZ(a.pure)
single = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='LogCollector',
             debug=False, bootloader_ignore_signals=False, strip=False,
             upx=False, console=False, disable_windowed_traceback=False, icon='assets/icon.ico')
folder_exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LogCollector',
                 debug=False, bootloader_ignore_signals=False, strip=False,
                 upx=False, console=False, disable_windowed_traceback=False, icon='assets/icon.ico')
folder = COLLECT(folder_exe, a.binaries, a.datas, strip=False, upx=False,
                 name='LogCollector-Portable')
