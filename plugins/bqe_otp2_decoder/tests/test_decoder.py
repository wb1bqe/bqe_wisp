import unittest
import numpy as np
from plugins.bqe_otp2_decoder.dsp import AudioDecoder
from plugins.bqe_tlm_decoder.protocols import crc_x25
from plugins.bqe_tlm_decoder.tests.test_decoder import ax25_bits, waveform


def frame():
    def address(call, final):
        return bytes(ord(c) << 1 for c in call.ljust(6)) + bytes([0x60 | final])
    # Synthetic addresses deliberately do not assert an OTP-2 callsign.
    return address('GROUND', 0) + address('TEST', 1) + b'\x03\xf0\x0e\x00\xff\xffraw telemetry'


def symbols(mode, corrupt=False):
    if mode == 'NRZI then G3RUH':
        return ax25_bits(frame(), corrupt)
    data = frame() + (crc_x25(frame()) ^ int(corrupt)).to_bytes(2, 'little')
    bits = []; ones = 0
    for byte in data:
        for i in range(8):
            bit = (byte >> i) & 1
            bits.append(bit)
            ones = ones + 1 if bit else 0
            if ones == 5:
                bits.append(0); ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    bits = flag * 40 + bits + flag * 8
    if mode != 'NRZ':
        level = 0; encoded = []
        for bit in bits:
            level ^= 1 - bit
            encoded.append(level)
        bits = encoded
    if mode == 'G3RUH then NRZI':
        encoded = []
        for i, bit in enumerate(bits):
            value = bit ^ (encoded[i-17] if i >= 17 else 0) ^ (encoded[i-12] if i >= 12 else 0)
            encoded.append(value)
        bits = encoded
    return np.asarray(bits) * 2 - 1


class DecoderTests(unittest.TestCase):
    def decode(self, audio, baud=4800):
        packets = []
        decoder = AudioDecoder(48000, packets.append, dict(bauds=[baud]))
        for i in range(0, len(audio), 1023):
            decoder.push(audio[i:i+1023])
        decoder.finish()
        return packets

    def test_audio_encodings_and_polarities(self):
        for mode in ('NRZ', 'NRZI', 'NRZI then G3RUH', 'G3RUH then NRZI'):
            for polarity in (-1, 1):
                with self.subTest(mode=mode, polarity=polarity):
                    packets = self.decode(polarity * waveform(symbols(mode), 4800))
                    self.assertEqual(len(packets), 1)
                    self.assertEqual(packets[0]['frame_hex'], frame().hex())
                    self.assertEqual(packets[0]['telemetry'], [])
                    self.assertEqual(packets[0]['satellite_identity'], 'unverified')
                    self.assertEqual(packets[0]['protocol'], 'AX.25 ' + ('NRZI then G3RUH' if mode == 'G3RUH then NRZI' else mode))

    def test_corruption_and_noise(self):
        for mode in ('NRZ', 'NRZI', 'NRZI then G3RUH', 'G3RUH then NRZI'):
            self.assertEqual(self.decode(waveform(symbols(mode, True), 4800)), [])
        self.assertEqual(self.decode(np.random.default_rng(23).normal(size=24000)), [])
        self.assertEqual(self.decode(np.zeros(48000)), [])

    def test_fallback_rate(self):
        self.assertEqual(len(self.decode(waveform(symbols('NRZI'), 9600), 9600)), 1)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            AudioDecoder(8000, lambda p: None, dict(bauds=[4800]))
        with self.assertRaises(ValueError):
            AudioDecoder(48000, lambda p: None, dict(protocol='usp'))


if __name__ == '__main__':
    unittest.main()
