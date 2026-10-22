"""Single-file Linux launcher with a persistent, private Python installation."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
from standalone_location import choose_destination, remember_destination


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unpack(bundle, destination, metadata):
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (destination / '.extract.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = destination / '.bundle-ready.json'
        if marker.exists():
            if json.loads(marker.read_text())['payload_sha256'] != metadata['payload_sha256']:
                raise RuntimeError('Data directory belongs to another build; choose another --data-dir.')
            return
        pending = destination / '.bundle-pending'
        existing = [p for p in destination.iterdir() if p.name != '.extract.lock']
        if existing and (not pending.is_file() or pending.read_text() != metadata['payload_sha256']):
            raise RuntimeError('Data directory is not empty; choose a new --data-dir.')
        pending.write_text(metadata['payload_sha256'])
        archive = bundle / 'payload.tar.gz'
        if digest(archive) != metadata['payload_sha256']:
            raise RuntimeError('Embedded payload integrity check failed.')
        print('Preparing BQE Wisp on first launch...', flush=True)
        with tarfile.open(archive) as source:
            source.extractall(destination, filter='data')
        for name, expected in metadata['files'].items():
            if digest(destination / name) != expected:
                raise RuntimeError(f'Extracted file failed verification: {name}')
        temporary = destination / '.bundle-ready.tmp'
        temporary.write_text(json.dumps(metadata))
        temporary.replace(marker)
        pending.unlink()


def main():
    parser = argparse.ArgumentParser(description='BQE Wisp standalone Linux x86-64 application')
    parser.add_argument('--dir', '--data-dir', dest='data_dir', type=Path,
                        help='Installation folder on this computer (relative or full path); required on first run, then remembered')
    mode = parser.add_mutually_exclusive_group()
    for option in ('check', 'self-test', 'smoke-test', 'extract-only'):
        mode.add_argument('--' + option, action='store_true')
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    metadata = json.loads((bundle / 'bundle.json').read_text())
    default = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'BQE_Wisp' / metadata['build_id']
    destination = choose_destination(args.data_dir, default, metadata)
    unpack(bundle, destination, metadata)
    remember_destination(destination, default, metadata)
    application = destination / 'bqe_wisp'
    runtime = destination / '_python'
    print(f'BQE Wisp settings and files: {application}', flush=True)
    if args.extract_only:
        return 0
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', '_PYI'))}
    env['PATH'] = str(runtime / 'bin') + os.pathsep + env.get('PATH', '')
    env['PYTHONNOUSERSITE'] = '1'
    env['PYTHONPATH'] = str(application)
    env['PYTHONUTF8'] = '1'
    # Do not pass PyInstaller's temporary library path to the real interpreter.
    original = env.pop('LD_LIBRARY_PATH_ORIG', '')
    env['LD_LIBRARY_PATH'] = str(runtime / 'lib') + (os.pathsep + original if original else '')
    script = ('check_dependencies.py' if args.check else 'plugins/self_tests.py' if args.self_test
              else '_standalone_smoke.py' if args.smoke_test else 'bqe_wisp.py')
    child = subprocess.Popen([str(runtime / 'bin/python3'), '-s', '-X', 'utf8', str(application / script)],
                             cwd=application, env=env)
    signal.signal(signal.SIGTERM, lambda *_: child.terminate())
    try:
        return child.wait()
    except KeyboardInterrupt:
        try:
            return child.wait(timeout=15)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            child.terminate()
            return child.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'BQE Wisp could not start: {exc}', file=sys.stderr)
        raise SystemExit(1)
