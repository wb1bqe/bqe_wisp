"""Independent UMKA capture, USP audio chain, and Geoscan v1/v2 regression tests."""
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from plugins.bqe_ssdv_decoder import geoscan, umka
from plugins.bqe_ssdv_decoder.protocol import kiss_packets, pn9_mask, crc16, SYNC
from plugins.bqe_tlm_decoder.protocols import USPFEC, TO_DUAL, FROM_DUAL, ccsds_randomizer, PLS, USP_SYNC
from plugins.bqe_ssdv_decoder.integration import SSDV_RECEIVER


def jpeg():
    stream = BytesIO()
    Image.new('RGB', (32, 32), (140, 50, 30)).save(stream, format='JPEG')
    return stream.getvalue()


def envelope(message, body):
    def address(call, last):
        return bytes(ord(c)*2 for c in call.ljust(6)) + bytes([0x60 | last])
    return (address('R3DDF', 0) + address('RS40S', 1) + b'\x00\xf0'
            + struct.pack('<4H', message, 15, 1, len(body)) + body)


def usp_packets():
    data = jpeg()
    return [envelope(0xc20, struct.pack('<BBHIH', 1, 7, 187, 0, 0)+b'test.jpg'),
            envelope(0xc2b, struct.pack('<I', len(data)))] + [
                envelope(0xc24, struct.pack('<BI', 7, offset)+data[offset:offset+180])
                for offset in range(0, len(data), 180)]


def usp_wire(frame):
    message = (b'\x00\x00'+struct.pack('<H', len(frame))+frame).ljust(223, b'\x00')
    encoded = bytes(USPFEC().rs.encode(message.translate(FROM_DUAL))).translate(TO_DUAL)
    randomized = bytes(a ^ b for a, b in zip(encoded, ccsds_randomizer(255)))
    bits = np.unpackbits(np.frombuffer(randomized, dtype=np.uint8))
    state = 0
    output = []
    for bit in bits:
        state = ((state << 1) | int(bit)) & 127
        output += [bin(state & 0x4f).count('1') % 2, 1 ^ (bin(state & 0x6d).count('1') % 2)]
    header = np.unpackbits(np.frombuffer(USP_SYNC.to_bytes(8, 'big')+PLS[1].to_bytes(8, 'big'), dtype=np.uint8))
    return np.r_[np.tile([0, 1], 64), header, output]


def geoscan_packets(version=1, satellite=1):
    data = jpeg()
    result = []
    size = 56 if version == 1 else 54
    for offset in range(0, len(data), size):
        block = data[offset:offset+size]
        if version == 1:
            address = 0x1ffe0 + offset  # Exercise the 16-bit boundary and base offset.
            packet = (bytes([satellite, 0, len(block)+6]) + struct.pack('<HHB',
                      0x901 if not offset else 0x905, address & 65535, address >> 16)
                      + block.ljust(56, b'\x00'))
        else:
            packet = bytes([satellite, 0, 70, 0, 0])+struct.pack('<IIH', 0x6f6b6f31, offset, 12)+block.ljust(54, b'\x00')+b'\x00'*3
        result.append(packet)
    return result


def geoscan_wire(packet):
    frame = packet + crc16(packet).to_bytes(2, 'big')
    return np.unpackbits(np.frombuffer(b'\xaa'*16+SYNC.to_bytes(4, 'big')+
                        bytes(a ^ b for a, b in zip(frame, pn9_mask(len(frame)))), dtype=np.uint8))


def waveform(bits, baud, rate, polarity):
    bits = np.r_[bits, np.zeros(200)]
    positions = (np.arange(int(len(bits)*rate/baud))*baud/rate).astype(int)
    signal = (bits[positions]*2.0-1)*0.4*polarity
    return signal + np.random.default_rng(42).normal(0, .006, len(signal)) + .01


