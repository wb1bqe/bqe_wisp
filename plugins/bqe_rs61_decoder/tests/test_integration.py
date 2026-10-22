"""Cold CLI, browser controls, configuration and ownership tests (no radio)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from urllib.request import Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import threading

import soundfile as sf
from plugins.bqe_rs61_decoder.tests.test_decoder import frame, audio_wave
from plugins.bqe_rs61_decoder.service import Session
from plugins.bqe_tlm_decoder.service import Session as GenericSession
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler
from plugins.bqe_tlm_decoder.protocols import kiss_encode

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_kiss_upload_and_generic_rejection(self):
        for session_type in (Session, GenericSession):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                session = session_type(root / 'decoded')
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler(session, {}, root=root))
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    request = Request(f'http://127.0.0.1:{server.server_port}/file',
                                      data=kiss_encode(frame()), headers={
                                          'Content-Type': 'application/octet-stream',
                                          'X-Filename': 'reference.kiss'})
                    if session_type is GenericSession:
                        with self.assertRaises(HTTPError) as result:
                            urlopen(request, timeout=3)
                        self.assertEqual(result.exception.code, 400)
                        continue
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                    session.thread.join(timeout=5)
                    status = session.snapshot()
                    self.assertEqual(status['state'], 'completed', status)
                    self.assertEqual(status['packets'], 1)
                    packet = status['recent'][0]
                    self.assertIsNone(packet['audio_seconds'])
                    self.assertIn('not rechecked', packet['validation'])
                    self.assertEqual(json.loads(Path(status['log']).read_text())['source'], 'RS61S')
                    self.assertEqual(list((root / '.uploads').iterdir()), [])
                finally:
                    session.stop()
                    server.shutdown(); server.server_close(); worker.join(timeout=3)

    def test_managed_capture_http_and_owner_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = folder / 'synthetic.wav'
            sf.write(recording, audio_wave([frame()]), 48000)
            with (folder / 'console.log').open('w+') as log:
                child = subprocess.Popen([
                    sys.executable, str(ROOT / 'bqe_rs61_decoder.py'),
                    '--managed', '--file', str(recording), '--port', '0',
                    '--output', str(folder / 'decoded'), '--device', 'USB Audio Codec',
                ], stdin=subprocess.PIPE, stdout=log, stderr=log)
                try:
                    status = None
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        log.seek(0)
                        lines = log.read().splitlines()
                        urls = [line.split('controls: ', 1)[1] for line in lines
                                if 'controls: http://' in line]
                        if urls:
                            with urlopen(urls[0] + '/status', timeout=3) as response:
                                status = json.load(response)
                            if status['state'] in ('completed', 'error'):
                                break
                        time.sleep(.1)
                    self.assertIsNotNone(status, lines)
                    self.assertEqual(status['state'], 'completed', status)
                    self.assertEqual(status['bauds'], [9600])
                    self.assertEqual(status['packets'], 1)
                    self.assertEqual(status['input_device'], 'USB Audio Codec')
                    self.assertEqual(status['recent'][0]['telemetry'][0]['fields']['Total battery voltage (mV)'], 7371)
                    with urlopen(urls[0], timeout=3) as response:
                        page = response.read().decode()
                    self.assertIn('BQE RS-61 Telemetry Decoder', page)
                    self.assertIn('syncDevice(s)', page)
                    self.assertIn('showPacket', page)
                finally:
                    child.stdin.close()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                self.assertEqual(child.returncode, 0)


if __name__ == '__main__':
    unittest.main()
