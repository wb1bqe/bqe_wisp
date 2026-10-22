"""Bounded offline recording batches, independent of managed live receivers."""
import atexit
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from plugins.bqe_speech_decoder.languages import validate_language


def recording_source(name):
    stem = Path(name).stem
    match = re.match(r'^(.*?)[_-](?:\d{8}T\d{6}Z|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:[_-].*)?$', stem)
    return match.group(1) if match and match.group(1) else 'Other recordings'


class RecordingDecoders:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.process = None
        self.state = dict(running=False, decoder=None, items=[])

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self, project, decoder, paths, gallery=None, language='en'):
        if decoder not in ('sstv', 'aprs', 'speech'):
            raise ValueError('Choose SSTV, APRS or speech transcription')
        if decoder == 'speech':
            validate_language(language)
        if not 1 <= len(paths) <= 100:
            raise ValueError('Select 1–100 recordings')
        if decoder == 'sstv' and gallery is None:
            raise ValueError('Configure the SSTV Gallery directory before decoding')
        with self.lock:
            if self.state['running']:
                raise ValueError('A recording batch is already running; wait or cancel it first')
            self.cancelled.clear()
            self.state = dict(running=True, decoder=decoder, language=language,
                              items=[dict(name=p.name, state='queued') for p in paths])
            threading.Thread(target=self._run, args=(Path(project), decoder, paths, gallery, language),
                             daemon=True).start()
            return self.snapshot()

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            return self.snapshot()

    def _update(self, index, **values):
        with self.lock:
            self.state['items'][index].update(values)

    def _run(self, project, decoder, paths, gallery, language='en'):
        plugin = project / 'plugins' / f'bqe_{decoder}_decoder'
        python = plugin / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        output = Path(gallery) if decoder == 'sstv' else plugin / 'decoded'
        try:
            for index, path in enumerate(paths):
                if self.cancelled.is_set():
                    self._update(index, state='cancelled')
                    continue
                self._update(index, state='running')
                command = [str(python) if python.is_file() else sys.executable,
                           str(plugin / f'bqe_{decoder}_decoder.py'), '--file', str(path),
                           '--no-web', '--output', str(output), '--source-name', recording_source(path.name)]
                if decoder == 'sstv':
                    command.append('--combine-fragments')
                try:
                    # A disk-backed stream avoids holding large decoder diagnostics in memory.
                    with tempfile.TemporaryFile() as log, tempfile.TemporaryDirectory() as scratch:
                        status_file = Path(scratch)/'speech-status.json'
                        if decoder == 'speech':
                            command = [str(python) if python.is_file() else sys.executable,
                                       str(plugin/'bqe_speech_decoder.py'), '--file', str(path),
                                       '--status-file', str(status_file), '--language', language]
                        with self.lock:
                            if self.cancelled.is_set():
                                self._update(index, state='cancelled')
                                continue
                            env = dict(os.environ, PYTHONIOENCODING='utf-8')
                            env.pop('BQE_PASS_STOP_FILE', None)
                            self.process = subprocess.Popen(command, cwd=str(project), env=env,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                            process = self.process
                        try:
                            if decoder != 'speech':
                                process.wait(timeout=7200)
                            else:
                                deadline = time.monotonic() + 7200
                                while True:
                                    try:
                                        process.wait(timeout=1)
                                        break
                                    except subprocess.TimeoutExpired:
                                        if time.monotonic() >= deadline:
                                            raise
                                        try:
                                            progress = json.loads(status_file.read_text(encoding='utf-8'))
                                            self._update(index, message=progress.get('message', ''),
                                                         percent=progress.get('percent', 0))
                                        except (OSError, ValueError):
                                            pass
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                            raise RuntimeError('Decoding exceeded the two-hour limit')
                        log.seek(0, 2)
                        log.seek(max(0, log.tell() - 1024 * 1024))
                        text = log.read().decode('utf-8', errors='replace')
                    if self.cancelled.is_set():
                        self._update(index, state='cancelled')
                        continue
                    result = None
                    for match in re.finditer(r'^\{', text, re.MULTILINE):
                        try:
                            value, _ = json.JSONDecoder().raw_decode(text[match.start():])
                            if isinstance(value, dict) and 'state' in value:
                                result = value
                        except ValueError:
                            continue
                    if result is None:
                        raise RuntimeError(text[-2000:].strip() or 'Decoder exited without a result')
                    if process.returncode or result['state'] != 'completed':
                        raise RuntimeError(result.get('error') or f"Decoder ended in state {result['state']}")
                    count = result.get({'sstv': 'images', 'aprs': 'packets', 'speech': 'segments'}[decoder], 0)
                    self._update(index, state='completed', count=count, result=result,
                                 message=result.get('message', ''), percent=100)
                except Exception as exc:
                    self._update(index, state='cancelled' if self.cancelled.is_set() else 'error', message=str(exc))
                finally:
                    with self.lock:
                        self.process = None
        finally:
            with self.lock:
                self.state['running'] = False


RECORDING_DECODERS = RecordingDecoders()
atexit.register(RECORDING_DECODERS.cancel)
