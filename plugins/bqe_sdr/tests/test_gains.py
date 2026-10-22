import ctypes
import json
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import numpy as np
from plugins.bqe_sdr.config import Settings
from plugins.bqe_sdr.dsp import Demodulator
from plugins.bqe_sdr.service import Session
from plugins.bqe_sdr.sources import USBSource, TCPSource
from plugins.bqe_sdr.bqe_sdr import handler
from plugins.bqe_sdr.tests.test_service import wait_state
from plugins.decoder_http import DecoderServer


class GainTests(unittest.TestCase):
    def test_usb_discrete_gain_and_automatic(self):
        with patch('plugins.bqe_sdr.sources.load_library'):
            source = USBSource(Settings(), threading.Event())
        source.handle = ctypes.c_void_p(1)
        source.gains = [0, 9.9, 19.7, 49.6]
        source.check = Mock()
        self.assertEqual(source.set_gain(20), 19.7)
        source.check.assert_any_call('set_tuner_gain', 197)
        self.assertIsNone(source.set_gain(None))
        source.check.assert_called_with('set_tuner_gain_mode', 0)

    def test_tcp_live_commands(self):
        source = TCPSource(Settings(), threading.Event())
        source.socket = Mock()
        source.set_gain(19.7)
        source.socket.sendall.assert_any_call(b'\x03\x00\x00\x00\x01')
        source.socket.sendall.assert_any_call(b'\x04\x00\x00\x00\xc5')
        source.set_gain(None)
        source.socket.sendall.assert_called_with(b'\x03\x00\x00\x00\x00')

    def test_live_multiplier_changes_audio_without_reset(self):
        session = Session('.')
        session.settings = Settings.from_dict(dict(source='demo', audio_gain=.15))
        session.state['state'] = 'running'
        dsp = Demodulator(session.settings)
        reference = Demodulator(Settings.from_dict(session.settings.as_dict()))
        times = np.arange(32768)/960000
        iq = np.exp(2j*np.pi*(-240000*times + 5000/1000/(2*np.pi)*np.sin(2*np.pi*1000*times)))
        dsp.push(iq);reference.push(iq)
        session.set_gains({'audio_gain': .3})
        np.testing.assert_allclose(dsp.push(iq), reference.push(iq)*2, atol=1e-7)
        for invalid in ({'audio_gain': 0}, {'audio_gain': float('nan')}, {'gain': 100}, {'frequency': 1}):
            with self.assertRaises(ValueError): session.set_gains(invalid)
        self.assertEqual(session.settings.audio_gain, .3)

    def test_http_live_gain_and_session_defaults(self):
        defaults = dict(source='demo', record_audio=False, audio_gain=.15)
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            server = DecoderServer(('127.0.0.1', 0), handler(session, defaults))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            root = f'http://127.0.0.1:{server.server_port}'
            def change(value, origin=None):
                headers={'Content-Type':'application/json'}
                if origin: headers['Origin']=origin
                with urlopen(Request(root+'/gains', json.dumps(value).encode(), headers)) as response:
                    return json.load(response)
            try:
                session.start(defaults);wait_state(session)
                worker = session.thread
                self.assertEqual(session.snapshot()['settings']['audio_gain'], .15)
                self.assertEqual(change({'audio_gain': .6})['audio_gain'], .6)
                self.assertIs(session.thread, worker)
                self.assertEqual(session.snapshot()['settings']['audio_gain'], .6)
                with self.assertRaises(HTTPError) as error: change({'audio_gain': .4}, 'https://example.org')
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError): change({'gain': 10}) # Demo has no RF tuner.
                session.stop();session.thread.join(5)
                with self.assertRaises(HTTPError): change({'audio_gain': .4})
                session.start(defaults);wait_state(session)
                self.assertEqual(session.snapshot()['settings']['audio_gain'], .15)
                self.assertEqual(defaults['audio_gain'], .15)
            finally:
                session.stop();session.thread.join(5)
                server.shutdown();server.server_close()
