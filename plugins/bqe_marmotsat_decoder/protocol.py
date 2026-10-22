# SPDX-License-Identifier: GPL-3.0-or-later
"""Uncoded OpenLST framing. Protocol references are in README.md."""

SYNC_WORD = 0xD391D391
BAUD = 10170


def crc16(data):
    """CC1110 software CRC: polynomial 0x8005, init FFFF, no reflection/xor."""
    crc = 0xFFFF
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x8005 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def pn9(length):
    """TI whitening bytes; reset to all ones at the length byte."""
    register = 0x1FF
    output = bytearray()
    for _ in range(length):
        output.append(register & 0xFF)
        for _ in range(8):
            feedback = (register ^ (register >> 5)) & 1
            register = (register >> 1) | (feedback << 8)
    return bytes(output)


WHITENING = pn9(256)


def parse_frame(frame):
    """Accept a dewhitened length-through-CRC frame, preserving unknown data."""
    if len(frame) < 10 or frame[0] != len(frame) - 1:
        raise ValueError('Invalid OpenLST packet length')
    if crc16(frame[:-2]) != int.from_bytes(frame[-2:], 'little'):
        raise ValueError('Invalid OpenLST software CRC')
    payload = frame[6:-4]
    return dict(protocol='OpenLST uncoded PN9', validation='CRC-16/0x8005',
                flags=frame[1], sequence=int.from_bytes(frame[2:4], 'little'),
                system=frame[4], command=frame[5],
                hardware_id=f'{int.from_bytes(frame[-4:-2], "little"):04X}',
                frame_hex=frame.hex(), payload_hex=payload.hex(),
                payload_text=''.join(chr(b) if 32 <= b < 127 else '.' for b in payload))


class Deframer:
    """MSB-first bits, either polarity, with bounded overlapping candidates."""
    def __init__(self, emit, sync_word=SYNC_WORD):
        if not isinstance(sync_word, int) or not 0 <= sync_word <= 0xFFFFFFFF:
            raise ValueError('Sync word must be a 32-bit integer')
        self.emit, self.sync_word = emit, sync_word
        self.shift = self.seen = 0
        self.candidates = []
        self.syncs = self.bad_crc = self.bad_length = self.truncated = 0

    def feed(self, sample, when):
        bit = int(sample >= 0)
        remaining = []
        for candidate in self.candidates:
            candidate['byte'] = (candidate['byte'] << 1) | (bit ^ candidate['invert'])
            candidate['bits'] += 1
            if candidate['bits'] < 8:
                remaining.append(candidate)
                continue
            frame = candidate['frame']
            frame.append(candidate['byte'] ^ WHITENING[len(frame)])
            candidate['byte'] = candidate['bits'] = 0
            if len(frame) == 1 and frame[0] < 9:
                self.bad_length += 1
            elif len(frame) == frame[0] + 1:
                try:
                    packet = parse_frame(bytes(frame))
                except ValueError:
                    self.bad_crc += 1
                else:
                    packet['inverted'] = bool(candidate['invert'])
                    self.emit(packet, when)
            else:
                remaining.append(candidate)
        self.candidates = remaining
        self.shift = ((self.shift << 1) | bit) & 0xFFFFFFFF
        self.seen = min(32, self.seen + 1)
        if self.seen == 32 and self.shift in (self.sync_word, self.sync_word ^ 0xFFFFFFFF):
            self.syncs += 1
            self.candidates.append(dict(byte=0, bits=0, frame=bytearray(),
                                        invert=int(self.shift != self.sync_word)))
            # Continue searching even when a damaged length claims a long frame.
            self.candidates = self.candidates[-4:]

    def finish(self):
        self.truncated += len(self.candidates)
        self.candidates.clear()
