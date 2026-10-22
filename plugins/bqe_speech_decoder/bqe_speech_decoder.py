#!/usr/bin/env python3
"""Transcribe multilingual speech locally on Windows or Linux; no audio is uploaded."""
import argparse
import json
import math
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from plugins.bqe_speech_decoder.languages import LANGUAGES, REGIONAL_LANGUAGES, validate_language
from plugins.bqe_speech_decoder.storage import atomic_write, fingerprint, save_transcript

# Context improves recognition of radio vocabulary. It is not a list of expected
# contacts: never substitute a guessed callsign, report or locator afterward.
RADIO_CONTEXT = (
    'Amateur radio repeater contacts. CQ, QSL, QRZ, roger, copy, over, callsign, '
    'signal report, grid square, locator. Callsigns combine letters and numbers. '
    'Signal reports are two or three digits. Grid squares have two letters followed by two digits. '
    'Phonetic spelling: Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett '
    'Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu. '
    'Numbers: zero one two three four five six seven eight nine.'
)


RADIO_PROMPTS = {
    'en': RADIO_CONTEXT,
    'ja': 'アマチュア無線のレピーター交信。日本語。CQ、QSL、QRZ、コールサイン、了解、どうぞ、'
          'シグナルレポート、グリッドロケーター。コールサインは英字と数字の組み合わせ。'
          'シグナルレポートは通常2桁または3桁の数字。グリッドロケーターは英字2文字と数字2桁。',
    'zh': '业余无线电中继通联。普通话。CQ、QSL、QRZ、呼号、收到、请讲、信号报告、网格定位。'
          '呼号由字母和数字组成。信号报告通常为两位或三位数字。网格定位由两个字母和两个数字组成。',
    'fr': 'Contacts radioamateurs par relais. CQ, QSL, QRZ, indicatif, reçu, à vous, '
          'rapport de signal, carré locator. Les indicatifs combinent lettres et chiffres. '
          'Les rapports ont deux ou trois chiffres. Le locator a deux lettres et deux chiffres.',
    'es': 'Contactos de radioaficionados por repetidor. CQ, QSL, QRZ, indicativo, cambio, recibido, '
          'informe de señal, cuadrícula, localizador. Los indicativos combinan letras y números. '
          'Los informes tienen dos o tres cifras. El localizador tiene dos letras y dos cifras.',
    'pt': 'Contatos de radioamadores por repetidor. CQ, QSL, QRZ, indicativo, câmbio, recebido, '
          'informe de sinal, quadrícula, localizador. Os indicativos combinam letras e números. '
          'Os informes têm dois ou três dígitos. O localizador tem duas letras e dois dígitos.',
}

RADIO_PROMPTS['pt-BR'] = RADIO_PROMPTS['pt'] + ' Português do Brasil.'
RADIO_PROMPTS['pt-PT'] = (
    'Contactos de radioamadores por repetidor. Português de Portugal. CQ, QSL, QRZ, indicativo, '
    'escuto, recebido, sinal, quadrícula, localizador. Os indicativos combinam letras e números. '
    'Os relatórios de sinal têm dois ou três algarismos. O localizador tem duas letras e dois algarismos.'
)


def load_corrections(path=None):
    """Read and validate ordered YAML rules once per recording."""
    import yaml
    path = Path(path) if path is not None else ROOT / 'corrections.yaml'
    try:
        rules = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f'Could not read speech corrections {path}: {exc}') from exc
    if not isinstance(rules, list):
        raise ValueError(f'Speech corrections {path} must be a list (use [] for no rules)')
    compiled = []
    for index, rule in enumerate(rules, 1):
        if (not isinstance(rule, dict) or set(rule) != {'pattern', 'replacement'}
                or not isinstance(rule['pattern'], str) or not rule['pattern']
                or not isinstance(rule['replacement'], str)):
            raise ValueError(f'Speech correction {index} in {path} needs a nonempty pattern and string replacement')
        try:
            pattern = re.compile(rule['pattern'], re.IGNORECASE)
            pattern.sub(rule['replacement'], '')  # Validate replacement backreferences too.
        except (re.error, IndexError) as exc:
            raise ValueError(f'Invalid speech correction {index} in {path}: {exc}') from exc
        compiled.append((pattern, rule['replacement']))
    return compiled


def correct_radio_transcript(text, corrections):
    """Apply known phonetic corrections to every newly decoded segment."""
    for pattern, replacement in corrections:
        text = pattern.sub(replacement, text)
    return text


def selected_model(options):
    language = validate_language(options.get('language', 'en'))
    model = options['model']
    if model == 'auto':
        return 'small.en' if language == 'en' else 'small'
    if language != 'en' and model.endswith('.en'):
        raise ValueError('An English-only .en model cannot transcribe this language; set model to auto or small')
    return model


