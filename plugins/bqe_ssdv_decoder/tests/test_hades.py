"""Operator reference, upstream encoder roundtrip, and SO-127 FSK recovery."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import numpy as np
from PIL import Image
from plugins.bqe_ssdv_decoder.hades import AudioDecoder, Validator, Framer, Images, executable
from plugins.bqe_ssdv_decoder.integration import SSDV_RECEIVER


def reference():
    return bytes.fromhex('5566bf35fb') + bytes.fromhex(Path(__file__).with_name('hades_sample.txt').read_text())


class HadesTests(unittest.TestCase):
    def test_operator_reference_and_error_correction(self):
        validator = Validator()
        raw = reference()
        p = validator.packet(raw)
        self.assertIsNotNone(p)
        self.assertEqual((p[6], int.from_bytes(p[7:9], 'big'), p[9]*16, p[10]*16), (0,41,320,240))
        bad = bytearray(raw)
        for i in range(70,78):
            bad[i] ^= 0x35
        self.assertEqual(validator.packet(bytes(bad)), p)
        for i in range(80,110):
            bad[i] ^= 0x55
        self.assertIsNone(validator.packet(bytes(bad)))

    def test_audio_rates_polarity_and_chunk_boundaries(self):
        raw = reference()
        bits = np.unpackbits(np.frombuffer(b'\x55'*30 + raw + b'\x55'*20, dtype=np.uint8))
        for baud, polarity in ((200,1),(800,-1),(1600,1)):
            with self.subTest(baud=baud), patch('plugins.bqe_ssdv_decoder.hades.Images'):
                packets = []
                decoder = AudioDecoder(48000, lambda *a:None, packet_sink=packets.append)
                audio = np.repeat(bits.astype(float)*2-1, 48000//baud)*polarity*.3
                audio += np.random.default_rng(3).normal(0,.015,len(audio))
                for i in range(0,len(audio),4093):
                    decoder.push(audio[i:i+4093])
                decoder.finish()
                self.assertEqual(packets, [raw])

    def test_real_ssdv_encoder_complete_reordered_and_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            src, out = Path(folder)/'source.jpg', Path(folder)/'source.ssdv'
            rng = np.random.default_rng(8)
            Image.fromarray(rng.integers(0,256,(48,64,3),dtype=np.uint8)).save(src, quality=85)
            subprocess.run([executable(), '-e', '-i', '23', str(src), str(out)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data = out.read_bytes()
            frames = [data[i:i+256] for i in range(0,len(data),256)]
            self.assertGreater(len(frames),2)
            valid = Validator()
            onair = [p[:2]+bytes.fromhex('bf35fba3')+p[6:] for p in frames]
            restored = [valid.packet(p) for p in onair]
            self.assertTrue(all(restored))
            images = []
            receiver = Images(lambda im,t:images.append(im), lambda **k:None, lambda im:None)
            for p in reversed(restored):
                receiver.packet(p, 1)
                receiver.packet(p, 1)
            receiver.finish()
            self.assertEqual(len(images),1)
            self.assertTrue(images[0].info['ssdv_complete'])
            self.assertEqual(images[0].size,(64,48))
            images.clear()
            receiver = Images(lambda im,t:images.append(im), lambda **k:None, lambda im:None)
            for p in restored[1:]:
                receiver.packet(p,1)
            receiver.finish()
            self.assertTrue(images)
            self.assertFalse(images[0].info['ssdv_complete'])
            self.assertEqual(images[0].info['missing_packets'],1)

    def test_noise_produces_no_image(self):
        with patch('plugins.bqe_ssdv_decoder.hades.Images') as images:
            decoder = AudioDecoder(48000, lambda *a:None)
            decoder.push(np.random.default_rng(4).normal(0,.1,48000))
            self.assertEqual(decoder.packets,0)
            images.return_value.packet.assert_not_called()

    def test_sdr_iq_demodulation_feeds_so127(self):
        from plugins.bqe_sdr.config import Settings
        from plugins.bqe_sdr.dsp import Demodulator
        raw = reference()
        bits = np.unpackbits(np.frombuffer(b'\x55'*30 + raw + b'\x55'*20, dtype=np.uint8))
        for baud, shift in ((200,1125), (800,1600), (1600,1600)):
            with self.subTest(baud=baud), patch('plugins.bqe_ssdv_decoder.hades.Images'):
                settings = Settings.from_dict(dict(sample_rate=240000, frequency=436875000,
                    center=436935000, bandwidth=12000, deviation=1600, audio_gain=.15))
                offsets = np.repeat(bits.astype(float)*-shift+shift/2, 240000//baud)-60000
                iq = .7*np.exp(2j*np.pi*np.cumsum(offsets)/240000)
                modem = Demodulator(settings)
                packets = []
                decoder = AudioDecoder(48000, lambda *a:None, packet_sink=packets.append)
                for i in range(0,len(iq),16384):
                    decoder.push(modem.push(iq[i:i+16384]))
                decoder.finish()
                self.assertEqual(packets, [raw])

    def test_scheduler_passes_profile_and_sdr_source(self):
        rig = {'radio_is_sdr':True,'sdr':{'port':8772}}
        process = Mock()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=process) as launch:
            SSDV_RECEIVER.start({'decode_ssdv_images':True,'ssdv_profile':'hades-sa'}, 'SO-127', rig)
            command = launch.call_args.args[0]
            self.assertEqual(command[command.index('--profile')+1], 'hades-sa')
            self.assertEqual(command[command.index('--device')+1], 'bqe-sdr://127.0.0.1:8772')
            SSDV_RECEIVER.stop()
