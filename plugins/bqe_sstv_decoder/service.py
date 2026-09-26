"""Audio lifecycle, BQE gallery configuration, and atomic image storage."""
from collections import deque
from datetime import datetime, timezone
import json
import io
from pathlib import Path
import queue
import threading
import uuid

import numpy as np
import soundfile as sf
import soundcard as sc  # Import on startup before worker threads initialize COM.
import yaml

from plugins.audio_devices import audio_com, input_device, input_recorder

PROJECT = Path(__file__).resolve().parents[2]


class SequentialAudio(sf.SoundFile):
    """Read compressed audio without SoundFile's post-read seek.

    Re-seeking after every block can disturb MP3 bit-reservoir state. This
    reader intentionally exposes a forward-only stream, also suitable for WAV.
    """
    def seekable(self):
        return False

    def chunks(self):
        while True:
            block = self.read(8192, dtype='float32', always_2d=True)
            if not len(block):
                break
            yield block


def gallery_settings(config=PROJECT / 'bqe_config' / 'general_settings.yaml'):
    with Path(config).open(encoding='utf-8') as stream:
        document = yaml.safe_load(stream) or {}
    settings = {}
    for section in document.get('program_settings', []):
        settings.update(section.get('common', {}))
    for section in document.get('program_settings', []):
        settings.update(section.get('bqe_wisp', {}))
    location = str(settings.get('sstv_gallery_location') or '').strip()
    if not location:
        raise ValueError('Set bqe_wisp.sstv_gallery_location in bqe_config/general_settings.yaml before decoding')
    folder = Path(location).expanduser()
    if not folder.is_absolute():
        folder = PROJECT / folder
    return folder.resolve(), int(settings.get('ui_port', 8028))


@audio_com()
def devices():
    return [dict(id=d.id, name=d.name, loopback=d.isloopback)
            for d in sc.all_microphones(include_loopback=True)]


