"""Cold CLI, browser controls, configuration and ownership tests (no radio)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

import soundfile as sf
from plugins.bqe_otp2_decoder.tests.test_decoder import frame, ax25_bits, waveform

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_managed_capture_http_and_owner_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = folder / 'synthetic.wav'
            sf.write(recording, waveform(ax25_bits(frame()), 4800), 48000)
            with (folder / 'console.log').open('w+') as log:
                child = subprocess.Popen([
                    sys.executable, str(ROOT / 'bqe_otp2_decoder.py'),
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
                    self.assertEqual(status['bauds'], [4800])
                    self.assertEqual(status['packets'], 1)
                    self.assertEqual(status['input_device'], 'USB Audio Codec')
                    self.assertEqual(status['recent'][0]['telemetry'], [])
                    self.assertEqual(status['recent'][0]['satellite_identity'], 'unverified')
                    with urlopen(urls[0], timeout=3) as response:
                        page = response.read().decode()
                    self.assertIn('BQE OTP-2 Experimental Decoder', page)
                    self.assertIn('syncDevice(s)', page)
                    self.assertIn('satellite identity', page.lower())
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
