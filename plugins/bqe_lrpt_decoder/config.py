"""LRPT settings are deliberately separate from the narrowband audio receiver."""
from dataclasses import dataclass, asdict
import math
from plugins.bqe_sdr.config import Settings


@dataclass
class Options:
    satellite: str = 'M2-4'
    symbol_rate: int = 72000
    frequency: float = 137100000
    sample_rate: int = 240000
    source: str = 'usb'
    device: str = '0'
    gain: object = None
    ppm: int = 0
    host: str = '127.0.0.1'
    tcp_port: int = 1234
    library: str = ''
    duration: float = 1200
    satdump: str = ''
    cli_version: str = '1'
    decode_timeout: int = 1800
    min_free_mb: int = 512
    source_name: str = 'Meteor LRPT'

    @classmethod
    def from_dict(cls, values):
        o = cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})
        if o.satellite not in ('M2-3', 'M2-4'):
            raise ValueError('Select Meteor M2-3 or M2-4')
        o.symbol_rate = int(o.symbol_rate)
        if o.symbol_rate not in (72000, 80000):
            raise ValueError('LRPT symbol rate must be 72000 or 80000')
        if o.source not in ('usb', 'tcp'):
            raise ValueError('Live capture requires USB or rtl_tcp; use Replay for saved IQ')
        o.frequency, o.duration = float(o.frequency), float(o.duration)
        if not math.isfinite(o.duration) or not 1 <= o.duration <= 3600:
            raise ValueError('Capture limit must be 1–3600 seconds')
        if not 136000000 <= o.frequency <= 138000000:
            raise ValueError('Meteor LRPT frequency must be within 136–138 MHz')
        o.decode_timeout, o.min_free_mb = int(o.decode_timeout), int(o.min_free_mb)
        if not 10 <= o.decode_timeout <= 7200 or o.min_free_mb < 64:
            raise ValueError('Invalid decoder timeout or free-space reserve')
        if o.cli_version not in ('1', '2'):
            raise ValueError('SatDump CLI version must be 1 or 2')
        # Only source validation is reused. No audio demodulator or channel filter runs.
        checked = Settings.from_dict(dict(source=o.source, frequency=o.frequency,
            center=o.frequency, sample_rate=o.sample_rate, gain=o.gain, ppm=o.ppm,
            host=o.host, tcp_port=o.tcp_port, device=str(o.device), library=o.library))
        for key in ('sample_rate', 'gain', 'ppm', 'tcp_port', 'device'):
            setattr(o, key, getattr(checked, key))
        return o

    def source_settings(self):
        return Settings.from_dict(dict(self.as_dict(), center=self.frequency))

    def as_dict(self):
        return asdict(self)