class Session:
    image_prefix = 'sstv'
    spectrum_limit = 4000
    mode_key = 'sstv_mode'
    complete_key = 'sstv_complete'
    audio_suffixes = ('.mp3', '.wav')
    waiting_message = 'Waiting for an SSTV header'
    no_image_message = 'No supported SSTV image detected. A recognizable VIS header is required.'

    def new_decoder(self, rate, options):
        from .decoder import AudioDecoder
        return AudioDecoder(rate, self._save, self.update, self.preview)

    def image_metadata(self, image):
        return {}

    def __init__(self, output, gallery_url=None):
        self.output = Path(output).resolve()
        self.gallery_url = gallery_url
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.recent = deque(maxlen=100)
        self.status = dict(state='idle', images=0, error=None)
        self.preview_bytes = None
        self.preview_version = 0
        self.signal = None
        self.signal_version = 0

    def update(self, **fields):
        with self.lock:
            self.status.update(fields)

    def snapshot(self):
        with self.lock:
            return dict(self.status, recent=list(self.recent), output=str(self.output), gallery_url=self.gallery_url,
                        preview_version=self.preview_version, has_preview=self.preview_bytes is not None)

    def update_signal(self, audio, rate):
        """A bounded spectrum and 20 ms waveform of the selected decode channel."""
        samples = np.asarray(audio[-4096:], dtype=np.float64)
        if len(samples) < 2:
            return
        window = np.hanning(len(samples))
        magnitude = 2 * np.abs(np.fft.rfft(samples * window)) / max(1, window.sum())
        frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
        keep = frequencies <= min(self.spectrum_limit, rate / 2)
        spectrum = np.clip(20 * np.log10(np.maximum(magnitude[keep], 1e-5)), -100, 0)
        count = min(len(audio), max(2, round(rate * .02)))
        waveform = np.interp(np.linspace(0, count - 1, 256), np.arange(count), audio[:count])
        with self.lock:
            self.signal_version += 1
            self.signal = dict(version=self.signal_version, spectrum_db=np.round(spectrum, 1).tolist(),
                               max_hz=float(frequencies[keep][-1]), waveform=waveform.tolist(),
                               waveform_ms=count / rate * 1000, peak=float(np.max(np.abs(audio))))
            self.status['signal'] = self.signal

    def preview(self, image):
        stream = io.BytesIO()
        image.convert('RGB').save(stream, format='JPEG', quality=85)
        with self.lock:
            self.preview_bytes = stream.getvalue()
            self.preview_version += 1
            self.status['preview_complete'] = bool(image.info.get(self.complete_key, False))

    def get_preview(self):
        with self.lock:
            return self.preview_bytes

    def start(self, options, file=None, cleanup=False):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Stop the current decoder before starting another source')
            channel = int(options.get('channel', 0))
            if not 0 <= channel <= 31:
                raise ValueError('Channel must be between 0 and 31')
            if file and Path(file).suffix.lower() not in self.audio_suffixes:
                raise ValueError('Choose a supported recording: ' + ', '.join(self.audio_suffixes))
            self.stop_event.clear()
            self.recent.clear()
            self.preview_bytes = None
            self.signal = None
            self.status = dict(state='starting', images=0, error=None, mode=None,
                               source=options.get('source_name') or (Path(file).name if file else 'Live audio'),
                               channel=channel,
                               audio_seconds=0, reception=self.waiting_message)
            self.thread = threading.Thread(target=self._run, args=(dict(options), file, cleanup), daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.status['state'] = 'stopping'
                self.stop_event.set()

    def _save(self, image, seconds):
        mode = str(image.info[self.mode_key]).removeprefix('Mode.')
        complete = bool(image.info.get(self.complete_key, False))
        stem = self.image_prefix + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + mode
        stem += ('-partial' if not complete else '') + '-' + uuid.uuid4().hex[:8]
        destination = self.output / (stem + '.jpg')
        temporary = destination.with_suffix('.tmp')
        try:
            image.convert('RGB').save(temporary, format='JPEG', quality=95, subsampling=0)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        record = dict(filename=destination.name, mode=mode, complete=complete,
                      width=image.width, height=image.height, audio_seconds=seconds,
                      decoded_at=datetime.now(timezone.utc).isoformat(), source=self.status['source'])
        record.update(self.image_metadata(image))
        with self.lock:
            self.recent.append(record)
            self.status['images'] += 1
        # JPEG is already committed, even if saving its optional metadata fails.
        try:
            destination.with_suffix('.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        except OSError as exc:
            self.update(warning=f'Image saved, but metadata could not be saved: {exc}')

    def _run(self, options, file, cleanup):
        capture = None
        decoder = None
        try:
            self.output.mkdir(parents=True, exist_ok=True)

            def consume(blocks, rate, total=None):
                nonlocal decoder
                decoder = self.new_decoder(rate, options)
                channel = int(options.get('channel', 0))
                count = 0
                last_signal = -rate
                self.update(state='running', sample_rate=rate)
                for block in blocks:
                    if self.stop_event.is_set():
                        break
                    if channel >= block.shape[1]:
                        raise ValueError(f'Channel {channel} unavailable; source has {block.shape[1]} channel(s)')
                    audio = block[:, channel]
                    decoder.push(audio)
                    count += len(audio)
                    if count - last_signal >= rate / 4:
                        self.update_signal(audio, rate)
                        last_signal = count
                    self.update(audio_seconds=count / rate, progress=count / total if total else None,
                                level_dbfs=float(20*np.log10(max(1e-9, np.sqrt(np.mean(audio**2))))),
                                clipping=bool(np.any(np.abs(audio) >= .999)))
                decoder.finish()

            if file:
                with SequentialAudio(file) as audio:
                    consume(audio.chunks(), audio.samplerate, len(audio))
            else:
                rate = int(options.get('sample_rate', 48000))
                if not 8000 <= rate <= 192000:
                    raise ValueError('Sample rate must be between 8000 and 192000 Hz')
                chunks = queue.Queue(maxsize=128)
                failures = []

                def record():
                    try:
                        with audio_com():
                            device = options.get('device')
                            source = input_device(sc, device)
                            self.update(device=source.name if source is not None else device)
                            if source is None:
                                raise ValueError('No audio input device is available')
                            # Capture all channels to avoid WASAPI mono capture corruption.
                            with input_recorder(source, samplerate=rate) as recorder:
                                while not self.stop_event.is_set():
                                    data = recorder.record(numframes=4096)
                                    try:
                                        chunks.put_nowait(data)
                                    except queue.Full:
                                        raise RuntimeError('Live audio overflow; recording stopped to avoid saving a corrupted image')
                    except Exception as exc:
                        failures.append(exc)

                capture = threading.Thread(target=record, daemon=True)
                capture.start()

                def blocks():
                    while not self.stop_event.is_set():
                        if failures:
                            raise failures[0]
                        try:
                            yield chunks.get(timeout=.2)
                        except queue.Empty:
                            if not capture.is_alive():
                                if failures:
                                    raise failures[0]
                                break
                consume(blocks(), rate)
            self.update(state='stopped' if self.stop_event.is_set() else 'completed',
                        reception='Images saved' if self.status['images'] else
                        self.no_image_message)
        except Exception as exc:
            self.update(state='error', error=str(exc) or type(exc).__name__)
        finally:
            self.stop_event.set()
            if capture:
                capture.join(timeout=5)
                if capture.is_alive():
                    self.update(state='error', error='Audio driver did not stop; restart the plugin before capturing again')
                    capture.join()
            if cleanup and file:
                Path(file).unlink(missing_ok=True)

