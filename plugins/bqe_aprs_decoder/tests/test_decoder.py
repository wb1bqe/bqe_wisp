"""Independent Bell 202 waveform generation, CRC rejection and APRS parsing."""
import binascii
import unittest
import numpy as np

from plugins.bqe_aprs_decoder.dsp import AudioDecoder
from plugins.bqe_aprs_decoder.protocol import parse_packet


def frame(payload=b'!4903.50N/07201.75W-Test /A=001234', destination='APRS', control=3):
    def address(call, last=False, repeated=False):
        name, _, ssid = call.partition('-')
        return bytes(ord(c) << 1 for c in name.ljust(6)) + bytes([
            0x60 | (int(ssid or 0) << 1) | int(last) | (0x80 if repeated else 0)])
    return (address(destination) + address('N0CALL-7') + address('WIDE1-1', True, True)
            + bytes([control, 0xF0]) + payload)


def afsk(packet, rate=48000, baud=1200, offset=0, corrupt=False):
    # Independent FCS via the standard-library forward CCITT implementation,
    # reflecting the input/output bits for CRC-16/X-25.
    def reverse(value, bits):
        return int(f'{value:0{bits}b}'[::-1], 2)
    fcs = reverse(binascii.crc_hqx(bytes(reverse(b, 8) for b in packet), 0xFFFF), 16) ^ 0xFFFF
    data = packet + (fcs ^ int(corrupt)).to_bytes(2, 'little')
    bits = []; ones = 0
    for byte in data:
        for k in range(8):
            bit = (byte >> k) & 1
            bits.append(bit)
            ones = ones + 1 if bit else 0
            if ones == 5:
                bits.append(0); ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    level = 0; tones = []
    for bit in flag * 40 + bits + flag * 8:
        level ^= 1 - bit
        tones.append((1200 if level else 2200) + offset)
    index = np.minimum((np.arange(int(len(tones)*rate/baud))*baud/rate).astype(int), len(tones)-1)
    phase = 2*np.pi*np.cumsum(np.asarray(tones)[index])/rate
    return np.r_[np.zeros(137), .45*np.sin(phase + .7), np.zeros(round(rate*.05))]


def receive(audio, rate=48000, bauds=(1200,), block=1023):
    packets = []
    decoder = AudioDecoder(rate, packets.append, dict(bauds=list(bauds)))
    for start in range(0, len(audio), block):
        decoder.push(audio[start:start+block])
    decoder.finish()
    return packets


class DecoderTests(unittest.TestCase):
    def test_afsk_rates_noise_polarity_and_chunks(self):
        expected = frame()
        for rate in (8000, 12000, 44100, 48000, 96000):
            for polarity in (-1, 1):
                with self.subTest(rate=rate, polarity=polarity):
                    audio = afsk(expected, rate, baud=1201, offset=25)
                    audio = polarity * audio + np.random.default_rng(42).normal(0, .025, len(audio)) + .1
                    packets = receive(audio, rate)
                    self.assertEqual(len(packets), 1)
                    self.assertEqual(packets[0]['frame_hex'], expected.hex())
                    self.assertAlmostEqual(packets[0]['aprs']['latitude'], 49.0583333)
                    self.assertEqual(packets[0]['spot']['callsign'], 'N0CALL-7')
                    self.assertAlmostEqual(packets[0]['spot']['longitude'], -72.0291667)
                    self.assertEqual(packets[0]['path'], ['WIDE1-1*'])
                    self.assertEqual(packets[0]['validation'], 'CRC-16/X-25')

    def test_corruption_noise_and_repeated_packets(self):
        self.assertEqual(receive(afsk(frame(), corrupt=True)), [])
        self.assertEqual(receive(np.random.default_rng(17).normal(0,.1,48000)), [])
        self.assertEqual(receive(np.zeros(48000)), [])
        audio = afsk(frame())
        self.assertEqual(len(receive(np.r_[audio, np.zeros(10000), audio], block=137)), 2)

    def test_aprs_formats_and_raw_fallback(self):
        cases = [(b'>Ready', 'status'), (b':N0CALL   :Hello{123', 'message'),
                 (b'T#001,001,002,003,004,005,10101010', 'telemetry'),
                 (b';LEADER   *092345z4903.50N/07201.75W>088/036', 'object'),
                 (b'_10090556c220s004g005t077r000p000P000h50b09900wRSW', 'wx'),
                 (b'!/.(M4I^C,O `DXa/A=040849', 'compressed')]
        for payload, kind in cases:
            with self.subTest(kind=kind):
                packet = parse_packet(frame(payload))
                self.assertEqual(packet['aprs']['format'], kind)
                self.assertTrue(packet['telemetry'])
                self.assertEqual(packet['payload_hex'], payload.hex())
        for payload in (b'T#broken', b'!broken', b'\x00\xffunknown'):
            packet = parse_packet(frame(payload))
            self.assertIsNone(packet['aprs'])
            self.assertEqual(packet['unparsed_hex'], payload.hex())
        self.assertIsNone(parse_packet(frame(control=0))['aprs'])
        mice = parse_packet(frame(b'`CF"l#![/`"3z}_ ', destination='SUSUR1'))
        self.assertEqual(mice['aprs']['format'], 'mic-e')
        self.assertAlmostEqual(mice['aprs']['latitude'], 35.5868333)
        relayed = parse_packet(frame(b'}N1TEST>APRS:>Relayed status'))
        self.assertEqual(relayed['aprs']['subpacket']['status'], 'Relayed status')

    def test_g3ruh_optional(self):
        from plugins.bqe_tlm_decoder.tests.test_decoder import ax25_bits, waveform
        packets = receive(waveform(ax25_bits(frame()), 9600), bauds=(1200,9600))
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]['baud'], 9600)
        self.assertEqual(packets[0]['frame_hex'], frame().hex())

    def test_invalid_options_and_audio(self):
        for options in (dict(bauds=[]), dict(bauds=[2400]), dict(protocol='usp')):
            with self.assertRaises(ValueError):
                AudioDecoder(48000, lambda p: None, options)
        with self.assertRaises(ValueError):
            AudioDecoder(8000, lambda p: None, dict(bauds=[9600]))
        decoder = AudioDecoder(8000, lambda p: None, dict(bauds=[1200,9600]))
        self.assertEqual(decoder.skipped_bauds, [9600])
        for audio in ([float('nan')], [[0,1]], [float('inf')]):
            with self.assertRaises(ValueError):
                decoder.push(audio)


if __name__ == '__main__':
    unittest.main()
