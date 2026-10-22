"""Offline framing, native modem loopback, and received-object checks."""
import ctypes as C
from io import BytesIO
from pathlib import Path
import gzip
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
import reedsolo
import soxr

from plugins.bqe_hf_image_decoder.freedv import Framer, library, crc16, SCRAMBLE
from plugins.bqe_hf_image_decoder.decoder import AudioDecoder
from plugins.bqe_hf_image_decoder.hamdrm import open_image, Modem as HamDRM
from plugins.bqe_ssdv_decoder.hades import executable


def frames(packet, size):
    payload = len(packet).to_bytes(2, 'big') + packet
    data = [payload[i:i+size].ljust(size, b'\0') for i in range(0, len(payload), size)]
    parity = bytes(np.bitwise_xor.reduce(np.array([list(f) for f in data], dtype=np.uint8)))
    return [bytes(size)] * 6 + data + [parity] * 4


def transmit(packets, mode):
    """Reference freedv-tnc layout, modulated by Codec2's real transmitter."""
    lib = library()
    handle = lib.freedv_open({'700D': 7, '700E': 13}[mode])
    try:
        size = lib.freedv_get_bits_per_modem_frame(handle)//8
        count = lib.freedv_get_n_nom_modem_samples(handle)
        audio = []
        for packet in packets:
            for frame in frames(packet, size-2):
                raw = bytes(b ^ SCRAMBLE[i % 16] for i, b in enumerate(frame))
                raw += crc16(raw).to_bytes(2, 'big')
                output = (C.c_short * count)()
                lib.freedv_rawdatatx(handle, output, (C.c_ubyte * size).from_buffer_copy(raw))
                audio.append(np.array(output, dtype=np.float32)/32768)
        return np.concatenate(audio + [np.zeros(16000, dtype=np.float32)])
    finally:
        lib.freedv_close(handle)


class DecoderTests(unittest.TestCase):
    def test_framer_recovers_one_lost_frame_and_rejects_two(self):
        packet = bytes(range(256))
        for lost, expected in [((), [packet]), ((8,), [packet]), ((8, 9), [])]:
            out = []
            framer = Framer(12, out.append)
            for i, frame in enumerate(frames(packet, 12)):
                framer.push(None if i in lost else frame)
            self.assertEqual(out, expected)
        self.assertEqual(crc16(b'123456789'), 0x906e)

    def test_hamdrm_image_gzip_and_rs_levels(self):
        # RS1 coefficient-order vector checked against the upstream native
        # RXAMADRM eras_dec_rs(), including correction of one damaged byte.
        reference = bytes(range(224)) + bytes.fromhex(
            '7b0a45138ee931fb63bcc55170dce6e7401ca129a6a7685ae9e1988021af2b')
        damaged = bytearray(reference)
        damaged[12] ^= 42
        fixed = bytes(reedsolo.RSCodec(31, fcr=1, prim=0x11d).decode(bytes(damaged)[::-1])[1])[::-1]
        self.assertEqual(fixed, reference)
        image = BytesIO()
        Image.new('RGB', (32, 16), 'orange').save(image, 'PNG')
        data = image.getvalue()
        self.assertEqual(open_image(gzip.compress(data)).size, (32, 16))
        for level, size in enumerate((224, 192, 160, 128), 1):
            rows = (len(data)+7+size-1)//size
            pad = rows*size-len(data)-7
            raw = bytes([pad])+rows.to_bytes(2, 'little')+b'\0'*4+data+b'\0'*pad
            rs = reedsolo.RSCodec(255-size, fcr=1, prim=0x11d)
            blocks = []
            for i in range(0, len(raw), size):
                coded = bytes(rs.encode(raw[i:i+size][::-1]))[::-1]
                blocks.append(list(coded[255-size:] + coded[:255-size]))
            encoded = bytearray(np.array(blocks, dtype=np.uint8).T.tobytes())
            encoded[20] ^= 0x12
            self.assertEqual(open_image(bytes(encoded), f'image.rs{level}').size, (32, 16))
        with self.assertRaises((OSError, ValueError)):
            open_image(b'not an image')

    def test_audio_to_image_both_rates(self):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = Path(folder)/'source.jpg', Path(folder)/'source.ssdv'
            Image.new('RGB', (32, 32), 'orange').save(src)
            subprocess.run([executable(), '-e', '-c', 'N0CALL', str(src), str(dst)],
                           check=True, capture_output=True, timeout=10)
            raw = dst.read_bytes()
        packets = [raw[i:i+256] for i in range(0, len(raw), 256)]
        for mode, rate in [('700D', 48000), ('700D', 44100)]:
            with self.subTest(mode=mode, rate=rate):
                images = []
                audio = soxr.resample(transmit(packets, mode), 8000, rate).astype(np.float32)
                decoder = AudioDecoder(rate, lambda image, when: images.append(image), profile='auto')
                try:
                    for offset in range(0, len(audio), 4567):
                        decoder.push(audio[offset:offset+4567])
                    decoder.finish()
                finally:
                    decoder.close()
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0].size, (32, 32))
                self.assertTrue(images[0].info['ssdv_complete'])
                self.assertEqual(images[0].info['ssdv_mode'], 'HF SSDV')

    def test_noise_does_not_emit_and_native_process_exits(self):
        images = []
        decoder = AudioDecoder(48000, lambda *args: images.append(args))
        process = decoder.chains[-1][0].process
        try:
            decoder.push(np.random.default_rng(42).normal(0, .05, 48000*3).astype(np.float32))
            decoder.finish()
        finally:
            decoder.close()
        self.assertEqual(images, [])
        self.assertEqual(process.returncode, 0)

    def test_freedv_frequency_offset_drains_buffered_audio(self):
        # The real Codec2 receiver requests nin == 0 during acquisition.
        # Rejecting that request used to stop otherwise valid reception.
        from scipy.signal import hilbert
        from plugins.bqe_hf_image_decoder.freedv import Modem
        base = transmit([bytes(range(256))], '700D')
        phase = 2j * np.pi * 100 * np.arange(len(base)) / 8000
        audio = (hilbert(base) * np.exp(phase)).real.astype(np.float32)
        noise = np.random.default_rng(2026).normal(
            0, np.std(audio) * 10 ** (-10 / 20), len(audio))
        audio += noise.astype(np.float32)
        modem = Modem('700D', lambda packet: None)
        requests = []
        native_nin = modem.lib.freedv_nin

        def nin(handle):
            count = native_nin(handle)
            requests.append(count)
            return count

        try:
            with patch.object(modem.lib, 'freedv_nin', side_effect=nin):
                for offset in range(0, len(audio), 683):
                    modem.push(audio[offset:offset + 683])
            self.assertIn(0, requests)
            self.assertLess(len(modem.pending), modem.maximum)
        finally:
            modem.close()

    def test_capture_failure_closes_decoder(self):
        from plugins.bqe_hf_image_decoder.service import Session
        import soundfile as sf
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'broken.wav'
            sf.write(path, np.zeros(1000), 48000)
            session = Session(Path(folder)/'gallery')
            decoder = Mock()
            decoder.push.side_effect = RuntimeError('test capture failure')
            with patch.object(session, 'new_decoder', return_value=decoder):
                session.start({}, path)
                session.thread.join(10)
            self.assertFalse(session.thread.is_alive())
            self.assertEqual(session.snapshot()['state'], 'error')
            decoder.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
