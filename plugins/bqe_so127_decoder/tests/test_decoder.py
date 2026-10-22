"""Operator packet CRCs, audio recovery, and BQE integration; no radio needed."""
from pathlib import Path
import json
import threading
from urllib.request import Request, urlopen
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
import yaml

from plugins.bqe_so127_decoder.protocol import Deframer, SIZES, descramble, parse_frame
from plugins.bqe_so127_decoder.dsp import AudioDecoder
from plugins.bqe_so127_decoder.integration import SO127_RECEIVER
from plugins.telemetry_view import catalog, TelemetryArchive
from plugins.decoder_http import DecoderServer

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def reference(kind=2):
    return bytes.fromhex((ROOT / 'tests' / 'fixtures' / f'sample_type_{kind:02}.txt').read_text())


def on_air(frame):
    # Independent transmitter implementation; retain the operator's original CRC.
    register = 0x10000
    payload = []
    for byte in frame[1:-2]:
        bits = [int(b) for b in f'{byte:08b}']
        for i in range(7):
            bits[i] ^= ((register >> 16) ^ (register >> 11)) & 1
            register = ((register << 1) | bits[i]) & 0x1ffff
        payload.append(int(''.join(str(bit) for bit in bits), 2))
    return frame[:1] + bytes(payload) + frame[-2:]


def waveform(baud=800, rate=48000, polarity=1, raw=None):
    raw = on_air(reference()) if raw is None else raw
    stream = b'\xaa' * 32 + b'\xbf\x35' + bytes([len(raw)]) + raw + b'\xaa' * 12
    bits = np.unpackbits(np.frombuffer(stream, dtype=np.uint8)).astype(float)
    # Non-integer sample clocks, arbitrary block boundaries and moderate noise.
    indices = np.floor(np.arange(int(len(bits) * rate / baud)) * baud / rate).astype(int)
    return polarity * (bits[indices] * 1.2 - .6) + np.random.default_rng(127).normal(0, .04, len(indices))


