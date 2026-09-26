import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from plugins.decoder_http import DecoderServer
from plugins.bqe_ssdv_decoder.bqe_ssdv_decoder import handler
from plugins.bqe_ssdv_decoder.service import Session
from plugins.bqe_ssdv_decoder.tests.test_decoder import packets
from plugins.bqe_tlm_decoder.protocols import kiss_encode


class HttpTests(unittest.TestCase):
    def test_upload_preview_saved_image_and_origin_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            server = DecoderServer(('127.0.0.1', 0), handler(session, {}))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            def request(method, path, body=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                try:
                    conn.request(method, path, body, headers or {})
                    response=conn.getresponse()
                    return response.status, response.read()
                finally:
                    conn.close()
            try:
                code, page = request('GET', '/')
                self.assertEqual(code,200)
                self.assertIn(b'RS61 / 239ALFEROV',page)
                self.assertIn(b'waterfall',page)
                data=b''.join(kiss_encode(p) for p in packets())
                code, body=request('POST','/file',data,{'Content-Type':'application/octet-stream','X-Filename':'rs61.kiss'})
                self.assertEqual(code,200,body)
                session.thread.join(10)
                status=json.loads(request('GET','/status')[1])
                self.assertEqual(status['images'],1,status)
                filename=status['recent'][0]['filename']
                self.assertTrue(request('GET','/image/'+filename)[1].startswith(b'\xff\xd8'))
                self.assertTrue(request('GET','/preview.jpg')[1].startswith(b'\xff\xd8'))
                self.assertEqual(request('GET','/image/../../requirements.txt')[0],404)
                self.assertEqual(request('POST','/stop','{}',{'Content-Type':'application/json','Origin':'https://example.com'})[0],403)
            finally:
                session.stop()
                if session.thread:
                    session.thread.join(5)
                server.shutdown();server.server_close();worker.join(5)
