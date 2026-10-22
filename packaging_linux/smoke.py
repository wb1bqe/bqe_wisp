"""Offline standalone-build check: no receiver, schedule, or hardware opened."""
import importlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent


def main():
    assert all(Path(path).resolve().is_relative_to(ROOT.parent) for path in sys.path), sys.path
    print('PASS: module search paths are restricted to this installation')
    import check_dependencies
    if check_dependencies.main():
        return 1
    import bqe_wisp
    server = bqe_wisp.start_web_console(port=0)
    try:
        url = f'http://127.0.0.1:{server.server_address[1]}/'
        with urllib.request.urlopen(url, timeout=15) as response:
            page = response.read().decode('utf-8')
            assert response.status == 200 and 'aprsSpotsMenuItem' in page
            class StorageField(HTMLParser):
                path = None
                def handle_starttag(self, tag, attrs):
                    fields = dict(attrs)
                    if fields.get('id') == 'storageLocationPath':
                        self.path = fields.get('value')
            storage = StorageField()
            storage.feed(page)
            assert storage.path == str(ROOT), storage.path
            assert 'id="storageLocationMenuItem"' in page
            print('PASS: Help > Storage Location shows the actual extracted application folder')
        print('PASS: bundled web console, including APRS and weather UI')
    finally:
        server.shutdown()
        server.server_close()
    # These imports exercise optional dynamic imports, extension modules and
    # DLL-backed libraries that a conventional freezing analysis can miss.
    for module in ['plugins.bqe_sstv_decoder.recovery',
                   'plugins.bqe_hf_image_decoder.freedv',
                   'plugins.bqe_aprs_decoder.dsp', 'plugins.bqe_sdr.service']:
        importlib.import_module(module)
        print('PASS: import ' + module)
    # Test actual child interpreter selection, including the release's plain
    # "python" setting, from outside the application working directory.
    for executable in [sys.executable, bqe_wisp.PYTHON_PATH]:
        command = [executable, '-c',
                   'import sys,numpy,scipy,sstv,plugins.bqe_aprs_decoder.protocol; print(sys.executable)']
        result = subprocess.run(command, cwd=ROOT.parent, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).resolve() == Path(sys.executable).resolve(), result.stdout
        print('PASS: private Python helper process')
    print(json.dumps({'status': 'passed', 'python': sys.executable, 'application': str(ROOT)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
