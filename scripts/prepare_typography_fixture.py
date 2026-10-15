"""Reproduce SimHei rendering in CI; the font fixture is never bundled/published."""
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

folder = Path('build/typography-fixture')
folder.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:',
                'chineseize-matplotlib==1.2', '-d', str(folder)], check=True)
package = folder / 'chineseize_matplotlib-1.2-py3-none-any.whl'
expected = '55562a2544e4a1bed15673e7bcf925e1cff631a43f69d67ca6045014dfb02c05'
if hashlib.sha256(package.read_bytes()).hexdigest() != expected:
    raise RuntimeError('Typography fixture checksum mismatch')
with zipfile.ZipFile(package) as archive:
    (folder / 'simhei.ttf').write_bytes(archive.read('chineseize_matplotlib/fonts/simhei.ttf'))
print('Typography test font ready (excluded from distribution)')
