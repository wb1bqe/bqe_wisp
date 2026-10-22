"""Offline pass announcements on the operating system's default audio output."""
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading

VOICE_ROOT = Path(__file__).resolve().parent / 'bqe_pass_voice'
PIPER_MODEL = VOICE_ROOT / 'models' / 'en_GB-cori-medium.onnx'
CACHE_ROOT = VOICE_ROOT / 'cache'
CACHE_LOCK = threading.Lock()
SATELLITE_CONFIG = VOICE_ROOT.parents[1] / 'bqe_config' / 'satellites.yaml'


def spoken_satellite_name(satellite, config_path=SATELLITE_CONFIG):
    """Spell short hyphenated letter groups only for configured satellites."""
    name = ' '.join(str(satellite).replace('_', ' ').split())[:160]
    pattern = r'(?<!\w)[A-Za-z]{2,3}(?=-)|(?<=-)[A-Za-z]{2,3}(?!\w)'
    if not re.search(pattern, name):
        return name
    import yaml
    try:
        config = yaml.safe_load(Path(config_path).read_text(encoding='utf-8'))
        entries = config.get('satellites', []) if isinstance(config, dict) else []
        if not isinstance(entries, list):
            return name
        configured = {
            ' '.join(str(entry.get(key) or '').replace('_', ' ').split()).casefold()
            for entry in entries if isinstance(entry, dict)
            for key in ('nickname', 'satellite_name')
        }
    except (OSError, UnicodeError, yaml.YAMLError):
        return name
    if name.casefold() not in configured:
        return name
    return re.sub(pattern, lambda match: ' '.join(match.group().upper()), name).replace('-', ', ')


