import json
from pathlib import Path
import queue
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave
import numpy as np
from plugins.bqe_sdr.audio_bridge import audio_device, audio_recorder
from plugins.bqe_sdr.bqe_sdr import handler
from plugins.bqe_sdr.service import Session, AudioHub, replay_options
from plugins.decoder_http import DecoderServer


def wait_state(session, state='running'):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        current=session.snapshot()['state']
        if current==state:
            return
        if current=='error':
            raise AssertionError(session.snapshot())
        time.sleep(.02)
    raise AssertionError(session.snapshot())


class ServiceTests(unittest.TestCase):
    def test_recorded_wav_and_sigmf_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            session=Session(directory)
            session.start(dict(source='demo',duration=.3,record_iq=True))
            session.thread.join(5)
            state=session.snapshot()
            self.assertEqual(state['state'],'completed',state)
            with wave.open(state['audio_file']) as wav:
                self.assertEqual((wav.getframerate(),wav.getnchannels()),(48000,1))
                x=np.frombuffer(wav.readframes(wav.getnframes()),'<i2')[3000:]
                self.assertGreater(np.sqrt(np.mean(x.astype(float)**2)),1000)
                tone=np.argmax(abs(np.fft.rfft(x)))*48000/len(x)
                self.assertAlmostEqual(tone,1000,delta=10)
            meta=json.loads(Path(state['iq_metadata']).read_text())
            self.assertEqual(meta['global']['core:datatype'],'cf32_le')
            self.assertEqual(Path(state['iq_file']).stat().st_size,state['iq_samples']*8)
            options=replay_options(state['iq_metadata'])
            self.assertEqual(options['center'],state['center'])
            replay=Session(Path(directory)/'replay')
            replay.start(dict(options,frequency=state['frequency']))
            replay.thread.join(5)
            self.assertEqual(replay.snapshot()['state'],'completed')
            self.assertEqual(replay.snapshot()['iq_samples'],state['iq_samples'])
            with wave.open(replay.snapshot()['audio_file']) as wav:
                y=np.frombuffer(wav.readframes(wav.getnframes()),'<i2')[3000:]
            np.testing.assert_array_equal(x,y)

    def test_tune_bounds_and_clean_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Session(directory);s.start(dict(source='demo',record_audio=False))
            try:
                wait_state(s)
                with self.assertRaises(ValueError):
                    s.start(dict(source='demo'))
                s.tune(145801000)
                self.assertEqual(s.snapshot()['frequency'],145801000)
                with self.assertRaises(ValueError):
                    s.tune(150000000)
                with self.assertRaises(ValueError):
                    s.tune(float('nan'))
            finally:
                s.stop();s.thread.join(5)
            self.assertEqual(s.snapshot()['state'],'stopped')
            self.assertFalse(s.thread.is_alive())

    def test_http_audio_fanout_and_origin_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            session=Session(directory)
            server=DecoderServer(('127.0.0.1',0),handler(session,{}))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def post(path,options,origin=None):
                headers={'Content-Type':'application/json'}
                if origin:
                    headers['Origin']=origin
                with urlopen(Request(base+path,data=json.dumps(options).encode(),headers=headers),timeout=5) as response:
                    return json.load(response)
            try:
                with self.assertRaises(HTTPError) as error:
                    post('/start',{'source':'demo'},'https://untrusted.example')
                self.assertEqual(error.exception.code,403)
                post('/start',dict(source='demo',record_audio=False))
                wait_state(session)
                device=audio_device(f'bqe-sdr://127.0.0.1:{server.server_port}')
                with audio_recorder(device,48000) as first, audio_recorder(device,48000) as second:
                    for reader in (first,second):
                        pcm=reader.record(4800)
                        self.assertEqual(pcm.shape,(4800,1))
                        self.assertGreater(np.sqrt(np.mean(pcm**2)),.1)
                post('/tune',{'frequency':145802000})
                self.assertEqual(session.snapshot()['frequency'],145802000)
                post('/stop',{})
            finally:
                session.stop()
                if session.thread:session.thread.join(5)
                server.shutdown();server.server_close();thread.join(2)

    def test_bad_bridge_addresses_and_sample_rate(self):
        for name in ('bqe-sdr://remote:8772','bqe-sdr://localhost:8772/secret',
                     'bqe-sdr://user@localhost:8772','bqe-sdr://localhost:8772?x=y'):
            with self.assertRaises(ValueError):audio_device(name)
        with self.assertRaisesRegex(ValueError,'48000'):
            with audio_recorder(audio_device('bqe-sdr://localhost:8772'),44100):
                pass

    def test_slow_consumer_disconnects_without_losing_other_clients(self):
        hub=AudioHub();hub.active=True
        slow,fast=hub.subscribe(),hub.subscribe()
        for _ in range(129):
            hub.publish(b'audio')
            self.assertEqual(fast.get_nowait(),b'audio')
        self.assertIsNone(slow.get_nowait())
        self.assertNotIn(slow,hub.clients)
        self.assertIn(fast,hub.clients)
        hub.close();self.assertIsNone(fast.get_nowait())

    def test_invalid_file_reports_error_without_success_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Session(directory)
            s.start(dict(source='file',iq_file=str(Path(directory)/'missing.iq')))
            s.thread.join(5)
            self.assertEqual(s.snapshot()['state'],'error')
            self.assertFalse(list(Path(directory).glob('*.wav')))


if __name__ == '__main__':
    unittest.main()
