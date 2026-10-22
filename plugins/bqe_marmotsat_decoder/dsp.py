# SPDX-License-Identifier: GPL-3.0-or-later
"""Streaming clock recovery for flat FM discriminator audio (not speaker CW)."""
from collections import deque
import math

import numpy as np
from scipy.signal import firwin, lfilter
from plugins.bqe_tlm_decoder.dsp import SymbolClock
from .protocol import BAUD, SYNC_WORD, Deframer


class AudioDecoder:
    def __init__(self, sample_rate, emit, options=None):
        options = options or {}
        self.baud = float(options.get('baud', BAUD))
        if not math.isfinite(self.baud) or not 10000 <= self.baud <= 10350:
            raise ValueError('MARMOTSat baud must be between 10000 and 10350 (default 10170)')
        if not math.isfinite(sample_rate) or sample_rate < self.baud * 3:
            raise ValueError('Sample rate too low: use flat discriminator audio at 48 kHz or higher')
        self.sample_rate, self.emit = sample_rate, emit
        self.bauds, self.skipped_bauds = [self.baud], []
        self.recent = deque(maxlen=128)
        self.duplicates = 0
        self.taps = firwin(max(17, int(6 * sample_rate / self.baud) | 1),
                           0.75 * self.baud, fs=sample_rate)
        self.filter_state = np.zeros(len(self.taps) - 1)
        self.dc_state = np.zeros(1)
        self.dc_pole = math.exp(-2 * math.pi * 5 / sample_rate)
        self.deframers, self.clocks = [], []
        for phase in (0, .25, .5, .75):
            framer = Deframer(self._packet, options.get('sync_word', SYNC_WORD))
            self.deframers.append(framer)
            self.clocks.append(SymbolClock(sample_rate, self.baud, phase, framer.feed,
                                           (len(self.taps) - 1) / 2))

    def _packet(self, packet, when):
        frame = packet['frame_hex']
        if any(old == frame and abs(when - end) < 2 / self.baud for old, end in self.recent):
            self.duplicates += 1
            return
        self.recent.append((frame, when))
        self.emit(dict(packet, baud=self.baud, audio_seconds=round(when, 6)))

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError('Expected finite mono FM discriminator audio')
        if not len(samples):
            return
        audio, self.dc_state = lfilter([1, -1], [1, -self.dc_pole], samples, zi=self.dc_state)
        audio, self.filter_state = lfilter(self.taps, [1], audio, zi=self.filter_state)
        for clock in self.clocks:
            clock.push(audio)

    def finish(self):
        self.push(np.zeros(len(self.taps) + 128))
        for framer in self.deframers:
            framer.finish()

    def diagnostics(self):
        return dict(sync_candidates=sum(f.syncs for f in self.deframers),
                    bad_crc=sum(f.bad_crc for f in self.deframers),
                    bad_length=sum(f.bad_length for f in self.deframers),
                    truncated_candidates=sum(f.truncated for f in self.deframers),
                    duplicate_candidates=self.duplicates)
