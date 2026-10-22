from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import bqe_wisp_web as web
from plugins.recording_decoders import RecordingDecoders, recording_source
from plugins.bqe_speech_decoder.storage import save_transcript, fingerprint


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / 'iss-sstv-20261007T120000Z-123abc.wav'
        self.audio.write_bytes(b'audio')

    def test_source_names_and_escaped_html(self):
        self.assertEqual(recording_source(self.audio.name), 'iss-sstv')
        self.assertEqual(recording_source('ao-91_2026-10-07_12-00-00.mp3'), 'ao-91')
        self.assertEqual(recording_source('unlabelled.wav'), 'Other recordings')
        page = web.build_recordings_html(str(self.root))
        self.assertIn('data-source="iss-sstv"', page)
        self.assertIn('id="satelliteFilterDialog"', page)
        self.assertIn('Decode APRS', page)
        self.assertIn('Decode SSTV', page)
        self.assertIn('/recordings-controls.js', page)
        self.assertIn('Transcribe speech', page)
        for code in ('en', 'fr', 'es', 'pt-BR', 'pt-PT', 'zh', 'ja'):
            self.assertIn('data-speech-language="' + code + '"', page)

    def test_transcript_http_and_saved_button(self):
        save_transcript(self.audio, fingerprint(self.audio), 'small.en',
                        [dict(start=1.25,end=2.5,text='Kilo One Alpha, five nine, FN31',uncertain=True)], 3)
        page = web.build_recordings_html(str(self.root))
        self.assertIn('data-transcript-name="' + self.audio.name + '" >View transcript', page)
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(web.WebConsoleHandler,
            status_payload_func=lambda: {}, recordings_location=str(self.root)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}/recording-transcript?name='
        with urlopen(base + self.audio.name) as response:
            result = json.load(response)
        self.assertEqual(result['segments'][0]['start'], 1.25)
        with urlopen(base + self.audio.name + '&format=txt') as response:
            self.assertIn(b'FN31', response.read())
        for name in ('../outside.wav', 'missing.wav'):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + name)
            self.assertEqual(error.exception.code, 404)
        self.audio.write_bytes(b'changed recording')
        with self.assertRaises(HTTPError) as error:
            urlopen(base + self.audio.name)
        self.assertEqual(error.exception.code, 404)

    def test_speech_batch_uses_plugin_and_reports_segment_count(self):
        job = RecordingDecoders()
        commands = []
        def launch(command, **kwargs):
            commands.append(command)
            kwargs['stdout'].write(json.dumps(dict(state='completed',segments=2,
                transcript=self.audio.name,message='Transcript saved')).encode())
            return Mock(returncode=0)
        with patch('plugins.recording_decoders.subprocess.Popen', side_effect=launch):
            job.start(self.root, 'speech', [self.audio], language='pt')
            self.wait(job)
        self.assertIn('bqe_speech_decoder.py', commands[0][1])
        self.assertIn('--status-file', commands[0])
        self.assertEqual(commands[0][commands[0].index('--language') + 1], 'pt')
        with self.assertRaises(ValueError):
            job.start(self.root, 'speech', [self.audio], language=['invalid'])
        self.assertNotIn('--device', commands[0])
        item = job.snapshot()['items'][0]
        self.assertEqual(item['count'], 2)
        self.assertEqual(item['state'], 'completed')

    def test_http_validates_entire_selection_before_starting(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(web.WebConsoleHandler,
            status_payload_func=lambda: {}, recordings_location=str(self.root)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        with patch.object(web.RECORDING_DECODERS, 'start', return_value={'running': True}) as start:
            for names in ([self.audio.name, '../outside.wav'], [self.audio.name, 'missing.wav'],
                          [self.audio.name, self.audio.name], [], ['file.exe']):
                request = Request(base+'/api/recordings/decode',
                    data=json.dumps(dict(decoder='aprs', names=names)).encode(),
                    headers={'Content-Type': 'application/json'})
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 400)
            start.assert_not_called()
            request = Request(base+'/api/recordings/decode',
                data=json.dumps(dict(decoder='aprs', names=[self.audio.name])).encode(),
                headers={'Content-Type': 'application/json', 'Origin': 'https://other.invalid'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            del request.headers['Origin']
            with urlopen(request) as response:
                self.assertTrue(json.load(response)['running'])
            self.assertEqual(start.call_args.args[1:3], ('aprs', [self.audio.resolve()]))
            with urlopen(base+'/recordings-controls.js') as response:
                self.assertIn(b'showModal', response.read())

    def test_batch_continues_after_error_and_uses_file_only_commands(self):
        job = RecordingDecoders()
        commands = []
        def launch(command, **kwargs):
            commands.append(command)
            self.assertNotIn('--device', command)
            self.assertNotIn('--managed', command)
            self.assertNotIn('BQE_PASS_STOP_FILE', kwargs['env'])
            result = dict(state='error', error='Bad recording') if len(commands) == 1 else dict(state='completed', images=2)
            kwargs['stdout'].write(json.dumps(result).encode())
            return Mock(returncode=1 if len(commands) == 1 else 0)
        with patch('plugins.recording_decoders.subprocess.Popen', side_effect=launch):
            job.start(self.root, 'sstv', [self.audio, self.root/'other.wav'], self.root/'gallery')
            self.wait(job)
        items = job.snapshot()['items']
        self.assertEqual([item['state'] for item in items], ['error', 'completed'])
        self.assertEqual(items[1]['count'], 2)
        self.assertIn('--combine-fragments', commands[0])

    def test_cancel_stops_current_and_skips_remaining(self):
        job = RecordingDecoders()
        started, ended = threading.Event(), threading.Event()
        process = Mock(returncode=-1)
        process.poll.return_value = None
        process.wait.side_effect = lambda **kwargs: ended.wait(5)
        process.terminate.side_effect = ended.set
        def launch(*args, **kwargs):
            started.set()
            return process
        with patch('plugins.recording_decoders.subprocess.Popen', side_effect=launch) as launch_mock:
            job.start(self.root, 'aprs', [self.audio, self.root/'second.wav'])
            self.assertTrue(started.wait(5))
            with self.assertRaisesRegex(ValueError, 'already running'):
                job.start(self.root, 'aprs', [self.audio])
            job.cancel()
            self.wait(job)
            launch_mock.assert_called_once()
        self.assertEqual([item['state'] for item in job.snapshot()['items']], ['cancelled', 'cancelled'])

    def test_real_aprs_file_batch_decodes_packet(self):
        import soundfile as sf
        try:
            from plugins.bqe_aprs_decoder.tests.test_decoder import afsk, frame
        except ImportError:
            self.skipTest('Run with the APRS test environment for packet generation')
        sf.write(self.audio, afsk(frame()), 48000)
        plugin = self.root/'plugins'/'bqe_aprs_decoder'
        plugin.mkdir(parents=True)
        (plugin/'bqe_aprs_decoder.py').write_text(
            'import sys\nsys.path.insert(0, '+repr(str(web.SCRIPT_DIR))+')\n'
            'from plugins.bqe_aprs_decoder.bqe_aprs_decoder import main\nraise SystemExit(main())\n')
        job = RecordingDecoders()
        job.start(self.root, 'aprs', [self.audio])
        try:
            self.wait(job, 60)
            item = job.snapshot()['items'][0]
            self.assertEqual(item['state'], 'completed', item)
            self.assertEqual(item['count'], 1)
            self.assertEqual(item['result']['recent'][0]['input_source'], 'iss-sstv')
        finally:
            job.cancel()

    def wait(self, job, timeout=10):
        deadline = time.monotonic() + timeout
        while job.snapshot()['running'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(job.snapshot()['running'], job.snapshot())

    def test_real_sstv_file_batch_saves_image_in_gallery(self):
        import numpy as np
        import soundfile as sf
        try:
            from pysstv.color import Robot36
            from plugins.bqe_sstv_decoder.tests.test_decoder import picture
        except ImportError:
            self.skipTest('Run with the SSTV test environment for the independent image encoder')
        encoder = Robot36(picture(320, 240), 12000, 16)
        audio = np.fromiter(encoder.gen_samples(), dtype=np.int16).astype(np.float32) / 32768
        sf.write(self.audio, audio, 12000)
        gallery = self.root/'gallery'
        job = RecordingDecoders()
        job.start(web.SCRIPT_DIR, 'sstv', [self.audio], gallery)
        try:
            self.wait(job, 90)
            item = job.snapshot()['items'][0]
            self.assertEqual(item['state'], 'completed', item)
            self.assertGreaterEqual(item['count'], 1)
            self.assertTrue((gallery/item['result']['recent'][0]['filename']).is_file())
        finally:
            job.cancel()
