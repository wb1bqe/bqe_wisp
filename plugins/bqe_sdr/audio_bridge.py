"""Local PCM input understood by existing BQE decoders and the MP3 recorder."""
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.request import urlopen


def audio_device(name):
    parsed = urlparse(name)
    if (parsed.scheme != 'bqe-sdr' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment):
        raise ValueError('SDR input must be bqe-sdr://127.0.0.1:PORT')
    port = parsed.port or 8772
    return SimpleNamespace(name=f'BQE SDR ({parsed.hostname}:{port})', id=name,
                           channels=1, isloopback=False,
                           sdr_url=f'http://{parsed.hostname}:{port}/audio')


@contextmanager
def audio_recorder(device, samplerate):
    import numpy as np
    if samplerate != 48000:
        raise ValueError('BQE SDR audio uses 48000 Hz; set the decoder/recorder sample rate to 48000')
    with urlopen(device.sdr_url, timeout=3) as response:
        if (response.headers.get('X-Audio-Format') != 'f32le' or
                response.headers.get('X-Audio-Rate') != '48000' or
                response.headers.get('X-Audio-Channels') != '1'):
            raise ValueError('Unexpected SDR audio stream format')

        class Recorder:
            def record(self, numframes):
                needed = numframes * 4
                data = bytearray()
                while len(data) < needed:
                    part = response.read(needed-len(data))
                    if not part:
                        raise RuntimeError('BQE SDR audio ended or the consumer fell behind; restart reception')
                    data.extend(part)
                return np.frombuffer(data, '<f4').copy().reshape(-1, 1)
        yield Recorder()
