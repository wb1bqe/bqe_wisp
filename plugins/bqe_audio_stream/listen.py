"""Browser playback of the configured radio input, never the default mic."""
from contextlib import contextmanager
from pathlib import Path
import atexit
import threading

ROOT = Path(__file__).resolve().parents[2]


def radio_source():
    import yaml
    rig = yaml.safe_load((ROOT / 'bqe_config/my_rig.yaml').read_text(encoding='utf-8')) or {}
    if rig.get('radio_is_sdr') is True:
        from plugins.bqe_sdr.integration import audio_input
        return audio_input(rig)
    name = rig.get('radio_soundcard')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Set radio_soundcard in my_rig.yaml before listening.')
    return name.strip()


@contextmanager
def radio_recorder():
    from plugins.audio_devices import audio_com, input_device, input_recorder
    name = radio_source()
    with audio_com():
        if name.startswith('bqe-sdr:'):
            source = input_device(None, name)
        else:
            import soundcard
            source = input_device(soundcard, name)
            if source.isloopback:
                raise ValueError('radio_soundcard must select a radio input, not speaker loopback.')
        with input_recorder(source, 48000) as recorder:
            yield recorder


class RadioListen:
    def __init__(self):
        self.closed = threading.Event()

    def close(self):
        self.closed.set()

    def serve(self, handler):
        if handler.headers.get('X-BQE-Audio') != '1':
            handler.send_bytes(403, 'text/plain', b'Use Audio / Listen in BQE WISP.')
            return
        headers_sent = False
        try:
            if self.closed.is_set():
                raise RuntimeError('BQE is shutting down.')
            import numpy as np
            with radio_recorder() as recorder:
                # Confirm samples arrive before sending success to the player.
                samples = recorder.record(1920)
                handler.connection.settimeout(5)
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/octet-stream')
                handler.send_header('X-Audio-Sample-Rate', '48000')
                handler.send_header('Cache-Control', 'no-store')
                handler.send_header('Connection', 'close')
                handler.end_headers()
                headers_sent = True
                handler.close_connection = True
                while not self.closed.is_set():
                    # Radio inputs commonly duplicate mono on two channels.
                    mono = np.asarray(samples)
                    if mono.ndim > 1:
                        mono = mono[:, 0]
                    pcm = (np.clip(np.nan_to_num(mono), -1, 1) * 32767).astype('<i2')
                    handler.wfile.write(pcm.tobytes())
                    handler.wfile.flush()
                    samples = recorder.record(1920)
        except Exception as exc:
            if not headers_sent:
                handler.send_bytes(503, 'text/plain; charset=utf-8', str(exc).encode('utf-8'))
            # After headers, closing the connection makes the player report
            # source loss. Context managers release only this listener's input.


RADIO_LISTEN = RadioListen()
atexit.register(RADIO_LISTEN.close)
