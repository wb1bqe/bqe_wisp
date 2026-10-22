import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from plugins import pass_announcements as speech


class PassAnnouncementTests(unittest.TestCase):
    def test_spells_only_short_hyphenated_groups_in_configured_satellites(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'satellites.yaml'
            names = ['iss-rpt', 'ao-123-tlm', 'luca-1', 'rs61', 'iss',
                     'iss-sstv', 't-micro-tlm', 'rs-18s', 'meteor-m2-4-lrpt']
            config.write_text(json.dumps({'satellites': [
                {'nickname': name, 'satellite_name': name.upper()} for name in names]}))
            expected = {
                'ISS-RPT': 'I S S, R P T', 'ao-123-tlm': 'A O, 123, T L M',
                'luca-1': 'luca-1', 'rs61': 'rs61', 'iss': 'iss',
                'iss-sstv': 'I S S, sstv', 't-micro-tlm': 't, micro, T L M',
                'rs-18s': 'R S, 18s', 'meteor-m2-4-lrpt': 'meteor-m2-4-lrpt',
                'XYZ-12': 'XYZ-12',
            }
            original = config.read_bytes()
            for name, spoken in expected.items():
                with self.subTest(name=name):
                    self.assertEqual(speech.spoken_satellite_name(name, config), spoken)
            self.assertEqual(config.read_bytes(), original)
            config.write_text('satellites: [')
            self.assertEqual(speech.spoken_satellite_name('ISS-RPT', config), 'ISS-RPT')

    def test_announcements_use_spoken_name_for_both_events(self):
        announcer = speech.PassAnnouncer()
        with patch.object(speech, 'spoken_satellite_name', return_value='I S S, R P T'), \
                patch.object(speech, 'speak') as speak:
            announcer.announce('ISS-RPT', 'beginning')
            announcer.announce('ISS-RPT', 'ended')
            announcer.pending.join()
        self.assertEqual([call.args[0] for call in speak.call_args_list], [
            'The pass for I S S, R P T is beginning.',
            'The pass for I S S, R P T has ended.'])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        model = root / 'model.onnx'
        model.write_bytes(b'model')
        Path(str(model) + '.json').write_text('{}')
        for name, value in [('CACHE_ROOT', root / 'cache'), ('PIPER_MODEL', model)]:
            patcher = patch.object(speech, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cache_reuses_speech_and_evicts_least_recently_used(self):
        def synthesize(message, path):
            path.write_bytes(message.encode().ljust(100, b' '))
        with patch.object(speech, 'synthesize_piper', side_effect=synthesize) as synth, \
             patch.object(speech, 'play_system_audio'):
            speech.speak('first', cache_size=2)
            speech.speak('second', cache_size=2)
            speech.speak('first', cache_size=2)
            self.assertEqual(synth.call_count, 2)
            # Make the second recording unambiguously older on every filesystem.
            second = next(p for p in speech.CACHE_ROOT.glob('*.wav') if p.read_bytes().startswith(b'second'))
            os.utime(second, (1, 1))
            speech.speak('third', cache_size=2)
            self.assertFalse(second.exists())
            self.assertEqual(len(list(speech.CACHE_ROOT.glob('*.wav'))), 2)
            speech.speak('first', cache_size=2)
            self.assertEqual(synth.call_count, 3)
            speech.speak('first', cache_size=0)
            speech.speak('first', cache_size=0)
            self.assertEqual(synth.call_count, 5)
            self.assertEqual(list(speech.CACHE_ROOT.glob('*.wav')), [])

    def test_changed_voice_invalidates_cache_and_failures_are_not_cached(self):
        with patch.object(speech, 'play_system_audio'), \
             patch.object(speech, 'synthesize_piper', side_effect=lambda message, path: path.write_bytes(b'x' * 100)) as synth:
            speech.speak('Test', windows_fallback=False)
            speech.PIPER_MODEL.write_bytes(b'new model')
            speech.speak('Test', windows_fallback=False)
            self.assertEqual(synth.call_count, 2)
        def fail(message, path):
            path.write_bytes(b'partial')
            raise RuntimeError('failed synthesis')
        with patch.object(speech, 'synthesize_piper', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'failed synthesis'):
                speech.speak('New message', windows_fallback=False)
        self.assertEqual(len(list(speech.CACHE_ROOT.iterdir())), 2)

    def test_piper_success_without_cache_uses_system_playback_and_cleans_up(self):
        with patch.object(speech, 'synthesize_piper') as synth, \
             patch.object(speech, 'play_system_audio') as play, \
             patch.object(speech, 'speak_windows') as native:
            speech.speak('Test', cache_size=0)
        audio = synth.call_args.args[1]
        play.assert_called_once_with(audio)
        native.assert_not_called()
        self.assertFalse(audio.parent.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows fallback')
    def test_piper_failure_respects_fallback_flag(self):
        with patch.object(speech, 'synthesize_piper', side_effect=RuntimeError('missing model')), \
             patch.object(speech, 'speak_windows') as native, patch('builtins.print'):
            speech.speak('Test', windows_fallback=True)
            native.assert_called_once_with('Test')
            native.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'missing model'):
                speech.speak('Test', windows_fallback=False)
            native.assert_not_called()

    def test_worker_preserves_fallback_setting(self):
        announcer = speech.PassAnnouncer()
        with patch.object(speech, 'speak') as speak:
            announcer.announce('LUCA-1', 'beginning', windows_fallback=False, cache_size=7)
            announcer.pending.join()
        speak.assert_called_once_with('The pass for LUCA-1 is beginning.', windows_fallback=False, cache_size=7)

    @unittest.skipUnless(os.name == 'nt', 'Windows playback')
    def test_playback_uses_default_windows_output(self):
        import winsound
        with patch.object(winsound, 'PlaySound') as play:
            speech.play_system_audio('announcement.wav')
        play.assert_called_once_with('announcement.wav', winsound.SND_FILENAME | winsound.SND_NODEFAULT)

    @unittest.skipUnless(os.name == 'nt', 'Windows fallback')
    def test_playback_failure_also_uses_fallback(self):
        with patch.object(speech, 'synthesize_piper', side_effect=lambda message, path: path.write_bytes(b'x' * 100)), \
             patch.object(speech, 'play_system_audio', side_effect=RuntimeError('audio failed')), \
             patch.object(speech, 'speak_windows') as native, patch('builtins.print'):
            speech.speak('Test')
        native.assert_called_once_with('Test')

    def test_piper_passes_names_as_stdin(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'voice.onnx'
            model.touch()
            Path(str(model) + '.json').touch()
            audio = Path(folder) / 'voice.wav'
            audio.write_bytes(b'x' * 100)
            name = 'Satellite $(exit); --help'
            with patch.object(speech, 'PIPER_MODEL', model), \
                 patch.object(speech.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                speech.synthesize_piper(name, audio)
            self.assertEqual(run.call_args.kwargs['input'], name)
            self.assertNotIn(name, run.call_args.args[0])

    def test_disabled_never_starts_worker(self):
        announcer = speech.PassAnnouncer()
        with patch.object(speech, 'speak') as speak:
            announcer.announce('LUCA-1', 'beginning', enabled=False)
            self.assertIsNone(announcer.worker)
            speak.assert_not_called()

    def test_session_override_and_restart_defaults(self):
        announcer = speech.PassAnnouncer()
        self.assertFalse(announcer.is_enabled(False))
        self.assertTrue(announcer.is_enabled(True))
        with patch.object(speech, 'speak') as speak:
            announcer.set_enabled(True)
            announcer.announce('LUCA-1', 'beginning', enabled=False)
            announcer.pending.join()
            speak.assert_called_once()
            announcer.set_enabled(False)
            announcer.announce('LUCA-1', 'ended', enabled=True)
            announcer.pending.join()
            speak.assert_called_once()
        restarted = speech.PassAnnouncer()
        self.assertFalse(restarted.is_enabled(False))
        self.assertTrue(restarted.is_enabled(True))

    def test_mute_discards_queued_speech_even_after_reenable(self):
        announcer = speech.PassAnnouncer()
        started, release = threading.Event(), threading.Event()
        def backend(*args, **kwargs):
            started.set()
            release.wait(5)
        with patch.object(speech, 'speak', side_effect=backend) as speak:
            try:
                announcer.announce('LUCA-1', 'beginning')
                self.assertTrue(started.wait(2))
                announcer.announce('LUCA-1', 'ended')
                announcer.set_enabled(False)
                announcer.set_enabled(True)
            finally:
                release.set()
                announcer.pending.join()
            speak.assert_called_once()

    def test_web_commands_override_without_mutating_yaml_settings(self):
        source = Path(__file__).resolve().parents[1] / 'bqe_wisp.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('handle_web_command', 'speech_announcements_enabled')]
        settings = {'pass_announcements_enabled': False,
                    'pass_announcements_windows_fallback': False,
                    'pass_announcements_cache_size': 7}
        ns = {'GENERAL_SETTINGS': settings.copy(), '_set_status_message': Mock()}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), ns)
        with patch.object(speech, 'PASS_ANNOUNCER', speech.PassAnnouncer()):
            self.assertFalse(ns['speech_announcements_enabled']())
            for action, expected in [('enable_speech_announcements', True),
                                     ('disable_speech_announcements', False)]:
                self.assertTrue(ns['handle_web_command'](action)['ok'])
                self.assertEqual(ns['speech_announcements_enabled'](), expected)
                self.assertEqual(ns['GENERAL_SETTINGS'], settings)

    def test_nonblocking_order_and_failure_recovery(self):
        announcer = speech.PassAnnouncer()
        started, release = threading.Event(), threading.Event()
        heard = []
        def backend(message, windows_fallback=True, cache_size=20):
            heard.append(message)
            if len(heard) == 1:
                started.set()
                release.wait(2)
                raise RuntimeError('No voice')
        with patch.object(speech, 'speak', side_effect=backend), patch('builtins.print'):
            announcer.announce('LUCA-1', 'beginning')
            self.assertTrue(started.wait(2))
            # Scheduler remains free to announce the end while speech is blocked.
            announcer.announce('LUCA-1', 'ended')
            release.set()
            announcer.pending.join()
        self.assertEqual(heard, ['The pass for LUCA-1 is beginning.', 'The pass for LUCA-1 has ended.'])

    @unittest.skipUnless(os.name == 'nt', 'Windows speech backend')
    def test_windows_names_are_data_and_process_is_hidden(self):
        name = 'The pass for $(exit); <speak>Satellite</speak> is beginning.'
        with patch.object(speech.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            speech.speak_windows(name)
        args, kwargs = run.call_args
        self.assertNotIn(name, args[0])
        self.assertEqual(json.loads(kwargs['input']), name)
        self.assertEqual(kwargs['creationflags'], speech.subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs['timeout'], 30)

    def test_backend_failure_is_reported(self):
        with patch.object(speech.shutil, 'which', return_value='espeak-ng'), \
             patch.object(speech.subprocess, 'run', return_value=Mock(returncode=1, stderr='Voice unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Voice unavailable'):
                speech.speak_windows('Test')


if __name__ == '__main__':
    unittest.main()
