"""HADES-SA framing and telemetry. AMSAT-EA reference: see THIRD_PARTY.md.

Bytes are sent MSB first; packed C integers are little endian. The scrambler
processes bits 7..1, leaving bit 0 untouched, exactly as the operator's code
and ten published telemetry examples demonstrate. CRC covers scrambled data.
"""
from binascii import crc_hqx
import struct

SIZES = {1: 31, 2: 17, 3: 41, 4: 35, 5: 27, 8: 31, 9: 123,
         12: 64, 14: 38, 15: 73}
NAMES = {1: 'Power', 2: 'Temperatures', 3: 'Status', 4: 'Power ranges',
         5: 'Temperature ranges', 8: 'Antenna deployment', 9: 'Extended power',
         12: 'Ephemerides', 14: 'Time series', 15: 'BBS'}
TEMPS = ('tpa', 'tpb', 'tpc', 'tpd', 'tpe', 'teps', 'ttx', 'ttx2', 'trx', 'tcpu')


def descramble(data):
    state = 0x2C350000
    output = bytearray()
    for octet in data:
        value = octet
        for shift in range(7, 0, -1):
            bit = (octet >> shift) & 1
            value ^= (((state >> 16) ^ (state >> 11)) & 1) << shift
            state = ((state << 1) | bit) & 0x1FFFF
        output.append(value)
    return bytes(output)


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def telemetry_fields(frame):
    """Decode a validated, descrambled packet using the operator's layouts."""
    kind = frame[0] >> 4
    data = frame[1:-2]
    fields = {}
    u16 = lambda offset: int.from_bytes(data[offset:offset + 2], 'little')
    u32 = lambda offset: int.from_bytes(data[offset:offset + 4], 'little')
    if kind in (1, 2, 3, 4, 5, 14):
        fields['System clock (s)'] = u32(0)
    if kind == 1:
        for name, value in zip(('spa', 'spb', 'spc', 'spd'), data[4:8]):
            fields[name + ' (mW)'] = value * 2
        fields['spi (mW)'] = u16(8) * 2
        a, b, c, d, e, f, g = struct.unpack_from('<7H', data, 10)
        adc = ((b & 255) << 4) | (c >> 12)
        fields.update({'vbus1 (mV)': (a >> 4) * 1400 // 1000,
                       'vbat1 (mV)': (((a & 15) << 8) | (b >> 8)) * 1400 // 1000,
                       'vcpu (mV)': 1210 * 4096 // adc if adc else None,
                       'vbus2 (mV)': (c & 4095) * 4, 'vbus3 (mV)': (d >> 4) * 4,
                       'vbat2 (mV)': (((d & 15) << 8) | (e >> 8)) * 4,
                       'ibat (mA)': signed(((e & 15) << 8) | (f >> 8), 12),
                       'icpu (mA)': abs(signed(((f & 255) << 4) | (g >> 12), 12)),
                       'ipl (mA)': signed(g & 4095, 12)})
        # Retain radio signal codes without inventing undocumented calibration.
        fields.update(zip(('Peak signal code', 'Noise signal code', 'Last command signal code',
                           'Last command noise code'), data[24:28]))
    elif kind in (2, 5):
        names = TEMPS if kind == 2 else tuple(p + n for p in ('min ', 'max ') for n in TEMPS)
        for name, value in zip(names, data[4:]):
            fields[name + ' (C)'] = None if value == 255 else value / 2 - 40
            if value in (0, 254, 255):
                fields[name + ' status'] = {0: 'at or below -40 C', 254: 'at or above 87 C',
                                            255: 'sensor error'}[value]
    elif kind == 3:
        fields.update({'Uptime (s)': u32(4), 'CPU starts': u16(8),
                       'Payload starts': data[10], 'Deployment attempts': data[11],
                       'Payload errors': data[13] >> 4, 'Last reset cause': data[13] & 15,
                       'Battery state': data[14] >> 4, 'Subsystem status bits': data[15],
                       'Missed tasks': data[16], 'Antenna deployed code': data[17],
                       'EEPROM errors': data[18], 'Failed task code': data[19],
                       'Messaging enabled': data[20], 'Last command ID': data[21],
                       'Last command key': u16(22), 'Last command value': u16(24),
                       'Command count': data[26]})
        for name, value in zip(('RX', 'Telemetry', 'Transponder', 'High power TX',
                                'Low power TX', 'Payload', 'Deployment', 'VBAT above VBUS'), data[27:35]):
            fields[name + ' (%)'] = value
        fields.update(zip(('Image frames', 'Payload parameters', 'Current image ID'), data[35:38]))
    elif kind == 4:
        for prefix, offset in (('min ', 4), ('max ', 15)):
            a, b = u16(offset), u16(offset + 2)
            adc = ((b & 255) << 4) | (data[offset + 4] >> 4)
            fields[prefix + 'vbus1 (mV)'] = (a >> 4) * 1400 // 1000
            fields[prefix + 'vbat1 (mV)'] = (((a & 15) << 8) | (b >> 8)) * 1400 // 1000
            fields[prefix + 'vcpu (mV)'] = 1210 * 4096 // adc if adc else None
            for name, value in zip(('vbus2', 'vbus3', 'vbat2'), data[offset + 5:offset + 8]):
                fields[prefix + name + ' (mV)'] = value * 64
            fields[prefix + 'ibat (mA)'] = data[offset + 8] * (-1 if prefix == 'min ' else 1)
            fields[prefix + 'icpu (mA)'] = signed(data[offset + 9], 8)
            fields[prefix + 'ipl (mA)'] = data[offset + 10] * (1 if prefix == 'min ' else 4)
        for name, value in zip(('RX charging', 'RX discharging', 'Low TX charging',
                                'Low TX discharging', 'High TX charging', 'High TX discharging'), data[26:32]):
            fields[name + ' (mA)'] = value
    elif kind == 8:
        names = ('v1oc (mV)', 'v1 (mV)', 'i1 (mA)', 'i1pk (mA)', 'r1 (mohm)',
                 'v2oc (mV)', 'v2 (mV)', 'r2 (mohm)', 'Start time (s)', 'Duration (s)',
                 'Initial switch state', 'Final switch state', 'Current switch state',
                 'Enabled', 'Counter', 'Temperature code')
        fields.update(zip(names, struct.unpack('<8HIH6B', data)))
    elif kind == 9:
        for channel in range(10):
            for name, value in zip(('Voltage (mV)', 'Current (mA)', 'Mean power (mW)',
                                    'Peak voltage (mV)', 'Peak current (mA)', 'Peak power (mW)'),
                                   struct.unpack_from('<6h', data, channel * 12)):
                fields[f'Channel {channel} {name}'] = value
    elif kind == 14:
        fields['Variable ID'] = data[4]
        # Series calibration depends on variable ID; preserve values explicitly as raw.
        fields.update({f'Sample {i} (raw)': value for i, value in enumerate(data[5:])})
    elif kind == 15:
        # Source structure stores arrays by column, not interleaved BBS rows.
        for i in range(5):
            fields[f'BBS {i + 1} callsign'] = data[i * 6:i * 6 + 6].decode('ascii', errors='replace').rstrip('\0 ')
            fields[f'BBS {i + 1} message'] = data[30 + i * 7:37 + i * 7].decode('ascii', errors='replace').rstrip('\0 ')
            fields[f'BBS {i + 1} voice frames'] = data[65 + i]
    return fields