class ImageTests(unittest.TestCase):
    def test_public_umka_capture_and_metadata(self):
        images = []
        receiver = umka.ImageReceiver(lambda image, when: images.append(image), lambda **f: None, lambda i: None)
        with Path(__file__).with_name('umka_reference.kss').open('rb') as stream:
            frames = list(kiss_packets(stream))
        for i, frame in enumerate(frames):
            receiver.packet(frame, i/10)
        receiver.finish()
        self.assertEqual(len(images), 1)
        image = images[0]
        self.assertEqual(image.size, (480, 480))
        self.assertTrue(image.info['ssdv_complete'])
        self.assertEqual(image.info['file_id'], 'UMK_6.jpg')
        self.assertEqual(image.info['expected_bytes'], 27377)
        self.assertEqual(image.info['missing_bytes'], 0)

    def test_umka_reordered_duplicates_missing_header_and_conflict(self):
        packets = usp_packets()
        out = []
        receiver = umka.ImageReceiver(lambda i, t: out.append(i), lambda **f: None, lambda i: None)
        for frame in packets[:2]+list(reversed(packets[2:]))+packets[2:]:
            receiver.packet(frame, 1)
        receiver.finish()
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].info['ssdv_complete'])
        for selected in (packets[:2]+packets[3:], packets[:-1],
                         packets[:3]+[packets[2][:-1]+bytes([packets[2][-1]^1])]+packets[3:]):
            out = []
            receiver = umka.ImageReceiver(lambda i, t: out.append(i), lambda **f: None, lambda i: None)
            for frame in selected:
                receiver.packet(frame, 1)
            receiver.finish()
            self.assertFalse(any(i.info['ssdv_complete'] for i in out))

    def test_umka_rejects_invalid_and_bounds_sessions(self):
        receiver = umka.ImageReceiver(lambda *a: None, lambda **f: None, lambda i: None)
        for frame in (envelope(0xc24, b'bad'), envelope(0xc2b, b'bad'),
                      envelope(0xc24, b'\x01'+b'\xff'*4+b'x'), usp_packets()[0][:-2]):
            receiver.packet(frame, 0)
        self.assertEqual(receiver.rejected, 4)
        for session in range(20):
            receiver.packet(envelope(0xc20, struct.pack('<BBHIH', 1, session, 187, 0, 0)+b'x.jpg'), 0)
        self.assertLessEqual(len(receiver.images), 4)

    def test_usp_audio_all_rates_and_polarities(self):
        bits = np.concatenate([usp_wire(p) for p in usp_packets()])
        for baud, rate, polarity in ((2400, 48000, 1), (4800, 44100, -1), (9600, 48000, 1)):
            with self.subTest(baud=baud):
                out = []
                decoder = umka.AudioDecoder(rate, lambda i, t: out.append(i))
                audio = waveform(bits, baud, rate, polarity)
                for start in range(0, len(audio), 2047):
                    decoder.push(audio[start:start+2047])
                decoder.finish()
                self.assertEqual(len(out), 1)
                self.assertTrue(out[0].info['ssdv_complete'])

    def test_geoscan_v1_v2_audio_and_satellite_separation(self):
        for version, satellite, rate, polarity in ((1, 1, 48000, 1), (1, 2, 44100, -1), (2, 9, 48000, -1), (2, 3, 44100, 1)):
            with self.subTest(version=version, satellite=satellite):
                out = []
                decoder = geoscan.AudioDecoder(rate, lambda i, t: out.append(i))
                bits = np.concatenate([geoscan_wire(p) for p in geoscan_packets(version, satellite)])
                audio = waveform(bits, 9600, rate, polarity)
                for start in range(0, len(audio), 997):
                    decoder.push(audio[start:start+997])
                decoder.finish()
                self.assertEqual(len(out), 1)
                self.assertTrue(out[0].info['ssdv_complete'])
        out = []
        decoder = geoscan.AudioDecoder(48000, lambda i, t: out.append(i))
        for pair in zip(geoscan_packets(2, 3), geoscan_packets(2, 9)):
            for packet in pair:
                decoder.packet(packet, 1)
        self.assertEqual(len(out), 2)
        self.assertNotEqual(out[0].info['file_id'], out[1].info['file_id'])

    def test_new_profiles_session_and_scheduled_source(self):
        from plugins.bqe_ssdv_decoder.service import Session
        from plugins.bqe_tlm_decoder.protocols import kiss_encode
        for profile, packets in [('umka-1', usp_packets()), ('geoscan', geoscan_packets())]:
            with tempfile.TemporaryDirectory() as folder:
                capture = Path(folder)/'input.kiss'
                capture.write_bytes(b''.join(kiss_encode(p) for p in packets))
                session = Session(Path(folder)/'gallery')
                session.start({'profile': profile}, capture)
                session.thread.join(10)
                self.assertEqual(session.snapshot()['state'], 'completed')
                self.assertEqual(session.snapshot()['images'], 1)
            with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=Mock()) as launch:
                SSDV_RECEIVER.start({'decode_ssdv_images': True, 'receive_sdr': True, 'ssdv_profile': profile},
                                    'test', {'sdr': {'port': 8872}})
                command = launch.call_args.args[0]
                self.assertIn(profile, command)
                self.assertIn('bqe-sdr://127.0.0.1:8872', command)
                SSDV_RECEIVER.stop()

    def test_geoscan_corruption_gaps_and_repeated_v1_images(self):
        from plugins.bqe_ssdv_decoder.protocol import Framer
        for packet in (geoscan_packets()[0], geoscan_packets(2, 3)[0]):
            received = []
            decoder = Framer(lambda p, t: received.append(p), len(packet)+2)
            bits = geoscan_wire(packet)
            bits[-12] ^= 1
            for bit in bits:
                decoder.feed(float(bit)*2-1, 0)
            self.assertEqual(received, [])
            self.assertGreater(decoder.bad_crc, 0)
        out = []
        decoder = geoscan.AudioDecoder(48000, lambda i, t: out.append(i))
        packets = geoscan_packets()
        for packet in packets[:-1]:
            decoder.packet(packet, 0)
        decoder.finish()
        self.assertFalse(any(i.info['ssdv_complete'] for i in out))
        out = []
        decoder = geoscan.AudioDecoder(48000, lambda i, t: out.append(i))
        for index, packet in enumerate(packets+packets):
            decoder.packet(packet, index)
        self.assertEqual(len(out), 2)
        self.assertNotEqual(out[0].info['file_id'], out[1].info['file_id'])

    def test_sdr_usb_iq_to_complete_images(self):
        """Exercise RTL unsigned IQ, RF tuning, FM demodulation and image assembly."""
        from scipy.ndimage import gaussian_filter1d
        from plugins.bqe_sdr.config import Settings
        from plugins.bqe_sdr.dsp import Demodulator, decode_iq
        cases = [
            ('umka-1', umka.AudioDecoder, [usp_wire(p) for p in usp_packets()], 2400),
            ('geoscan-v1', geoscan.AudioDecoder, [geoscan_wire(p) for p in geoscan_packets()], 5000),
            ('geoscan-v2', geoscan.AudioDecoder, [geoscan_wire(p) for p in geoscan_packets(2, 9)], 5000),
        ]
        expected = Image.open(BytesIO(jpeg())).convert('RGB')
        for label, decoder_class, frames, deviation in cases:
            with self.subTest(profile=label):
                bits = np.concatenate(frames + [np.tile([0, 1], 100)])
                # Gaussian FSK (h=0.5 for USP), shaped at 240 kHz then sampled
                # at the station's configured 960 kHz RTL-SDR rate.
                baseband = gaussian_filter1d(np.repeat(bits.astype(float)*2-1, 25),
                                             25*np.sqrt(np.log(2))/(2*np.pi*.5))
                settings = Settings.from_dict(dict(sample_rate=960000, frequency=437625000,
                    center=437865000, mode='FM', bandwidth=25000, audio_gain=.15))
                demodulator = Demodulator(settings)
                images = []
                decoder = decoder_class(48000, lambda image, when: images.append(image))
                phase = 0.0
                rng = np.random.default_rng(31)
                for start in range(0, len(baseband)*4, 65537):
                    indexes = np.arange(start, min(start+65537, len(baseband)*4))
                    frequencies = baseband[indexes//4]*deviation - 240000 + 500
                    phases = phase + 2*np.pi*np.cumsum(frequencies)/960000
                    phase = phases[-1] % (2*np.pi)
                    iq = .65*np.exp(1j*phases)
                    iq += .006*(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))
                    interleaved = np.column_stack((iq.real, iq.imag)).reshape(-1)
                    raw = np.clip(np.rint(interleaved*128+127.5), 0, 255).astype('uint8').tobytes()
                    audio = demodulator.push(decode_iq(raw), frequency=settings.frequency+500)
                    decoder.push(audio)
                decoder.finish()
                self.assertEqual(len(images), 1)
                self.assertTrue(images[0].info['ssdv_complete'])
                np.testing.assert_array_equal(np.asarray(images[0]), np.asarray(expected))


if __name__ == '__main__':
    unittest.main()
