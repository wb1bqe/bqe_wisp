import json
from functools import partial
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
import bqe_wisp_web as web
from plugins import weather_gallery as gallery


class WeatherGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = gallery.archive_root(self.root)

    def product(self, job, captured=None):
        folder = self.base / job
        (folder / 'products' / 'MSU-MR').mkdir(parents=True)
        if captured:
            (folder / 'job.json').write_text(json.dumps(dict(captured_at=captured, options=dict(satellite='M2-4'))))
        name = job + '/products/MSU-MR/channel & 1.png'
        Image.new('RGB', (1600, 900), 'blue').save(self.base / name)
        return name

    def test_grouping_uses_capture_time_and_finds_unlisted_products(self):
        self.assertIn('No LRPT images', gallery.gallery_html(self.root))
        self.product('20261003T074114Z-replay', '2026-09-30T08:00:00Z')
        self.product('20261002T192534Z-original')
        groups = gallery.passes(self.root)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['id'], '20261002T192534Z-original')
        page = gallery.gallery_html(self.root)
        self.assertIn('2026-09-30 · 08:00:00 UTC', page)
        self.assertIn('channel &amp; 1.png', page)
        self.assertEqual(page.count('class="card"'), 2)
        self.assertIn("window.open(card.href,'_blank'", page)

    def test_original_and_thumbnail_and_path_restrictions(self):
        name = self.product('20261003T074114Z-test')
        image = gallery.resolve_image(self.root, name)
        original = image.read_bytes()
        with Image.open(BytesIO(gallery.thumbnail(image))) as thumb:
            self.assertLessEqual(thumb.width, 360)
            self.assertLessEqual(thumb.height, 240)
        self.assertEqual(image.read_bytes(), original)
        for bad in ('../outside.png', str(image), name.replace('MSU-MR/', '../'), 'job/job.json', ''):
            self.assertIsNone(gallery.resolve_image(self.root, bad))
        self.assertIn('max-width:none', gallery.viewer_html(name))

    def test_delete_selected_pass_includes_images_iq_logs_preserves_other_pass(self):
        name = self.product('selected', '2026-10-04T18:00:00Z')
        keep = self.product('keep')
        folder = self.base/'selected'
        rotated = folder/'products/north_up/channel.png'
        rotated.parent.mkdir()
        rotated.write_bytes(b'image')
        for relative in ('capture.sigmf-data', 'capture.sigmf-meta', 'satdump.log', 'products/MSU-MR/product.cbor'):
            (folder/relative).write_bytes(b'keep')
        job = dict(state='completed', images=['products/MSU-MR/channel & 1.png', 'products/north_up/channel.png'],
                   orientation=dict(replacements={'products/MSU-MR/channel & 1.png':'products/north_up/channel.png'}))
        (folder/'job.json').write_text(json.dumps(job))
        page = gallery.gallery_html(self.root)
        self.assertIn('class="pass-select"', page)
        self.assertIn('Delete selected passes’ images, recordings and logs', page)
        result = gallery.delete_pass_images(self.root, ['selected'])
        self.assertEqual(len(result['deleted']), 5)
        self.assertEqual(result['completed'], ['selected'])
        self.assertEqual(result['errors'], [])
        self.assertFalse((self.base/name).exists())
        self.assertFalse(rotated.exists())
        self.assertTrue((self.base/keep).exists())
        for relative in ('capture.sigmf-data', 'capture.sigmf-meta', 'satdump.log'):
            self.assertFalse((folder/relative).exists())
        self.assertEqual((folder/'products/MSU-MR/product.cbor').read_bytes(), b'keep')
        self.assertEqual(json.loads((folder/'job.json').read_text())['images'], [])
        self.assertEqual([group['id'] for group in gallery.passes(self.root)], ['keep'])

    def test_delete_rejects_bad_selection_and_active_pass_before_any_deletion(self):
        name = self.product('good')
        self.product('active')
        (self.base/'active/job.json').write_text(json.dumps(dict(state='decoding')))
        for selected in ([], 'good', ['good', '../outside'], ['good', 'missing'],
                         ['good', 'good'], ['good', 'active'], ['good', 1]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                gallery.delete_pass_images(self.root, selected)
            self.assertTrue((self.base/name).exists())

    def test_delete_reports_locked_images(self):
        name = self.product('locked')
        with patch.object(Path, 'unlink', side_effect=PermissionError('File in use')):
            result = gallery.delete_pass_images(self.root, ['locked'])
        self.assertEqual(result['deleted'], [])
        self.assertEqual(result['completed'], [])
        self.assertEqual(result['errors'][0]['pass_id'], 'locked')
        self.assertTrue((self.base/name).exists())

    def test_http_gallery_viewer_thumbnail_original_and_missing(self):
        name = self.product('20261003T074114Z-test')
        with patch.object(web, 'SCRIPT_DIR', self.root):
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(web.WebConsoleHandler, status_payload_func=lambda: {}))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for route, content_type in (('/weather', 'text/html'), ('/weather-viewer', 'text/html'),
                                            ('/weather-thumbnail', 'image/jpeg'), ('/weather-image', 'image/png')):
                    with urlopen(base + gallery.url(route, name)) as response:
                        self.assertIn(content_type, response.headers['Content-Type'])
                        data = response.read()
                        if route == '/weather-image':
                            self.assertEqual(data, (self.base / name).read_bytes())
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + gallery.url('/weather-image', '../outside.png'))
                self.assertEqual(error.exception.code, 404)
                body = json.dumps(dict(passes=['20261003T074114Z-test'])).encode()
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/weather/delete', data=body, headers={
                        'Content-Type':'application/json', 'Origin':'https://other.invalid'}))
                self.assertEqual(error.exception.code, 403)
                self.assertTrue((self.base/name).exists())
                with urlopen(Request(base+'/api/weather/delete', data=body,
                                     headers={'Content-Type':'application/json'})) as response:
                    self.assertEqual(json.load(response)['completed'], ['20261003T074114Z-test'])
                self.assertFalse((self.base/name).exists())
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
