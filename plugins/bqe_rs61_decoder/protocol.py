# SPDX-License-Identifier: GPL-3.0-or-later
"""Geoscan 3U v2.7 Type-I beacon; offsets include the 16-byte AX.25 header."""
from datetime import datetime, timezone
import struct


def parse_packet(frame, when=0, validation='CC11xx CRC-16'):
    """None for images/foreign frames; unknown RS61 formats remain raw.

    CRC checking belongs to the Geoscan framer. KISS callers must explicitly
    mark their packets as imported, since KISS contains no radio CRC.
    """
    if len(frame) != 72:
        return None
    # RS61's public captures null-pad the sixth source character. Accept that
    # and standard space padding here without weakening generic AX.25 parsing.
    if frame[:7] != bytes.fromhex('848a82869e9c60'):
        return None
    if frame[7:12] != bytes(ord(c) << 1 for c in 'RS61S') or frame[12] not in (0, 0x40):
        return None
    if frame[13] != 0xe1 or frame[14:16] != b'\x03\xf0':
        return None
    payload = frame[16:]
    fields = {}
    parsed = payload[0] == 1
    if parsed:
        def u16(offset):
            return struct.unpack_from('<H', frame, offset)[0]
        def signed(offset):
            return struct.unpack_from('b', frame, offset)[0]
        timestamp = struct.unpack_from('<I', frame, 17)[0]
        fields = {
            'Spacecraft time (UTC)': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            'Spacecraft time (Unix seconds)': timestamp,
            'EPS mode (raw enum)': frame[21],
            'Platform current (mA)': u16(23),
            'Solar panel current (mA)': u16(25),
            'Single battery voltage (mV)': u16(27),
            'Total battery voltage (mV)': u16(29),
            'Battery 1 temperature (C)': signed(33),
            'Battery 2 temperature (C)': signed(34),
            'OBC activity bitfield (raw)': frame[42],
        }
        # Operator v2.7 assigns bit 0 to flywheels, bit 7 to panel Y-.
        # Some third-party parsers reverse this order; follow the operator.
        for bit, name in enumerate(('Flywheels', 'Coils', 'INS', 'Camera',
                                    'Panel X+', 'Panel X-', 'Panel Y+', 'Panel Y-')):
            fields[name + ' active'] = bool(frame[42] & (1 << bit))
        for offset, axis in zip(range(43, 47), ('X+', 'X-', 'Y+', 'Y-')):
            fields['Panel ' + axis + ' temperature (C)'] = signed(offset)
        fields.update({
            'GNSS satellites': frame[47], 'Camera media files': frame[50],
            'VBUS voltage (mV)': u16(57),
            'Last received packet RSSI (dBm)': signed(61),
            'Minimum RSSI (dBm)': signed(62),
            'Transmitted packets': frame[65], 'Received QSOs': frame[69],
        })
    record = dict(record_type='type_i' if parsed else 'unknown',
                  name='Geoscan 3U Type-I beacon' if parsed else 'Unknown beacon format',
                  parse_status='parsed' if parsed else 'unknown', fields=fields,
                  raw_hex=payload.hex(), beacon_id=payload[0])
    return dict(protocol='Geoscan 3U', validation=validation, source='RS61S',
                destination='BEACON', via=[], control=3, pid=0xf0, baud=9600,
                satellite={'name': '239ALFEROV', 'callsign': 'RS61S', 'norad': 64881},
                audio_seconds=when, frame_hex=frame.hex(), payload_hex=payload.hex(),
                telemetry=[record], record_type=record['record_type'],
                parse_status=record['parse_status'], unparsed_hex='' if parsed else payload.hex())
