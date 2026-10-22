import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

import numpy as np
import soundfile as sf

from plugins.bqe_aprs_decoder.tests.test_decoder import afsk, frame
from plugins.bqe_aprs_decoder.service import Session
from plugins.bqe_aprs_decoder.integration import APRSReceiver
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler
from plugins.bqe_tlm_decoder.protocols import kiss_encode
from plugins.telemetry_view import catalog, TelemetryArchive

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_file_upload_stereo_logs_and_page(self):
        audio = afsk(frame())
        data = io.BytesIO()
        sf.write(data, np.column_stack((np.zeros(len(audio)), audio)), 48000, format='WAV')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'index.html').write_bytes((ROOT/'index.html').read_bytes())
            session = Session(root/'decoded')
            server = ThreadingHTTPServer(('127.0.0.1',0), handler(session, {}, root=root))
            worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url) as response:
                    self.assertIn('BQE APRS Decoder', response.read().decode())
                request = Request(url+'/file', data=data.getvalue(), headers={
                    'Content-Type':'application/octet-stream','X-Filename':'aprs.wav',
                    'X-Options':json.dumps(dict(channel=1))})
                with urlopen(request,timeout=5) as response:
                    self.assertEqual(response.status,200)
                session.thread.join(10)
                status = session.snapshot()
                self.assertEqual(status['state'],'completed',status)
                self.assertEqual(status['packets'],1,status)
                packet = json.loads(Path(status['log']).read_text())
                self.assertEqual(packet['aprs']['format'],'uncompressed')
                self.assertEqual(Path(status['kiss']).read_bytes(),kiss_encode(frame()))
                self.assertEqual(list((root/'.uploads').iterdir()),[])
            finally:
                session.stop();server.shutdown();server.server_close();worker.join(3)

    def test_managed_process_exits_on_owner_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); wav=folder/'sample.wav'
            sf.write(wav,afsk(frame()),48000)
            with (folder/'console.log').open('w+') as log:
                child=subprocess.Popen([sys.executable,str(ROOT/'bqe_aprs_decoder.py'),
                    '--managed','--file',str(wav),'--port','0','--output',str(folder/'decoded')],
                    stdin=subprocess.PIPE,stdout=log,stderr=log)
                try:
                    status=None; deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        log.seek(0); text=log.read()
                        urls=[line.split('controls: ',1)[1] for line in text.splitlines() if 'controls: http://' in line]
                        if urls:
                            with urlopen(urls[0]+'/status',timeout=3) as response:status=json.load(response)
                            if status['state'] in ('completed','error'):break
                        time.sleep(.1)
                    self.assertIsNotNone(status,text)
                    self.assertEqual(status['state'],'completed',status)
                    self.assertEqual(status['packets'],1)
                    self.assertEqual(status['bauds'],[1200])
                finally:
                    child.stdin.close()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                self.assertEqual(child.returncode,0)

    def test_receiver_options_and_disabled_config(self):
        receiver=APRSReceiver(key='decode_aprs',root=ROOT,port=8781,label='APRS')
        for context in ('satellite_pass','idle_preset'):
            self.assertEqual(receiver.decoder_args({'_aprs_reception_context':context})[-2:],
                             ['--reception-context',context])
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen') as popen:
            self.assertIsNone(receiver.start({},'test'))
            popen.assert_not_called()
            receiver.start(dict(decode_aprs=True,aprs_bauds=[1200,9600]),'test',dict(radio_soundcard='USB Audio'))
            command=popen.call_args.args[0]
            self.assertEqual(command[-3:],['--bauds','1200','9600'])
            receiver.stop();popen.return_value.stdin.close.assert_called_once()
        with self.assertRaises(ValueError):receiver.decoder_args(dict(aprs_bauds=[2400]))

    def test_saved_telemetry_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'satellites.yaml'
            config.write_text('satellites:\n- nickname: iss-aprs\n  satellite_name: ISS\n  catalog_number: 25544\n  decode_aprs: true\n')
            entry=catalog(config)[0]
            self.assertEqual(entry['decoders'][0]['plugin'],'bqe_aprs_decoder')
            output=root/'plugins'/'bqe_aprs_decoder'/'decoded';output.mkdir(parents=True)
            packet=dict(frame_hex=frame().hex(),input_source='iss-aprs',source='N0CALL-7')
            (output/'sample.jsonl').write_text(json.dumps(packet)+'\n')
            self.assertEqual(len(TelemetryArchive().snapshot(root,entry)['packets']),1)


if __name__=='__main__':unittest.main()
