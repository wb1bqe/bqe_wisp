"""Cold managed startup must not deadlock native imports against stdin."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

import numpy as np
import soundfile as sf


class ManagedStartupTests(unittest.TestCase):
    def test_managed_file_capture_and_http_remain_responsive(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            recording = folder / 'silence.wav'
            sf.write(recording, np.zeros(48000), 48000)
            with (folder / 'console.log').open('w+') as log:
                process = subprocess.Popen([
                    sys.executable,
                    str(root / 'plugins/bqe_hf_image_decoder/bqe_hf_image_decoder.py'),
                    '--managed', '--file', str(recording), '--port', '0',
                    '--output', str(folder / 'images'), '--device', 'USB Audio Codec',
                ], cwd=root, stdin=subprocess.PIPE, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 20
                    status = None
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
                    self.assertIsNotNone(status)
                    self.assertEqual(status['state'], 'completed', status)
                    self.assertEqual(status['audio_seconds'], 1)
                    self.assertIn('signal', status)
                    self.assertEqual(status['input_device'], 'USB Audio Codec')
                    self.assertEqual(status['profile'], 'auto')
                finally:
                    process.stdin.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)
