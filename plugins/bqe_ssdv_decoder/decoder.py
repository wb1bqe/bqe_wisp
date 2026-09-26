# SPDX-License-Identifier: GPL-3.0-or-later
"""Streaming RS61 FM-discriminator audio to validated Geoscan image packets."""
from collections import deque
import math

import numpy as np
from scipy.signal import firwin, lfilter

from plugins.bqe_tlm_decoder.dsp import SymbolClock
from .images import ImageReceiver
from .protocol import Framer


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **fields: None, preview=lambda image: None,
                 baud=9600, packet_sink=lambda packet: None):
        if baud != 9600 or rate < 3 * baud:
            raise ValueError('RS61 requires 9600 baud and an audio rate of at least 28,800 Hz')
        self.rate, self.baud = rate, baud
        self.report, self.packet_sink = report, packet_sink
        self.images = ImageReceiver(emit, report, preview)
        self.samples = self.packets = self.duplicates = 0
        self.recent = deque(maxlen=64)
        self.framers = [Framer(self.packet) for _ in range(4)]
        self.taps = firwin(max(17, int(6*rate/baud) | 1), min(.75*baud, rate*.45), fs=rate)
        self.filter_state = np.zeros(len(self.taps)-1)
        self.dc_state = np.zeros(1)
        self.pole = math.exp(-2*math.pi*5/rate)
        self.clocks = [SymbolClock(rate, baud, phase/4, framer.feed, (len(self.taps)-1)/2)
                       for phase, framer in enumerate(self.framers)]

    def packet(self, payload, when):
        if any(data == payload and abs(when-time) < .01 for data, time in self.recent):
            self.duplicates += 1
            return
        self.recent.append((payload, when))
        self.packets += 1
        self.packet_sink(payload)
        self.images.packet(payload, when)
        if not self.images.image_packets:
            self.report(reception='Valid Geoscan packets received; waiting for RS61 image fragments')
        self.report(packets=self.packets, bad_crc=sum(f.bad_crc for f in self.framers),
                    sync_candidates=sum(f.syncs for f in self.framers),
                    duplicate_candidates=self.duplicates)

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim != 1 or not np.all(np.isfinite(samples)):
            raise ValueError('Expected finite mono FM-discriminator audio')
        if not len(samples):
            return
        dc, self.dc_state = lfilter([1, -1], [1, -self.pole], samples, zi=self.dc_state)
        filtered, self.filter_state = lfilter(self.taps, [1], dc, zi=self.filter_state)
        for clock in self.clocks:
            clock.push(filtered)
        before = self.samples // self.rate
        self.samples += len(samples)
        if self.samples // self.rate != before:
            self.report(packets=self.packets, bad_crc=sum(f.bad_crc for f in self.framers),
                        sync_candidates=sum(f.syncs for f in self.framers))

    def finish(self):
        self.push(np.zeros(len(self.taps)+128))
        self.images.finish()
        self.report(packets=self.packets, bad_crc=sum(f.bad_crc for f in self.framers),
                    sync_candidates=sum(f.syncs for f in self.framers))
