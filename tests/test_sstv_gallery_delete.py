import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import bqe_wisp_web as web


class SSTVDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gallery = self.root/'gallery'
        self.gallery.mkdir()
        for name in ('first.png', 'second.jpg', 'keep.bmp', 'first.json'):
            (self.gallery/name).write_bytes(b'test')
        self.worker = patch.object(web.ENHANCEMENT_JOB, 'process', None)
        self.worker.start()
        self.addCleanup(self.worker.stop)

    def test_only_selected_images_are_deleted(self):
        result = web.delete_sstv_images(str(self.gallery), ['first.png', 'second.jpg'])
        self.assertEqual(result['deleted'], ['first.png', 'second.jpg'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(sorted(p.name for p in self.gallery.iterdir()), ['first.json', 'keep.bmp'])

    def test_bad_selection_cannot_partially_delete(self):
        for names in ([], 'first.png', ['first.png', '../outside.png'],
                      ['first.png', 'first.json'], ['first.png', 'missing.png'],
                      ['first.png', 'first.png'], ['first.png', 3]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                web.delete_sstv_images(str(self.gallery), names)
            self.assertTrue((self.gallery/'first.png').exists())

    def test_running_enhancement_blocks_deletion(self):
        with patch.object(web.ENHANCEMENT_JOB, 'process', Mock(poll=Mock(return_value=None))):
            with self.assertRaisesRegex(ValueError, 'enhancement'):
                web.delete_sstv_images(str(self.gallery), ['first.png'])
        self.assertTrue((self.gallery/'first.png').exists())

    def test_file_failure_is_reported_without_claiming_deletion(self):
        unlink = Path.unlink
        def fail_one(path, *args, **kwargs):
            if path.name == 'first.png':
                raise PermissionError('File in use')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_one):
            result = web.delete_sstv_images(str(self.gallery), ['first.png', 'second.jpg'])
        self.assertEqual(result['deleted'], ['second.jpg'])
        self.assertEqual(result['errors'][0]['name'], 'first.png')
        self.assertTrue((self.gallery/'first.png').exists())

    def test_http_and_button(self):
        self.assertIn('id="deleteImagesButton" disabled>Delete selected images',
                      web.build_sstv_gallery_html(str(self.gallery)))
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(
            web.WebConsoleHandler, status_payload_func=lambda: {}, sstv_gallery_location=str(self.gallery)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/sstv/delete'
        try:
            body = json.dumps(dict(names=['first.png'])).encode()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, data=body, headers={'Content-Type':'application/json', 'Origin':'https://elsewhere.invalid'}))
            self.assertEqual(error.exception.code, 403)
            self.assertTrue((self.gallery/'first.png').exists())
            with urlopen(Request(url, data=body, headers={'Content-Type':'application/json'})) as response:
                self.assertEqual(json.load(response)['deleted'], ['first.png'])
            self.assertTrue((self.gallery/'second.jpg').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
