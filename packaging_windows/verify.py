"""Verify the completed executable without using host Python packages or hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--test-dir', type=Path, required=True, help='New disposable test folder')
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    exe, data, source = (p.resolve() for p in (args.executable, args.test_dir, args.source))
    if data.exists():
        raise FileExistsError('Choose a fresh test directory')
    data.mkdir(parents=True)
    sibling = data / 'unrelated-files'
    sibling.mkdir()
    sentinel = sibling / 'keep.txt'
    sentinel.write_bytes(b'Unrelated parent content must survive installation.')
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
    env['PATH'] = os.environ['SystemRoot'] + '\\System32;' + os.environ['SystemRoot']
    reports = exe.parent
    base = [str(exe), '--data-dir', str(data)]
    for option, log_name, timeout in [('--smoke-test', 'smoke-test.log', 180),
                                      ('--self-test', 'decoder-self-tests.log', 360)]:
        with (reports / log_name).open('w', encoding='utf-8') as log:
            result = subprocess.run(base + [option], cwd=reports, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f'{option} failed: see {reports / log_name}')
        assert sentinel.read_bytes() == b'Unrelated parent content must survive installation.'
        print(f'PASS: {option}', flush=True)
    log = (reports / 'decoder-self-tests.log').read_text(encoding='utf-8')
    counts = re.findall(r'^\[PASS\].*?: (\d+) tests;', log, re.M)
    suite_count = int(subprocess.check_output(
        [str(data / '_python/python.exe'), '-c',
         'from plugins.self_tests import SUITES; print(len(SUITES))'], env=env, text=True))
    assert len(counts) == suite_count and '[FAIL]' not in log and '[WARNING]' not in log, log
    app = data / 'bqe_wisp'
    originals = json.loads((app / 'STANDALONE_SOURCE.json').read_text())['files']
    for name, expected in originals.items():
        assert digest(source / name) == expected, 'Source release changed: ' + name
        assert digest(app / name) == expected, 'Packaged source changed: ' + name
    # Only the disposable extracted copy is edited; no original file is touched.
    satellite = app / 'bqe_config/satellites.yaml'
    satellite.write_bytes(satellite.read_bytes() + b'\n# standalone persistence check\n')
    expected_satellite = satellite.read_bytes()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    settings = app / 'bqe_config/general_settings.yaml'
    settings.write_text(settings.read_text().replace('ui_port: 8028', f'ui_port: {port}'))
    with (reports / 'startup-shutdown.log').open('w', encoding='utf-8') as log_file:
        process = subprocess.Popen(base, cwd=reports, env=env, stdout=log_file,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 40
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Application exited early; see startup-shutdown.log')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.25)
            assert satellite.read_bytes() == expected_satellite
            request = urllib.request.Request(f'http://127.0.0.1:{port}/api/command',
                data=json.dumps({'action': 'exit'}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response:
                assert json.load(response)['ok']
            assert process.wait(timeout=20) == 0
            assert satellite.read_bytes() == expected_satellite
            print('PASS: normal startup, web UI, graceful exit, settings persistence', flush=True)
        finally:
            if process.poll() is None:
                subprocess.run([os.environ['SystemRoot'] + '\\System32\\taskkill.exe',
                                '/PID', str(process.pid), '/T', '/F'], capture_output=True)
    build = json.loads((reports / 'BQE_Wisp.build.json').read_text())
    assert digest(exe) == build['executable_sha256']
    summary = dict(source_release=str(source), unchanged_source_files=len(originals),
        python_packages=len(build['packages']), offline_suites_passed=len(counts),
        offline_tests_passed=sum(map(int, counts)),
        checks=['Embedded archive and extracted hashes', 'No host Python or user site-packages',
                'Dependency imports', 'Web console', 'Private Python helpers', 'Normal startup',
                'Graceful File > Exit', 'Settings persistence', 'Nonempty parent contents preserved'], hardware_tested=False,
        separate_prerequisites=['Hamlib', 'SatDump for LRPT', 'RTL-SDR libraries/USB driver'],
        executable_sha256=build['executable_sha256'])
    (reports / 'VERIFICATION.json').write_text(json.dumps(summary, indent=2) + '\n')
    (reports / 'SHA256SUMS.txt').write_text(build['executable_sha256'] + '  BQE_Wisp.exe\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