class DecoderTests(unittest.TestCase):
    def test_operator_references(self):
        for kind in SIZES:
            with self.subTest(kind=kind):
                plain = reference(kind)
                raw = on_air(plain)
                result = parse_frame(raw)
                self.assertEqual(result['decoded_frame_hex'], plain.hex())
                self.assertEqual(result['record_type'], kind)
                self.assertEqual(result['satellite']['norad'], 68446)
                damaged = bytearray(raw); damaged[6] ^= 0x20
                with self.assertRaises(ValueError):
                    parse_frame(damaged)
        fields = parse_frame(on_air(reference()))['telemetry'][0]['fields']
        self.assertEqual(fields['System clock (s)'], 2539)
        self.assertEqual(fields['tcpu (C)'], 27.5)
        self.assertIsNone(fields['tpa (C)'])
        status = parse_frame(on_air(reference(3)))['telemetry'][0]['fields']
        self.assertEqual(status['Uptime (s)'], 1403)
        self.assertEqual(status['CPU starts'], 5)
        self.assertEqual(status['Last command key'], 83)

    def test_audio_rates_polarities_and_chunks(self):
        for baud in (200, 800, 1600):
            for polarity in (-1, 1):
                with self.subTest(baud=baud, polarity=polarity):
                    packets = []
                    decoder = AudioDecoder(44100, packets.append)
                    audio = waveform(baud, 44100, polarity)
                    for i in range(0, len(audio), 731):
                        decoder.push(audio[i:i + 731])
                    decoder.finish()
                    self.assertEqual(len(packets), 1, decoder.diagnostics())
                    self.assertEqual(packets[0]['baud'], baud)
                    self.assertEqual(packets[0]['decoded_frame_hex'], reference().hex())

    def test_corruption_resync_and_repeated_packets(self):
        raw = on_air(reference())
        bad = bytearray(raw); bad[5] ^= 1
        packets = []
        decoder = AudioDecoder(48000, packets.append, {'bauds': [800]})
        for frame in (bad, raw, raw):
            decoder.push(waveform(raw=frame))
        decoder.finish()
        self.assertEqual(len(packets), 2)
        self.assertGreater(decoder.diagnostics()['bad_crc'], 0)
        packets = []
        framer = Deframer(lambda p, t: packets.append(p))
        stream = b'\xbf\x35\xff\xbf\x35' + bytes([len(raw)]) + raw
        for bit in np.unpackbits(np.frombuffer(stream, dtype=np.uint8)):
            framer.feed(1 if bit else -1, 0)
        self.assertEqual(len(packets), 1)
        framer.finish()
        packets = []
        decoder = AudioDecoder(8000, packets.append)
        decoder.push(np.random.default_rng(2).normal(size=16000))
        decoder.finish()
        self.assertEqual(packets, [])
        with self.assertRaises(ValueError):
            AudioDecoder(48000, packets.append, {'bauds': []})

    def test_saved_packets_and_catalog(self):
        from plugins.bqe_so127_decoder.service import Session
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'source.wav'
            sf.write(source, waveform(), 48000)
            output = folder / 'plugins' / 'bqe_so127_decoder' / 'decoded'
            session = Session(output)
            session.start({'source_name': 'SO-127', 'bauds': [800]}, source)
            session.thread.join(15)
            result = session.snapshot()
            self.assertEqual(result['state'], 'completed', result)
            self.assertEqual(result['packets'], 1)
            self.assertIsNone(result['kiss'])
            records = [json.loads(s) for s in Path(result['log']).read_text().splitlines()]
            self.assertEqual(records[0]['source'], 'SO-127')
            config = folder / 'satellites.yaml'
            config.write_text('satellites:\n- nickname: so-127-ssdv\n  satellite_name: SO-127\n  catalog_number: 68446\n  decode_so127_telemetry: true\n')
            entry = catalog(config)[0]
            self.assertEqual(len(TelemetryArchive().snapshot(folder, entry)['packets']), 1)

    def test_sdr_iq_path(self):
        from plugins.bqe_sdr.config import Settings
        from plugins.bqe_sdr.dsp import Demodulator
        raw = on_air(reference())
        stream = b'\xaa' * 32 + b'\xbf\x35' + bytes([len(raw)]) + raw + b'\xaa' * 12
        bits = np.unpackbits(np.frombuffer(stream, dtype=np.uint8))
        for baud, shift in ((200, 1125), (800, 1600), (1600, 1600)):
            settings = Settings.from_dict(dict(sample_rate=240000, frequency=436875000,
                center=436935000, bandwidth=12000, deviation=1600, audio_gain=.15))
            offsets = np.repeat(bits.astype(float) * -shift + shift / 2, 240000 // baud) - 60000
            iq = .7 * np.exp(2j * np.pi * np.cumsum(offsets) / 240000)
            modem = Demodulator(settings)
            packets = []
            decoder = AudioDecoder(48000, packets.append)
            for i in range(0, len(iq), 16384):
                decoder.push(modem.push(iq[i:i + 16384]))
            decoder.finish()
            self.assertEqual(len(packets), 1, (baud, decoder.diagnostics()))
            self.assertEqual(packets[0]['decoded_frame_hex'], reference().hex())

    def test_http_upload(self):
        from plugins.bqe_so127_decoder.service import Session
        from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'index.html').write_bytes((ROOT / 'index.html').read_bytes())
            source = folder / 'source.wav'
            sf.write(source, waveform(), 48000)
            session = Session(folder / 'decoded')
            server = DecoderServer(('127.0.0.1', 0), handler(session, {}, root=folder))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url) as response:
                    self.assertIn(b'SO-127 Telemetry Decoder', response.read())
                req = Request(url + '/file', data=source.read_bytes(), headers={
                    'Content-Type': 'application/octet-stream', 'X-Filename': 'test.wav',
                    'X-Options': json.dumps({'bauds': [800], 'protocol': 'so127'})})
                with urlopen(req) as response:
                    self.assertTrue(json.load(response)['ok'])
                session.thread.join(15)
                with urlopen(url + '/status') as response:
                    result = json.load(response)
                self.assertEqual(result['state'], 'completed', result)
                self.assertEqual(result['packets'], 1)
                self.assertEqual(list((folder / '.uploads').iterdir()), [])
            finally:
                session.stop()
                server.shutdown()
                server.server_close()
                worker.join(5)

    def test_receiver_and_configuration(self):
        process = Mock()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=process) as spawn:
            SO127_RECEIVER.start({}, 'SO-127', {'radio_soundcard': 'USB Audio'})
            spawn.assert_not_called()
            try:
                SO127_RECEIVER.start({'decode_so127_telemetry': True}, 'SO-127', {'radio_soundcard': 'USB Audio'})
                command = spawn.call_args.args[0]
                self.assertTrue(any('bqe_so127_decoder.py' in s for s in command))
                self.assertIn('USB Audio', command)
                self.assertIn('--managed', command)
            finally:
                SO127_RECEIVER.stop()
            process.stdin.close.assert_called_once()
            spawn.reset_mock()
            try:
                SO127_RECEIVER.start({'decode_so127_telemetry': True}, 'SO-127',
                                     {'radio_is_sdr': True, 'sdr': {'port': 8775}})
                self.assertIn('bqe-sdr://127.0.0.1:8775', spawn.call_args.args[0])
            finally:
                SO127_RECEIVER.stop()
        for name in ('bqe_config/satellites.yaml', 'release_defaults/bqe_config/satellites.yaml'):
            config = yaml.safe_load((REPO / name).read_text(encoding='utf-8'))
            entry = next(s for s in config['satellites'] if s['nickname'] == 'so-127-ssdv')
            self.assertTrue(entry['decode_so127_telemetry'])
            self.assertFalse(entry.get('decode_telemetry', False))
            self.assertTrue(entry['decode_ssdv_images'])
            self.assertEqual(entry['ssdv_profile'], 'hades-sa')

    def test_cli_rates_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.wav'
            sf.write(source, waveform(200), 48000)
            command = [sys.executable, str(ROOT / 'bqe_so127_decoder.py')]
            result = subprocess.run(command + ['--file', str(source), '--bauds', '200', '--no-web',
                                    '--output', str(Path(directory) / 'decoded')], capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['packets'], 1)
            child = subprocess.Popen(command + ['--idle', '--managed', '--port', '0'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                out, err = child.communicate('', timeout=15)
                self.assertEqual(child.returncode, 0, err)
                self.assertIn('SO-127 telemetry controls', out)
            finally:
                if child.poll() is None:
                    child.kill(); child.wait()


if __name__ == '__main__':
    unittest.main()
