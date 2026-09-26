# SPDX-License-Identifier: GPL-3.0-or-later
import json
import ast
import os
import sys
import shutil
import struct
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import numpy as np
import soundfile as sf
from plugins.bqe_tlm_decoder.dsp import AudioDecoder
from plugins.bqe_tlm_decoder.protocols import USPFEC, crc_x25, kiss_encode, telemetry_records
from plugins.bqe_tlm_decoder.service import Session
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler

VECTORS = json.loads(Path(__file__).with_name('usp_vectors.json').read_text())


def usp_bits(name='short'):
    return np.unpackbits(np.frombuffer(bytes.fromhex('5072f64b2d90b1f5' + VECTORS['frame_' + name]), dtype=np.uint8)).astype(float) * 2 - 1


def waveform(bits, baud, rate=48000):
    symbols = np.r_[np.tile([-1, 1], 100), bits, np.zeros(40)]
    # Non-integer sample timing and small sample-clock mismatch.
    indices = np.floor(np.arange(int(len(symbols) * rate / baud / 1.0002)) * baud * 1.0002 / rate).astype(int)
    return .45 * symbols[indices] + .015 * np.random.default_rng(7).normal(size=len(indices)) + .03


def ax25_bits(frame, corrupt=False):
    fcs = crc_x25(frame) ^ int(corrupt)
    data = frame + fcs.to_bytes(2, 'little')
    stuffed = []; ones = 0
    for value in data:
        for bit in [(value >> i) & 1 for i in range(8)]:
            stuffed.append(bit); ones = ones + 1 if bit else 0
            if ones == 5:
                stuffed.append(0); ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    register = 0; level = 0; symbols = []
    for bit in flag * 40 + stuffed + flag * 8:
        scrambled = bit ^ ((register >> 16) & 1) ^ ((register >> 11) & 1)
        register = ((register << 1) | scrambled) & 0x1ffff
        if not scrambled:
            level ^= 1
        symbols.append(2 * level - 1)
    return symbols


