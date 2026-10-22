import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import contextmanager
import numpy as np
from urllib.error import URLError
from plugins.bqe_audio_stream import listen
from plugins.bqe_sdr import audio_bridge


class RadioListenTests(unittest.TestCase):
    def test_refused_sdr_connection_reports_recovery_before_streaming(self):
        handler = Mock(headers={'X-BQE-Audio': '1'})
        with patch.object(listen, 'radio_source', return_value='bqe-sdr://127.0.0.1:8772'), \
             patch('plugins.audio_devices.audio_com'), \
             patch.object(audio_bridge, 'urlopen', side_effect=URLError(ConnectionRefusedError(10061, 'refused'))):
            listen.RadioListen().serve(handler)
        code, mime, body = handler.send_bytes.call_args.args
        self.assertEqual(code, 503)
        self.assertIn(b'SDR audio service is not running', body)
        self.assertIn(b'restart tracking', body)
        handler.send_response.assert_not_called()

    def test_source_uses_rig_and_never_default_microphone(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'bqe_config').mkdir()
            config = root/'bqe_config/my_rig.yaml'
            with patch.object(listen, 'ROOT', root):
                config.write_text('radio_is_sdr: true\nsdr: {port: 8872}\n')
                self.assertEqual(listen.radio_source(), 'bqe-sdr://127.0.0.1:8872')
                config.write_text('radio_is_sdr: false\nradio_soundcard: USB Audio CODEC\n')
                self.assertEqual(listen.radio_source(), 'USB Audio CODEC')
                config.write_text('radio_is_sdr: false\n')
                with self.assertRaisesRegex(ValueError, 'radio_soundcard'):
                    listen.radio_source()

    def test_pcm_and_disconnect_release_source(self):
        closed = []
        recorder = Mock()
        recorder.record.side_effect = [np.array([[.5, -.5], [2, -2], [-2, 2]]), RuntimeError('ended')]
        @contextmanager
        def source():
            try:
                yield recorder
            finally:
                closed.append(True)
        handler = Mock(headers={'X-BQE-Audio': '1'}, wfile=io.BytesIO())
        with patch.object(listen, 'radio_recorder', source):
            listen.RadioListen().serve(handler)
        self.assertEqual(np.frombuffer(handler.wfile.getvalue(), '<i2').tolist(), [16383,32767,-32767])
        self.assertEqual(closed, [True])
        handler.send_response.assert_called_once_with(200)
        handler.send_bytes.assert_not_called()

    def test_missing_source_reports_error_before_streaming(self):
        handler = Mock(headers={'X-BQE-Audio': '1'})
        with patch.object(listen, 'radio_recorder', side_effect=ValueError('Radio unavailable')):
            listen.RadioListen().serve(handler)
        self.assertEqual(handler.send_bytes.call_args.args[0], 503)
        handler.send_response.assert_not_called()

    def test_no_header_does_not_open_input(self):
        handler = Mock(headers={})
        with patch.object(listen, 'radio_recorder') as source:
            listen.RadioListen().serve(handler)
            source.assert_not_called()
        self.assertEqual(handler.send_bytes.call_args.args[0], 403)

    def test_shutdown_does_not_open_input(self):
        handler = Mock(headers={'X-BQE-Audio': '1'})
        service = listen.RadioListen()
        service.close()
        with patch.object(listen, 'radio_recorder') as source:
            service.serve(handler)
            source.assert_not_called()
        self.assertEqual(handler.send_bytes.call_args.args[0], 503)
