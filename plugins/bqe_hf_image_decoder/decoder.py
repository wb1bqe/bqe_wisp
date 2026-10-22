"""Run HF SSDV and HamDRM receivers continuously on the same audio stream."""
from collections import deque
import zlib

import numpy as np
import reedsolo
import soxr

from plugins.bqe_ssdv_decoder.hades import Images
from .freedv import Modem as FreeDV
from .hamdrm import Modem as HamDRM


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **kw: None, preview=lambda image: None, profile='auto'):
        if not 8000 <= rate <= 192000:
            raise ValueError('HF reception requires 8000–192000 audio samples per second')
        if profile not in ('auto', 'freedv', 'hamdrm'):
            raise ValueError('HF profile must be auto, freedv, or hamdrm')
        self.report, self.emit, self.preview = report, emit, preview
        self.rate, self.samples, self.packets = rate, 0, 0
        self.closed = False
        self.recent = deque(maxlen=256)
        self.chains = []
        self.images = None
        self.rs = reedsolo.RSCodec(32, nsize=255, fcr=112, prim=0x187, generator=173)
        try:
            if profile in ('auto', 'freedv'):
                self.images = Images(self._image, self._image_report, lambda image: None)
                for mode in ('700D',):
                    modem = FreeDV(mode, lambda data, mode=mode: self.packet(data, mode))
                    self.chains.append((modem, soxr.ResampleStream(rate, 8000, 1, dtype='float32')))
            if profile in ('auto', 'hamdrm'):
                self.chains.append((HamDRM(self._image, report), soxr.ResampleStream(rate, 48000, 1, dtype='float32')))
        except Exception:
            self.close()
            raise
        report(mode='Auto' if profile == 'auto' else profile,
               reception='Waiting for validated HF SSDV or HamDRM data', packets=0)

    def _image_report(self, **values):
        # The shared SSDV assembler also serves satellite reception.
        self.report(**{key: value.replace('SO-127', 'HF SSDV') if isinstance(value, str) else value
                       for key, value in values.items()})

    def _image(self, image, when):
        if image.info.get('ssdv_mode') == 'SO-127':
            image.info['ssdv_mode'] = 'HF SSDV'
        self.preview(image)
        self.emit(image, when)

    def packet(self, data, mode):
        if len(data) != 256 or data[0] != 0x55 or data[1] not in (0x66, 0x67):
            return
        if data[1] == 0x66:
            try:
                data = b'\x55' + bytes(self.rs.decode(data[1:])[1])
            except reedsolo.ReedSolomonError:
                return
        end = 220 if data[1] == 0x66 else 252
        if zlib.crc32(data[1:end]) != int.from_bytes(data[end:end+4], 'big') or not data[9] or not data[10]:
            return
        if data in self.recent:
            return
        self.recent.append(data)
        self.packets += 1
        self.report(mode=f'HF SSDV / FreeDV {mode}', packets=self.packets, reception='Receiving HF SSDV image packets')
        self.images.packet(data, self.samples/self.rate)

    def push(self, samples):
        if self.closed:
            raise RuntimeError('Decoder has been closed')
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.all(np.isfinite(samples)):
            raise ValueError('Expected finite mono audio')
        self.samples += len(samples)
        # Bound resampler output and native queues for large caller blocks.
        for offset in range(0, len(samples), 8192):
            for modem, resampler in self.chains:
                audio = resampler.resample_chunk(samples[offset:offset+8192])
                if len(audio):
                    modem.push(audio)

    def finish(self):
        if self.closed:
            return
        try:
            for modem, resampler in self.chains:
                tail = resampler.resample_chunk(np.zeros(round(self.rate*.8), dtype=np.float32), last=True)
                if len(tail):
                    modem.push(tail)
            if self.images:
                self.images.finish()
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            errors = []
            for modem, _ in self.chains:
                try:
                    modem.close()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise errors[0]
