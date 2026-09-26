"""Shared, unambiguous input selection for BQE audio decoders."""
from contextlib import contextmanager
import ctypes
import queue
import sys

# PortAudio initializes its Windows host APIs on import; load before capture
# workers start, just as the services do for SoundCard.
try:
    if sys.platform == 'win32':
        import sounddevice as _sounddevice
    else:
        _sounddevice = None
except ImportError:
    _sounddevice = None


class _InputRecorder:
    """Preserve every callback sample across arbitrary decoder block sizes.

    WASAPI blocking reads on the tested USB codec lose continuity when the
    requested block size differs from the driver period. Callbacks deliver
    the full native-rate stream; assemble decoder blocks here instead.
    """
    def __init__(self):
        self.blocks = queue.Queue(maxsize=512)
        self.pending = None
        self.error = None

    def callback(self, data, frames, timing, status):
        if self.error is not None:
            return
        if status:
            self.error = RuntimeError(f'Live audio lost samples: {status}')
            return
        try:
            self.blocks.put_nowait(data.copy())
        except queue.Full:
            self.error = RuntimeError('Live audio overflow; capture lost samples')

    def record(self, numframes):
        import numpy as np
        parts = []
        remaining = numframes
        while remaining:
            if self.error is not None:
                raise self.error
            if self.pending is None or not len(self.pending):
                try:
                    self.pending = self.blocks.get(timeout=2)
                except queue.Empty:
                    raise RuntimeError('Audio input stopped delivering samples')
            take = min(remaining, len(self.pending))
            parts.append(self.pending[:take])
            self.pending = self.pending[take:]
            remaining -= take
        if self.error is not None:
            raise self.error
        return np.concatenate(parts)


@contextmanager
def input_recorder(source, samplerate):
    """Use PortAudio for Windows inputs that SoundCard cannot reliably open.

    Some USB codecs report plain WAVEFORMATEX float audio rather than
    WAVEFORMATEXTENSIBLE. SoundCard 0.4.6 asserts on these valid formats.
    Speaker loopback and other platforms retain SoundCard capture.
    """
    if isinstance(getattr(source, 'sdr_url', None), str):
        from plugins.bqe_sdr.audio_bridge import audio_recorder
        with audio_recorder(source, samplerate) as recorder:
            yield recorder
        return
    if sys.platform != 'win32' or source.isloopback:
        with source.recorder(samplerate=samplerate) as recorder:
            yield recorder
        return
    if _sounddevice is None:
        raise RuntimeError('Windows microphone capture requires sounddevice; '
                           'install this decoder\'s requirements.txt in its Python environment')
    hosts = _sounddevice.query_hostapis()
    matches = [(index, device) for index, device in enumerate(_sounddevice.query_devices())
               if device['max_input_channels'] > 0
               and hosts[device['hostapi']]['name'] == 'Windows WASAPI'
               and device['name'].strip().casefold() == source.name.strip().casefold()]
    if len(matches) != 1:
        raise ValueError(f'Cannot uniquely match {source.name!r} to a Windows WASAPI input '
                         f'({len(matches)} matches); give the device a unique name in Windows Sound settings')
    index, device = matches[0]
    recorder = _InputRecorder()
    settings = _sounddevice.WasapiSettings(auto_convert=True)
    # Radio static and digital modulation must not be removed by Windows
    # microphone noise suppression. PortAudio exposes RAW shared-mode capture;
    # sounddevice 0.5 has no public keyword for this field yet.
    # https://portaudio.com/docs/v19-doxydocs/pa__win__wasapi_8h.html
    settings._streaminfo.streamOption = _sounddevice._lib.eStreamOptionRaw
    with _sounddevice.InputStream(device=index, samplerate=samplerate,
                                 channels=device['max_input_channels'], dtype='float32',
                                 callback=recorder.callback,
                                 extra_settings=settings):
        yield recorder


@contextmanager
def audio_com():
    """Initialize COM on the calling audio thread, balancing only our calls."""
    if sys.platform != 'win32':
        yield
        return
    ole32 = ctypes.WinDLL('ole32')
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_int32
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    result = ole32.CoInitializeEx(None, 0) & 0xffffffff
    # An existing STA is already initialized; do not uninitialize its COM.
    if result not in (0, 1, 0x80010106):
        raise OSError(f'Audio COM initialization failed: 0x{result:08X}')
    try:
        yield
    finally:
        if result in (0, 1):
            ole32.CoUninitialize()


def input_device(sc, name):
    """Prefer input devices over same-named speaker loopbacks; never guess."""
    if isinstance(name, str) and name.startswith('bqe-sdr:'):
        from plugins.bqe_sdr.audio_bridge import audio_device
        return audio_device(name)
    if not name:
        return sc.default_microphone()
    available = sc.all_microphones(include_loopback=True)
    exact_id = [d for d in available if d.id == name]
    if len(exact_id) == 1:
        return exact_id[0]
    wanted = name.strip().casefold()
    for candidates in ([d for d in available if not d.isloopback], available):
        matched = [d for d in candidates if d.name.casefold() == wanted]
        if not matched:
            matched = [d for d in candidates if wanted in d.name.casefold()]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            raise ValueError(f'Audio device {name!r} is ambiguous; use a full device name or ID from --list-devices')
    raise ValueError(f'Audio input {name!r} was not found; connect it or use --list-devices')



