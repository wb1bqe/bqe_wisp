"""Telemetry opt-in, rig soundcard routing, and parent shutdown handshake."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from plugins.bqe_sstv_decoder.integration import Receiver
from plugins.bqe_tlm_decoder.integration import TELEMETRY_RECEIVER


class IntegrationTests(unittest.TestCase):
    def receiver(self):
        return Receiver('decode_telemetry', TELEMETRY_RECEIVER.root, 8769, 'Telemetry')

    def test_telemetry_command_and_rig_input(self):
        receiver = self.receiver()
        with patch('subprocess.Popen') as popen:
            receiver.start({'decode_telemetry': True}, 'umka-1', {'radio_soundcard': 'USB Audio Codec'})
            command = popen.call_args.args[0]
            self.assertTrue(command[1].endswith('bqe_tlm_decoder.py'))
            self.assertEqual(command[command.index('--device')+1], 'USB Audio Codec')
            self.assertEqual(command[command.index('--source-name')+1], 'umka-1')
            self.assertEqual(command[command.index('--port')+1], '8769')
            self.assertIn('--managed', command)
            self.assertIn('--fallback-port', command)
            receiver.stop()
            popen.return_value.stdin.close.assert_called_once()

    def test_disabled_or_invalid_configuration_never_captures_default_input(self):
        receiver = self.receiver()
        with patch('subprocess.Popen') as popen:
            for cfg in ({}, {'decode_telemetry': False}):
                receiver.start(cfg, 'satellite', {})
            for rig in ({}, {'radio_soundcard': ''}, {'radio_soundcard': True}):
                with self.assertRaisesRegex(ValueError, 'radio_soundcard'):
                    receiver.start({'decode_telemetry': True}, 'satellite', rig)
            for value in ('true', 'USB Audio Codec', 1):
                with self.assertRaisesRegex(ValueError, 'YAML boolean'):
                    receiver.start({'decode_telemetry': value}, 'satellite', {})
            popen.assert_not_called()

    def test_existing_port_and_owner_eof(self):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        # Keep the preferred port occupied. A managed decoder must still start
        # on an available port and exit when its owner closes the pipe.
        server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            with tempfile.TemporaryDirectory() as folder:
                child = subprocess.Popen([sys.executable, str(TELEMETRY_RECEIVER.root/'bqe_tlm_decoder.py'),
                                          '--idle', '--managed', '--fallback-port', '--port', str(server.server_port),
                                          '--output', folder], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    stdout, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, stderr.decode())
                    self.assertIn(b'Telemetry controls:', stdout)
                    self.assertNotIn(f':{server.server_port}\r'.encode(), stdout)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
        finally:
            server.server_close()


if __name__ == '__main__':
    unittest.main()
