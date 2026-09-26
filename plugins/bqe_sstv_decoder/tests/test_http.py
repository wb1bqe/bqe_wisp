import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from PIL import Image
import sstv

from plugins.bqe_sstv_decoder.bqe_sstv_decoder import handler
from plugins.bqe_sstv_decoder.service import Session


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.session, {'channel': 0}))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.session.stop()
        if self.session.thread:
            self.session.thread.join(10)
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_upload_decodes_and_serves_jpeg(self):
        self.assertEqual(self.request('GET', '/')[0], 200)
        wav = sstv.encode_to_wav(Image.new('RGB', (320,240), 'red'), sstv.Mode.ROBOT_36, 12000)
        code, body = self.request('POST', '/file', wav, {'Content-Type': 'application/octet-stream', 'X-Filename': 'test.wav'})
        self.assertEqual(code, 200, body)
        self.session.thread.join(15)
        status = json.loads(self.request('GET', '/status')[1])
        self.assertEqual(status['images'], 1, status)
        name = status['recent'][0]['filename']
        code, jpg = self.request('GET', '/image/'+name)
        self.assertEqual(code, 200)
        self.assertTrue(jpg.startswith(b'\xff\xd8'))
        code, preview = self.request('GET', '/preview.jpg?v=1')
        self.assertEqual(code, 200)
        self.assertTrue(preview.startswith(b'\xff\xd8'))
        self.assertEqual(self.request('GET', '/image/../../general_settings.yaml')[0], 404)

    def test_origin_and_invalid_upload(self):
        self.assertEqual(self.request('POST', '/stop', '{}', {'Content-Type':'application/json', 'Origin':'https://example.com'})[0], 403)
        self.assertEqual(self.request('POST', '/file', 'bad', {'Content-Type':'application/octet-stream', 'X-Filename':'bad.exe'})[0], 400)


if __name__ == '__main__':
    unittest.main()
