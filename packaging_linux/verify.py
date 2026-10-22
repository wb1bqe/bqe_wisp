"""Verify a built Linux executable in a new disposable installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--test-dir', type=Path, required=True)
    args = parser.parse_args()
    exe, folder = args.executable.resolve(), args.test_dir.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    data = folder / 'installation'
    env = dict(os.environ, PATH='/usr/bin:/bin', PYTHONPATH='/invalid/host/python', PYTHONHOME='/invalid/host/python')
    def run(*options):
        result = subprocess.run([str(exe), '--data-dir', str(data), *options], env=env,
                                capture_output=True, text=True, timeout=240)
        (folder / (options[0].lstrip('-') + '.log')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout
    run('--smoke-test')
    report = run('--self-test')
    if '[FAIL]' in report or '[WARNING]' in report:
        raise RuntimeError('Offline diagnostics reported a failure/warning; see self-test.log.')
    app = data / 'bqe_wisp'
    # Test settings preservation and first-run ownership checks.
    settings = app / 'bqe_config/general_settings.yaml'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    settings.write_text(settings.read_text().replace('ui_port: 8028', f'ui_port: {port}') + '\n# persistent setting test\n')
    expected = hashlib.sha256(settings.read_bytes()).hexdigest()
    run('--extract-only')
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == expected
    bad = folder / 'occupied'
    bad.mkdir()
    (bad / 'keep.txt').write_text('do not overwrite')
    result = subprocess.run([str(exe), '--data-dir', str(bad), '--extract-only'], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and (bad / 'keep.txt').read_text() == 'do not overwrite'
    with (folder / 'startup.log').open('w') as log:
        child = subprocess.Popen([str(exe), '--data-dir', str(data)], env=env, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                        assert response.status == 200
                        break
                except OSError:
                    if child.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Normal startup did not serve the UI; see startup.log.')
                    time.sleep(.25)
            os.killpg(child.pid, signal.SIGINT)
            assert child.wait(timeout=25) == 0
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    assert 'exited cleanly' in (folder / 'startup.log').read_text()
    summary = dict(status='passed', smoke_test=True, offline_self_tests=True, startup=True,
                   graceful_shutdown=True, settings_preserved=True, nonempty_directory_rejected=True)
    (folder / 'verification.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
