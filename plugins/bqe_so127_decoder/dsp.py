"""Streaming SO-127 FSK from flat FM discriminator audio."""
from collections import deque
import math

import numpy as np
from scipy.signal import firwin, lfilter
from plugins.bqe_tlm_decoder.dsp import SymbolClock
from .protocol import Deframer


class AudioDecoder:
    def __init__(self, sample_rate, emit, options=None):
        options = options or {}
        self.bauds = list(dict.fromkeys(options.get('bauds', (200, 800, 1600))))
        if (not math.isfinite(sample_rate) or sample_rate < 8000 or not self.bauds
                or any(b not in (200, 800, 1600) for b in self.bauds)):
            raise ValueError('SO-127 needs at least 8000 samples/s and baud rates 200, 800 or 1600')
        if options.get('protocol', 'so127') != 'so127':
            raise ValueError('Expected SO-127 protocol')
        self.rate, self.emit = sample_rate, emit
        self.skipped_bauds = []
        self.recent = deque(maxlen=128)
        self.duplicates = 0
        self.dc = np.zeros(1)
        self.pole = math.exp(-2 * math.pi * .5 / sample_rate)
        self.chains, self.deframers = [], []
        for baud in self.bauds:
            taps = firwin(int(6 * sample_rate / baud) | 1, .75 * baud, fs=sample_rate)
            framers = [Deframer(lambda packet, when, b=baud: self._packet(packet, when, b)) for _ in range(4)]
            clocks = [SymbolClock(sample_rate, baud, i / 4, framer.feed, (len(taps) - 1) / 2)
                      for i, framer in enumerate(framers)]
            self.deframers.extend(framers)
            self.chains.append([taps, np.zeros(len(taps) - 1), clocks])

    def _packet(self, packet, when, baud):
        key = packet['frame_hex']
        if any(old == key and abs(when - end) < 3 / baud for old, end in self.recent):
            self.duplicates += 1
            return
        self.recent.append((key, when))
        self.emit(dict(packet, baud=baud, audio_seconds=round(when, 6)))

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError('Expected finite mono FM discriminator audio')
        if not len(samples):
            return
        audio, self.dc = lfilter([1, -1], [1, -self.pole], samples, zi=self.dc)
        for chain in self.chains:
            taps, state, clocks = chain
            filtered, chain[1] = lfilter(taps, [1], audio, zi=state)
            for clock in clocks:
                clock.push(filtered)

    def finish(self):
        self.push(np.zeros(int(self.rate * .1)))
        for framer in self.deframers:
            framer.finish()

    def diagnostics(self):
        return dict(sync_candidates=sum(f.syncs for f in self.deframers),
                    bad_crc=sum(f.bad_crc for f in self.deframers),
                    bad_length=sum(f.bad_length for f in self.deframers),
                    truncated_candidates=sum(f.truncated for f in self.deframers),
                    duplicate_candidates=self.duplicates)
