"""Regressions for VIS acquisition and fades with a continuing sample clock."""
import unittest
import json
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image
import sstv

from plugins.bqe_sstv_decoder.decoder import AudioDecoder
from plugins.bqe_sstv_decoder.recovery import LAYOUTS, find_header, recover
from plugins.bqe_sstv_decoder.service import Session


class RecoveryTests(unittest.TestCase):
    def test_every_mode_recovers_correct_rows_after_a_long_fade(self):
        rate = 12000
        for layout in LAYOUTS.values():
            with self.subTest(mode=layout.name):
                mode = layout.mode
                source = Image.new('RGB',(mode.image_width,mode.image_height))
                for row in range(source.height):
                    source.paste((40+row*180//source.height,180-row*120//source.height,50), (0,row,source.width,row+1))
                audio = sstv.encode(source,mode,rate).astype(np.float32)/32768
                header = find_header(audio[:3*rate],rate)
                self.assertIsNotNone(header)
                self.assertEqual(header[0].name,layout.name)
                self.assertAlmostEqual(len(audio)/rate-header[1],layout.duration,delta=.01)
                start = header[1]
                audio[int((start+layout.duration*.25)*rate):int((start+layout.duration*.60)*rate)] = 0
                image = recover(audio,rate,*header)
                self.assertFalse(image.info['sstv_complete'])
                self.assertGreater(image.info['sstv_missing_sync'],10)
                row = int(source.height*.82)
                np.testing.assert_allclose(image.getpixel((source.width//2,row)),source.getpixel((source.width//2,row)),atol=20)
                self.assertEqual(image.getpixel((source.width//2,int(source.height*.4))),(0,0,0))

    def test_live_duration_save_and_no_duplicate_on_finish(self):
        rate = 12000
        source = Image.new('RGB',(320,240),(220,40,30))
        audio = sstv.encode(source,sstv.Mode.ROBOT_36,rate).astype(np.float32)/32768
        audio[10*rate:22*rate] = 0
        saved = []
        decoder = AudioDecoder(rate,lambda image,seconds:saved.append((image,decoder.seconds)))
        for offset in range(0,len(audio),3077):
            decoder.push(audio[offset:offset+3077])
        self.assertEqual(saved,[],'retain reception through the complete expected duration')
        decoder.push(np.zeros(3*rate,dtype=np.float32))
        self.assertEqual(len(saved),1,'save without waiting for stop/EOF or another header')
        self.assertLess(saved[0][1],41)
        self.assertFalse(saved[0][0].info['sstv_complete'])
        np.testing.assert_allclose(saved[0][0].getpixel((160,210)),(220,40,30),atol=20)
        decoder.finish()
        self.assertEqual(len(saved),1)
        self.assertEqual(decoder.parts,[])
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            session.update(source='fade regression')
            session._save(saved[0][0],0)
            path = next(Path(folder).glob('*.json'))
            self.assertIn('-partial-',path.name)
            metadata = json.loads(path.read_text())
            self.assertFalse(metadata['complete'])
            self.assertEqual(metadata['expected_seconds'],36)
            self.assertGreater(metadata['missing_sync'],0)

    def test_noise_during_fade_does_not_shift_returning_picture(self):
        rate = 12000
        source = Image.new('RGB',(320,240),(30,180,60))
        audio = sstv.encode(source,sstv.Mode.ROBOT_36,rate).astype(np.float32)/32768
        header = find_header(audio[:3*rate],rate)
        audio[10*rate:22*rate] *= .001
        audio[10*rate:22*rate] += np.random.default_rng(18).normal(0,.15,12*rate)
        image = recover(audio,rate,*header)
        self.assertFalse(image.info['sstv_complete'])
        np.testing.assert_allclose(image.getpixel((160,210)),(30,180,60),atol=20)

    def test_bad_parity_and_noise_never_start_a_recovery(self):
        rate = 12000
        source = Image.new('RGB',(320,240))
        audio = sstv.encode(source,sstv.Mode.ROBOT_36,rate).astype(np.float32)/32768
        _, end = find_header(audio[:3*rate],rate)
        # Invert the parity bit, leaving leader, start and stop intact.
        left,right = round((end-.06)*rate), round((end-.03)*rate)
        audio[left:right] = .7*np.sin(2*np.pi*1300*np.arange(right-left)/rate)
        self.assertIsNone(find_header(audio[:3*rate],rate))
        self.assertIsNone(find_header(np.zeros(3*rate),rate))
        rng = np.random.default_rng(314)
        for _ in range(20):
            self.assertIsNone(find_header(rng.normal(0,.15,3*rate),rate))

    def test_noisy_vis_with_valid_parity_is_accepted(self):
        rate = 12000
        audio = sstv.encode(Image.new('RGB',(320,240)),sstv.Mode.ROBOT_36,rate).astype(np.float32)/32768
        audio = audio[:3*rate] + np.random.default_rng(12).normal(0,.30,3*rate)
        header = find_header(audio,rate)
        self.assertIsNotNone(header)
        self.assertEqual(header[0].name,'ROBOT_36')


if __name__ == '__main__':
    unittest.main()
