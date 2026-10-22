# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental OTP-2 UHF profile; CRC proves framing, not spacecraft identity."""
from plugins.bqe_tlm_decoder.dsp import AudioDecoder as BaseDecoder, RateDecoder, SymbolClock
from plugins.bqe_tlm_decoder.protocols import HDLC, ax25_header


class LineDecoder:
    def __init__(self, mode, emit, invert=False):
        self.mode, self.invert = mode, invert
        self.previous = self.register = 0
        self.hdlc = HDLC(lambda frame, when, meta: emit(
            frame, when, dict(meta, protocol='AX.25 ' + mode)))

    def descramble(self, bit):
        decoded = bit ^ ((self.register >> 16) & 1) ^ ((self.register >> 11) & 1)
        self.register = ((self.register << 1) | bit) & 0x1ffff
        return decoded

    def feed(self, sample, when):
        bit = int(sample >= 0) ^ self.invert
        if self.mode != 'NRZ':
            bit, self.previous = int(bit == self.previous), bit
        if self.mode == 'NRZI then G3RUH':
            bit = self.descramble(bit)
        self.hdlc.feed(bit, when)


class SearchRate(RateDecoder):
    def __init__(self, sample_rate, baud, emit):
        super().__init__(sample_rate, baud, 'ax25', None, emit)
        self.decoders, self.clocks = [], []
        for phase in (0, .25, .5, .75):
            chains = []
            # Descrambling and differential decoding commute after acquisition;
            # testing both orders would duplicate the same frame candidates.
            for mode in ('NRZ', 'NRZI', 'NRZI then G3RUH'):
                for invert in ((False, True) if mode == 'NRZ' else (False,)):
                    chains.append(LineDecoder(mode, lambda frame, when, meta:
                                              emit(frame, when, baud, meta), invert))
            self.decoders.extend(chains)

            def feed(value, when, chains=chains):
                for chain in chains:
                    chain.feed(value, when)

            self.clocks.append(SymbolClock(sample_rate, baud, phase, feed, (len(self.taps) - 1) / 2))


class AudioDecoder(BaseDecoder):
    def __init__(self, sample_rate, emit, options):
        if options.get('protocol', 'ax25') != 'ax25':
            raise ValueError('OTP-2 profile requires AX.25')
        super().__init__(sample_rate, emit, options.get('bauds', [4800]), 'ax25')
        self.rates = [SearchRate(sample_rate, baud, self._packet) for baud in self.bauds]

    def _packet(self, frame, when, baud, metadata):
        try:
            header = ax25_header(frame)
        except ValueError:
            self.rejected_headers += 1
            return
        if any(old == frame and old_baud == baud and abs(when - end) <= max(.002, 2 / baud)
               for old, end, old_baud in self.recent):
            self.duplicates += 1
            return
        self.recent.append((frame, when, baud))
        payload = header.pop('payload')
        self.emit(dict(metadata, **header, baud=baud, audio_seconds=round(when, 6),
                       frame_hex=frame.hex(), payload_hex=payload.hex(),
                       payload_text=''.join(chr(b) if 32 <= b < 127 else '.' for b in payload),
                       telemetry=[], unparsed_hex=payload.hex(), parse_status='raw',
                       satellite_identity='unverified', profile='OTP-2 UHF (experimental)'))

    def diagnostics(self):
        return dict(duplicate_candidates=self.duplicates, rejected_headers=self.rejected_headers,
                    bad_crc=sum(d.hdlc.bad_crc for r in self.rates for d in r.decoders),
                    payload_parser='unavailable', satellite_identity='unverified')