class DecoderTests(unittest.TestCase):
    def test_telemetry_validity_and_unknown_records(self):
        payload = struct.pack('<4HhhB', 14, 2, 1, 5, 120, 7400, 1)
        records, tail = telemetry_records(payload + b'partial')
        self.assertEqual(records[0]['fields']['voltage_V'], 7.4)
        self.assertIsNone(records[0]['fields']['current_mA'])
        self.assertEqual(tail, b'partial'.hex())
        records, tail = telemetry_records(struct.pack('<4H', 999, 2, 1, 3) + b'abc')
        self.assertEqual(records[0]['fields'], {})
        self.assertEqual(records[0]['raw_hex'], b'abc'.hex())

    def test_bqe_graceful_stop_environment(self):
        root = Path(__file__).resolve().parents[3]
        tree = ast.parse((root / 'bqe_track_continuously.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'start_pass_program')
        process = Mock()
        ns = dict(os=os, sys=sys, shutil=shutil, subprocess=process,
                  normalize_program_command=lambda c: list(c), write_pid_file=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<pass helper>', 'exec'), ns)
        with tempfile.TemporaryDirectory() as folder, patch('builtins.print'):
            for script in ('bqe_tlm_decoder.py', 'bqe_sound_recorder.py', 'other.py'):
                ns['start_pass_program']([sys.executable, script], str(Path(folder) / 'pass.pid'))
                kwargs = process.Popen.call_args.kwargs
                self.assertEqual('env' in kwargs, script != 'other.py')
                if 'env' in kwargs:
                    self.assertTrue(kwargs['env']['BQE_PASS_STOP_FILE'].endswith('pass.pid.stop'))

    def decode(self, audio, baud, rate=48000, protocol='auto'):
        out = []; decoder = AudioDecoder(rate, out.append, [baud], protocol)
        for start in range(0, len(audio), 1023):
            decoder.push(audio[start:start + 1023])
        decoder.finish()
        return out

    def test_reference_fec_and_damaged_symbols(self):
        fec = USPFEC()
        for name, length in [('short', 80), ('long', 255)]:
            symbols = usp_bits(name)[128:128 + length * 16].copy()
            for damaged in (False, True):
                if damaged:
                    symbols[200:212] *= -1
                frame, errors = fec.decode(symbols)
                self.assertEqual(frame.hex(), VECTORS['frame_' + name + '_out'])

    def test_audio_usp_all_rates_and_polarities(self):
        for baud in (1200, 2400, 4800, 9600):
            for rate, polarity in [(48000, 1), (44100, -1)]:
                with self.subTest(baud=baud, rate=rate):
                    out = self.decode(waveform(usp_bits() * polarity, baud, rate), baud, rate)
                    self.assertEqual(len(out), 1)
                    self.assertEqual(out[0]['frame_hex'], VECTORS['frame_short_out'])

    def test_ax25_crc_and_audio(self):
        frame = bytes.fromhex(VECTORS['frame_short_out'])
        self.assertEqual(crc_x25(b'123456789'), 0x906e)
        for baud in (1200, 2400, 4800, 9600):
            for corrupt in (False, True):
                out = self.decode(waveform(ax25_bits(frame, corrupt), baud), baud, protocol='ax25')
                self.assertEqual(len(out), 0 if corrupt else 1)
                if out:
                    self.assertEqual(out[0]['frame_hex'], frame.hex())

    def test_file_mp3_wav_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            for extension in ('wav', 'mp3'):
                source = Path(folder) / ('input.' + extension)
                sf.write(source, waveform(usp_bits(), 1200), 48000)
                session = Session(Path(folder) / extension)
                session.start({'bauds': [1200]}, source)
                session.thread.join(20)
                result = session.snapshot()
                self.assertEqual(result['state'], 'completed', result)
                self.assertEqual(result['packets'], 1, result)
                saved = json.loads(Path(result['log']).read_text())
                self.assertEqual(saved['frame_hex'], VECTORS['frame_short_out'])
                self.assertEqual(Path(result['kiss']).read_bytes(), kiss_encode(bytes.fromhex(saved['frame_hex'])))

    def test_invalid_file_and_noise(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            session.start({}, Path(folder) / 'missing.mp3')
            session.thread.join(5)
            self.assertEqual(session.snapshot()['state'], 'error')
        self.assertEqual(self.decode(np.random.default_rng(4).normal(size=48000), 9600), [])

    def test_live_capture_and_stop(self):
        audio = waveform(usp_bits(), 1200)
        class Recorder:
            offset = 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def record(self, numframes):
                time.sleep(.01)
                block = np.zeros((numframes, 2))
                part = audio[self.offset:self.offset + numframes]
                block[:len(part), 0] = part
                self.offset += numframes
                return block
        class Device:
            name = 'Microphone (USB Audio Codec)'
            id = 'usb-radio-input'
            isloopback = False
            def recorder(self, samplerate): return Recorder()
        with tempfile.TemporaryDirectory() as folder, patch('soundcard.all_microphones', return_value=[Device()]), \
                patch('plugins.bqe_tlm_decoder.service.input_recorder', return_value=Recorder()):
            session = Session(folder)
            session.start({'bauds': [1200], 'device': 'USB Audio Codec', 'source_name': 'umka-1'})
            deadline = time.monotonic() + 10
            while not session.snapshot()['packets'] and time.monotonic() < deadline:
                time.sleep(.02)
            session.stop(); session.thread.join(5)
            self.assertEqual(session.snapshot()['state'], 'stopped')
            self.assertEqual(session.snapshot()['packets'], 1)
            self.assertEqual(session.snapshot()['device'], 'Microphone (USB Audio Codec)')
            self.assertEqual(session.snapshot()['source'], 'umka-1')
            self.assertEqual(session.snapshot()['recent'][0]['input_source'], 'umka-1')

    def test_http_upload_and_origin_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'sample.wav'
            sf.write(source, waveform(usp_bits(), 1200), 48000)
            session = Session(folder)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(session, {'bauds': [1200]}))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url) as r:
                    self.assertIn(b'Start live audio', r.read())
                req = Request(url + '/file', data=source.read_bytes(), headers={'Content-Type':'application/octet-stream', 'X-Filename':'sample.wav'})
                with urlopen(req) as r: self.assertEqual(r.status, 200)
                session.thread.join(10)
                self.assertEqual(session.snapshot()['packets'], 1)
                bad = Request(url + '/stop', data=b'{}', headers={'Content-Type':'application/json', 'Origin':'http://evil.example'})
                with self.assertRaises(HTTPError) as error: urlopen(bad)
                self.assertEqual(error.exception.code, 403)
            finally:
                server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
