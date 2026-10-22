"""Real CLI lifecycle checks; never starts audio capture or a browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import soundfile as sf
from plugins.bqe_monitor_decoder.tests.test_decoder import waveform

SCRIPT = Path(__file__).resolve().parents[1] / 'bqe_monitor_decoder.py'


class IntegrationTests(unittest.TestCase):
    def test_headless_recording_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            recording = folder / 'monitor.wav'
            sf.write(recording, waveform(), 48000)
            result = subprocess.run([sys.executable, str(SCRIPT), '--file', str(recording),
                                     '--no-web', '--bauds', '1200', '--output', str(folder / 'decoded')],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(status['state'], 'completed')
            self.assertEqual(status['packets'], 1)
            self.assertTrue(Path(status['kiss']).is_file())

    def test_port_fallback_and_owner_eof(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with tempfile.TemporaryDirectory() as directory:
                child = subprocess.Popen([sys.executable, str(SCRIPT), '--idle', '--managed',
                                          '--fallback-port', '--port', str(server.server_port),
                                          '--output', directory], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    stdout, stderr = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, stderr.decode())
                    self.assertIn(b'Monitor telemetry controls:', stdout)
                    actual_port = int(stdout.decode().strip().rsplit(':', 1)[1])
                    self.assertNotEqual(actual_port, server.server_port)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
        finally:
            server.server_close()


if __name__ == '__main__':
    unittest.main()
