# SPDX-License-Identifier: GPL-3.0-or-later
"""RS61 Geoscan framing: 9600-bit/s NRZ, PN9, 72-byte payload + CRC.

Parameters verified against gr-satellites and Geoscan's 3U protocol.
Image packet layout verified against SatsDecoder. See THIRD_PARTY.md.
"""
import struct

SYNC = 0x930B51DE
FRAME_SIZE = 74
SATELLITE_ID = 9
IMAGE_MARKER = 0x6F6B6F31


def crc16(data):
    """CC11xx CRC: polynomial 0x8005, initial 0xffff, MSB first, no xorout."""
    value = 0xffff
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x8005 if value & 0x8000 else 0)) & 0xffff
    return value


def pn9_mask(length):
    state = 0x1ff
    mask = bytearray()
    for _ in range(length):
        octet = 0
        for bit in range(8):
            octet |= (state & 1) << bit
            feedback = ((state >> 5) ^ state) & 1
            state = (state >> 1) | (feedback << 8)
        mask.append(octet)
    return bytes(mask)


MASK = pn9_mask(FRAME_SIZE)


class Framer:
    """Bounded synchronization candidates; accept only CRC-validated packets."""
    def __init__(self, emit):
        self.emit = emit
        self.shift = 0
        self.bits_seen = 0
        self.candidates = []
        self.bad_crc = 0
        self.syncs = 0

    def feed(self, value, when):
        bit = int(value >= 0)
        remaining = []
        for polarity, bits in self.candidates:
            bits.append(bit ^ polarity)
            if len(bits) == FRAME_SIZE * 8:
                raw = bytes(sum(bits[i+j] << (7-j) for j in range(8))
                            for i in range(0, len(bits), 8))
                frame = bytes(a ^ b for a, b in zip(raw, MASK))
                if crc16(frame[:-2]) == int.from_bytes(frame[-2:], 'big'):
                    self.emit(frame[:-2], when)
                else:
                    self.bad_crc += 1
            else:
                remaining.append((polarity, bits))
        self.candidates = remaining
        self.shift = ((self.shift << 1) | bit) & 0xffffffff
        self.bits_seen += 1
        if self.bits_seen >= 32:
            distance = (self.shift ^ SYNC).bit_count()
            if distance <= 4 or distance >= 28:
                self.syncs += 1
                if len(self.candidates) < 8:
                    self.candidates.append((int(distance >= 28), []))


def image_fragment(packet):
    """Return (file number, byte offset, JPEG fragment), or None for telemetry."""
    if len(packet) != 72 or packet[0] != SATELLITE_ID:
        return None
    marker, offset, number = struct.unpack_from('<IIH', packet, 5)
    if marker != IMAGE_MARKER:
        return None
    return number, offset, packet[15:69]


def kiss_packets(stream):
    """Read bounded KISS data frames; recover cleanly after malformed escapes."""
    packet = bytearray()
    escaped = False
    invalid = False
    started = False
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return
        for byte in chunk:
            if byte == 0xc0:
                if started and packet and not invalid and not escaped and packet[0] & 15 == 0:
                    yield bytes(packet[1:])
                packet.clear()
                escaped = invalid = False
                started = True
            elif started and not invalid:
                if escaped:
                    escaped = False
                    if byte not in (0xdc, 0xdd):
                        invalid = True
                        continue
                    packet.append(0xc0 if byte == 0xdc else 0xdb)
                elif byte == 0xdb:
                    escaped = True
                else:
                    packet.append(byte)
                if len(packet) > 4096:
                    invalid = True
