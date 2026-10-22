import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import http.client
from functools import partial
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import unittest

import numpy as np
from PIL import Image

from plugins.bqe_sstv_decoder.enhance import enhance_files, translated, load_image, alignment
from plugins.bqe_sstv_decoder.storage import load_confidence, save_confidence
from plugins.bqe_sstv_decoder.service import Session
from plugins.sstv_enhancement import EnhancementJob
from bqe_wisp_web import build_sstv_gallery_html, list_sstv_jpg_files, resolve_sstv_image_file
from bqe_wisp_web import WebConsoleHandler


class GalleryEnhancementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        y,x = np.mgrid[:240,:320]
        self.pixels = np.clip(np.stack((120+90*np.sin(x/18),120+90*np.cos(y/13),
                                      110+80*np.sin((x+y)/22)),axis=-1),0,255).astype('uint8')

    def save(self, name, pixels, quality=None):
        image = Image.fromarray(np.uint8(pixels))
        path = self.root/name
        image.save(path)
        if quality is not None:
            save_confidence(path,image,quality)
        return path

    def test_png_and_confidence_round_trip_and_stale_sidecar_rejection(self):
        quality=np.random.default_rng(12).random((240,320),dtype=np.float32)
        image=Image.fromarray(self.pixels)
        image.info.update(sstv_mode='ROBOT_36',sstv_complete=False,sstv_confidence=quality)
        session=Session(self.root)
        session.update(source='unit test')
        session._save(image,12)
        path=next(self.root.glob('*.png'))
        with Image.open(path) as saved:
            np.testing.assert_array_equal(np.asarray(saved),self.pixels)
            np.testing.assert_array_equal(load_confidence(path,saved),quality)
        Image.new('RGB',(320,240),'white').save(path)
        with Image.open(path) as saved:
            self.assertIsNone(load_confidence(path,saved))

    def test_aligned_composite_reduces_complementary_noise_and_preserves_originals(self):
        rng=np.random.default_rng(31)
        a=self.pixels.copy();b=self.pixels.copy()
        qa=np.ones((240,320),dtype=np.float32);qb=qa.copy()
        a[65:105]=rng.integers(0,255,(40,320,3));qa[65:105]=.02
        b[145:185]=rng.integers(0,255,(40,320,3));qb[145:185]=.02
        pa=self.save('a.png',a,qa)
        pb=self.save('b.png',translated(b,5,-4),translated(qb,5,-4))
        hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in (pa,pb)]
        result=enhance_files(self.root,['a.png','b.png'])
        self.assertEqual(result['state'],'completed')
        self.assertEqual(len(result['outputs']),1)
        output=result['outputs'][0]
        self.assertEqual(output['sources'][1]['offset_y'],-5)
        self.assertEqual(output['sources'][1]['offset_x'],4)
        with Image.open(self.root/output['filename']) as image:
            actual=np.asarray(image,dtype=float)
            self.assertLess(np.mean(abs(actual[10:-10,10:-10]-self.pixels[10:-10,10:-10])),1)
            self.assertIsNotNone(load_confidence(self.root/output['filename'],image))
        self.assertEqual(hashes,[hashlib.sha256(p.read_bytes()).hexdigest() for p in (pa,pb)])

    def test_old_jpeg_pair_and_unrelated_interior(self):
        self.save('a.jpg',self.pixels)
        self.save('b.bmp',self.pixels)
        different=self.pixels.copy()
        different[48:192]=255-different[48:192]
        self.save('different.png',different)
        result=enhance_files(self.root,['a.jpg','b.bmp','different.png'])
        self.assertEqual(len(result['outputs']),1)
        self.assertEqual(result['outputs'][0]['confidence_kind'],'estimated')
        self.assertEqual(result['unmatched'],['different.png'])

    def test_bad_image_is_skipped_and_cancel_preserves_inputs(self):
        self.save('a.png',self.pixels)
        (self.root/'broken.png').write_bytes(b'bad')
        result=enhance_files(self.root,['a.png','broken.png'])
        self.assertEqual(len(result['skipped']),1)
        self.assertEqual(result['outputs'],[])
        result=enhance_files(self.root,['a.png','broken.png'],cancelled=lambda:True)
        self.assertEqual(result['state'],'cancelled')
        self.assertTrue((self.root/'a.png').exists())

    def test_gallery_formats_selection_and_path_restrictions(self):
        for name in ('a.png','b.jpg','c.jpeg','d.bmp'):
            self.save(name,self.pixels)
        (self.root/'hidden.confidence.npz').write_bytes(b'not an image')
        _,images,_=list_sstv_jpg_files(str(self.root))
        self.assertEqual(len(images),4)
        page=build_sstv_gallery_html(str(self.root))
        self.assertEqual(page.count('class="enhance-select"'),4)
        self.assertIn('Aggressive Multi-image Enhancement',page)
        self.assertIn('/sstv-enhancement.js',page)
        for name in ('../a.png','a.confidence.npz','missing.png'):
            self.assertIsNone(resolve_sstv_image_file(str(self.root),name))
        for names in (['a.png','../b.jpg'],['a.png','a.png'],['a.png'],[{},'a.png']):
            with self.assertRaises(ValueError):
                EnhancementJob().start(self.root,self.root,names)

    def test_background_worker_saves_result_and_reports_completion(self):
        self.save('a.png',self.pixels)
        self.save('b.png',self.pixels)
        job=EnhancementJob()
        project=Path(__file__).resolve().parents[3]
        job.start(project,self.root,['a.png','b.png'])
        try:
            job.process.wait(timeout=30)
            result=job.snapshot()
            self.assertEqual(result['state'],'completed',result)
            self.assertEqual(len(result['outputs']),1)
            self.assertTrue((self.root/result['outputs'][0]['filename']).exists())
        finally:
            if job.process.poll() is None:
                job.process.kill()
                job.process.wait()

    def test_http_png_and_same_origin_job_requests(self):
        self.save('a.png',self.pixels)
        handler=partial(WebConsoleHandler,status_payload_func=lambda:{},sstv_gallery_location=str(self.root))
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def request(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request(method,path,body,headers or {})
                response=connection.getresponse()
                return response.status,response.getheader('Content-Type'),response.read()
            finally:
                connection.close()
        try:
            code,mime,body=request('GET','/sstv-image?name=a.png')
            self.assertEqual((code,mime),(200,'image/png'))
            self.assertTrue(body.startswith(b'\x89PNG'))
            self.assertEqual(request('GET','/sstv-enhancement.js')[0],200)
            with patch('bqe_wisp_web.ENHANCEMENT_JOB') as job:
                job.start.return_value={'state':'running','outputs':[]}
                code,_,_=request('POST','/api/sstv/enhancement','{"names":["a.png","b.png"]}',{'Content-Type':'application/json'})
                self.assertEqual(code,200)
                self.assertEqual(job.start.call_args.args[2],['a.png','b.png'])
                job.cancel.return_value={'state':'running','outputs':[]}
                self.assertEqual(request('POST','/api/sstv/enhancement/cancel','{}',{'Content-Type':'application/json'})[0],200)
                job.cancel.assert_called_once()
                code,_,_=request('POST','/api/sstv/enhancement','{}',{'Content-Type':'application/json','Origin':'https://example.com'})
                self.assertEqual(code,403)
                job.start.assert_called_once()
        finally:
            server.shutdown();server.server_close();thread.join()

    @unittest.skipUnless(os.environ.get('SSTV_GALLERY_REGRESSION'), 'set SSTV_GALLERY_REGRESSION to replay-report.json')
    def test_real_pass_repeat_matches_but_shared_education_banner_does_not(self):
        report=json.loads(Path(os.environ['SSTV_GALLERY_REGRESSION']).read_text())
        root=Path(report['output'])
        items=[load_image(root/record['filename']) for record in report['images']]
        self.assertEqual(alignment(items[0],items[7]),(0,0))
        self.assertIsNone(alignment(items[9],items[10]))


if __name__=='__main__':
    unittest.main()
