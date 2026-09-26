"""One receiver, bounded audio fan-out, and durable audio/IQ recordings."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
import time
import uuid
import wave
import numpy as np
from .config import Settings, AUDIO_RATE
from .dsp import Demodulator, decode_iq, spectrum
from .sources import source


class AudioHub:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.active = False

    def subscribe(self):
        with self.lock:
            if not self.active:
                raise ValueError('Start the SDR receiver before connecting an audio consumer')
            if len(self.clients) >= 16:
                raise ValueError('Too many audio consumers')
            client = queue.Queue(maxsize=128)
            self.clients.add(client)
            return client

    def unsubscribe(self, client):
        with self.lock:
            self.clients.discard(client)

    def publish(self, data):
        with self.lock:
            for client in list(self.clients):
                try:
                    client.put_nowait(data)
                except queue.Full:
                    # A stalled decoder must not corrupt or block other receivers.
                    self.clients.remove(client)
                    while not client.empty():
                        client.get_nowait()
                    client.put_nowait(None)

    def close(self):
        with self.lock:
            self.active = False
            for client in self.clients:
                while not client.empty():
                    client.get_nowait()
                client.put_nowait(None)
            self.clients.clear()


class Session:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.capture = None
        self.hub = AudioHub()
        self.settings = None
        self.state = dict(state='idle', error=None, audio_seconds=0, iq_samples=0)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, options):
        settings = Settings.from_dict(options)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Stop the current receiver before starting another')
            driver = getattr(self.capture, 'thread', None)
            if driver and driver.is_alive():
                raise ValueError('USB driver is still running; restart the plugin')
            self.settings = settings
            self.stop_event.clear()
            self.state = dict(state='starting', error=None, frequency=settings.frequency,
                              center=settings.center, sample_rate=settings.sample_rate,
                              audio_rate=AUDIO_RATE, mode=settings.mode, bandwidth=settings.bandwidth,
                              source=settings.source, source_name=settings.source_name,
                              audio_seconds=0, iq_samples=0, level_dbfs=None,
                              output=str(self.output), settings=settings.as_dict())
            self.thread = threading.Thread(target=self._run, daemon=True, name='bqe-sdr')
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.thread and self.thread.is_alive() and self.state['state'] in ('starting', 'running'):
                self.state['state'] = 'stopping'

    def tune(self, frequency):
        frequency = float(frequency)
        with self.lock:
            if self.state['state'] not in ('starting', 'running'):
                raise ValueError('Receiver is not running')
            self.settings.validate_tune(frequency)
            self.state['frequency'] = frequency

    def _run(self):
        s = self.settings
        stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        meta_path, meta = None, None
        count, audio_count = 0, 0
        frequency = s.frequency
        started, last_ui = time.monotonic(), 0
        try:
            dsp = Demodulator(s)
            with ExitStack() as stack:
                self.capture = source(s, self.stop_event)
                capture = stack.enter_context(self.capture)
                audio, iq_file = None, None
                if s.record_audio or s.record_iq:
                    self.output.mkdir(parents=True, exist_ok=True)
                if s.record_audio:
                    path = self.output / (stem + '.wav')
                    audio = stack.enter_context(wave.open(str(path), 'wb'))
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(AUDIO_RATE)
                    self.update(audio_file=str(path))
                if s.record_iq:
                    path = self.output / (stem + '.sigmf-data')
                    iq_file = stack.enter_context(path.open('xb'))
                    meta_path = path.with_suffix('.sigmf-meta')
                    meta = {'global': {'core:datatype': capture.datatype, 'core:sample_rate': s.sample_rate,
                                      'core:version': '1.2.5', 'core:recorder': 'BQE SDR',
                                      'core:description': s.source_name},
                            'captures': [{'core:sample_start': 0, 'core:frequency': s.center,
                                          'core:datetime': datetime.now(timezone.utc).isoformat()}],
                            'annotations': []}
                    meta_path.write_text(json.dumps(meta, indent=2), encoding='utf-8')
                    self.update(iq_file=str(path), iq_metadata=str(meta_path))
                self.hub.active = True
                self.update(state='running')
                for raw in capture:
                    if self.stop_event.is_set():
                        break
                    iq = decode_iq(raw, capture.datatype)
                    if not np.all(np.isfinite(iq)):
                        raise ValueError('IQ source contains non-finite samples')
                    new_frequency = self.snapshot()['frequency']
                    if new_frequency != frequency and meta is not None:
                        meta['annotations'].append({'core:sample_start': count,
                            'core:comment': f'Demodulator tuned to {new_frequency} Hz; RF center unchanged'})
                    frequency = new_frequency
                    if iq_file:
                        iq_file.write(raw)
                    pcm = dsp.push(iq, frequency)
                    count += len(iq)
                    audio_count += len(pcm)
                    if len(pcm):
                        clipped = np.clip(pcm, -1, 1)
                        self.hub.publish(clipped.astype('<f4').tobytes())
                        if audio:
                            audio.writeframes((clipped*32767).astype('<i2').tobytes())
                    now = time.monotonic()
                    if now-last_ui >= .15:
                        self.update(iq_samples=count, audio_seconds=audio_count/AUDIO_RATE,
                                    level_dbfs=float(20*np.log10(max(1e-9, np.sqrt(np.mean(pcm**2))))) if len(pcm) else None,
                                    clipping=bool(np.any(np.abs(pcm) > 1)), spectrum=spectrum(iq),
                                    elapsed=now-started)
                        last_ui = now
                    if s.duration and count / s.sample_rate >= s.duration:
                        break
                if getattr(capture, 'error', None) is not None:
                    raise capture.error
                self.update(iq_samples=count, audio_seconds=audio_count/AUDIO_RATE)
            self.update(state='stopped' if self.stop_event.is_set() else 'completed')
        except Exception as exc:
            self.update(state='error', error=str(exc) or type(exc).__name__)
        finally:
            self.hub.close()
            if meta_path:
                try:
                    meta['global']['core:description'] += f'; {count} complex samples; {self.snapshot()["state"]}'
                    temporary = meta_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(meta, indent=2), encoding='utf-8')
                    temporary.replace(meta_path)
                except OSError as exc:
                    self.update(state='error', error=f'Could not finalize IQ metadata: {exc}')


def replay_options(path):
    """Read our single-capture SigMF metadata; never guess a raw file's rate."""
    path = Path(path)
    if path.suffix == '.sigmf-meta':
        path = path.with_suffix('.sigmf-data')
    metadata = path.with_suffix('.sigmf-meta')
    if not metadata.exists():
        return dict(source='file', iq_file=str(path))
    data = json.loads(metadata.read_text(encoding='utf-8'))
    captures = data['captures']
    if len(captures) != 1 or captures[0]['core:sample_start'] != 0:
        raise ValueError('Replay currently supports one continuous RF center frequency per IQ file')
    return dict(source='file', iq_file=str(path), iq_format=data['global']['core:datatype'],
                sample_rate=data['global']['core:sample_rate'], center=captures[0]['core:frequency'])
