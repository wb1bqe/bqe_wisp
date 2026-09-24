# SPDX-License-Identifier: GPL-3.0-or-later
"""AX.25/G3RUH and USP framing, independent of the audio source.

USP parameters/PLS handling follow gr-satellites (Daniel Estevez, 2021).
CCSDS basis conversion follows Phil Karn's libfec (2002, LGPL).
See THIRD_PARTY.md for source attribution and protocol references.
"""
import struct

import numpy as np
import reedsolo


def crc_x25(data):
    crc = 0xFFFF
    for octet in data:
        crc ^= octet
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xFFFF


def ax25_header(frame):
    """Decode addresses/control without inventing a satellite identity."""
    addresses = []
    offset = 0
    for _ in range(10):
        if offset + 7 > len(frame):
            raise ValueError("truncated AX.25 address")
        raw = frame[offset:offset + 7]
        if any(b & 1 for b in raw[:6]) or raw[6] & 0x60 != 0x60:
            raise ValueError("invalid AX.25 address encoding")
        call = ''.join(chr(b >> 1) for b in raw[:6]).rstrip()
        if not call or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' for c in call):
            raise ValueError("invalid AX.25 callsign")
        ssid = (raw[6] >> 1) & 15
        addresses.append(call + (f'-{ssid}' if ssid else ''))
        offset += 7
        if raw[6] & 1:
            break
    else:
        raise ValueError("too many AX.25 addresses")
    if len(addresses) < 2 or offset >= len(frame):
        raise ValueError("incomplete AX.25 header")
    control = frame[offset]
    offset += 1
    pid = None
    # UI and modulo-8 I frames carry a protocol identifier.
    if control & 0xEF == 3 or not control & 1:
        if offset >= len(frame):
            raise ValueError("missing AX.25 PID")
        pid = frame[offset]
        offset += 1
    return dict(destination=addresses[0], source=addresses[1], via=addresses[2:],
                control=control, pid=pid, payload=frame[offset:])


def telemetry_records(payload):
    """Parse length-delimited SPUTNIX records; unknown fields remain raw.

    Known field layouts follow SatsDecoder (Alexander Baskikh, MIT).
    Only exact supported record lengths produce engineering-unit values.
    """
    records = []
    offset = 0
    names = {0x000E: 'Common power', 0x000F: 'X-axis power', 0x0010: 'Y-axis power',
             0x0011: 'Z-axis power', 0x4246: 'UHF beacon'}
    while offset + 8 <= len(payload):
        message, sender, receiver, size = struct.unpack_from('<4H', payload, offset)
        if size > len(payload) - offset - 8 or size == 0:
            break
        body = payload[offset + 8:offset + 8 + size]
        record = dict(message_id=f'0x{message:04X}', sender=sender, receiver=receiver,
                      name=names.get(message, 'Unknown record'), raw_hex=body.hex(), fields={})
        fields = record['fields']
        if message in (0x000E, 0x000F, 0x0010, 0x0011) and size == 5:
            current, voltage, flags = struct.unpack('<hhB', body)
            fields.update(current_mA=None if flags & 1 else current,
                          voltage_V=None if flags & 2 else voltage / 1000, flags=flags)
        elif message == 0x4246 and size == 25:
            amp, radio, rssi, idle, forward, reflected, resets, flags = struct.unpack_from('<bbBBbbBB', body)
            timestamp, uptime, rate, current, voltage, schedules = struct.unpack_from('<iIIHhB', body, 8)
            fields.update(amplifier_temperature_C=amp, radio_temperature_C=radio,
                          receive_RSSI_dBm=-rssi, idle_RSSI_dBm=-idle,
                          forward_power_dBm=forward, reflected_power_dBm=reflected,
                          reset_count=resets, flags=flags, satellite_unix_time=timestamp,
                          uptime_seconds=uptime, uplink_baud=rate, current_mA=current,
                          voltage_V=voltage / 1000, schedule_flags=schedules)
        records.append(record)
        offset += 8 + size
    # Partial/unrecognized data is kept, never guessed or silently discarded.
    return records, payload[offset:].hex()


class HDLC:
    """Incremental flag recognition, bit unstuffing, and CRC validation."""
    def __init__(self, emit):
        self.emit = emit
        self.shift = 0
        self.bits = None
        self.bad_crc = 0

    def feed(self, bit, when):
        self.shift = ((self.shift >> 1) | (int(bit) << 7)) & 255
        if self.bits is not None:
            self.bits.append(int(bit))
        if self.shift == 0x7E:
            raw = self.bits[:-8] if self.bits is not None else []
            self.bits = []
            if len(raw) < 18 * 8:
                return
            output = bytearray()
            octet = count = ones = 0
            for value in raw:
                if ones == 5:
                    if value:
                        return  # abort or malformed bit stuffing
                    ones = 0
                    continue
                ones = ones + 1 if value else 0
                octet |= value << count
                count += 1
                if count == 8:
                    output.append(octet)
                    octet = count = 0
            if count or len(output) < 18:
                return
            if crc_x25(output[:-2]) != int.from_bytes(output[-2:], 'little'):
                self.bad_crc += 1
                return
            self.emit(bytes(output[:-2]), when, {'protocol': 'AX.25 G3RUH', 'validation': 'CRC-16/X-25'})
        elif self.bits is not None and len(self.bits) > 33000:
            self.bits = None


class G3RUH:
    def __init__(self, emit):
        self.previous = 0
        self.register = 0
        self.hdlc = HDLC(emit)

    def feed(self, sample, when):
        bit = int(sample >= 0)
        nrzi = int(bit == self.previous)
        self.previous = bit
        decoded = nrzi ^ ((self.register >> 16) & 1) ^ ((self.register >> 11) & 1)
        self.register = ((self.register << 1) | nrzi) & 0x1FFFF
        self.hdlc.feed(decoded, when)


