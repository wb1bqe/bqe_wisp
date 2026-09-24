# SPDX-License-Identifier: GPL-3.0-or-later
"""Streaming FM-discriminator-audio modem. No I/Q assumptions or fixed pitch."""
from collections import deque
import math

import numpy as np
from scipy.signal import firwin, lfilter

from .protocols import G3RUH, USP, USPFEC, ax25_header, telemetry_records


class SymbolClock:
    """Fractional-sample Gardner clock with bounded sample-clock tracking."""
    def __init__(self, sample_rate, baud, phase, emit, delay):
        self.nominal = sample_rate / baud
        self.omega = self.nominal
        self.next = self.nominal * (1 + phase)
        self.total = 0
        self.tail = np.empty(0)
        self.previous = 0.0
        self.energy = 0.01
        self.emit = emit
        self.sample_rate = sample_rate
        self.delay = delay

    def push(self, samples):
        data = np.concatenate((self.tail, samples))
        base = self.total - len(self.tail)
        end = base + len(data) - 1
        while self.next < end:
            position = self.next - base
            index = int(position)
            fraction = position - index
            value = float(data[index] * (1 - fraction) + data[index + 1] * fraction)
            middle = position - self.omega / 2
            mid_index = int(middle)
            mid_fraction = middle - mid_index
            if mid_index < 0:
                self.next += self.omega
                continue
            mid = float(data[mid_index] * (1 - mid_fraction) + data[mid_index + 1] * mid_fraction)
            self.energy = 0.98 * self.energy + 0.02 * value * value
            error = max(-1.0, min(1.0, (self.previous - value) * mid / max(self.energy, 1e-12)))
            self.omega = max(self.nominal * 0.997, min(self.nominal * 1.003,
                            self.omega + self.nominal * 0.0001 * error))
            self.emit(value, max(0, (self.next - self.delay) / self.sample_rate))
            self.previous = value
            self.next += self.omega + self.nominal * 0.035 * error
        self.total += len(samples)
        self.tail = data[-math.ceil(3 * self.nominal + 4):].copy()


class RateDecoder:
    def __init__(self, sample_rate, baud, protocol, fec, emit):
        self.baud = baud
        taps = max(17, int(6 * sample_rate / baud) | 1)
        self.taps = firwin(taps, min(0.75 * baud, sample_rate * 0.45), fs=sample_rate)
        self.state = np.zeros(taps - 1)
        self.decoders = []
        self.clocks = []
        for phase in (0.0, 0.25, 0.5, 0.75):
            chains = []
            if protocol in ('auto', 'ax25'):
                chains.append(G3RUH(lambda frame, when, meta: emit(frame, when, baud, meta)))
            if protocol in ('auto', 'usp'):
                chains.append(USP(lambda frame, when, meta: emit(frame, when, baud, meta), fec))
            self.decoders.extend(chains)

            def feed(value, when, chains=chains):
                for chain in chains:
                    chain.feed(value, when)

            self.clocks.append(SymbolClock(sample_rate, baud, phase, feed, (taps - 1) / 2))

    def push(self, samples):
        filtered, self.state = lfilter(self.taps, [1], samples, zi=self.state)
        for clock in self.clocks:
            clock.push(filtered)


class AudioDecoder:
    """One instance per capture/file. Buffers, clocks, and frames span chunks."""
    def __init__(self, sample_rate, emit, bauds=(1200, 2400, 4800, 9600), protocol='auto'):
        if sample_rate < 8000 or not bauds or protocol not in ('auto', 'ax25', 'usp'):
            raise ValueError('Unsupported sample rate, baud list, or protocol')
        if any(baud not in (1200, 2400, 4800, 9600) for baud in bauds):
            raise ValueError('Baud must be 1200, 2400, 4800, or 9600')
        self.sample_rate = sample_rate
        self.emit = emit
        self.samples = 0
        self.recent = deque(maxlen=128)
        self.duplicates = 0
        self.rejected_headers = 0
        self.dc_state = np.zeros(1)
        self.dc_pole = math.exp(-2 * math.pi * 5 / sample_rate)
        self.fec = USPFEC() if protocol != 'ax25' else None
        self.bauds = [baud for baud in dict.fromkeys(bauds) if sample_rate >= baud * 3]
        self.skipped_bauds = [baud for baud in bauds if baud not in self.bauds]
        if not self.bauds:
            raise ValueError('Input sample rate is too low for the selected baud rates')
        self.rates = [RateDecoder(sample_rate, baud, protocol, self.fec, self._packet) for baud in self.bauds]

    def _packet(self, frame, when, baud, metadata):
        try:
            header = ax25_header(frame)
        except ValueError:
            self.rejected_headers += 1
            return
        if any(old == frame and old_baud == baud and abs(when - end) <= max(0.002, 2 / baud)
               for old, end, old_baud in self.recent):
            self.duplicates += 1
            return
        self.recent.append((frame, when, baud))
        payload = header.pop('payload')
        records, tail = telemetry_records(payload) if header['pid'] == 0xF0 else ([], payload.hex())
        self.emit(dict(metadata, **header, baud=baud, audio_seconds=round(when, 6),
                       frame_hex=frame.hex(), payload_hex=payload.hex(),
                       payload_text=''.join(chr(b) if 32 <= b < 127 else '.' for b in payload),
                       telemetry=records, unparsed_hex=tail))

    def push(self, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError('Expected finite mono FM audio samples')
        if not len(samples):
            return
        filtered, self.dc_state = lfilter([1, -1], [1, -self.dc_pole], samples, zi=self.dc_state)
        for rate in self.rates:
            rate.push(filtered)
        self.samples += len(samples)

    def finish(self):
        # Flush FIR group delay and any last closing flag at file EOF.
        self.push(np.zeros(max(len(rate.taps) for rate in self.rates) + 128))

    def diagnostics(self):
        return dict(duplicate_candidates=self.duplicates, rejected_headers=self.rejected_headers,
                    bad_crc=sum(getattr(d, 'hdlc', None).bad_crc for r in self.rates
                                for d in r.decoders if isinstance(d, G3RUH)),
                    bad_fec=sum(d.bad_fec for r in self.rates for d in r.decoders if isinstance(d, USP)))
