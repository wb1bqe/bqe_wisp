# SPDX-License-Identifier: GPL-3.0-or-later
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

from plugins.bqe_marmotsat_decoder.protocol import Deframer, parse_frame, crc16, pn9
from plugins.bqe_marmotsat_decoder.dsp import AudioDecoder
from plugins.bqe_marmotsat_decoder.service import Session
from plugins.bqe_marmotsat_decoder.integration import MARMOTSAT_RECEIVER
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler

# Fixed vectors generated independently with gr-openlst's published CRC/PN9
# algorithms. This is a synthetic OpenLST packet, NOT an on-air capture.
FRAME = bytes.fromhex('0f40cdab011048454c4c4f21341200c9')
WHITENED = bytes.fromhex('f0a1d031ec957b61a6369d18448557c3')


def symbols(data=WHITENED):
    return np.unpackbits(np.frombuffer(b'\xaa' * 32 + bytes.fromhex('d391d391') + data,
                                       dtype=np.uint8)).astype(float) * 2 - 1


def waveform(rate=48000, invert=1, drift=1.0002):
    bits = np.r_[np.zeros(300), symbols(), np.zeros(400), symbols(), np.zeros(100)]
    indices = np.floor(np.arange(int(len(bits) * rate / (10170 * drift))) * 10170 * drift / rate).astype(int)
    return invert * .45 * bits[indices] + .03 + .012 * np.random.default_rng(9).normal(size=len(indices))


class DecoderTests(unittest.TestCase):
    def test_reference_frame_and_whitening(self):
        self.assertEqual(crc16(b'123456789'), 0xAEE7)
        self.assertEqual(bytes(a ^ b for a, b in zip(FRAME, pn9(len(FRAME)))), WHITENED)
        packet = parse_frame(FRAME)
        self.assertEqual(packet['sequence'], 0xABCD)
        self.assertEqual(packet['hardware_id'], '1234')
        self.assertEqual((packet['flags'], packet['system'], packet['command']), (0x40, 1, 0x10))
        self.assertEqual(packet['payload_hex'], b'HELLO!'.hex())
        with self.assertRaises(ValueError):
            parse_frame(FRAME[:-1] + bytes([FRAME[-1] ^ 1]))

    def test_polarity_corruption_and_resynchronization(self):
        for polarity in (1, -1):
            packets = []
            framer = Deframer(lambda p, t: packets.append(p))
            damaged = bytearray(WHITENED)
            damaged[7] ^= 1
            # A bogus long length must not hide a following valid sync.
            stream = np.r_[symbols(bytes([0])), symbols(damaged), symbols(), symbols()]
            for index, bit in enumerate(stream * polarity):
                framer.feed(bit, index / 10170)
            framer.finish()
            self.assertEqual(len(packets), 2)
            self.assertTrue(all(p['frame_hex'] == FRAME.hex() for p in packets))
            self.assertGreater(framer.bad_crc, 0)

    def test_audio_rates_polarities_and_chunk_boundaries(self):
        for rate in (44100, 48000, 96000):
            for polarity in (1, -1):
                with self.subTest(rate=rate, polarity=polarity):
                    packets = []
                    decoder = AudioDecoder(rate, packets.append)
                    audio = waveform(rate, polarity)
                    for start in range(0, len(audio), 137):
                        decoder.push(audio[start:start + 137])
                    decoder.finish()
                    self.assertEqual(len(packets), 2)
                    self.assertTrue(all(p['frame_hex'] == FRAME.hex() for p in packets))
                    self.assertGreater(packets[1]['audio_seconds'], packets[0]['audio_seconds'])

    def test_noise_truncation_and_invalid_settings(self):
        packets = []
        decoder = AudioDecoder(48000, packets.append)
        decoder.push(np.random.default_rng(4).normal(size=48000))
        decoder.finish()
        self.assertEqual(packets, [])
        framer = Deframer(lambda p, t: packets.append(p))
        for bit in symbols()[:-16]:
            framer.feed(bit, 0)
        framer.finish()
        self.assertEqual(packets, [])
        self.assertEqual(framer.truncated, 1)
        for rate, options in ((8000, {}), (48000, {'baud': float('nan')}),
                              (48000, {'baud': 9600}), (48000, {'sync_word': -1})):
            with self.assertRaises(ValueError):
                AudioDecoder(rate, packets.append, options)

    def test_recording_session_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.wav'
            sf.write(source, np.column_stack([np.zeros(len(waveform())), waveform()]), 48000, subtype='PCM_16')
            session = Session(Path(directory) / 'decoded')
            session.start({'channel': 1, 'source_name': 'synthetic MARMOTSat test'}, source)
            session.thread.join(timeout=15)
            result = session.snapshot()
            self.assertEqual(result['state'], 'completed', result)
            self.assertEqual(result['packets'], 2)
            self.assertIsNone(result['kiss'])
            records = [json.loads(line) for line in Path(result['log']).read_text().splitlines()]
            self.assertEqual(records[0]['frame_hex'], FRAME.hex())
            self.assertEqual(list((Path(directory) / 'decoded').glob('*.kiss')), [])

    def test_pass_receiver_opt_in(self):
        process = Mock()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=process) as spawn:
            MARMOTSAT_RECEIVER.start({}, 'CO-128', {'radio_soundcard': 'USB Audio'})
            spawn.assert_not_called()
            MARMOTSAT_RECEIVER.start({'decode_marmotsat_telemetry': True}, 'CO-128',
                                    {'radio_soundcard': 'USB Audio'})
            command = spawn.call_args.args[0]
            self.assertTrue(any('bqe_marmotsat_decoder.py' in part for part in command))
            self.assertIn('USB Audio', command)
            MARMOTSAT_RECEIVER.stop()
            process.stdin.close.assert_called_once()

    def test_http_controls_and_upload(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'index.html').write_bytes((root / 'index.html').read_bytes())
            source = folder / 'source.wav'
            sf.write(source, waveform(), 48000)
            session = Session(folder / 'decoded')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(session, {}, root=folder))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url) as response:
                    self.assertIn(b'BQE MARMOTSat UHF Decoder', response.read())
                request = Request(url + '/file', data=source.read_bytes(), headers={
                    'Content-Type': 'application/octet-stream', 'X-Filename': 'test.wav',
                    'X-Options': json.dumps({'baud': 10170})})
                with urlopen(request) as response:
                    self.assertTrue(json.load(response)['ok'])
                session.thread.join(10)
                with urlopen(url + '/status') as response:
                    result = json.load(response)
                self.assertEqual(result['state'], 'completed', result)
                self.assertEqual(result['packets'], 2)
                self.assertEqual(list((folder / '.uploads').iterdir()), [])
            finally:
                session.stop()
                server.shutdown()
                server.server_close()
                worker.join(5)

    def test_cli_file_and_managed_shutdown(self):
        script = Path(__file__).resolve().parents[1] / 'bqe_marmotsat_decoder.py'
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.wav'
            sf.write(source, waveform(), 48000)
            result = subprocess.run([sys.executable, str(script), '--file', str(source),
                                     '--no-web', '--output', str(Path(directory) / 'decoded')],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)['packets'], 2)
            result = subprocess.run([sys.executable, str(script), '--idle', '--managed', '--port', '0'],
                                    input='', capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn('MARMOTSat controls:', result.stdout)


if __name__ == '__main__':
    unittest.main()