USP_SYNC = 0x5072F64B2D90B1F5
PLS = (0x719D83C953422DFA, 0x24C8D69C061778AF)


def ccsds_randomizer(length):
    register = 0xFF
    values = bytearray()
    for _ in range(length):
        octet = 0
        for _ in range(8):
            octet = (octet << 1) | (register & 1)
            feedback = (register & 0xA9).bit_count() & 1
            register = (register >> 1) | (feedback << 7)
        values.append(octet)
    return bytes(values)


# Matrix columns from the CCSDS conventional-to-dual-basis conversion.
_columns = (0x7B, 0xAF, 0x99, 0xFA, 0x86, 0xEC, 0xEF, 0x8D)
TO_DUAL = bytearray(256)
FROM_DUAL = bytearray(256)
for _value in range(256):
    _dual = 0
    for _bit, _column in enumerate(_columns):
        if _value & (1 << _bit):
            _dual ^= _column
    TO_DUAL[_value] = _dual
    FROM_DUAL[_dual] = _value
TO_DUAL, FROM_DUAL = bytes(TO_DUAL), bytes(FROM_DUAL)


class USPFEC:
    """Soft Viterbi K=7/rate-1/2, CCSDS whitening, dual-basis RS(255,223)."""
    def __init__(self):
        # alpha**11 = 173 in GF(256), primitive polynomial x^8+x^7+x^2+x+1.
        self.rs = reedsolo.RSCodec(32, nsize=255, fcr=112, prim=0x187, generator=173)
        states = np.arange(64, dtype=np.int32)
        self.previous = np.stack((states >> 1, (states >> 1) | 32))
        registers = (self.previous << 1) | (states & 1)
        self.expected = np.array([[[1 if (int(r) & 0x4F).bit_count() & 1 else -1,
                                    -1 if (int(r) & 0x6D).bit_count() & 1 else 1]
                                   for r in row] for row in registers], dtype=np.float64)

    def viterbi(self, symbols):
        pairs = np.asarray(symbols, dtype=np.float64).reshape(-1, 2)
        metrics = np.full(64, -1e20)
        metrics[0] = 0
        choices = np.empty((len(pairs), 64), dtype=np.uint8)
        for index, pair in enumerate(pairs):
            score = metrics[self.previous] + (self.expected * pair).sum(axis=2)
            selected = score[1] > score[0]
            choices[index] = selected
            metrics = np.maximum(score[0], score[1])
            metrics -= metrics.max()
        state = int(np.argmax(metrics))  # truncated, not zero-terminated
        bits = np.empty(len(pairs), dtype=np.uint8)
        for index in range(len(pairs) - 1, -1, -1):
            bits[index] = state & 1
            state = int(self.previous[choices[index, state], state])
        return np.packbits(bits).tobytes()

    def decode(self, symbols):
        encoded = self.viterbi(symbols)
        randomized = bytes(a ^ b for a, b in zip(encoded, ccsds_randomizer(len(encoded))))
        # Leading shortening zeros are handled by reedsolo's shortened codeword.
        message, _, errors = self.rs.decode(randomized.translate(FROM_DUAL))
        message = bytes(message).translate(TO_DUAL)
        if len(message) < 4:
            raise ValueError('short USP transfer frame')
        length = int.from_bytes(message[2:4], 'little')
        if not 16 <= length <= len(message) - 4:
            raise ValueError('invalid USP encapsulated frame length')
        return message[4:4 + length], len(errors)


class USP:
    def __init__(self, emit, fec):
        self.emit = emit
        self.fec = fec
        self.shift = 0
        self.count = 0
        self.pending = []
        self.bad_fec = 0

    def feed(self, sample, when):
        bit = int(sample >= 0)
        self.shift = ((self.shift << 1) | bit) & ((1 << 64) - 1)
        self.count += 1
        keep = []
        for candidate in self.pending:
            candidate['soft'].append(float(sample) * candidate['polarity'])
            values = candidate['soft']
            if len(values) == 64:
                hard = int.from_bytes(np.packbits(np.asarray(values) >= 0).tobytes(), 'big')
                distances = [(hard ^ code).bit_count() for code in PLS]
                code = int(np.argmin(distances))
                if distances[code] > 12:
                    continue
                candidate['length'] = 64 + (80 if code == 0 else 255) * 16
            if len(values) == candidate['length']:
                try:
                    frame, corrected = self.fec.decode(values[64:])
                    # A validated RS codeword may encapsulate an AX.25 FCS.
                    if len(frame) >= 18 and crc_x25(frame[:-2]) == int.from_bytes(frame[-2:], 'little'):
                        frame = frame[:-2]
                    self.emit(frame, when, {'protocol': 'USP', 'validation': 'Reed-Solomon verified',
                                            'rs_corrected_bytes': corrected})
                except (reedsolo.ReedSolomonError, ValueError, IndexError):
                    self.bad_fec += 1
                continue
            keep.append(candidate)
        self.pending = keep
        if self.count >= 64 and len(keep) < 4:
            errors = (self.shift ^ USP_SYNC).bit_count()
            if errors <= 6 or errors >= 58:
                self.pending.append({'soft': [], 'polarity': 1 if errors <= 6 else -1, 'length': 10**9})


def kiss_encode(frame):
    data = b'\x00' + frame
    return b'\xc0' + data.replace(b'\xdb', b'\xdb\xdd').replace(b'\xc0', b'\xdb\xdc') + b'\xc0'
