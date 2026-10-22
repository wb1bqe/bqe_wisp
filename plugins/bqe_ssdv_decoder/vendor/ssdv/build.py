"""Build SSDV from pinned sources, or verify the shipped Windows executable."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def digest(path, text=False):
    data = path.read_bytes()
    if text:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def verify_sources():
    lock = json.loads((ROOT / 'SOURCE.json').read_text())
    for name, expected in lock['sha256_lf'].items():
        if digest(ROOT / name, text=True) != expected:
            raise ValueError(f'SSDV source differs from pinned upstream: {name}')
    return lock


def verify_windows():
    lock = verify_sources()
    record = json.loads((ROOT / 'BUILD-WINDOWS.json').read_text())
    if record['revision'] != lock['revision'] or record['source_sha256_lf'] != lock['sha256_lf']:
        raise ValueError('SSDV binary build record does not match the pinned sources')
    if digest(ROOT / 'ssdv.exe') != record['binary_sha256']:
        raise ValueError('SSDV Windows binary differs from its verified build record')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--target', choices=('windows', 'linux'), default='windows' if os.name == 'nt' else 'linux')
    parser.add_argument('--cc', default='gcc', help='Compiler executable, e.g. x86_64-w64-mingw32-gcc')
    args = parser.parse_args()
    if args.check:
        verify_windows()
        print('SSDV pinned sources and Windows binary verified')
        return
    lock = verify_sources()
    flags = ['-O3', '-Wall']
    if args.target == 'windows':
        flags += ['-static', '-Wl,--no-insert-timestamp']
    name = 'ssdv.exe' if args.target == 'windows' else 'ssdv'
    version = subprocess.check_output([args.cc, '--version'], text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='.ssdv-build-', dir=ROOT) as temp:
        output = Path(temp) / name
        command = [args.cc, *flags, 'main.c', 'ssdv.c', 'rs8.c', '-o', str(output)]
        subprocess.run(command, cwd=ROOT, check=True)
        record = dict(revision=lock['revision'], source_sha256_lf=lock['sha256_lf'],
                      compiler=version, flags=flags, sources=['main.c', 'ssdv.c', 'rs8.c'],
                      binary_sha256=digest(output))
        output.replace(ROOT / name)
    (ROOT / ('BUILD-' + args.target.upper() + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    print(f'Built {name} from {lock["revision"]}: {record["binary_sha256"]}')


if __name__ == '__main__':
    main()
