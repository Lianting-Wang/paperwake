"""Render the web preview with the exact C layout and bitmap fonts used by the Pi.

The native library contains only drawing code. It cannot access display hardware.
"""
import argparse
import ctypes
from functools import lru_cache
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zlib

SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'display/IT8951'
DEFAULT_LIBRARY = Path(__file__).resolve().parents[2] / 'build/display/libpage-preview.so'
_NATIVE_LOCK = threading.Lock()  # Waveshare Paint keeps a global framebuffer pointer.
_SWAP_NIBBLES = bytes((value >> 4) | ((value & 15) << 4) for value in range(256))


class PreviewUnavailable(RuntimeError):
    pass


def native_sources():
    return sorted([SOURCE_ROOT / 'singlepage/page_render.c', SOURCE_ROOT / 'singlepage/page_render.h',
                   SOURCE_ROOT / 'lib/GUI/GUI_Paint.c', SOURCE_ROOT / 'lib/GUI/GUI_Paint.h',
                   SOURCE_ROOT / 'lib/Config/DEV_Config.h', SOURCE_ROOT / 'lib/Config/Debug.h',
                   *list((SOURCE_ROOT / 'lib/Fonts').glob('*.c')),
                   *list((SOURCE_ROOT / 'lib/Fonts').glob('*.h'))])


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
        command = [compiler, '-dynamiclib' if sys.platform == 'darwin' else '-shared', '-fPIC', '-O2',
                   '-std=gnu99', '-funsigned-char', '-I' + str(SOURCE_ROOT),
                   '-DPAGE_RENDER_REVISION="' + source_revision() + '"',
                   *[str(path) for path in native_sources() if path.suffix == '.c'], '-lm', '-o', str(output)]
        subprocess.run(command, check=True, capture_output=True, timeout=90)
        output.replace(destination)
    return destination


class PageContent(ctypes.Structure):
    _fields_ = [('title', ctypes.c_char_p), ('status', ctypes.c_char_p),
                ('labels', ctypes.c_char_p * 6), ('values', ctypes.c_char_p * 6),
                ('row_count', ctypes.c_uint)]


def _ascii(value, limit):
    if not isinstance(value, str) or len(value) > limit or any(not 32 <= ord(char) <= 126 for char in value):
        raise ValueError('Preview text must be printable ASCII within the display limits.')
    return value.encode('ascii')


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
        self.library.Page_Render.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(PageContent), ctypes.c_char_p, ctypes.c_char_p]
        self.library.Page_Render.restype = ctypes.c_int

    def render(self, display, clock='N/A', battery='N/A', width=1872):
        rows = display.get('rows', [])
        if not isinstance(rows, list) or len(rows) > 6 or any(not isinstance(row, dict) for row in rows) or width not in (1872, 1856):
            raise ValueError('Invalid preview rows or panel width.')
        title = _ascii(display.get('title'), 40)
        status = _ascii(display.get('status'), 24)
        values = tuple((_ascii(row.get('label'), 20), _ascii(row.get('value'), 48)) for row in rows)
        return self._render(title, status, values, _ascii(clock, 31), _ascii(battery, 31), width)

    @lru_cache(maxsize=16)
    def _render(self, title, status, rows, clock, battery, width):
        content = PageContent(title=title, status=status, row_count=len(rows))
        for index, (label, value) in enumerate(rows):
            content.labels[index], content.values[index] = label, value
        height = 1404
        frame = (ctypes.c_ubyte * (width // 2 * height))()
        with _NATIVE_LOCK:
            result = self.library.Page_Render(frame, len(frame), width, height, 0,
                                               ctypes.byref(content), clock, battery)
        if result:
            raise ValueError('The display renderer rejected the preview.')
        return png(bytes(frame), width, height)


@lru_cache(maxsize=4)
def get_renderer(library=None):
    return PageRenderer(library or DEFAULT_LIBRARY)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    args = parser.parse_args()
    print(build_library(args.build))
