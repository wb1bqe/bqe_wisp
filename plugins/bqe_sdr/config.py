"""Validate the receiver contract before opening hardware or output files."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math

RATES = (240000, 960000, 1200000, 1440000, 1920000, 2400000)
AUDIO_RATE = 48000


@dataclass
class Settings:
    source: str = 'usb'
    frequency: float = 145800000
    center: float | None = None
    sample_rate: int = 960000
    mode: str = 'NFM'
    bandwidth: float = 25000
    deviation: float = 5000
    audio_gain: float = 1
    gain: float | None = None
    ppm: int = 0
    device: str = '0'
    host: str = '127.0.0.1'
    tcp_port: int = 1234
    iq_file: str = ''
    iq_format: str = 'cu8'
    library: str = ''
    record_audio: bool = True
    record_iq: bool = False
    duration: float = 0
    source_name: str = 'SDR'

    @classmethod
    def from_dict(cls, values):
        s = cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        s.mode = str(s.mode).upper()
        if s.mode == 'FM':
            s.mode = 'NFM'
        if s.source not in ('usb', 'tcp', 'file', 'demo'):
            raise ValueError('Source must be usb, tcp, file, or demo')
        if s.mode not in ('NFM', 'AM', 'USB', 'LSB', 'CW'):
            raise ValueError('Supported modes: NFM, AM, USB, LSB, CW')
        for key in ('frequency', 'bandwidth', 'deviation', 'audio_gain', 'duration'):
            value = float(getattr(s, key))
            if not math.isfinite(value):
                raise ValueError(f'{key} must be finite')
            setattr(s, key, value)
        s.sample_rate = int(s.sample_rate)
        if s.sample_rate not in RATES:
            raise ValueError(f'IQ sample rate must be one of {RATES}')
        if not 1 <= s.frequency <= 2000000000:
            raise ValueError('Frequency must be between 1 Hz and 2 GHz; hardware limits also apply')
        if not 500 <= s.bandwidth <= 40000:
            raise ValueError('Channel bandwidth must be 500–40000 Hz')
        if s.mode in ('USB', 'LSB') and s.bandwidth > 12000:
            raise ValueError('SSB bandwidth must not exceed 12000 Hz')
        if not 100 <= s.deviation <= 20000 or not 0 < s.audio_gain <= 100:
            raise ValueError('Invalid FM deviation or audio gain')
        if not 0 <= s.duration <= 86400:
            raise ValueError('Duration must be 0 (unlimited) or at most 86400 seconds')
        s.center = float(s.center) if s.center is not None else s.frequency + s.sample_rate / 4
        if not math.isfinite(s.center) or not 1 <= s.center <= 2000000000:
            raise ValueError('Invalid center frequency')
        s.validate_tune(s.frequency)
        s.tcp_port = int(s.tcp_port)
        s.ppm = int(s.ppm)
        if not 1 <= s.tcp_port <= 65535 or abs(s.ppm) > 1000:
            raise ValueError('Invalid TCP port or PPM correction')
        if s.gain is not None:
            s.gain = float(s.gain)
            if not math.isfinite(s.gain) or not -10 <= s.gain <= 60:
                raise ValueError('Gain must be auto (null) or -10 to 60 dB')
        if s.iq_format not in ('cu8', 'cf32_le'):
            raise ValueError('IQ format must be cu8 or cf32_le')
        if s.source == 'file' and not s.iq_file:
            raise ValueError('Select an IQ file')
        for key in ('record_audio', 'record_iq'):
            if not isinstance(getattr(s, key), bool):
                raise ValueError(f'{key} must be a boolean')
        return s

    def validate_tune(self, frequency):
        if not math.isfinite(frequency) or abs(frequency - self.center) + self.bandwidth > self.sample_rate * .45:
            raise ValueError('Frequency is outside the captured IQ band; stop and restart with a new center')

    def as_dict(self):
        return asdict(self)
