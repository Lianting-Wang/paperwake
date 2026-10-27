#!/usr/bin/env python3
"""Compile no-hardware C regression probes and run them with ASan/UBSan."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import json

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from src.server.preview import freetype_flags
from src.scheduler.display_model import default_layout
cc=shutil.which('clang') or shutil.which('gcc')
if not cc:
    raise SystemExit('C compiler required (clang or gcc)')
base=root/'src/display/IT8951'
extra=['-Wno-invalid-source-encoding'] if 'clang' in cc else []
with tempfile.TemporaryDirectory() as folder:
    common=[cc,*extra,'-g','-O1','-fsanitize=address,undefined','-funsigned-char','-I'+str(base)]
    page=Path(folder)/'page.json'
    page.write_text(json.dumps(dict(title='中文电子纸',status='Ready · 就绪',rows=[{'label':'混排','value':'中文 / English 123\n第二行'}],
                                    layout=default_layout(),clock='2026-10-27 18:00 UTC-0400',battery='100.0%'),ensure_ascii=False))
    for name in ('paint','driver'):
        sources=[root/f'tests/display/{name}_probe.c']
        if name=='paint':
            sources += [base/'singlepage/page.c',base/'singlepage/page_render.c',base/'lib/GUI/GUI_Paint.c',base/'lib/json/yyjson.c']
        target=Path(folder)/name
        subprocess.run(common+[str(p) for p in sources]+(freetype_flags() if name=='paint' else [])+['-lm','-o',str(target)],check=True)
        subprocess.run([str(target)]+([str(page),str(base/'assets/fonts')] if name=='paint' else []),check=True,timeout=30,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
print('C pixel boundaries, 4bpp page and driver deadlines passed without hardware.')
