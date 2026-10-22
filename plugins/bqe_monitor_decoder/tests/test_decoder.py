# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import soundfile as sf

from plugins.bqe_monitor_decoder.dsp import AudioDecoder
from plugins.bqe_monitor_decoder.integration import MONITOR_RECEIVER
from plugins.bqe_monitor_decoder.service import Session
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler
from plugins.bqe_tlm_decoder.protocols import kiss_encode
from plugins.decoder_http import DecoderServer

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT.parent / 'bqe_tlm_decoder/tests/usp_vectors.json').read_text())


def waveform(baud=1200, rate=48000, polarity=1, kind='short'):
    data = bytes.fromhex('5072f64b2d90b1f5' + VECTORS['frame_' + kind])
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8)).astype(float) * 2 - 1
    symbols = np.r_[np.tile([-1, 1], 100), bits * polarity, np.zeros(40)]
    index = np.floor(np.arange(int(len(symbols) * rate / baud / 1.0002)) * baud * 1.0002 / rate).astype(int)
    return .45 * symbols[index] + .015 * np.random.default_rng(7).normal(size=len(index)) + .03


def frame(call, payload, ssid=0):
    def address(text, last, ssid=0):
        return bytes(ord(c) << 1 for c in text.ljust(6)) + bytes([0x60 | ssid << 1 | last])
    return address('CQ', 0) + address(call, 1, ssid) + b'\x03\xf0' + payload


class MonitorTests(unittest.TestCase):
    def test_reference_audio_all_rates_and_polarities(self):
        for baud in (1200, 2400, 4800, 9600):
            for rate, polarity in ((48000, 1), (44100, -1)):
                with self.subTest(baud=baud, rate=rate):
                    packets = []
                    decoder = AudioDecoder(rate, packets.append, {'bauds': [baud]})
                    audio = waveform(baud, rate, polarity)
                    for start in range(0, len(audio), 997):
                        decoder.push(audio[start:start + 997])
                    decoder.finish()
                    self.assertEqual(len(packets), 1)
                    self.assertEqual(packets[0]['frame_hex'], VECTORS['frame_short_out'])
                    self.assertEqual(packets[0]['protocol'], 'USP')
                    self.assertIsNone(packets[0]['satellite'])

    def test_satellite_identity_and_raw_records(self):
        power = struct.pack('<4HhhB', 14, 2, 1, 5, 120, 7400, 0)
        unknown = struct.pack('<4H', 0x1234, 2, 1, 3) + b'abc'
        payload = power + unknown + b'tail'
        for call, name, norad in [('RS58S', 'MONITOR-3', 57180), ('RS57S', 'MONITOR-4', 57182)]:
            for ssid in (0, 7):
                packets = []
                decoder = AudioDecoder(48000, packets.append, {'bauds': [1200]})
                raw = frame(call, payload, ssid)
                decoder._packet(raw, 1.0, 1200, {'protocol': 'USP'})
                packet = packets[0]
                self.assertEqual(packet['satellite']['name'], name)
                self.assertEqual(packet['satellite']['norad'], norad)
                self.assertEqual(packet['frame_hex'], raw.hex())
                self.assertEqual(packet['telemetry'][0]['fields']['voltage_V'], 7.4)
                self.assertEqual(packet['telemetry'][1]['fields'], {})
                self.assertEqual(packet['telemetry'][1]['raw_hex'], b'abc'.hex())
                self.assertEqual(packet['unparsed_hex'], b'tail'.hex())

    def test_long_reference_and_noise(self):
        packets = []
        decoder = AudioDecoder(48000, packets.append, {'bauds': [9600]})
        decoder.push(waveform(9600, kind='long'))
        decoder.finish()
        self.assertEqual(packets[0]['frame_hex'], VECTORS['frame_long_out'])
        packets.clear()
        decoder = AudioDecoder(48000, packets.append, {'bauds': [9600]})
        decoder.push(np.random.default_rng(4).normal(size=48000))
        decoder.finish()
        self.assertEqual(packets, [])

    def test_unsupported_protocol_and_low_rate(self):
        with self.assertRaises(ValueError):
            AudioDecoder(48000, lambda p: None, {'protocol': 'ax25'})
        with self.assertRaises(ValueError):
            AudioDecoder(8000, lambda p: None, {'bauds': [9600]})
        decoder = AudioDecoder(8000, lambda p: None, {})
        self.assertEqual(decoder.bauds, [1200, 2400])
        self.assertEqual(decoder.skipped_bauds, [4800, 9600])

    def test_file_storage_and_source_not_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'MONITOR3.wav'
            sf.write(source, waveform(), 48000)
            session = Session(Path(directory) / 'decoded')
            session.start({'bauds': [1200], 'source_name': 'MONITOR3'}, source)
            session.thread.join(20)
            status = session.snapshot()
            self.assertEqual(status['state'], 'completed', status)
            self.assertEqual(status['packets'], 1)
            packet = json.loads(Path(status['log']).read_text())
            self.assertEqual(packet['input_source'], 'MONITOR3')
            self.assertIsNone(packet['satellite'])
            self.assertEqual(Path(status['kiss']).read_bytes(), kiss_encode(bytes.fromhex(packet['frame_hex'])))

    def test_pass_receiver_opt_in_and_shutdown(self):
        child = Mock()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=child) as spawn:
            try:
                MONITOR_RECEIVER.start({}, 'MONITOR3', {'radio_soundcard': 'USB Audio'})
                spawn.assert_not_called()
                MONITOR_RECEIVER.start({'decode_monitor_telemetry': True}, 'MONITOR4',
                                       {'radio_soundcard': 'USB Audio'})
                command = spawn.call_args.args[0]
                self.assertTrue(any('bqe_monitor_decoder.py' in part for part in command))
                self.assertIn('USB Audio', command)
                self.assertIn('MONITOR4', command)
                self.assertIn('--managed', command)
            finally:
                MONITOR_RECEIVER.stop()
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once_with(timeout=12)

    def test_http_controls_upload_and_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'index.html').write_bytes((ROOT / 'index.html').read_bytes())
            source = folder / 'source.wav'
            sf.write(source, waveform(), 48000)
            session = Session(folder / 'decoded')
            server = DecoderServer(('127.0.0.1', 0), handler(session, {'bauds': [1200]}, root=folder))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url) as response:
                    self.assertIn(b'MONITOR-4 (RS57S)', response.read())
                request = Request(url + '/file', data=source.read_bytes(), headers={
                    'Content-Type': 'application/octet-stream', 'X-Filename': 'monitor.wav'})
                with urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                session.thread.join(20)
                self.assertEqual(session.snapshot()['packets'], 1)
                self.assertEqual(list((folder / '.uploads').iterdir()), [])
                request = Request(url + '/stop', data=b'{}', headers={
                    'Content-Type': 'application/json', 'Origin': 'http://evil.example'})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 403)
            finally:
                session.stop()
                if session.thread:
                    session.thread.join(10)
                server.shutdown()
                server.server_close()
                thread.join(5)


if __name__ == '__main__':
    unittest.main()
