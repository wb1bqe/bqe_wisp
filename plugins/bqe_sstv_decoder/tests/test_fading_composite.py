"""Weak-signal estimates, conservative repeat matching, and WAV regression."""
import os
import json
import tempfile
from pathlib import Path
import unittest

import numpy as np
from PIL import Image
from scipy.signal import butter, hilbert, sosfilt
import soundfile as sf
import sstv

from plugins.bqe_sstv_decoder.composite import FragmentCombiner
from plugins.bqe_sstv_decoder.decoder import AudioDecoder
from plugins.bqe_sstv_decoder.service import Session
from plugins.bqe_sstv_decoder.recovery import tone_track, find_header, recover


class FadingTests(unittest.TestCase):
    def test_spectral_estimate_reduces_weak_tone_error(self):
        rate=12000
        a=.2*np.sin(2*np.pi*2100*np.arange(rate*2)/rate)
        a+=np.random.default_rng(42).normal(0,.3,len(a))
        a=sosfilt(butter(6,2700,fs=rate,output='sos'),a)
        phase=hilbert(a)
        old=np.angle(phase[1:]*phase[:-1].conj())*rate/(2*np.pi)
        hz,q=tone_track(a,rate)
        weight=np.clip((q[:-1]-.65)/.25,0,1)
        new=old*weight+hz[:-1]*(1-weight)
        self.assertLess(np.mean(abs(new[100:-100]-2100)),np.mean(abs(old[100:-100]-2100))*.4)
        self.assertTrue(np.all(np.isfinite(q)))
        self.assertTrue(np.all(tone_track(np.zeros(rate),rate)[1]==0))

    def test_false_sync_during_fade_cannot_accumulate_timing_drift(self):
        rate=12000
        y,x=np.mgrid[:240,:320]
        source=np.stack((30+x*.6,40+y*.6,200-x*.4),axis=-1).astype('uint8')
        audio=sstv.encode(Image.fromarray(source),sstv.Mode.ROBOT_36,rate).astype('float32')/32768
        header=find_header(audio[:3*rate],rate)
        start=header[1]
        left,right=round((start+40*.15)*rate),round((start+120*.15)*rate)
        audio[left:right]=0
        # A spurious sync train walks one millisecond per line during a fade.
        # Accumulating nearest-pulse corrections follows it out of alignment.
        for row in range(40,120):
            a=round((start+row*.15+(row-40)*.001)*rate)
            b=a+round(.009*rate)
            audio[a:b]=.7*np.sin(2*np.pi*1200*np.arange(b-a)/rate)
        recovered=recover(audio,rate,*header)
        actual=np.asarray(recovered)[170:220,40:280].astype(float)
        self.assertLess(np.mean(abs(actual-source[170:220,40:280])),12)

    def test_composite_uses_good_fragments_and_rejects_shared_banner(self):
        rng=np.random.default_rng(21)
        # Textured interior with enough spatial contrast to establish identity.
        base=rng.integers(30,225,(240,320,3),dtype=np.uint8)
        first=base.astype(float)+rng.normal(0,3,base.shape)
        second=base.astype(float)+rng.normal(0,3,base.shape)
        qa=np.ones((240,320),dtype='float32')
        qb=qa.copy()
        first[70:95]=rng.integers(0,255,(25,320,3));qa[70:95]=.05
        second[145:170]=rng.integers(0,255,(25,320,3));qb[145:170]=.05
        def picture(data,q):
            im=Image.fromarray(np.clip(data,0,255).astype('uint8'))
            im.info.update(sstv_mode=sstv.Mode.ROBOT_36,sstv_confidence=q,sstv_complete=False)
            return im
        a,b=picture(first,qa),picture(second,qb)
        combiner=FragmentCombiner()
        self.assertIsNone(combiner.add(a,10))
        result=combiner.add(b,170)
        self.assertIsNotNone(result)
        self.assertEqual(result.info['sstv_composite_sources'],[10.,170.])
        with tempfile.TemporaryDirectory() as folder:
            session=Session(folder)
            session.update(source='repeated-fragment regression')
            session._save(a,10)
            session._save(b,170)
            session._save(result,170)
            self.assertEqual(len(list(Path(folder).glob('*.png'))),3)
            metadata=json.loads(next(Path(folder).glob('*-composite-*.json')).read_text())
            self.assertEqual(metadata['composite_sources'],[10.,170.])
            self.assertFalse(metadata['complete'])
        original_error=np.mean(abs(np.asarray(a,dtype=float)-base))
        combined_error=np.mean(abs(np.asarray(result,dtype=float)-base))
        self.assertLess(combined_error,original_error*.4)
        different=base.copy();different[48:192]=rng.integers(0,255,(144,320,3))
        self.assertIsNone(combiner.add(picture(different,np.ones_like(qa)),330))
        # Disjoint fragments cannot establish that they represent the same image.
        q1=np.zeros_like(qa);q1[:100]=1
        q2=np.zeros_like(qa);q2[140:]=1
        self.assertFalse(FragmentCombiner.match(picture(base,q1),picture(base,q2)))
        flat=np.full_like(base,90)
        self.assertFalse(FragmentCombiner.match(picture(flat,qa),picture(flat,qb)))
        np.testing.assert_array_equal(np.asarray(a),np.clip(first,0,255).astype('uint8'))

    @unittest.skipUnless(os.environ.get('SSTV_RECORDING_REGRESSION'), 'set SSTV_RECORDING_REGRESSION to the ISS WAV')
    def test_iss_recording_recovers_three_distinct_receptions_without_false_merge(self):
        path=Path(os.environ['SSTV_RECORDING_REGRESSION'])
        images=[]
        with sf.SoundFile(path) as audio:
            decoder=AudioDecoder(audio.samplerate,lambda im,t:images.append((im,t)),combine_fragments=True)
            for block in audio.blocks(blocksize=8192,dtype='float32',always_2d=True):
                decoder.push(block[:,0])
            decoder.finish()
        self.assertEqual(len(images),3)
        for (image,start),expected in zip(images,[219,379,537]):
            self.assertAlmostEqual(start,expected,delta=1)
            self.assertEqual(image.size,(320,240))
            self.assertEqual(str(image.info['sstv_mode']),'Mode.ROBOT_36')
            self.assertNotIn('sstv_composite_sources',image.info)
            self.assertGreater(np.std(np.asarray(image)[-30:]),15)
            self.assertEqual(image.info['sstv_missing_rows'],0)


if __name__=='__main__':
    unittest.main()
