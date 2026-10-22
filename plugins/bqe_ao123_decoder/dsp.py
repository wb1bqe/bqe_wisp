# SPDX-License-Identifier: GPL-3.0-or-later
"""Streaming 9600-symbol/s BPSK modem for wide linear audio or stereo I/Q."""
import math
import numpy as np
from scipy.signal import firwin, lfilter
from plugins.bqe_tlm_decoder.dsp import SymbolClock
from .protocol import SymbolDecoder


class AudioDecoder:
    def __init__(self, sample_rate, emit, options=None):
        options = options or {}
        self.rate = sample_rate
        self.input_format = options.get('input_format', 'audio')
        self.carrier = float(options.get('carrier', 12000))
        if self.input_format not in ('audio', 'iq'):
            raise ValueError('Input format must be audio or iq')
        if sample_rate < 38400 or sample_rate > 192000:
            raise ValueError('Use 38400–192000 Hz input (48000 recommended); 9600 BPSK needs wideband input')
        if not math.isfinite(self.carrier) or (self.input_format == 'audio' and
                not 6500 <= self.carrier <= sample_rate / 2 - 6500):
            raise ValueError('Audio carrier must leave at least 6500 Hz on each side; use 12000 Hz at 48000 Hz')
        self.bauds, self.skipped_bauds = [9600], []
        self.samples = 0
        self.phase = 0.0
        self.frequency = 0.0
        self.alpha, self.beta = .035, .0006
        self.taps = firwin(81, 6500, fs=sample_rate)
        self.state = np.zeros(len(self.taps)-1, dtype=complex)
        self.symbols = SymbolDecoder(emit)
        self.clock = SymbolClock(sample_rate, 9600, .25, self.symbols.feed, (len(self.taps)-1)/2)

    def select_samples(self, block, channel):
        if self.input_format == 'iq':
            if block.shape[1] != 2:
                raise ValueError('I/Q input requires exactly two channels: I then Q')
            return block[:, 0] + 1j * block[:, 1]
        if not 0 <= channel < block.shape[1]:
            raise ValueError('Selected audio channel is unavailable')
        return block[:, channel]

    def push(self, samples):
        data = np.asarray(samples)
        if data.ndim != 1 or not np.isfinite(data).all():
            raise ValueError('Expected finite audio or complex I/Q samples')
        if not len(data):
            return
        if self.input_format == 'audio':
            indexes = self.samples + np.arange(len(data))
            data = 2*data*np.exp(-2j*np.pi*self.carrier/self.rate*indexes)
        filtered, self.state = lfilter(self.taps, [1], data, zi=self.state)
        output = np.empty(len(filtered))
        phase, frequency = self.phase, self.frequency
        for index, value in enumerate(filtered):
            rotated = value * complex(math.cos(phase), -math.sin(phase))
            # BPSK Costas detector, normalized for receiver gain variation.
            error = (1 if rotated.real >= 0 else -1) * rotated.imag / max(abs(rotated), 1e-6)
            frequency = max(-.12, min(.12, frequency + self.beta*error))
            phase = (phase + frequency + self.alpha*error) % (2*math.pi)
            output[index] = rotated.real
        self.phase, self.frequency = phase, frequency
        self.clock.push(output)
        self.samples += len(data)

    def finish(self):
        # Flush channel/clock delay and the Viterbi lookahead, not a partial packet.
        self.push(np.zeros(round(self.rate*.30), dtype=complex if self.input_format == 'iq' else float))

    def diagnostics(self):
        return dict(self.symbols.diagnostics(), carrier_offset_hz=round(self.frequency*self.rate/(2*math.pi), 1),
                    input_format=self.input_format)
