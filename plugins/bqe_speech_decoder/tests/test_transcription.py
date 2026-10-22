import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from plugins.bqe_speech_decoder.bqe_speech_decoder import transcribe, settings, RADIO_CONTEXT, load_corrections, correct_radio_transcript
from plugins.bqe_speech_decoder.storage import read_transcript, transcript_path, transcript_text
from plugins.bqe_speech_decoder import storage


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root/'iss-rpt.wav'
        self.audio.write_bytes(b'original audio')
        self.options = settings(self.root/'settings.json')

    def engine(self, segments):
        model = Mock()
        model.transcribe.return_value = iter(segments), SimpleNamespace(duration=12)
        return Mock(return_value=model)

    def test_radio_terms_timestamps_and_original_text_are_preserved(self):
        spoken = 'Kilo One Alpha Bravo, five nine, grid Foxtrot November three one.'
        factory = self.engine([SimpleNamespace(start=2.25, end=7.5, text=spoken,
                              avg_logprob=-.9, no_speech_prob=.1)])
        progress = Mock()
        result = transcribe(self.audio, self.options, progress, factory)
        self.assertEqual(result['segments'], 1)
        data = read_transcript(self.audio)
        self.assertEqual(data['segments'][0]['text'], spoken)
        self.assertEqual(data['segments'][0]['start'], 2.25)
        self.assertTrue(data['segments'][0]['uncertain'])
        self.assertEqual(self.audio.read_bytes(), b'original audio')
        self.assertIn('[00:00:02.250', transcript_text(data))
        self.assertTrue(transcript_path(self.audio).with_suffix('.txt').is_file())
        options = factory.return_value.transcribe.call_args.kwargs
        self.assertEqual(options['language'], 'en')
        self.assertTrue(options['vad_filter'])
        self.assertFalse(options['condition_on_previous_text'])
        self.assertEqual(options['initial_prompt'], RADIO_CONTEXT)
        self.assertIn('Grid squares', options['initial_prompt'])
        progress.assert_any_call(message='Transcript saved', percent=100)

    def test_phonetic_corrections_reach_both_saved_formats(self):
        for language in ('en', 'auto', 'fr'):
            with self.subTest(language=language):
                factory = self.engine([SimpleNamespace(
                    start=2.25, end=7.5,
                    text=' Killing, KILLING! husky Husky HUSKY. overkilling huskys kilo Whiskey ',
                    avg_logprob=-.9, no_speech_prob=.1)])
                factory.return_value.transcribe.return_value[1].language = 'en'
                transcribe(self.audio, dict(self.options, language=language), model_factory=factory)
                expected = 'kilo, kilo! Whiskey Whiskey Whiskey. overkilling huskys kilo Whiskey'
                data = read_transcript(self.audio)
                self.assertEqual(data['segments'][0]['text'], expected)
                self.assertEqual(data['segments'][0]['start'], 2.25)
                self.assertEqual(data['segments'][0]['end'], 7.5)
                self.assertTrue(data['segments'][0]['uncertain'])
                self.assertIn(expected, transcript_path(self.audio).with_suffix('.txt').read_text(encoding='utf-8'))
                self.assertEqual(self.audio.read_bytes(), b'original audio')

    def test_yaml_rules_reload_and_preserve_regex_backslashes(self):
        path = self.root / 'corrections.yaml'
        path.write_text("- pattern: '\\bexample\\b'\n  replacement: 'Alpha'\n", encoding='utf-8')
        self.assertEqual(correct_radio_transcript('EXAMPLE examples', load_corrections(path)), 'Alpha examples')
        path.write_text("- pattern: 'example'\n  replacement: 'Bravo'\n", encoding='utf-8')
        self.assertEqual(correct_radio_transcript('example', load_corrections(path)), 'Bravo')
        path.write_text('[]', encoding='utf-8')
        self.assertEqual(correct_radio_transcript('killing', load_corrections(path)), 'killing')

    def test_invalid_yaml_rules_fail_before_model_loading(self):
        path = self.root / 'corrections.yaml'
        for contents in ('[', '{}', '- pattern: word',
                         '- pattern: 123\n  replacement: word',
                         "- pattern: '['\n  replacement: word",
                         "- pattern: word\n  replacement: '\\9'"):
            with self.subTest(contents=contents):
                path.write_text(contents, encoding='utf-8')
                factory = self.engine([])
                with patch('plugins.bqe_speech_decoder.bqe_speech_decoder.ROOT', self.root):
                    with self.assertRaises(ValueError):
                        transcribe(self.audio, self.options, model_factory=factory)
                factory.assert_not_called()

    def test_no_speech_does_not_invent_text(self):
        result = transcribe(self.audio, self.options, model_factory=self.engine([]))
        self.assertEqual(result['segments'], 0)
        self.assertEqual(read_transcript(self.audio)['segments'], [])
        self.assertIn('No speech was transcribed', transcript_text(read_transcript(self.audio)))

    def test_changed_recording_rejects_stale_or_in_progress_transcript(self):
        transcribe(self.audio, self.options, model_factory=self.engine([]))
        self.audio.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            read_transcript(self.audio)
        model = self.engine([])
        def change(*args, **kwargs):
            self.audio.write_bytes(b'changed during transcription')
            return iter([]), SimpleNamespace(duration=1)
        model.return_value.transcribe.side_effect = change
        with self.assertRaisesRegex(ValueError, 'changed during'):
            transcribe(self.audio, self.options, model_factory=model)

    def test_model_failure_does_not_replace_saved_transcript(self):
        transcribe(self.audio, self.options, model_factory=self.engine([]))
        before = transcript_path(self.audio).read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Could not load'):
            transcribe(self.audio, self.options, model_factory=Mock(side_effect=OSError('offline')))
        self.assertEqual(transcript_path(self.audio).read_bytes(), before)

    def test_invalid_saved_timestamps_and_settings_are_rejected(self):
        factory = self.engine([SimpleNamespace(start=-1, end=2, text='bad', avg_logprob=0, no_speech_prob=0)])
        with self.assertRaisesRegex(ValueError, 'timestamps'):
            transcribe(self.audio, self.options, model_factory=factory)
        path = self.root/'settings.json'
        path.write_text(json.dumps(dict(cpu_threads=True)))
        with self.assertRaises(ValueError):
            settings(path)
        path.write_text(json.dumps(dict(device='cpu',compute_type='float16')))
        with self.assertRaises(ValueError):
            settings(path)

    def test_selected_languages_preserve_language_and_unicode(self):
        for code, text, label in [('es', 'Señal cinco nueve, cuadrícula FN31.', 'Spanish'),
                                  ('pt', 'Recebido, sinal cinco nove, localização FN31.', 'Portuguese'),
                                  ('zh', '收到，信号报告五九，网格 FN31。', 'Mandarin Chinese'),
                                  ('ja', '了解、シグナルレポートは五九、グリッドは FN31 です。', 'Japanese')]:
            with self.subTest(language=code):
                factory = self.engine([SimpleNamespace(start=1, end=4, text=text,
                                                       avg_logprob=0, no_speech_prob=0)])
                result = transcribe(self.audio, dict(self.options, language=code), model_factory=factory)
                self.assertEqual(factory.call_args.args[0], 'small')
                kwargs = factory.return_value.transcribe.call_args.kwargs
                self.assertEqual(kwargs['language'], code)
                self.assertEqual(kwargs['task'], 'transcribe')
                self.assertNotEqual(kwargs['initial_prompt'], RADIO_CONTEXT)
                data = read_transcript(self.audio)
                self.assertEqual(data['language'], code)
                self.assertEqual(data['segments'][0]['text'], text)
                self.assertIn(label + ' transcript', transcript_text(data))
                self.assertEqual(result['language'], code)
                self.assertIn(text, transcript_path(self.audio).with_suffix('.txt').read_text(encoding='utf-8'))
                factory.return_value.transcribe.assert_called_once()

    def test_french_and_portuguese_regions_use_only_selected_language(self):
        for language, engine in [('fr', 'fr'), ('pt-BR', 'pt'), ('pt-PT', 'pt')]:
            factory = self.engine([])
            transcribe(self.audio, dict(self.options, language=language), model_factory=factory)
            factory.return_value.transcribe.assert_called_once()
            self.assertEqual(factory.return_value.transcribe.call_args.kwargs['language'], engine)
            data = read_transcript(self.audio)
            self.assertEqual(data['language'], engine)
            self.assertEqual(data['requested_language'], language)
            if language.startswith('pt-'):
                self.assertIn('Brazil' if language == 'pt-BR' else 'Portugal', transcript_text(data))

    def test_auto_detection_records_detected_language_without_english_prompt(self):
        factory = self.engine([SimpleNamespace(start=1, end=4, text='Olá rádio',
                                               avg_logprob=0, no_speech_prob=0)])
        factory.return_value.transcribe.return_value[1].language = 'pt'
        transcribe(self.audio, dict(self.options, language='auto'), model_factory=factory)
        kwargs = factory.return_value.transcribe.call_args.kwargs
        self.assertIsNone(kwargs['language'])
        self.assertIsNone(kwargs['initial_prompt'])
        data = read_transcript(self.audio)
        self.assertEqual(data['language'], 'pt')
        self.assertEqual(data['requested_language'], 'auto')
        transcribe(self.audio, dict(self.options, language='auto'), model_factory=self.engine([]))
        self.assertEqual(read_transcript(self.audio)['language'], 'und')

    def test_language_validation_and_english_only_models(self):
        for language in ('invalid', None, ['es']):
            with self.assertRaises(ValueError):
                transcribe(self.audio, dict(self.options, language=language), model_factory=self.engine([]))
        with self.assertRaisesRegex(ValueError, 'English-only'):
            transcribe(self.audio, dict(self.options, language='es', model='small.en'), model_factory=self.engine([]))
        factory = self.engine([])
        factory.return_value.model.is_multilingual = False
        with self.assertRaisesRegex(ValueError, 'multilingual'):
            transcribe(self.audio, dict(self.options, language='pt', model='/local/model'), model_factory=factory)

    def test_existing_english_sidecars_remain_readable(self):
        transcribe(self.audio, self.options, model_factory=self.engine([]))
        data = read_transcript(self.audio)
        del data['requested_language']
        transcript_path(self.audio).write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(read_transcript(self.audio)['language'], 'en')

    def test_windows_temporary_file_lock_is_retried(self):
        replace = storage.os.replace
        attempts = []
        def locked_once(source, destination):
            attempts.append(destination)
            if len(attempts) == 1:
                raise PermissionError('Reader has file open')
            return replace(source, destination)
        with patch.object(storage.os, 'replace', side_effect=locked_once), patch.object(storage.time, 'sleep'):
            storage.atomic_write(self.root/'progress.json', '{"percent":20}')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads((self.root/'progress.json').read_text())['percent'], 20)


if __name__ == '__main__':
    unittest.main()
