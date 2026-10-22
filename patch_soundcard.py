"""Apply the reviewed SoundCard 0.4.6 COM fix after pip installs/updates.

No SoundCard import: importing its Windows backend initializes COM.
"""
import argparse
import ast
from importlib.metadata import distribution
from pathlib import Path
import sys


def patched_source(source):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == '_COMLibrary')
    init = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                and node.name == '__init__')
    lines = source.splitlines(keepends=True)
    original = ''.join(lines[init.lineno - 1:init.end_lineno])
    updated = original
    for old, new in (
        ('    def __init__(self):\n',
         '    def __init__(self):\n        self.com_loaded = False\n'),
        ('            self.check_error(hr)\n',
         '            if hr not in (0, 1):  # S_OK and S_FALSE both require CoUninitialize.\n'
         '                self.check_error(hr)\n'),
    ):
        if new in updated:
            continue
        if updated.count(old) != 1:
            raise RuntimeError('Unrecognized SoundCard COM initializer; review before patching')
        updated = updated.replace(old, new, 1)
    lines[init.lineno - 1:init.end_lineno] = [updated]
    return ''.join(lines)


def apply_patch():
    if sys.platform != 'win32':
        return False
    package = distribution('SoundCard')
    if package.version != '0.4.6':
        raise RuntimeError('Review COM patch for SoundCard ' + package.version)
    path = Path(package.locate_file('soundcard/mediafoundation.py'))
    raw = path.read_bytes()
    source = raw.decode('utf-8').replace('\r\n', '\n')
    updated = patched_source(source)
    if updated == source:
        return False
    newline = '\r\n' if b'\r\n' in raw else '\n'
    path.write_bytes(updated.replace('\n', newline).encode('utf-8'))
    # Avoid a previously cached backend surviving an install/update.
    cache = path.parent / '__pycache__'
    if cache.is_dir():
        for compiled in cache.glob('mediafoundation.*.pyc'):
            compiled.unlink()
    return True


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print('SoundCard COM patch applied.' if apply_patch() else 'SoundCard COM patch: no change needed.')
