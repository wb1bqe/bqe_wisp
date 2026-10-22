"""Pass recording contracts without radio hardware."""
import io
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
from plugins import pass_audio as audio
from plugins.bqe_sdr import integration


class PassAudioTests(unittest.TestCase):
    def test_explicit_flag_and_legacy_program(self):
        command = ['python', 'plugins/bqe_sound_recorder/bqe_sound_recorder.py']
        self.assertEqual(audio.pass_program({'program_to_run_during_pass': command}), command)
        for enabled in (True, False):
            config = dict(record_audio=enabled, program_to_run_during_pass=command)
            self.assertEqual(audio.recording_requested(config), enabled)
            self.assertEqual(audio.pass_program(config), '')
            config['program_to_run_during_pass'] = ['other-decoder']
            self.assertEqual(audio.pass_program(config), ['other-decoder'])
        with self.assertRaises(ValueError):
            audio.recording_requested({'record_audio': 'false'})

    def test_sources_and_graceful_stop(self):
        for sdr, expected in [(False, 'USB Audio Codec'), (True, 'bqe-sdr://127.0.0.1:8872')]:
            with patch.object(audio.subprocess, 'Popen') as popen:
                receiver = audio.PassAudio()
                receiver.start({'record_audio': True, 'nickname': 'test'},
                               {'radio_soundcard': 'USB Audio Codec', 'sdr': {'port': 8872}}, use_sdr=sdr)
                self.assertIn(expected, popen.call_args.args[0])
                receiver.stop()
                popen.return_value.stdin.close.assert_called_once()
                popen.return_value.wait.assert_called_once_with(timeout=8)
        with patch.object(audio.subprocess, 'Popen') as popen:
            receiver = audio.PassAudio()
            receiver.start({'record_audio': False}, {})
            with self.assertRaises(ValueError):
                receiver.start({'record_audio': True}, {})
            with self.assertRaises(ValueError):
                receiver.start({'record_audio': True}, {}, use_lrpt=True)
            popen.assert_not_called()

    def test_sdr_native_recording_precedence(self):
        for flag, suppressed in [(None, False), (True, True), (False, True)]:
            config = dict(receive_sdr=True, downlink_frequency_mhz='145.8', sdr={'record_audio': True})
            if flag is not None:
                config['record_audio'] = flag
            response = Mock()
            response.__enter__ = Mock(return_value=io.BytesIO(json.dumps({'state': 'running', 'instance': 'test'}).encode()))
            response.__exit__ = Mock(return_value=False)
            with patch.object(integration.subprocess, 'Popen') as popen, patch.object(integration, 'urlopen', return_value=response), patch.object(integration.uuid, 'uuid4', return_value=Mock(hex='test')):
                popen.return_value.poll.return_value = None
                receiver = integration.SDRReceiver()
                receiver.start(config, 'test', {})
                self.assertEqual('--no-record-audio' in popen.call_args.args[0], suppressed)
                receiver.stop()

    def test_wav_samples_and_source_failure(self):
        from plugins import audio_devices
        stop = threading.Event()
        capture = Mock()
        def block(_):
            if capture.record.call_count > 1:
                stop.set()
            return np.array([[0.5], [-0.5], [1.0], [-1.0]])
        capture.record.side_effect = block
        with tempfile.TemporaryDirectory() as folder, patch.object(audio_devices, 'audio_com', return_value=nullcontext()), patch.object(audio_devices, 'input_device'), patch.object(audio_devices, 'input_recorder', return_value=nullcontext(capture)):
            state = audio.record('bqe-sdr://127.0.0.1:8872', folder, '../test satellite', stop)
            self.assertEqual(state['status'], 'stopped')
            with wave.open(state['output_file']) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()), (48000, 1, 2, 4))
                np.testing.assert_array_equal(np.frombuffer(wav.readframes(4), dtype='<i2'), [16383, -16383, 32767, -32767])
            self.assertEqual(Path(state['output_file']).parent, Path(folder))
            capture.record.side_effect = RuntimeError('source unavailable')
            state = audio.record('bqe-sdr://127.0.0.1:8872', folder, 'test', threading.Event())
            self.assertEqual(state['status'], 'error')
            self.assertEqual(len(list(Path(folder).glob('*.wav'))), 1)

    def test_browser_accepts_wav_and_rejects_traversal(self):
        import bqe_wisp_web as web
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'pass.wav'
            target.write_bytes(b'test')
            (Path(folder)/'ignore.txt').write_text('test')
            self.assertEqual(web.list_recording_mp3_files(folder)[1], [target])
            self.assertEqual(web.resolve_recording_file(folder, 'pass.wav'), target)
            self.assertIsNone(web.resolve_recording_file(folder, '../pass.wav'))
            self.assertIsNone(web.resolve_recording_file(folder, 'ignore.txt'))

    def test_worker_finalizes_when_parent_pipe_closes(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('X-Audio-Format', 'f32le')
                self.send_header('X-Audio-Rate', '48000')
                self.send_header('X-Audio-Channels', '1')
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(np.full(4800, 0.25, dtype='<f4').tobytes())
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        worker = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                # Exercise the actual child lifetime without changing the live UI heartbeat.
                code = 'from plugins.bqe_sound_recorder import bqe_sound_recorder as r; r.publish_recording_status=lambda *a:None; from plugins.pass_audio import main; raise SystemExit(main())'
                worker = subprocess.Popen([sys.executable, '-c', code, '--source',
                    f'bqe-sdr://127.0.0.1:{server.server_port}', '--name', 'test', '--output', folder],
                    cwd=str(audio.ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                deadline = time.monotonic()+10
                while time.monotonic() < deadline:
                    files = list(Path(folder).glob('*.wav'))
                    if files and files[0].stat().st_size > 44:
                        break
                    if worker.poll() is not None:
                        self.fail(worker.stderr.read().decode())
                    time.sleep(0.05)
                if not files:
                    worker.kill()
                    worker.wait()
                    self.fail(worker.stderr.read().decode())
                worker.stdin.close()
                self.assertEqual(worker.wait(timeout=8), 0, worker.stderr.read().decode())
                with wave.open(str(files[0])) as wav:
                    self.assertGreater(wav.getnframes(), 0)
                    self.assertEqual(np.frombuffer(wav.readframes(1), dtype='<i2')[0], 8191)
        finally:
            if worker:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait()
                worker.stdin.close()
                worker.stdout.close()
                worker.stderr.close()
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