WINDOWS_SPEECH = r'''
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Speech
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $voice = $speaker.GetInstalledVoices() | Where-Object {
            $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'en-GB' -and
            $_.VoiceInfo.Gender -eq [System.Speech.Synthesis.VoiceGender]::Female
        } | Select-Object -First 1
        if (-not $voice) {
            throw 'No British female speech voice is installed. Install an English (United Kingdom) speech voice in Windows settings, then restart BQE.'
        }
        $speaker.SelectVoice($voice.VoiceInfo.Name)
        $speaker.SetOutputToDefaultAudioDevice()
        $message = [Console]::In.ReadToEnd() | ConvertFrom-Json
        $speaker.Speak([string]$message)
    } finally { if ($speaker) { $speaker.Dispose() } }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''


def speak_windows(message):
    """Never selects a rig soundcard, opens a microphone, or contacts a service."""
    if os.name == 'nt':
        executable = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                         'System32/WindowsPowerShell/v1.0/powershell.exe')
        command = [executable, '-NoProfile', '-NonInteractive', '-EncodedCommand',
                   base64.b64encode(WINDOWS_SPEECH.encode('utf-16le')).decode('ascii')]
        # JSON on stdin keeps satellite names out of executable code and SSML.
        payload = json.dumps(message, ensure_ascii=True)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW}
    else:
        executable = shutil.which('espeak-ng') or shutil.which('espeak')
        if not executable:
            raise RuntimeError('Install espeak-ng for British female pass announcements')
        command = [executable, '-v', 'en-gb+f3', '-s', '155', '--stdin']
        payload = message
        options = {}
    result = subprocess.run(command, input=payload, text=True, capture_output=True,
                            timeout=30, **options)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Speech synthesis failed')


def synthesize_piper(message, destination):
    """Synthesize locally in a subprocess; never download assets during a pass."""
    if not PIPER_MODEL.is_file() or not Path(str(PIPER_MODEL) + '.json').is_file():
        raise RuntimeError('Piper Cori model is missing from plugins/bqe_pass_voice/models')
    local_python = VOICE_ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    interpreter = str(local_python) if local_python.is_file() else sys.executable
    command = [interpreter, '-m', 'piper', '-m', str(PIPER_MODEL), '-f', str(destination)]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run(command, input=message, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=60, **options)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Piper synthesis failed')
    if not Path(destination).is_file() or Path(destination).stat().st_size <= 44:
        raise RuntimeError('Piper did not produce an audio file')


def play_system_audio(filename):
    if os.name == 'nt':
        import winsound
        # Windows wave mapper/default playback; never sounddevice or radio_soundcard.
        winsound.PlaySound(str(filename), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    else:
        player = shutil.which('paplay') or shutil.which('aplay')
        if not player:
            raise RuntimeError('Install paplay or aplay for system audio playback')
        subprocess.run([player, str(filename)], check=True, capture_output=True, timeout=60)


def play_piper(message, cache_size=20):
    """Keep a bounded disk LRU across restarts; publish only completed synthesis."""
    with CACHE_LOCK:
        if cache_size == 0:
            if CACHE_ROOT.exists():
                for path in CACHE_ROOT.glob('*.wav'):
                    path.unlink(missing_ok=True)
            with tempfile.TemporaryDirectory(prefix='bqe-pass-voice-') as folder:
                audio = Path(folder) / 'announcement.wav'
                synthesize_piper(message, audio)
                play_system_audio(audio)
            return
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        # Model/config replacement invalidates old speech without hashing 63 MB
        # on every announcement. Bump the format version if synthesis options change.
        identity = ['piper-cori-v1', message]
        for path in (PIPER_MODEL, Path(str(PIPER_MODEL) + '.json')):
            stat = path.stat()
            identity.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns))
        key = hashlib.sha256(json.dumps(identity).encode('utf-8')).hexdigest()
        audio = CACHE_ROOT / (key + '.wav')
        if not audio.is_file() or audio.stat().st_size <= 44:
            with tempfile.TemporaryDirectory(prefix='synthesis-', dir=CACHE_ROOT) as folder:
                pending = Path(folder) / 'announcement.wav'
                synthesize_piper(message, pending)
                pending.replace(audio)
        audio.touch()
        others = sorted((p for p in CACHE_ROOT.glob('*.wav') if p != audio),
                        key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in others[max(0, cache_size - 1):]:
            path.unlink(missing_ok=True)
        play_system_audio(audio)


def speak(message, windows_fallback=True, cache_size=20):
    try:
        play_piper(message, cache_size)
    except Exception as exc:
        if os.name != 'nt' or not windows_fallback:
            raise
        print(f'[WARNING] Piper unavailable; trying Windows speech: {exc}', flush=True)
        speak_windows(message)


class PassAnnouncer:
    def __init__(self):
        self.pending = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.worker = None
        self.enabled_override = None
        self.generation = 0

    def is_enabled(self, configured_enabled=True):
        with self.lock:
            return configured_enabled if self.enabled_override is None else self.enabled_override

    def set_enabled(self, enabled):
        """Override speech for this process only, without changing configuration."""
        with self.lock:
            self.enabled_override = bool(enabled)
            if not enabled:
                # Invalidate queued announcements even if speech is re-enabled
                # before the current utterance finishes.
                self.generation += 1

    def announce(self, satellite, event, enabled=True, windows_fallback=True, cache_size=20):
        if not self.is_enabled(enabled):
            return
        if event not in ('beginning', 'ended'):
            raise ValueError('Unknown pass announcement event')
        name = spoken_satellite_name(satellite)
        message = (f'The pass for {name} is beginning.' if event == 'beginning'
                   else f'The pass for {name} has ended.')
        with self.lock:
            if self.enabled_override is False:
                return
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name='pass-announcements', daemon=True)
                self.worker.start()
            try:
                self.pending.put_nowait((message, windows_fallback, cache_size, self.generation))
            except queue.Full:
                print('[WARNING] Pass announcement skipped: speech queue is full.')

    def _run(self):
        while True:
            message, windows_fallback, cache_size, generation = self.pending.get()
            try:
                with self.lock:
                    if self.enabled_override is False or generation != self.generation:
                        continue
                speak(message, windows_fallback=windows_fallback, cache_size=cache_size)
            except Exception as exc:
                print(f'[WARNING] Pass announcement unavailable: {exc}', flush=True)
            finally:
                self.pending.task_done()


PASS_ANNOUNCER = PassAnnouncer()
