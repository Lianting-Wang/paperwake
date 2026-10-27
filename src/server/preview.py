"""Render the web preview with the exact C layout and bitmap fonts used by the Pi.

The native library contains only drawing code. It cannot access display hardware.
"""
import argparse
import ctypes
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zlib

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.scheduler.display_model import default_layout, validate_display, text

SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'display/IT8951'
DEFAULT_LIBRARY = Path(__file__).resolve().parents[2] / 'build/display/libpage-preview.so'
FONT_DIRECTORY = SOURCE_ROOT / 'assets/fonts'
_NATIVE_LOCK = threading.Lock()  # Waveshare Paint keeps a global framebuffer pointer.
_SWAP_NIBBLES = bytes((value >> 4) | ((value & 15) << 4) for value in range(256))


class PreviewUnavailable(RuntimeError):
    pass


def native_sources():
    return sorted([SOURCE_ROOT / 'singlepage/page_render.c', SOURCE_ROOT / 'singlepage/page_render.h',
                   SOURCE_ROOT / 'lib/GUI/GUI_Paint.c', SOURCE_ROOT / 'lib/GUI/GUI_Paint.h',
                   SOURCE_ROOT / 'lib/Config/DEV_Config.h', SOURCE_ROOT / 'lib/Config/Debug.h',
                   *list((SOURCE_ROOT / 'lib/json').glob('*')),
                   *list((SOURCE_ROOT / 'assets/fonts').glob('*'))])


def source_revision():
    digest = hashlib.sha256()
    for path in native_sources():
        digest.update(str(path.relative_to(SOURCE_ROOT)).encode() + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_library(destination):
    """Build for the server's architecture; no GPIO/SPI driver is linked."""
    compiler = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    if not compiler:
        raise PreviewUnavailable('A C compiler is required to build the page preview.')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
        output = Path(folder) / 'preview.so'
        flags = freetype_flags()
        command = [compiler, '-dynamiclib' if sys.platform == 'darwin' else '-shared', '-fPIC', '-O2',
                   '-std=gnu99', '-funsigned-char', '-I' + str(SOURCE_ROOT),
                   '-DPAGE_RENDER_REVISION="' + source_revision() + '"',
                   *[str(path) for path in native_sources() if path.suffix == '.c'], *flags, '-lm', '-o', str(output)]
        subprocess.run(command, check=True, capture_output=True, timeout=90)
        output.replace(destination)
    return destination


def freetype_flags():
    import shlex
    command = shutil.which('pkg-config') or '/opt/homebrew/bin/pkg-config'
    try:
        return shlex.split(subprocess.check_output([command, '--cflags', '--libs', 'freetype2'], text=True))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PreviewUnavailable('Install FreeType development files and pkg-config to build the renderer.') from exc


def _chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)


def png(frame, width, height):
    # C stores the left pixel in the low nibble; PNG stores it in the high nibble.
    pixels = frame.translate(_SWAP_NIBBLES)
    stride = width // 2
    scanlines = b''.join(b'\0' + pixels[y * stride:(y + 1) * stride] for y in range(height))
    header = struct.pack('!IIBBBBB', width, height, 4, 0, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(scanlines)) + _chunk(b'IEND', b'')


class PageRenderer:
    def __init__(self, library):
        try:
            self.library = ctypes.CDLL(str(library))
            self.library.Page_RendererRevision.restype = ctypes.c_char_p
            if self.library.Page_RendererRevision().decode('ascii') != source_revision():
                raise PreviewUnavailable('Page preview is out of date; rebuild it from the display sources.')
        except (OSError, AttributeError) as exc:
            raise PreviewUnavailable('Build the page preview before starting the management service.') from exc
        self.library.Page_RenderDocument.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
        self.library.Page_RenderDocument.restype = ctypes.c_int

    def render(self, display, clock='N/A', battery='N/A', width=1872):
        image, issues = self.preview(display, clock, battery, width)
        if issues:
            raise ValueError('文字超出区域或字体缺字，请在版式编辑器中调整。')
        return image

    def preview(self, display, clock='N/A', battery='N/A', width=1872):
        display = validate_display(display, draft=True)
        text(clock, 63)
        text(battery, 31)
        document = dict(display, layout=display.get('layout', default_layout()), clock=clock, battery=battery)
        return self._render(json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode(), width)

    def validate(self, display):
        # Reserve stable bounds for all generated metadata, at both panel widths.
        for width in (1872, 1856):
            _, issues = self.preview(display, '2026-12-31 23:59 UTC-04:00', '100.0%', width)
            if issues:
                return issues
        return []

    @lru_cache(maxsize=16)
    def _render(self, document, width):
        if width not in (1872, 1856):
            raise ValueError('Invalid preview width.')
        height = 1404
        frame = (ctypes.c_ubyte * (width // 2 * height))()
        issues = ctypes.create_string_buffer(8192)
        with _NATIVE_LOCK:
            result = self.library.Page_RenderDocument(frame, len(frame), width, height, 0,
                document, len(document), str(FONT_DIRECTORY).encode(), issues, len(issues), 1)
        if result == 1:
            raise PreviewUnavailable('The native renderer or its font resources are unavailable.')
        return png(bytes(frame), width, height), json.loads(issues.value)


@lru_cache(maxsize=4)
def get_renderer(library=None):
    return PageRenderer(library or DEFAULT_LIBRARY)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    args = parser.parse_args()
    print(build_library(args.build))
