"""Streaming channel selection and demodulation, preserving state at every boundary."""
import numpy as np
from scipy.signal import firwin, lfilter
from .config import AUDIO_RATE


def decode_iq(raw, datatype='cu8'):
    if datatype == 'cu8':
        values = (np.frombuffer(raw, np.uint8).astype(np.float32) - 127.5) / 128
        return values[::2] + 1j * values[1::2]
    return np.frombuffer(raw, '<c8')


class Decimator:
    def __init__(self, taps, factor):
        self.taps = taps
        self.factor = factor
        self.state = np.zeros(len(taps) - 1, dtype=np.complex128)
        self.count = 0

    def push(self, x):
        if not len(x):
            return x
        y, self.state = lfilter(self.taps, [1], x, zi=self.state)
        start = (-self.count) % self.factor
        self.count += len(x)
        return y[start::self.factor]


class Demodulator:
    def __init__(self, settings):
        self.s = settings
        self.phase = 0.0
        self.beat_phase = 0.0
        self.previous = 1 + 0j
        self.dc_state = np.zeros(1)
        self.coarse = None
        factor = settings.sample_rate // 240000
        if factor > 1:
            self.coarse = Decimator(firwin(16 * factor + 1, 90000, fs=settings.sample_rate), factor)
        bw = settings.bandwidth
        if settings.mode in ('USB', 'LSB'):
            # Select just one sideband, with a 150 Hz gap around the carrier.
            low, high = 150, bw
            mid = (low + high) / 2 * (1 if settings.mode == 'USB' else -1)
            taps = firwin(1025, (high - low) / 2, fs=240000)
            taps = taps * np.exp(2j * np.pi * mid / 240000 * (np.arange(len(taps)) - (len(taps)-1)/2))
        else:
            taps = firwin(513, bw / 2, fs=240000)
        self.channel = Decimator(taps, 5)

    def push(self, iq, frequency=None):
        frequency = self.s.frequency if frequency is None else frequency
        step = -2 * np.pi * (frequency - self.s.center) / self.s.sample_rate
        mixed = iq * np.exp(1j * (self.phase + step * np.arange(len(iq))))
        self.phase = (self.phase + step * len(iq)) % (2 * np.pi)
        if self.coarse:
            mixed = self.coarse.push(mixed)
        x = self.channel.push(mixed)
        if not len(x):
            return np.empty(0, np.float32)
        if self.s.mode == 'NFM':
            prior = np.concatenate(([self.previous], x[:-1]))
            audio = np.angle(x * np.conj(prior)) * AUDIO_RATE / (2 * np.pi * self.s.deviation)
            self.previous = x[-1]
        elif self.s.mode == 'AM':
            audio = np.abs(x)
        elif self.s.mode == 'CW':
            step = 2 * np.pi * 700 / AUDIO_RATE
            audio = (x * np.exp(1j * (self.beat_phase + step*np.arange(len(x))))).real
            self.beat_phase = (self.beat_phase + step * len(x)) % (2*np.pi)
        else:
            audio = x.real
        # A gentle DC blocker, not speech filtering, squelch, or de-emphasis.
        audio, self.dc_state = lfilter([1, -1], [1, -np.exp(-2*np.pi*10/AUDIO_RATE)], audio, zi=self.dc_state)
        return (audio * self.s.audio_gain).astype(np.float32)


def spectrum(iq, bins=512):
    n = min(len(iq), 4096)
    if not n:
        return []
    fft = np.fft.fftshift(np.fft.fft(iq[:n] * np.hanning(n))) / max(1, n/2)
    power = 20 * np.log10(np.maximum(np.abs(fft), 1e-7))
    return np.max(power[:(n//bins)*bins].reshape(bins, -1), axis=1).round(1).tolist() if n >= bins else power.tolist()
