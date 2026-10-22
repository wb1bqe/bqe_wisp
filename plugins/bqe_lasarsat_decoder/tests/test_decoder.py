# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline LASARsat framing/field checks. Audio vectors are synthetic."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from plugins.bqe_lasarsat_decoder.dsp import AudioDecoder
from plugins.bqe_lasarsat_decoder.protocol import parse_payload
from plugins.bqe_lasarsat_decoder.integration import LASARSAT_RECEIVER
from plugins.bqe_lasarsat_decoder.service import Session
from plugins.bqe_tlm_decoder.tests.test_decoder import ax25_bits, waveform


def frame(payload=b'OBC,4,3600,924425,-15,32768\x00', call='OK0LSR', pid=0xF0):
    def address(text, last):
        return bytes(ord(c) << 1 for c in text.ljust(6)) + bytes([0x60 | last])
    return address('CQ', 0) + address(call, 1) + bytes([3, pid]) + payload


class DecoderTests(unittest.TestCase):
    def test_housekeeping_layouts_and_invalid_records(self):
        examples = [
            (b'OBC,4,3600,924425,-15,32768\0', 'obc_temp_mcu', -15),
            (b'PSU,2,10,30,7400,18,19,120,90,5a,1,600\0', 'psu_ch_state_num', 0x5a),
            (b'MGS,1,2,-3,4,5,6,7,8\0', 'mgs_int_mag_x', -3),
            (b'SOL,nan,2,3,4,5,6,7,8,9,10,11,12\0', 'sol_temp_zp', None),
            (b'DOS,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16\0', 'dos_uptime', 16),
            (b'NAV,1,2,3,4,5,6,7,8,9,10,11,12,13\0', 'nav_max_snr', 13),
            (b'U,1,2,3,4,5,6,7,8,N0CALL-1,10,11,-120,-90\0', 'uhf_last_digipeater', 'N0CALL-1'),
            (b'V,1,2,3,4,5,6,7,8,N0CALL-1,10,11,-120,-90\0', 'vhf_act_rssi_raw', -120),
        ]
        for payload, key, value in examples:
            with self.subTest(payload=payload):
                parsed = parse_payload(payload)
                self.assertEqual(parsed['parse_status'], 'parsed', parsed)
                self.assertEqual(parsed['fields'][key], value)
                self.assertEqual(parsed['raw_hex'], payload.hex())
        psu = parse_payload(examples[1][0])['fields']
        self.assertEqual([psu[f'psu_ch{i}_state'] for i in range(7)], [0, 1, 0, 1, 1, 0, 1])
        for payload in (b'OBC,4,12', b'OBC,4,12,3,x,5', b'OBC,4,12,3,4,5,6',
                        b'OBC,4,12,3,1.5,5', b'OBC,4,12,3,4\0junk,5'):
            parsed = parse_payload(payload)
            self.assertEqual(parsed['parse_status'], 'invalid')
            self.assertEqual(parsed['fields'], {})
        for payload in (b'new firmware message', b'\xff\xfe', b'DE OK0LSR = U1R2T3P4 AR'):
            self.assertEqual(parse_payload(payload)['parse_status'], 'unknown')

    def test_audio_crc_identity_and_chunk_boundaries(self):
        for rate, polarity in ((48000, 1), (44100, -1)):
            for call, corrupt in (('OK0LSR', False), ('OK0LSR', True), ('N0CALL', False)):
                with self.subTest(rate=rate, call=call, corrupt=corrupt):
                    packets = []
                    decoder = AudioDecoder(rate, packets.append, {})
                    audio = waveform(np.asarray(ax25_bits(frame(call=call), corrupt))*polarity, 9600, rate)
                    for start in range(0, len(audio), 997):
                        decoder.push(audio[start:start+997])
                    decoder.finish()
                    self.assertEqual(len(packets), int(call == 'OK0LSR' and not corrupt))
                    if packets:
                        packet = packets[0]
                        self.assertEqual(packet['satellite']['norad'], 62391)
                        self.assertEqual(packet['telemetry'][0]['fields']['obc_uptime_tot'], 924425)
                        self.assertEqual(packet['frame_hex'], frame().hex())
                        self.assertEqual(decoder.diagnostics()['parsed_packets'], 1)

    def test_unknown_and_malformed_payloads_preserved(self):
        packets = []
        decoder = AudioDecoder(48000, packets.append, {})
        for i, raw in enumerate((frame(b'new firmware'), frame(b'OBC,1,2'), frame(pid=0xCF))):
            decoder._packet(raw, i, 9600, {'protocol': 'AX.25 G3RUH'})
        self.assertEqual([p['parse_status'] for p in packets], ['unknown', 'invalid', 'unknown'])
        self.assertTrue(all(p['unparsed_hex'] for p in packets))
        self.assertTrue(all(not p['telemetry'][0]['fields'] for p in packets))

    def test_noise_and_silence_do_not_create_packets(self):
        packets = []
        decoder = AudioDecoder(48000, packets.append, {})
        decoder.push(np.zeros(48000))
        decoder.push(np.random.default_rng(5).normal(0, .05, 48000))
        decoder.finish()
        self.assertEqual(packets, [])

    def test_file_capture_and_logs(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'synthetic.wav'
            sf.write(source, waveform(ax25_bits(frame()), 9600), 48000)
            session = Session(folder / 'decoded')
            session.start({'device': 'USB Audio Codec'}, source)
            session.thread.join(10)
            state = session.snapshot()
            self.assertEqual(state['state'], 'completed', state)
            self.assertEqual(state['packets'], 1)
            self.assertEqual(state['input_device'], 'USB Audio Codec')
            record = json.loads(Path(state['log']).read_text())
            self.assertEqual(record['parse_status'], 'parsed')
            self.assertEqual(record['telemetry'][0]['fields']['obc_reset_cnt'], 4)
            self.assertTrue(Path(state['kiss']).read_bytes().startswith(b'\xc0\0'))

    def test_pass_receiver_opt_in_and_audio_source(self):
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=Mock()) as spawn:
            LASARSAT_RECEIVER.start({}, 'lasarsat-tlm', {'radio_soundcard': 'USB Audio Codec'})
            spawn.assert_not_called()
            for rig, device in (({'radio_soundcard': 'USB Audio Codec'}, 'USB Audio Codec'),
                                ({'radio_is_sdr': True, 'sdr': {'port': 8772}}, 'bqe-sdr://127.0.0.1:8772')):
                LASARSAT_RECEIVER.start({'decode_lasarsat_telemetry': True}, 'lasarsat-tlm', rig)
                command = spawn.call_args.args[0]
                self.assertEqual(command[command.index('--device')+1], device)
                self.assertIn('--managed', command)
                self.assertIn('8778', command)
                LASARSAT_RECEIVER.stop()


if __name__ == '__main__':
    unittest.main()
