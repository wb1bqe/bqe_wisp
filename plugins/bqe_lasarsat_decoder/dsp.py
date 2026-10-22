# SPDX-License-Identifier: GPL-3.0-or-later
"""LASARsat profile over BQE's streaming G3RUH/HDLC/CRC audio modem."""
from plugins.bqe_tlm_decoder.dsp import AudioDecoder as AX25Decoder
from .protocol import CALLSIGN, SATELLITE, parse_payload


class AudioDecoder(AX25Decoder):
    def __init__(self, sample_rate, emit, options):
        if options.get('protocol', 'ax25') != 'ax25':
            raise ValueError('LASARsat digital telemetry uses AX.25/G3RUH')
        self.foreign_packets = 0
        self.parsed_packets = 0
        self.invalid_payloads = 0

        def identified(packet):
            # A tuned frequency or filename is not proof of satellite identity.
            # Repeated amateur packets also must not become spacecraft telemetry.
            if packet['source'].split('-')[0] != CALLSIGN:
                self.foreign_packets += 1
                return
            packet['satellite'] = dict(SATELLITE)
            packet['telemetry'] = []  # Discard the generic SPUTNIX interpretation.
            payload = bytes.fromhex(packet['payload_hex'])
            if packet['control'] & 0xEF == 3 and packet['pid'] == 0xF0:
                record = parse_payload(payload)
            else:
                record = dict(record_type='raw', name='Unsupported AX.25 control/PID',
                              parse_status='unknown', fields={}, raw_hex=payload.hex())
            packet['record_type'] = record['record_type']
            packet['parse_status'] = record['parse_status']
            packet['telemetry'] = [record]
            packet['unparsed_hex'] = '' if record['parse_status'] == 'parsed' else payload.hex()
            self.parsed_packets += record['parse_status'] == 'parsed'
            self.invalid_payloads += record['parse_status'] == 'invalid'
            emit(packet)

        super().__init__(sample_rate, identified, options.get('bauds', [9600]), 'ax25')

    def diagnostics(self):
        return dict(super().diagnostics(), foreign_packets=self.foreign_packets,
                    parsed_packets=self.parsed_packets, invalid_payloads=self.invalid_payloads)