def settings(path=None):
    path = Path(path) if path else ROOT / 'settings.json'
    values = dict(model='auto', language='en', device='cpu', compute_type='int8', cpu_threads=4, offline=False)
    if path.exists():
        configured = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(configured, dict) or set(configured) - values.keys():
            raise ValueError('Speech settings must contain only model, language, device, compute_type, cpu_threads and offline')
        values.update(configured)
    if values['device'] not in ('cpu', 'cuda'):
        raise ValueError('Speech device must be cpu or cuda')
    if values['compute_type'] not in ('int8', 'float32', 'float16', 'int8_float16'):
        raise ValueError('Unsupported speech compute_type')
    if type(values['cpu_threads']) is not int or not 1 <= values['cpu_threads'] <= 64:
        raise ValueError('cpu_threads must be an integer from 1 to 64')
    if type(values['offline']) is not bool or not isinstance(values['model'], str) or not values['model'].strip():
        raise ValueError('Use a model name/path and a boolean offline setting')
    if values['device'] == 'cpu' and values['compute_type'] not in ('int8', 'float32'):
        raise ValueError('CPU transcription requires int8 or float32')
    selected_model(values)
    return values


def transcribe(audio, options, progress=lambda **values: None, model_factory=None):
    audio = Path(audio).resolve()
    if not audio.is_file() or audio.suffix.lower() not in ('.wav', '.mp3'):
        raise ValueError('Select an existing WAV or MP3 recording')
    corrections = load_corrections()
    source = fingerprint(audio)
    language = validate_language(options.get("language", "en"))
    model_name = selected_model(options)
    engine_language = 'pt' if language in REGIONAL_LANGUAGES else language
    if model_factory is None:
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    progress(message='Loading speech model (first use may download it)…', percent=0)
    try:
        model = model_factory(model_name, device=options['device'], compute_type=options['compute_type'],
                              cpu_threads=options['cpu_threads'], num_workers=1,
                              download_root=str(ROOT/'models'), local_files_only=options['offline'])
    except Exception as exc:
        raise RuntimeError('Could not load the speech model. First use requires internet to download it; '
                           'use --download-model to prepare it, or check the configured model/device. ' + str(exc)) from exc
    if language != 'en' and getattr(getattr(model, 'model', None), 'is_multilingual', None) is False:
        raise ValueError('Choose a multilingual model for this language (for example small)')
    progress(message='Finding speech and transcribing…', percent=0)
    segments, info = model.transcribe(str(audio), language=None if language == 'auto' else engine_language, task='transcribe', beam_size=5,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=700),
        condition_on_previous_text=False, word_timestamps=True, hallucination_silence_threshold=2,
        initial_prompt=RADIO_PROMPTS.get(language))
    duration = float(info.duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('Could not determine recording duration')
    output = []
    for segment in segments:
        text = correct_radio_transcript(segment.text.strip(), corrections)
        if text:
            output.append(dict(start=round(float(segment.start), 3), end=round(float(segment.end), 3),
                               text=text, uncertain=segment.avg_logprob < -.8 or segment.no_speech_prob > .3))
        progress(message=f'Transcribing: {segment.end:.0f} of {duration:.0f} seconds',
                 percent=min(99, round(100 * segment.end / max(1, duration))))
    detected = getattr(info, 'language', language) if language == 'auto' else engine_language
    if language == 'auto' and not output:
        detected = 'und'
    data = save_transcript(audio, source, model_name, output, duration,
                           language=detected, requested_language=language)
    progress(message='Transcript saved', percent=100)
    return dict(state='completed', segments=len(output), duration=duration,
                model=data['model'], language=data['language'], transcript=audio.name,
                message='Transcript saved' if output else 'No speech was transcribed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--model')
    parser.add_argument('--language', choices=['auto', *LANGUAGES, *REGIONAL_LANGUAGES], help='Spoken language code, or auto detection per recording')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check', action='store_true', help='Check dependencies without downloading a model')
    parser.add_argument('--download-model', action='store_true')
    parser.add_argument('--status-file', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    def progress(**values):
        if args.status_file:
            try:
                atomic_write(args.status_file, json.dumps(dict(state='running', **values)))
            except OSError:
                pass  # A transient progress-file lock must not lose a transcript.
    try:
        options = settings(args.config)
        if args.model:
            options['model'] = args.model
        if args.language:
            options['language'] = args.language
        model_name = selected_model(options)
        if args.offline:
            options['offline'] = True
        try:
            from faster_whisper import WhisperModel
            import ctranslate2
            import av
            import onnxruntime
            import yaml
        except ImportError as exc:
            raise RuntimeError('Speech dependencies are missing. Run plugins/bqe_speech_decoder/start_windows.bat '
                               '--check on Windows, or bash plugins/bqe_speech_decoder/start_linux.sh --check on Linux.') from exc
        if args.check:
            print(json.dumps(dict(ok=True, python=sys.version.split()[0], device=options['device'],
                model=model_name, language=options['language'], cpu_compute_types=sorted(ctranslate2.get_supported_compute_types('cpu')),
                model_cache=str(ROOT/'models'), note='Dependency check only; no model downloaded or audio processed')))
            return 0
        if args.download_model:
            WhisperModel(model_name, device=options['device'], compute_type=options['compute_type'],
                         cpu_threads=options['cpu_threads'], download_root=str(ROOT/'models'),
                         local_files_only=options['offline'])
            print(json.dumps(dict(ok=True, model=model_name, message='Model ready for offline use')))
            return 0
        if args.file is None:
            parser.error('Use --file RECORDING, --check or --download-model')
        result = transcribe(args.file, options, progress, WhisperModel)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps(dict(state='error', error=str(exc)), ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
