"""On-demand sound-card capture shared by browser listeners (no NumPy/FFmpeg)."""
import atexit
import json
import queue
import sys
import threading
from pathlib import Path


class AudioStream:
    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = ()
        self._stream = None
        self._closed = False
        self.rate = 48000
        self.channel = 1
        self.channels = 1

    def _capture(self, data, frames, timing, status):
        # Callback never waits on a listener or acquires the lifecycle lock.
        raw = bytes(data)
        offset = (self.channel - 1) * 2
        stride = self.channels * 2
        if self.channels > 1:
            raw = b''.join(raw[i:i+2] for i in range(offset, len(raw), stride))
        if sys.byteorder != 'little':
            raw = b''.join(raw[i:i+2][::-1] for i in range(0, len(raw), 2))
        for listener in self._listeners:
            try:
                listener.put_nowait(raw)
            except queue.Full:
                try:
                    listener.get_nowait()
                except queue.Empty:
                    pass
                try:
                    listener.put_nowait(raw)
                except queue.Full:
                    pass

    def subscribe(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Audio streaming is shutting down.')
            if self._stream is None:
                try:
                    import sounddevice as sd
                except (ImportError, OSError) as exc:
                    raise RuntimeError('Audio capture unavailable. Install sounddevice using the Python running BQE WISP; on Linux also install PortAudio. ' + str(exc)) from exc
                config_path = Path(__file__).with_name('settings.json')
                config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
                device = config.get('device')
                info = sd.query_devices(device, 'input')
                self.rate = int(config.get('sample_rate') or info['default_samplerate'])
                self.channel = int(config.get('channel', 1))
                if not 8000 <= self.rate <= 96000:
                    raise ValueError('sample_rate must be between 8000 and 96000.')
                if not 1 <= self.channel <= int(info['max_input_channels']):
                    raise ValueError('Selected channel is not available on this input device.')
                self.channels = self.channel
                # Some USB codecs require stereo even when selecting the left channel.
                try:
                    sd.check_input_settings(device=device, channels=self.channels,
                                            dtype='int16', samplerate=self.rate)
                except Exception:
                    self.channels = max(2, self.channel)
                    sd.check_input_settings(device=device, channels=self.channels,
                                            dtype='int16', samplerate=self.rate)
                stream = sd.RawInputStream(device=device, samplerate=self.rate,
                    channels=self.channels, dtype='int16', blocksize=int(self.rate * .04),
                    callback=self._capture)
                try:
                    stream.start()
                except Exception:
                    stream.close()
                    raise
                self._stream = stream
            listener = queue.Queue(maxsize=8)
            self._listeners += (listener,)
            return listener, self.rate

    def unsubscribe(self, listener):
        with self._lock:
            self._listeners = tuple(q for q in self._listeners if q is not listener)
            if not self._listeners:
                self._close_stream()

    def _close_stream(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def close(self):
        with self._lock:
            self._closed = True
            self._listeners = ()
            self._close_stream()

    def serve(self, handler):
        # Custom fetch header prevents cross-origin pages starting capture.
        if handler.headers.get('X-BQE-Audio') != '1':
            handler.send_bytes(403, 'text/plain', b'Use Audio / Stream Audio in BQE WISP.')
            return
        try:
            listener, rate = self.subscribe()
        except Exception as exc:
            handler.send_bytes(503, 'text/plain; charset=utf-8', str(exc).encode('utf-8'))
            return
        try:
            handler.connection.settimeout(5)
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/octet-stream')
            handler.send_header('X-Audio-Sample-Rate', str(rate))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Accel-Buffering', 'no')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            handler.close_connection = True
            while not self._closed:
                try:
                    data = listener.get(timeout=2)
                except queue.Empty:
                    break  # Device stopped delivering audio.
                handler.wfile.write(data)
                handler.wfile.flush()
        except (OSError, ConnectionError):
            pass
        finally:
            self.unsubscribe(listener)


AUDIO_STREAM = AudioStream()
atexit.register(AUDIO_STREAM.close)
