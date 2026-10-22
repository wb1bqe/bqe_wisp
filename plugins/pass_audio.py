"""Radio-independent pass WAV recording and dependency-light lifecycle bridge."""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def recording_requested(config):
    value = config.get('record_audio', False)
    if not isinstance(value, bool):
        raise ValueError('record_audio must be a YAML boolean (true or false)')
    return value


def legacy_recorder(command):
    parts = command if isinstance(command, (list, tuple)) else [command]
    return any(str(p).replace('\\', '/').rsplit('/', 1)[-1].lower() == 'bqe_sound_recorder.py' for p in parts)


def pass_program(config):
    command = config.get('program_to_run_during_pass')
    if 'record_audio' in config and legacy_recorder(command):
        return '' # The explicit flag controls recording; never launch a second recorder.
    return command


def recordings_directory():
    import yaml
    data = yaml.safe_load((ROOT/'bqe_config/general_settings.yaml').read_text(encoding='utf-8')) or {}
    sections = data.get('program_settings', {})
    if isinstance(sections, list):
        sections = {key: value for item in sections if isinstance(item, dict) for key,value in item.items()}
    value = sections.get('plugins', {}).get('bqe_sound_recorder', {}).get('recordings')
    path = Path(os.path.expanduser(value or 'plugins/bqe_sound_recorder/recordings'))
    return path if path.is_absolute() else ROOT/path


class PassAudio:
    def __init__(self):
        self.process = None

    def start(self, config, rig, use_sdr=False, use_lrpt=False):
        self.stop()
        if not recording_requested(config):
            return None
        if use_lrpt:
            raise ValueError('LRPT captures raw IQ and has no demodulated audio to record')
        if use_sdr:
            from plugins.bqe_sdr.integration import audio_input
            source = audio_input(rig)
        else:
            source = rig.get('radio_soundcard')
            if not isinstance(source, str) or not source.strip():
                raise ValueError('Pass recording requires radio_soundcard in my_rig.yaml')
        python = ROOT/'plugins/bqe_sound_recorder/.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        name = str(config.get('nickname') or config.get('satellite_name') or 'satellite')
        cmd = [str(python) if python.is_file() else sys.executable, str(Path(__file__).resolve()),
               '--source', source, '--name', name, '--output', str(recordings_directory())]
        self.process = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        print(f'[INFO] Pass WAV recording started for {name}: {source}')
        return self.process

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


PASS_AUDIO = PassAudio()


def record(source_name, output, name, stop, publish=lambda state: None):
    """Write a valid PCM WAV; close capture and finalize on stop, EOF or error."""
    from contextlib import ExitStack
    from datetime import datetime, timezone
    import time
    import uuid
    import wave
    import numpy as np
    from plugins.audio_devices import audio_com, input_device, input_recorder
    state = dict(status='starting', output_file=None, error=None)
    publish(state)
    try:
        with ExitStack() as stack:
            stack.enter_context(audio_com())
            if source_name.startswith('bqe-sdr:'):
                source = input_device(None, source_name)
            else:
                import soundcard
                source = input_device(soundcard, source_name)
                if source.isloopback:
                    raise ValueError('radio_soundcard must name an input, not speaker loopback')
            capture = stack.enter_context(input_recorder(source, 48000))
            # Do not create an empty recording when the source cannot deliver samples.
            samples = capture.record(4800)
            output = Path(output)
            output.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[^A-Za-z0-9_.-]+', '-', name).strip('.-')[:80] or 'satellite'
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            target = output/f'{safe}-{stamp}-{uuid.uuid4().hex[:8]}.wav'
            raw_file = stack.enter_context(target.open('xb'))
            wav = stack.enter_context(wave.open(raw_file, 'wb'))
            wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            state.update(status='recording', output_file=str(target))
            last = 0
            while not stop.is_set():
                mono = np.asarray(samples)
                if mono.ndim > 1:
                    mono = mono[:, 0]
                # writeframes refreshes the header each block, preserving a usable
                # file even if the process later suffers an unclean shutdown.
                wav.writeframes((np.clip(np.nan_to_num(mono), -1, 1)*32767).astype('<i2').tobytes())
                if time.monotonic()-last >= 1:
                    publish(dict(state))
                    last = time.monotonic()
                samples = capture.record(4800)
        state['status'] = 'stopped'
    except Exception as exc:
        state.update(status='error', error=str(exc))
        print(f'[WARNING] Pass recording failed: {exc}', file=sys.stderr)
    finally:
        publish(dict(state))
    return state


def main():
    import argparse
    # Load native libraries before a background thread blocks on Windows stdin.
    import numpy
    import signal
    import threading
    from types import SimpleNamespace
    from plugins.bqe_sound_recorder.bqe_sound_recorder import publish_recording_status
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    def owner():
        sys.stdin.buffer.read()
        stop.set()
    threading.Thread(target=owner, daemon=True).start()
    state = record(args.source, args.output, args.name, stop,
        lambda state: publish_recording_status(SimpleNamespace(snapshot=lambda: dict(state))))
    return int(state['status'] == 'error')


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
