"""Dependency-free, versioned transcript sidecars for original recordings."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import uuid
import time

from .languages import LANGUAGES, language_name, validate_language

MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024


def transcript_path(audio):
    audio = Path(audio)
    return audio.with_name(audio.name + '.transcript.json')


def fingerprint(audio):
    stat = Path(audio).stat()
    return dict(name=Path(audio).name, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f'{hours:02}:{minute:02}:{sec:02}.{ms:03}'


def transcript_text(data):
    header = (f"{language_name(data.get('requested_language') if data.get('requested_language') not in (None, 'auto') else data['language'])} transcript — {data['source']['name']}\n"
              f"Model: {data['model']} · {data['created_at']}\n"
              'Automatic transcription; verify callsigns and unclear speech against the audio.\n\n')
    lines = [f"[{timestamp(s['start'])} – {timestamp(s['end'])}] "
             + ('[uncertain] ' if s.get('uncertain') else '') + s['text'] for s in data['segments']]
    return header + ('\n'.join(lines) if lines else 'No speech was transcribed.') + '\n'


def atomic_write(path, text):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(text, encoding='utf-8')
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers/antivirus may briefly hold the destination.
                if attempt == 9:
                    raise
                time.sleep(.025)
    finally:
        temporary.unlink(missing_ok=True)


def validate(data):
    if (not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('language'), str) or data.get('language') not in (*LANGUAGES, 'und')
            or not isinstance(data.get('source'), dict) or not isinstance(data.get('model'), str)
            or not isinstance(data.get('created_at'), str) or not isinstance(data.get('segments'), list)
            or len(data['segments']) > 20000):
        raise ValueError('Invalid saved transcript')
    if 'requested_language' in data:
        validate_language(data['requested_language'])
    previous = 0
    for segment in data['segments']:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str):
            raise ValueError('Invalid transcript segment')
        start, end = segment.get('start'), segment.get('end')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or not 0 <= previous <= start <= end or len(segment['text']) > 20000):
            raise ValueError('Invalid transcript timestamps or text')
        previous = start
    return data


def save_transcript(audio, source, model, segments, duration, language='en', requested_language='en'):
    if fingerprint(audio) != source:
        raise ValueError('The recording changed during transcription; wait until recording finishes and retry')
    data = validate(dict(version=1, language=language, requested_language=requested_language, source=source, model=model,
                         created_at=datetime.now(timezone.utc).isoformat(),
                         duration=duration, segments=segments))
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    if len(encoded.encode('utf-8')) > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Transcript exceeds the supported size')
    path = transcript_path(audio)
    atomic_write(path.with_suffix('.txt'), transcript_text(data))
    atomic_write(path, encoded)
    return data


def read_transcript(audio):
    path = transcript_path(audio)
    if path.is_symlink() or path.resolve().parent != Path(audio).resolve().parent:
        raise ValueError('Transcript must be beside its recording')
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Saved transcript is too large')
    data = validate(json.loads(path.read_text(encoding='utf-8')))
    if data['source'] != fingerprint(audio):
        raise ValueError('The recording has changed; transcribe it again to update this transcript')
    return data
