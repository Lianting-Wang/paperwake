#!/usr/bin/env python3
"""Compile no-hardware C regression probes and run them with ASan/UBSan."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
cc=shutil.which('clang') or shutil.which('gcc')
if not cc:
    raise SystemExit('C compiler required (clang or gcc)')
base=root/'src/display/IT8951'
extra=['-Wno-invalid-source-encoding'] if 'clang' in cc else []
with tempfile.TemporaryDirectory() as folder:
    common=[cc,*extra,'-g','-O1','-fsanitize=address,undefined','-funsigned-char','-I'+str(base)]
    for name in ('paint','driver'):
        sources=[root/f'tests/display/{name}_probe.c']
        if name=='paint':
            sources += [base/'singlepage/page.c',base/'lib/GUI/GUI_Paint.c',*sorted((base/'lib/Fonts').glob('*.c'))]
        target=Path(folder)/name
        subprocess.run(common+[str(p) for p in sources]+['-lm','-o',str(target)],check=True)
        subprocess.run([str(target)],check=True,timeout=10,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
print('C pixel boundaries, 4bpp page and driver deadlines passed without hardware.')
