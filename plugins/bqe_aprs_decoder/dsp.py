# SPDX-License-Identifier: GPL-3.0-or-later
"""Streaming Bell 202 AFSK (1200 baud) and optional G3RUH (9600 baud)."""
from collections import deque
import math

import numpy as np
from scipy.signal import lfilter

from plugins.bqe_tlm_decoder.dsp import SymbolClock, RateDecoder
from plugins.bqe_tlm_decoder.protocols import HDLC
from .protocol import parse_packet


class NRZI:
    def __init__(self, emit):
        self.previous = 0
        self.hdlc = HDLC(emit)

    def feed(self, value, when):
        level = int(value >= 0)
        self.hdlc.feed(int(level == self.previous), when)
        self.previous = level


class AFSK:
    """Quadrature tone-energy discriminator followed by fractional symbol clocks."""
    def __init__(self, rate, emit):
        self.baud = 1200
        length = max(5, round(rate / self.baud))
        # A one-symbol correlation rejects out-of-band noise without depending
        # on audio polarity or carrier phase. FIR and clock state span chunks.
        self.taps = [np.exp(2j * np.pi * tone * np.arange(length) / rate) / length
                     for tone in (1200, 2200)]
        self.states = [np.zeros(length - 1, complex) for _ in self.taps]
        self.decoders = [NRZI(lambda frame, when, meta: emit(
            frame, when, 1200, dict(meta, protocol='AX.25 AFSK1200')))
                         for _ in range(4)]
        self.clocks = [SymbolClock(rate, self.baud, phase, decoder.feed, (length - 1) / 2)
                       for phase, decoder in zip((0, .25, .5, .75), self.decoders)]
        self.flush_samples = length + 128

    def push(self, samples):
        energy = []
        for index, taps in enumerate(self.taps):
            tone, self.states[index] = lfilter(taps, [1], samples, zi=self.states[index])
            energy.append(np.abs(tone)**2)
        discriminator = (energy[0] - energy[1]) / np.maximum(energy[0] + energy[1], 1e-12)
        for clock in self.clocks:
            clock.push(discriminator)


class AudioDecoder:
    def __init__(self, sample_rate, emit, options):
        bauds = options.get('bauds', [1200])
        if (not 8000 <= sample_rate <= 192000 or options.get('protocol', 'aprs') != 'aprs'
                or not isinstance(bauds, (list, tuple)) or not bauds
                or any(type(b) is not int or b not in (1200, 9600) for b in bauds)):
            raise ValueError('APRS requires 8-192 kHz audio and baud rates 1200 and/or 9600')
        self.sample_rate, self.emit = sample_rate, emit
        self.bauds = [b for b in dict.fromkeys(bauds) if sample_rate >= 3*b]
        self.skipped_bauds = [b for b in dict.fromkeys(bauds) if b not in self.bauds]
        if not self.bauds:
            raise ValueError('Input sample rate is too low for the selected baud rates')
        self.recent = deque(maxlen=128)
        self.duplicates = self.rejected_headers = 0
        self.dc_state = np.zeros(1)
        self.dc_pole = math.exp(-2*np.pi*20/sample_rate)
        self.rates = [AFSK(sample_rate, self._packet) if b == 1200 else
                      RateDecoder(sample_rate, b, 'ax25', None, self._packet) for b in self.bauds]

    def _packet(self, frame, when, baud, metadata):
        if any(old == frame and old_baud == baud and abs(when - end) <= max(.002, 2/baud)
               for old, end, old_baud in self.recent):
            self.duplicates += 1
            return
        try:
            packet = parse_packet(frame)
        except ValueError:
            self.rejected_headers += 1
            return
        self.recent.append((frame, when, baud))
        self.emit(dict(metadata, **packet, baud=baud, audio_seconds=round(when, 6)))

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError('Expected finite mono FM audio samples')
        if not len(samples):
            return
        filtered, self.dc_state = lfilter([1, -1], [1, -self.dc_pole], samples, zi=self.dc_state)
        for decoder in self.rates:
            decoder.push(filtered)

    def finish(self):
        self.push(np.zeros(max(d.flush_samples if isinstance(d, AFSK) else len(d.taps) + 128
                               for d in self.rates)))

    def diagnostics(self):
        return dict(duplicate_candidates=self.duplicates, rejected_headers=self.rejected_headers,
                    bad_crc=sum(d.hdlc.bad_crc for rate in self.rates for d in rate.decoders))
