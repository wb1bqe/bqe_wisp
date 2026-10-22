# SPDX-License-Identifier: GPL-3.0-or-later
"""AO-123 CCSDS frames, differential precoding and TM short KISS transport.

Protocol reference: gr-satellites ASRTU-1.yml, by02.TMPrimaryHeader,
tm_kiss_transport, and asrtu.py (Daniel Estevez, GPL-3.0-or-later).
"""
from collections import deque
import numpy as np
import reedsolo
from plugins.bqe_tlm_decoder.protocols import USPFEC, ccsds_randomizer


class Transport:
    """Separate bounded KISS streams per spacecraft/VC, reset on frame loss."""
    def __init__(self, emit):
        self.emit = emit
        self.channels = {}
        self.frames = self.gaps = self.invalid = self.ignored = 0

    def push(self, frame, when, corrected=0):
        if len(frame) != 223:
            self.invalid += 1
            return
        flags = int.from_bytes(frame[:2], 'big')
        version, spacecraft, vc = flags >> 14, (flags >> 4) & 1023, (flags >> 1) & 7
        if version != 0 or vc not in (0, 2):
            self.ignored += 1
            return
        key = (spacecraft, vc)
        if key not in self.channels:
            if len(self.channels) >= 16:
                self.channels.pop(next(iter(self.channels)))
            self.channels[key] = dict(count=None, data=bytearray(), escape=False, ready=False, corrected=0)
        state = self.channels[key]
        count = frame[3]
        if state['count'] is not None and count != (state['count'] + 1) % 256:
            self.gaps += 1
            state.update(data=bytearray(), escape=False, ready=False, corrected=0)
        state['count'] = count
        state['corrected'] += corrected
        self.frames += 1
        # The mission's short TM header is 5 bytes. Its payload is a KISS
        # stream, with no KISS command/port byte. Match upstream on OCF handling.
        for value in frame[5:]:
            if value == 0xC0:
                if state['ready'] and state['data']:
                    packet = self.parse(bytes(state['data']), when, spacecraft, vc,
                                        frame[2], count, state['corrected'])
                    if packet is not None:
                        self.emit(packet)
                state.update(data=bytearray(), escape=False, ready=True, corrected=corrected)
            elif state['ready']:
                if state['escape']:
                    state['escape'] = False
                    if value not in (0xDC, 0xDD):
                        state.update(data=bytearray(), ready=False)
                        self.invalid += 1
                        continue
                    state['data'].append(0xC0 if value == 0xDC else 0xDB)
                elif value == 0xDB:
                    state['escape'] = True
                else:
                    state['data'].append(value)
                if len(state['data']) > 65542:
                    state.update(data=bytearray(), escape=False, ready=False)
                    self.invalid += 1

    def parse(self, packet, when, spacecraft, vc, master_count, vc_count, corrected):
        if len(packet) < 7:
            self.invalid += 1
            return None
        first, sequence, length = (int.from_bytes(packet[i:i+2], 'big') for i in (0, 2, 4))
        if first >> 13 != 0 or len(packet) != 7 + length:
            self.invalid += 1
            return None
        apid = first & 2047
        if apid == 2047:  # CCSDS idle packet
            self.ignored += 1
            return None
        fields = dict(apid=apid, packet_type=(first >> 12) & 1,
                      secondary_header=bool(first & 2048), sequence_flags=sequence >> 14,
                      sequence_count=sequence & 16383, payload_bytes=length + 1,
                      spacecraft_id=spacecraft, virtual_channel=vc,
                      master_frame_count=master_count, virtual_channel_frame_count=vc_count)
        return dict(protocol='AO-123 CCSDS', validation='Reed-Solomon verified; CCSDS length checked',
                    rs_corrected_bytes=corrected, satellite=dict(name='AO-123', norad=61781),
                    identity_basis='Configured ASRTU-1 protocol; spacecraft_id retained for verification',
                    source='AO-123', destination=f'APID {apid}', baud=9600,
                    audio_seconds=round(when, 6), frame_hex=packet.hex(), payload_hex=packet[6:].hex(),
                    record_type=f'CCSDS APID {apid}', parse_status='header_only',
                    telemetry=[dict(name='CCSDS space packet', parse_status='header_only', fields=fields,
                                    raw_hex=packet[6:].hex())])


class Deframer:
    def __init__(self, emit):
        self.emit = emit
        self.rs = reedsolo.RSCodec(32, nsize=255, fcr=112, prim=0x187, generator=173)
        self.whitening = ccsds_randomizer(255)
        self.previous = 0
        self.shift = self.count = self.bad_fec = 0
        self.pending = None

    def push(self, bits, times):
        for bit, when in zip(bits, times):
            decoded = int(bit) ^ self.previous
            self.previous = int(bit)
            if self.pending is not None:
                self.pending.append(decoded)
                if len(self.pending) == 2040:
                    raw = np.packbits(self.pending).tobytes()
                    self.pending = None
                    try:
                        message, _, errors = self.rs.decode(bytes(a ^ b for a, b in zip(raw, self.whitening)))
                        if len(message) != 223:
                            raise ValueError('Invalid RS length')
                        self.emit(bytes(message), float(when), len(errors))
                    except (reedsolo.ReedSolomonError, ValueError, IndexError):
                        self.bad_fec += 1
                continue
            self.shift = ((self.shift << 1) | decoded) & 0xffffffff
            self.count += 1
            if self.count >= 32 and (self.shift ^ 0x1ACFFC1D).bit_count() <= 2:
                self.pending = []


class SymbolDecoder:
    """Two convolutional pair alignments, bounded overlapping soft Viterbi.

    Discard 64 decoded bits at both window ends to avoid initialization/traceback
    artifacts. Differential decoding also removes the BPSK polarity ambiguity.
    """
    WINDOW = 640
    GUARD = 64

    def __init__(self, emit):
        self.fec = USPFEC()
        self.transport = Transport(emit)
        self.recent = deque(maxlen=16)
        self.duplicates = 0
        self.decoders = [Deframer(self._frame), Deframer(self._frame)]
        self.symbols = []
        self.times = []

    def _frame(self, frame, when, corrected):
        if any(old == frame and abs(when - time) < .04 for old, time in self.recent):
            self.duplicates += 1
            return
        self.recent.append((frame, when))
        self.transport.push(frame, when, corrected)

    def feed(self, value, when):
        self.symbols.append(value)
        self.times.append(when)
        if len(self.symbols) >= 2*self.WINDOW + 1:
            for offset, decoder in enumerate(self.decoders):
                decoded = self.fec.viterbi(self.symbols[offset:offset + 2*self.WINDOW])
                bits = np.unpackbits(np.frombuffer(decoded, dtype=np.uint8))
                start, end = self.GUARD, self.WINDOW - self.GUARD
                decoder.push(bits[start:end], self.times[offset+2*start:offset+2*end:2])
            advance = 2*(self.WINDOW - 2*self.GUARD)
            del self.symbols[:advance]
            del self.times[:advance]

    def diagnostics(self):
        return dict(transfer_frames=self.transport.frames, bad_fec=sum(d.bad_fec for d in self.decoders),
                    duplicate_candidates=self.duplicates, transport_gaps=self.transport.gaps,
                    malformed_packets=self.transport.invalid, ignored_frames=self.transport.ignored)
