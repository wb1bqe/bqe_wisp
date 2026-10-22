import json
from pathlib import Path
import unittest
import numpy as np
from plugins.bqe_rs61_decoder.protocol import parse_packet
from plugins.bqe_rs61_decoder.dsp import AudioDecoder
from plugins.bqe_ssdv_decoder.protocol import crc16, MASK, SYNC

FRAMES = [bytes.fromhex(x) for x in json.loads(Path(__file__).with_name('reference.json').read_text())['frames']]


def frame():
    return FRAMES[0]


def wire(payload):
    encoded = payload + crc16(payload).to_bytes(2, 'big')
    whitened = bytes(a ^ b for a, b in zip(encoded, MASK))
    return np.unpackbits(np.frombuffer(b'\xaa'*16 + SYNC.to_bytes(4, 'big') + whitened, dtype=np.uint8))


def audio_wave(frames, rate=48000, inverted=False):
    bits = np.concatenate([wire(p) for p in frames] + [np.zeros(80, dtype=np.uint8)])
    positions = np.arange(int(len(bits)*rate/9600)) * 9600/rate
    audio = (bits[np.minimum(positions.astype(int), len(bits)-1)].astype(float)*2-1)*.35
    return -audio if inverted else audio


class DecoderTests(unittest.TestCase):
    def test_reference_beacons(self):
        self.assertEqual(len(FRAMES), 6)
        for packet in FRAMES:
            self.assertEqual(parse_packet(packet)['parse_status'], 'parsed')
        fields = parse_packet(frame())['telemetry'][0]['fields']
        self.assertEqual(fields['Spacecraft time (UTC)'], '2026-08-16T10:31:39+00:00')
        self.assertEqual(fields['Total battery voltage (mV)'], 7371)
        self.assertEqual(fields['Single battery voltage (mV)'], 3693)
        self.assertEqual(fields['Platform current (mA)'], 968)
        self.assertEqual(fields['VBUS voltage (mV)'], 7268)
        self.assertEqual(fields['Panel X- temperature (C)'], -1)
        self.assertEqual(fields['Last received packet RSSI (dBm)'], -117)

    def test_identity_unknown_and_activity_bits(self):
        padded = bytearray(frame()); padded[12] = 0x40
        self.assertIsNotNone(parse_packet(padded))
        for offset in (0, 7, 12, 13, 14, 15):
            bad = bytearray(frame()); bad[offset] ^= 1
            self.assertIsNone(parse_packet(bad))
        self.assertIsNone(parse_packet(frame()[:-1]))
        unknown = bytearray(frame()); unknown[16] = 2
        self.assertEqual(parse_packet(unknown)['parse_status'], 'unknown')
        self.assertEqual(parse_packet(unknown)['telemetry'][0]['fields'], {})
        # Deliberately asymmetric bits verify operator numbering, independent
        # of the third-party parser's reversed BitStruct ordering.
        changed = bytearray(frame()); changed[42] = 1
        fields = parse_packet(changed)['telemetry'][0]['fields']
        self.assertTrue(fields['Flywheels active'])
        self.assertFalse(fields['Panel Y- active'])

    def test_audio_crc_polarity_and_chunks(self):
        for rate in (44100, 48000):
            for invert in (False, True):
                packets = []
                decoder = AudioDecoder(rate, packets.append, {})
                audio = audio_wave([frame()], rate, invert)
                for i in range(0, len(audio), 1023): decoder.push(audio[i:i+1023])
                decoder.finish()
                self.assertEqual(len(packets), 1)
                self.assertEqual(packets[0]['frame_hex'], frame().hex())
                self.assertEqual(packets[0]['validation'], 'CC11xx CRC-16')
        # Flip a payload bit without updating the transmitted checksum.
        from plugins.bqe_ssdv_decoder.protocol import Framer
        bits = wire(frame()); bits[-30] ^= 1; packets = []
        framer = Framer(lambda b,t: packets.append(b))
        for bit in bits: framer.feed(int(bit)*2-1, 0)
        self.assertEqual(packets, [])
        decoder = AudioDecoder(48000, packets.append, {})
        decoder.push(np.zeros(48000)); decoder.finish()
        self.assertEqual(packets, [])


if __name__ == '__main__':
    unittest.main()