def parse_frame(raw):
    """Validate type/address-through-CRC bytes before descrambling payload."""
    if not raw or raw[0] & 15 != 3 or SIZES.get(raw[0] >> 4) != len(raw):
        raise ValueError('Unsupported SO-127 packet type, address or length')
    if crc_hqx(raw[:-2], 0xFFFF) != int.from_bytes(raw[-2:], 'big'):
        raise ValueError('Invalid SO-127 CRC')
    frame = raw[:1] + descramble(raw[1:-2]) + raw[-2:]
    kind = frame[0] >> 4
    fields = telemetry_fields(frame)
    status = 'raw' if kind == 12 else ('partial' if kind == 14 else 'parsed')
    record = dict(record_type=kind, name=NAMES[kind], parse_status=status,
                  fields=fields, raw_hex=frame[1:-2].hex())
    return dict(protocol='HADES-SA', validation='CRC-16/CCITT-FALSE (scrambled bytes)',
                source='SO-127', destination='Earth', satellite={'name': 'SO-127', 'norad': 68446},
                frame_hex=raw.hex(), payload_hex=frame[1:-2].hex(), decoded_frame_hex=frame.hex(),
                record_type=kind, packet_type=NAMES[kind], parse_status=status,
                telemetry=[record], unparsed_hex=frame[1:-2].hex() if status != 'parsed' else '')


class Deframer:
    """Bounded overlapping sync candidates; either polarity, MSB-first bits."""
    def __init__(self, emit):
        self.emit = emit
        self.shift = self.seen = 0
        self.candidates = []
        self.syncs = self.bad_crc = self.bad_length = self.truncated = 0

    def feed(self, sample, when):
        bit = int(sample >= 0)
        pending = []
        for candidate in self.candidates:
            candidate['byte'] = (candidate['byte'] << 1) | (bit ^ candidate['invert'])
            candidate['bits'] += 1
            if candidate['bits'] == 8:
                candidate['data'].append(candidate['byte'])
                candidate['byte'] = candidate['bits'] = 0
                data = candidate['data']
                if (len(data) == 1 and data[0] not in SIZES.values()) or (
                    len(data) == 2 and (data[1] & 15 != 3 or SIZES.get(data[1] >> 4) != data[0])):
                    self.bad_length += 1
                    continue
                if len(data) == data[0] + 1:
                    try:
                        packet = parse_frame(bytes(data[1:]))
                    except ValueError:
                        self.bad_crc += 1
                    else:
                        self.emit(packet, when)
                    continue
            pending.append(candidate)
        self.candidates = pending
        self.shift = ((self.shift << 1) | bit) & 0xFFFF
        self.seen += 1
        if self.seen >= 16 and self.shift in (0xBF35, 0x40CA):
            self.syncs += 1
            self.candidates.append(dict(byte=0, bits=0, data=bytearray(), invert=int(self.shift == 0x40CA)))
            self.candidates = self.candidates[-16:]

    def finish(self):
        self.truncated += len(self.candidates)
        self.candidates.clear()
