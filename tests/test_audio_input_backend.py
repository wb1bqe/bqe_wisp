"""Device selection and error handling for Windows microphone capture."""
from types import SimpleNamespace
import unittest
import numpy as np
from unittest.mock import Mock, patch

from plugins.audio_devices import input_recorder, _InputRecorder


class InputBackendTests(unittest.TestCase):
    def setUp(self):
        self.source = SimpleNamespace(name='Microphone (USB Audio CODEC )', isloopback=False)
        self.sd = Mock()
        self.sd.query_hostapis.return_value = [{'name': 'MME'}, {'name': 'Windows WASAPI'}]
        self.device = dict(name=self.source.name, max_input_channels=2, hostapi=1)
        self.sd.query_devices.return_value = [dict(self.device, hostapi=0), self.device]
        self.stream = Mock()
        self.context = self.sd.InputStream.return_value
        self.context.__enter__ = Mock(return_value=self.stream)
        self.context.__exit__ = Mock(return_value=False)
        self.addCleanup(patch.stopall)
        patch('plugins.audio_devices.sys.platform', 'win32').start()
        patch('plugins.audio_devices._sounddevice', self.sd).start()

    def test_selects_exact_wasapi_input_and_preserves_channels(self):
        samples = np.arange(8192, dtype=np.float32).reshape(4096, 2)
        with input_recorder(self.source, 48000) as recorder:
            callback = self.sd.InputStream.call_args.kwargs['callback']
            callback(samples, len(samples), None, False)
            np.testing.assert_array_equal(recorder.record(4096), samples)
        self.assertEqual(self.sd.InputStream.call_args.kwargs['device'], 1)
        self.assertEqual(self.sd.InputStream.call_args.kwargs['channels'], 2)
        settings = self.sd.InputStream.call_args.kwargs['extra_settings']
        self.assertEqual(settings._streaminfo.streamOption, self.sd._lib.eStreamOptionRaw)
        self.sd.WasapiSettings.assert_called_once_with(auto_convert=True)
        self.stream.read.assert_not_called()
        self.context.__exit__.assert_called_once()

    def test_ambiguous_or_missing_input_never_opens_default_device(self):
        for devices in ([], [self.device, self.device]):
            self.sd.query_devices.return_value = devices
            with self.assertRaisesRegex(ValueError, 'Cannot uniquely match'):
                with input_recorder(self.source, 48000):
                    self.fail('Must not capture')
        self.sd.InputStream.assert_not_called()

    def test_overflow_stops_capture_and_closes_stream(self):
        with self.assertRaisesRegex(RuntimeError, 'lost samples'):
            with input_recorder(self.source, 48000) as recorder:
                recorder.callback(None, 0, None, 'input overflow')
                recorder.record(4096)
        self.context.__exit__.assert_called_once()

    def test_native_callback_blocks_reassemble_without_gaps(self):
        recorder = _InputRecorder()
        expected = np.arange(480*40*2, dtype=np.float32).reshape(-1, 2)
        for start in range(0, len(expected), 480):
            block = expected[start:start+480].copy()
            recorder.callback(block, len(block), None, False)
            block[:] = -1  # Driver may reuse its callback buffer immediately.
        actual = np.concatenate([recorder.record(4096) for _ in range(4)] +
                                [recorder.record(len(expected)-4*4096)])
        np.testing.assert_array_equal(actual, expected)

    def test_callback_queue_is_bounded_and_overflow_is_reported(self):
        recorder = _InputRecorder()
        for _ in range(513):
            recorder.callback(np.zeros((480, 2)), 480, None, False)
        self.assertEqual(recorder.blocks.qsize(), 512)
        with self.assertRaisesRegex(RuntimeError, 'lost samples'):
            recorder.record(4096)

    def test_loopback_stays_on_soundcard(self):
        source = Mock(isloopback=True)
        context = source.recorder.return_value
        context.__enter__ = Mock(return_value='loopback recorder')
        context.__exit__ = Mock(return_value=False)
        with input_recorder(source, 48000) as recorder:
            self.assertEqual(recorder, 'loopback recorder')
        self.sd.InputStream.assert_not_called()

    def test_missing_dependency_has_actionable_error(self):
        with patch('plugins.audio_devices._sounddevice', None):
            with self.assertRaisesRegex(RuntimeError, 'requirements.txt'):
                with input_recorder(self.source, 48000):
                    self.fail('Must not capture')
