"""End-to-end audio tests; PySSTV is an independent test-only encoder."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import soundfile as sf
import sstv
from pysstv.color import MartinM1, ScottieS1, PD120, Robot36

from plugins.bqe_sstv_decoder.decoder import AudioDecoder
from plugins.bqe_sstv_decoder.service import Session, gallery_settings


def picture(width, height):
    image = Image.new('RGB', (width, height))
    colors = [(220, 30, 30), (30, 220, 30), (30, 30, 220), (200, 200, 200)]
    for i, color in enumerate(colors):
        image.paste(color, (i*width//4, 0, (i+1)*width//4, height))
    return image


def receive(audio, rate=12000, block=8192):
    images = []
    decoder = AudioDecoder(rate, lambda image, seconds: images.append(image))
    for pos in range(0, len(audio), block):
        decoder.push(audio[pos:pos+block])
    decoder.finish()
    return images


class DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = picture(320, 240)
        cls.robot = sstv.encode(cls.original, sstv.Mode.ROBOT_36, 12000).astype(np.float32)/32768

    def assert_colors(self, image):
        for x, wanted in zip((.125, .375, .625, .875), [(220,30,30),(30,220,30),(30,30,220),(200,200,200)]):
            actual = image.getpixel((int(x*image.width), image.height//2))
            self.assertLess(np.max(np.abs(np.array(actual)-wanted)), 35, (actual, wanted))

    def test_chunk_boundaries_and_consecutive_transmissions(self):
        audio = np.concatenate((np.zeros(7133), self.robot, np.zeros(4811), self.robot, np.zeros(12000)))
        images = receive(audio, block=3077)
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertTrue(image.info['sstv_complete'])
            self.assert_colors(image)

    def test_partial_and_noise(self):
        images = receive(self.robot[:12*12000])
        self.assertEqual(len(images), 1)
        self.assertFalse(images[0].info['sstv_complete'])
        self.assertEqual(receive(np.random.default_rng(1).normal(0,.1,12000*10)), [])
        self.assertEqual(receive(np.zeros(12000*10)), [])

    def test_callback_capture_preserves_vis_header(self):
        from plugins.audio_devices import _InputRecorder
        rate = 48000
        signal = sstv.encode(self.original, sstv.Mode.ROBOT_36, rate).astype(np.float32)[:3*rate]/32768
        capture = _InputRecorder()
        for start in range(0, len(signal), 480):
            block = np.column_stack((signal[start:start+480], signal[start:start+480]))
            capture.callback(block, len(block), None, False)
        images = []
        decoder = AudioDecoder(rate, lambda image, seconds: images.append(image))
        for start in range(0, len(signal), 4096):
            decoder.push(capture.record(min(4096, len(signal)-start))[:, 0])
        decoder.finish()
        self.assertEqual(len(images), 1)
        self.assertIn('ROBOT_36', str(images[0].info['sstv_mode']))

    def test_monitor_reports_selected_audio_spectrum(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            rate = 48000
            audio = .1 * np.sin(2*np.pi*1900*np.arange(4096)/rate)
            session.update_signal(audio, rate)
            monitor = session.snapshot()['signal']
            peak_bin = np.argmax(monitor['spectrum_db'])
            frequency = peak_bin * monitor['max_hz'] / (len(monitor['spectrum_db'])-1)
            self.assertLess(abs(frequency-1900), 15)
            self.assertEqual(len(monitor['waveform']), 256)
            self.assertEqual(monitor['waveform_ms'], 20)
            self.assertLess(len(monitor['spectrum_db']), 400)
            self.assertTrue(np.all(np.isfinite(monitor['spectrum_db'])))

    def test_44100_header_detection(self):
        rate = 44100
        audio = sstv.encode(self.original, sstv.Mode.ROBOT_36, rate).astype(np.float32)[:rate*4]/32768
        images = receive(audio, rate=rate, block=4096)
        self.assertEqual(len(images), 1)
        self.assertIn('ROBOT_36', str(images[0].info['sstv_mode']))

    def test_independent_encoder_families(self):
        for mode in (Robot36, MartinM1, ScottieS1, PD120):
            with self.subTest(mode=mode.__name__):
                image = picture(mode.WIDTH, mode.HEIGHT)
                encoder = mode(image, 12000, 16)
                audio = np.fromiter(encoder.gen_samples(), dtype=np.int16).astype(np.float32)/32768
                decoded = receive(np.concatenate((np.zeros(5501), audio, np.zeros(12000))))
                self.assertEqual(len(decoded), 1)
                self.assertTrue(decoded[0].info['sstv_complete'])
                self.assert_colors(decoded[0])

    def test_all_engine_modes(self):
        for name in dir(sstv.Mode):
            if not name.isupper():
                continue
            with self.subTest(mode=name):
                mode = getattr(sstv.Mode, name)
                image = picture(mode.image_width, mode.image_height)
                audio = sstv.encode(image, mode, 12000).astype(np.float32)/32768
                decoded = receive(audio)
                self.assertEqual(len(decoded), 1)
                self.assertTrue(decoded[0].info['sstv_complete'])
                self.assertEqual(str(decoded[0].info['sstv_mode']), str(mode))
                self.assert_colors(decoded[0])

    def test_file_sessions_wav_mp3_stereo_and_saved_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sf.write(root/'audio.wav', np.column_stack((np.zeros(len(self.robot)), self.robot)), 12000)
            sf.write(root/'audio.mp3', self.robot, 12000)
            for suffix, channel in (('wav', 1), ('mp3', 0)):
                with self.subTest(suffix=suffix):
                    output = root/suffix
                    session = Session(output)
                    session.start({'channel': channel}, root/f'audio.{suffix}')
                    session.thread.join(30)
                    status = session.snapshot()
                    self.assertEqual(status['state'], 'completed', status)
                    self.assertEqual(status['images'], 1, status)
                    record = status['recent'][0]
                    self.assertTrue(record['complete'])
                    jpg = output/record['filename']
                    with Image.open(jpg) as image:
                        self.assert_colors(image)
                    self.assertEqual(json.loads(jpg.with_suffix('.json').read_text())['mode'], 'ROBOT_36')
                    self.assertEqual(list(output.glob('*.tmp')), [])

    def test_bad_file_and_unavailable_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.wav'
            path.write_bytes(b'not audio')
            session = Session(folder)
            session.start({}, path)
            session.thread.join(5)
            self.assertEqual(session.snapshot()['state'], 'error')
            sf.write(path, self.robot, 12000)
            session.start({'channel': 2}, path)
            session.thread.join(5)
            self.assertIn('unavailable', session.snapshot()['error'])

    def test_gallery_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'settings.yaml'
            config.write_text('program_settings:\n- bqe_wisp:\n    sstv_gallery_location: images\n    ui_port: 8028\n')
            output, port = gallery_settings(config)
            self.assertEqual(output.name, 'images')
            self.assertTrue(output.is_absolute())
            self.assertEqual(port, 8028)
            config.write_text('program_settings: []')
            with self.assertRaisesRegex(ValueError, 'sstv_gallery_location'):
                gallery_settings(config)

    def test_live_capture_stop_and_duplicate_start(self):
        import soundcard
        class Recorder:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def record(self, numframes):
                time.sleep(.01)
                return np.zeros((numframes, 2), dtype=np.float32)
        class Microphone:
            name = 'Test input'
            def recorder(self, **kwargs): return Recorder()
        with tempfile.TemporaryDirectory() as folder, patch.object(soundcard, 'default_microphone', return_value=Microphone()), \
                patch('plugins.bqe_sstv_decoder.service.input_recorder', return_value=Recorder()):
            session = Session(folder)
            session.start({'sample_rate': 12000})
            with self.assertRaisesRegex(ValueError, 'Stop the current'):
                session.start({})
            time.sleep(.1)
            session.stop()
            session.thread.join(5)
            self.assertFalse(session.thread.is_alive())
            self.assertEqual(session.snapshot()['state'], 'stopped')


if __name__ == '__main__':
    unittest.main()
