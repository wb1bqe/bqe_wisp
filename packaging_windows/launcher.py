"""Single-file launcher for an unmodified BQE release and private Python."""
import argparse
import ctypes
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from standalone_location import choose_destination, remember_destination, validate

FIRST_RUN_DIRECTORY = Path("C:/bqe")


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unpack(bundle, destination, metadata):
    destination.mkdir(parents=True, exist_ok=True)
    # Serialize first-run extraction; a second launch must not see half a runtime.
    with (destination / '.extract.lock').open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        deadline = time.monotonic() + 180
        while True:
            try:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Another BQE launch is still extracting this installation.')
                time.sleep(.2)
        try:
            marker = destination / '.bundle-ready.json'
            if marker.exists():
                saved = json.loads(marker.read_text(encoding='utf-8'))
                if saved['payload_sha256'] != metadata['payload_sha256']:
                    raise RuntimeError('This data folder belongs to another build. Choose another --data-dir.')
                return
            # Repeat destination validation under the extraction lock. Unrelated
            # siblings are allowed; application/runtime collisions are not.
            validate(destination, metadata, allow_nonempty_parent=True)
            pending = destination / '.bundle-pending'
            pending.write_text(metadata['payload_sha256'], encoding='ascii')
            archive = bundle / 'payload.zip'
            if sha256(archive) != metadata['payload_sha256']:
                raise RuntimeError('The embedded application archive failed its integrity check.')
            print('Preparing BQE Wisp on first launch. This can take a minute...', flush=True)
            with zipfile.ZipFile(archive) as source:
                for member in source.infolist():
                    target = (destination / member.filename).resolve()
                    root_name = member.filename.split('/')[0]
                    if (root_name not in ('bqe_wisp', '_python') or
                            not target.is_relative_to((destination / root_name).resolve())):
                        raise RuntimeError('Invalid path in the application archive.')
                source.extractall(destination)
            # Confirm the actual bytes, not merely successful decompression.
            for name, expected in metadata['files'].items():
                if sha256(destination / name) != expected:
                    raise RuntimeError(f'Extracted file failed verification: {name}')
            temporary = destination / '.bundle-ready.tmp'
            temporary.write_text(json.dumps(metadata), encoding='utf-8')
            temporary.replace(marker)
            pending.unlink()
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser(description='BQE Wisp standalone Windows application')
    parser.add_argument('--dir', '--data-dir', dest='data_dir', type=Path,
                        help='Parent folder for bqe_wisp and _python; defaults to the remembered location, or C:/bqe on first run')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Check bundled Python dependencies and exit')
    mode.add_argument('--self-test', action='store_true', help='Run offline decoder diagnostics and exit')
    mode.add_argument('--smoke-test', action='store_true', help='Check the web UI and helper processes without hardware')
    mode.add_argument('--extract-only', action='store_true', help='Prepare the data folder without starting BQE')
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    metadata = json.loads((bundle / 'bundle.json').read_text(encoding='utf-8'))
    default = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'BQE_Wisp' / metadata['build_id']
    destination = choose_destination(args.data_dir, default, metadata, allow_nonempty_parent=True,
                                     first_run_destination=FIRST_RUN_DIRECTORY)
    unpack(bundle, destination, metadata)
    remember_destination(destination, default, metadata)
    print(f'BQE Wisp settings and files: {destination / "bqe_wisp"}', flush=True)
    if args.extract_only:
        return 0
    runtime = destination / '_python'
    application = destination / 'bqe_wisp'
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PYTHON', '_PYI'))}
    # Resolve the source release's "python" helper commands to our runtime.
    env['PATH'] = str(runtime) + os.pathsep + env.get('PATH', '')
    env['PYTHONUTF8'] = '1'
    # Undo the PyInstaller bootloader's DLL directory before launching a real
    # Python interpreter (and the application's external decoder binaries).
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    script = ('check_dependencies.py' if args.check else 'plugins/self_tests.py' if args.self_test
              else '_standalone_smoke.py' if args.smoke_test else 'bqe_wisp.py')
    process = subprocess.Popen([str(runtime / 'python.exe'), '-X', 'utf8', str(application / script)],
                               cwd=application, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The child shares the console and receives Ctrl+C too. Let BQE shut
        # down its decoder processes before the launcher exits.
        try:
            return process.wait(timeout=15)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.terminate()
            return process.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'BQE Wisp could not start: {exc}', file=sys.stderr, flush=True)
        if sys.stdin and sys.stdin.isatty():
            input('Press Enter to close...')
        raise SystemExit(1)
