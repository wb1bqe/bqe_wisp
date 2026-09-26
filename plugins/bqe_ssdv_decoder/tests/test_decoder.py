"""Synthetic framing, image recovery, audio, and session regressions."""
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image
import soundfile as sf

from plugins.bqe_ssdv_decoder.protocol import crc16, MASK, SYNC, Framer, image_fragment, kiss_packets
from plugins.bqe_ssdv_decoder.images import ImageReceiver, MAX_IMAGE_BYTES
from plugins.bqe_ssdv_decoder.decoder import AudioDecoder
from plugins.bqe_ssdv_decoder.service import Session
from plugins.bqe_tlm_decoder.protocols import kiss_encode


def jpeg_bytes():
    stream = BytesIO()
    Image.new('RGB', (64, 48), (190, 40, 25)).save(stream, format='JPEG', quality=85)
    return stream.getvalue()


def packet(offset, data, number=12):
    return bytes([9, 0, 70, 0, 0]) + struct.pack('<IIH', 0x6f6b6f31, offset, number) + data.ljust(54, b'\x00') + b'\x00'*3


def packets(jpeg=None):
    data = jpeg if jpeg is not None else jpeg_bytes()
    return [packet(offset, data[offset:offset+54]) for offset in range(0, len(data), 54)]


def wire(payload):
    frame = payload + crc16(payload).to_bytes(2, 'big')
    whitened = bytes(a ^ b for a, b in zip(frame, MASK))
    return np.unpackbits(np.frombuffer(b'\xaa'*16 + SYNC.to_bytes(4, 'big') + whitened, dtype=np.uint8))


def audio_signal(rate=48000, inverted=False):
    bits = np.concatenate([wire(p) for p in packets()] + [np.zeros(80,dtype=np.uint8)])
    samples = np.arange(int(len(bits)*rate/9600)) * 9600/rate
    audio = (bits[np.minimum(samples.astype(int), len(bits)-1)].astype(float)*2-1)*.35
    return -audio if inverted else audio


class DecoderTests(unittest.TestCase):
    def test_independent_rs61_packet_layout(self):
        fixture = json.loads(Path(__file__).with_name('rs61_packet.json').read_text())
        number, offset, data = image_fragment(bytes.fromhex(fixture['packet_hex']))
        self.assertEqual(number, fixture['file_id'])
        self.assertEqual(offset, fixture['offset'])
        self.assertEqual(data.hex(), fixture['data_hex'])

    def test_known_crc_and_whitening(self):
        self.assertEqual(crc16(b'123456789'), 0xaee7)
        self.assertEqual(MASK[:8].hex(), 'ffe11d9aed853324')

    def test_framing_polarity_and_bad_crc(self):
        payload = packets()[0]
        for inversion in (False, True):
            found=[]
            framer=Framer(lambda data, when: found.append(data))
            for bit in wire(payload):
                framer.feed((int(bit)^inversion)*2-1, 0)
            self.assertEqual(found,[payload])
        found=[]
        framer=Framer(lambda data, when: found.append(data))
        damaged=wire(payload);damaged[-30]^=1
        for bit in damaged:
            framer.feed(int(bit)*2-1,0)
        self.assertEqual(found,[])
        self.assertGreater(framer.bad_crc,0)

    def test_reordered_duplicate_fragments_recover_one_complete_image(self):
        saved=[]
        receiver=ImageReceiver(lambda image,t:saved.append(image),lambda **s:None,lambda i:None)
        chunks=packets()
        for index,p in enumerate(reversed(chunks)):
            receiver.packet(p,index*.1)
            receiver.packet(p,index*.1)
        receiver.finish()
        self.assertEqual(len(saved),1)
        self.assertTrue(saved[0].info['ssdv_complete'])
        self.assertEqual(saved[0].size,(64,48))
        self.assertEqual(saved[0].info['packets'],len(chunks))

    def test_missing_data_is_never_claimed_complete(self):
        saved=[]
        receiver=ImageReceiver(lambda image,t:saved.append(image),lambda **s:None,lambda i:None)
        chunks=packets()
        for index,p in enumerate(chunks[:-2]+chunks[-1:]):
            receiver.packet(p,index*.2)
        receiver.finish()
        self.assertFalse(any(image.info['ssdv_complete'] for image in saved))
        self.assertEqual(receiver.images[12].received,len(chunks[:-2]+chunks[-1:])*54)

    def test_rejects_other_satellites_and_oversize_offsets(self):
        p=bytearray(packets()[0]);p[0]=8
        self.assertIsNone(image_fragment(p))
        receiver=ImageReceiver(lambda *a:None,lambda **s:None,lambda i:None)
        receiver.packet(packet(MAX_IMAGE_BYTES,b'\xff\xd8'),0)
        self.assertEqual(len(receiver.images),0)

    def test_audio_at_both_rates_and_polarities(self):
        for rate in (44100,48000):
            for inverted in (False,True):
                with self.subTest(rate=rate,inverted=inverted):
                    saved=[]
                    decoder=AudioDecoder(rate,lambda i,t:saved.append(i))
                    audio=audio_signal(rate,inverted)
                    for start in range(0,len(audio),3077):
                        decoder.push(audio[start:start+3077])
                    decoder.finish()
                    self.assertEqual(len(saved),1)
                    self.assertTrue(saved[0].info['ssdv_complete'])

    def test_kiss_escaping_and_recovery(self):
        payload=b'\xc0\xdb'+bytes(70)
        raw=b'noise\xc0\x00\xdb\x01\xc0'+kiss_encode(payload)
        self.assertEqual(list(kiss_packets(BytesIO(raw))),[payload])

    def test_audio_and_kiss_sessions_save_gallery_and_packet_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            wav=root/'input.wav';sf.write(wav,audio_signal(),48000)
            kiss=root/'input.kiss';kiss.write_bytes(b''.join(kiss_encode(p) for p in packets()))
            for source in (wav,kiss):
                session=Session(root/source.suffix[1:])
                session.start({},source)
                session.thread.join(20)
                self.assertFalse(session.thread.is_alive())
                status=session.snapshot()
                self.assertEqual(status['state'],'completed',status)
                self.assertEqual(status['images'],1,status)
                record=status['recent'][0]
                self.assertTrue(record['complete'])
                self.assertEqual(record['file_id'],12)
                self.assertTrue(Path(status['packet_log']).stat().st_size)
                metadata=json.loads((session.output/record['filename']).with_suffix('.json').read_text())
                self.assertEqual(metadata['missing_bytes'],0)


if __name__ == '__main__':
    unittest.main()
