"""IGate tests use isolated settings and a fake loopback server, never APRS-IS."""
import json
import io
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plugins.bqe_aprs_decoder.igate import IGate, gate_packet, GateRejected, position_beacon, BEACON_INTERVAL
from plugins.bqe_aprs_decoder.igate_settings import SettingsStore, serve_settings
from plugins.bqe_aprs_decoder.protocol import parse_packet
from plugins.bqe_aprs_decoder.tests.test_decoder import frame
from plugins.bqe_aprs_decoder.service import Session


def received(payload=None):
    p=parse_packet(frame() if payload is None else frame(payload))
    p['validation']='CRC-16/X-25'
    return p


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'bqe_config').mkdir()
        self.qth=self.root/'bqe_config/my_qth.yaml'
        self.qth.write_text('my_callsign: WB1BQE\n')
        self.store=SettingsStore(self.root)

    def test_qth_callsign_and_write_only_passcode(self):
        self.assertEqual(self.store.public()['callsign'],'WB1BQE')
        public=self.store.save(dict(enabled=True,passcode='12345'))
        self.assertNotIn('passcode',public)
        self.assertNotIn('12345',json.dumps(public))
        self.assertTrue(public['has_passcode'])
        self.qth.write_text('my_call: W1TEST-1\nmy_callsign: WB1BQE\n')
        self.assertEqual(self.store.read()['callsign'],'WB1BQE')
        self.qth.write_text('my_callsign: W1TEST-1\n')
        self.assertEqual(self.store.read()['callsign'],'W1TEST-1')
        self.store.save(dict(enabled=False,clear_passcode=True))
        self.assertFalse(self.store.public()['has_passcode'])
        with self.assertRaises(ValueError):self.store.save(dict(enabled=True))
        with self.assertRaises(ValueError):self.store.save(dict(passcode='website password'))
        with self.assertRaises(ValueError):self.store.save(dict(callsign='W1OTHER'))
        self.assertNotIn('callsign',json.loads(self.store.path.read_text()))
        self.qth.write_text('my_call: W1TEST\n')
        self.assertEqual(self.store.public()['callsign'],'')
        with self.assertRaises(ValueError):self.store.save(dict(enabled=True,passcode='12345'))

    def test_main_settings_endpoint_redacts_and_rejects_foreign_origin(self):
        handler=Mock()
        handler.server.server_port=8080
        handler.client_address=('127.0.0.1',1234)
        body=json.dumps(dict(enabled=False,passcode='12345')).encode()
        handler.headers={'Host':'127.0.0.1:8080','Content-Type':'application/json','Content-Length':str(len(body))}
        handler.rfile=io.BytesIO(body)
        with patch('plugins.bqe_aprs_decoder.igate_settings.SettingsStore',return_value=self.store):
            serve_settings(handler,write=True)
            self.assertEqual(handler.send_bytes.call_args.args[0],200)
            result=json.loads(handler.send_bytes.call_args.args[2])
            self.assertTrue(result['has_passcode'])
            self.assertNotIn('passcode',result)
            self.assertNotIn(b'12345',handler.send_bytes.call_args.args[2])
            handler.headers['Origin']='http://untrusted.example'
            serve_settings(handler,write=True)
            self.assertEqual(handler.send_bytes.call_args.args[0],403)

    def test_gate_paths_limits_and_third_party(self):
        p=received()
        line,key=gate_packet(p,'WB1BQE')
        self.assertIn(b'WIDE1-1*,qAO,WB1BQE:',line)
        self.assertTrue(line.endswith(b'\r\n'))
        for path in ('TCPIP*','TCPXX','NOGATE','RFONLY','qAR'):
            bad=dict(p,tnc2=f'N0CALL-7>APRS,{path}:!4903.50N/07201.75W-Test')
            with self.assertRaises(GateRejected):gate_packet(bad,'WB1BQE')
        for bad in (dict(p,control=0x13),dict(p,validation='unchecked'),dict(p,pid=0),
                    dict(p,tnc2=p['tnc2']+'\nuser injected'),received(b'?APRS?'),received(b'>'+b'x'*510)):
            with self.assertRaises(GateRejected):gate_packet(bad,'WB1BQE')
        third=received(b'}W1TEST>APRS,WIDE2-1:>test')
        line,_=gate_packet(third,'WB1BQE')
        self.assertEqual(line,b'W1TEST>APRS,WIDE2-1,qAO,WB1BQE:>test\r\n')
        with self.assertRaises(GateRejected):gate_packet(received(b'}W1TEST>APRS,TCPIP*:>test'),'WB1BQE')

    def test_files_and_sdr_replays_cannot_upload(self):
        factory=Mock()
        session=Session(self.root/'output',store=self.store,gate_factory=factory)
        with patch('plugins.bqe_tlm_decoder.service.Session._run'):
            session._run({},self.root/'recording.wav',False)
        factory.assert_not_called()
        self.assertEqual(session.reception_context,'recording')
        with patch('plugins.bqe_tlm_decoder.service.Session._run'):
            session._run({'reception_context':'satellite_pass'},None,False)
        self.assertEqual(session.reception_context,'satellite_pass')
        emitted=Mock()
        with patch('plugins.bqe_aprs_decoder.service.AudioDecoder') as decoder:
            session.decoder(48000,emitted,{})
            packet=received()
            decoder.call_args.args[1](packet)
        self.assertEqual(emitted.call_args.args[0]['reception_context'],'satellite_pass')
        session.igate=Mock()
        session.input_ready(SimpleNamespace(sdr_url='http://127.0.0.1/audio'),SimpleNamespace(live_rf=False))
        session.igate.start.assert_not_called()
        session.input_ready(SimpleNamespace(sdr_url='http://127.0.0.1/audio'),SimpleNamespace(live_rf=True))
        session.igate.start.assert_called_once()

    def test_rf_line_endings_and_mice_bytes_are_preserved(self):
        payload=b'!4158.29N/07110.79WoPinPoint v2.1  '
        baseline,key=gate_packet(received(payload),'WB1BQE')
        for ending in (b'\r',b'\n',b'\r\n',b'\r\n\r\n'):
            packet=received(payload+ending)
            original=packet['tnc2']
            line,normalized_key=gate_packet(packet,'WB1BQE')
            self.assertEqual(line,baseline)
            self.assertEqual(normalized_key,key)
            self.assertEqual(packet['tnc2'],original)
            self.assertTrue(line.endswith(b'v2.1  \r\n'))
        mice=b'`c9XnH\x1c>/]&'
        line,_=gate_packet(received(mice+b'\r'),'WB1BQE')
        self.assertTrue(line.endswith(b':'+mice+b'\r\n'))
        line,_=gate_packet(received(b'`\x1c\x1d\x1e\x1f\x7fX>/]'),'WB1BQE')
        self.assertIn(b'\x1c\x1d\x1e\x1f\x7f',line)
        for separator in (b'\r',b'\n',b'\r\n',b'\x00'):
            with self.assertRaises(GateRejected):
                gate_packet(received(b'>status'+separator+b'W1TEST>APRS:injected'),'WB1BQE')
        with self.assertRaises(GateRejected):
            gate_packet(received(b'}W1TEST>APRS,TCPIP*:>status\r'),'WB1BQE')

    def fake_server(self, verified=True):
        listener=socket.socket()
        listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
        port=listener.getsockname()[1]
        lines=[]; done=threading.Event()
        def serve():
            try:
                conn,_=listener.accept()
                with conn:
                    conn.settimeout(.2)
                    conn.sendall(b'# local test server\r\n')
                    buffer=b''
                    while not done.is_set():
                        try:data=conn.recv(4096)
                        except socket.timeout:continue
                        if not data:break
                        buffer+=data
                        while b'\n' in buffer:
                            line,buffer=buffer.split(b'\n',1)
                            if line.startswith(b'user '):
                                # Do not retain or print the test login/passcode.
                                conn.sendall(b'# logresp WB1BQE '+(b'verified' if verified else b'unverified')+b', server TEST\r\n')
                            else:lines.append(line)
            finally:listener.close()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        self.addCleanup(worker.join,6);self.addCleanup(done.set)
        self.store.save(dict(enabled=True,passcode='12345',host='127.0.0.1',port=port))
        return lines

    def wait(self,predicate):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if predicate():return
            time.sleep(.02)
        self.fail('Timed out waiting for fake server or IGate')

    def test_verified_connection_duplicates_and_disable(self):
        lines=self.fake_server()
        gate=IGate(self.store);self.addCleanup(gate.close);gate.start()
        self.wait(lambda:gate.snapshot()['state']=='connected')
        p=dict(received(),upload_id='a'*32);gate.submit(p);gate.submit(p)
        changed=dict(p,tnc2=p['tnc2'].replace('WIDE1-1*','WIDE2-2*'));gate.submit(changed)
        self.wait(lambda:len(lines)==1)
        self.assertIn(b'qAO,WB1BQE',lines[0])
        receipt=self.store.folder/'upload_receipts'/('a'*32+'.json')
        self.wait(receipt.exists)
        self.assertTrue(json.loads(receipt.read_text())['uploaded_at'])
        self.assertEqual(gate.snapshot()['filtered'],2)
        self.store.save(dict(enabled=False))
        self.wait(lambda:gate.snapshot()['state']=='disabled')
        gate.submit(received(b'>should not upload'))
        time.sleep(.15)
        self.assertEqual(len(lines),1)
        self.assertNotIn('12345',json.dumps(gate.snapshot()))

    def test_unverified_connection_sends_no_packets(self):
        self.qth.write_text('my_callsign: WB1BQE\nmy_latitude: 42.7833\nmy_longitude: -71.5167\n')
        lines=self.fake_server(False)
        gate=IGate(self.store);self.addCleanup(gate.close);gate.start()
        self.wait(lambda:gate.snapshot()['state']=='error')
        gate.submit(dict(received(),upload_id='b'*32))
        time.sleep(.15)
        self.assertEqual(lines,[])
        self.assertFalse((self.store.folder/'upload_receipts'/('b'*32+'.json')).exists())

    def test_verified_login_sends_station_position(self):
        self.qth.write_text('my_callsign: WB1BQE\nmy_latitude: 42.7833\nmy_longitude: -71.5167\n')
        lines=self.fake_server()
        gate=IGate(self.store);self.addCleanup(gate.close);gate.start()
        self.wait(lambda:len(lines)==1)
        import aprslib
        packet=aprslib.parse(lines[0].decode())
        self.assertEqual(packet['from'],'WB1BQE')
        self.assertEqual(packet['path'],['TCPIP*'])
        self.assertAlmostEqual(packet['latitude'],42.7833,places=4)
        self.assertAlmostEqual(packet['longitude'],-71.5167,places=4)
        self.assertEqual(gate.snapshot()['beacons_sent'],1)
        self.assertTrue(gate.snapshot()['last_beacon_at'])
        self.assertEqual(gate.snapshot()['sent'],0)

    def test_beacon_interval_position_changes_and_failed_send(self):
        config=dict(callsign='WB1BQE',latitude=-12.999999,longitude=179.999999)
        self.assertIn(b'!1300.00SI18000.00E&',position_beacon(config))
        gate=IGate(self.store);connection=Mock()
        gate.send_beacon_if_due(connection,config,100)
        gate.send_beacon_if_due(connection,config,101)
        connection.sendall.assert_called_once()
        gate.send_beacon_if_due(connection,config,100+BEACON_INTERVAL)
        self.assertEqual(connection.sendall.call_count,2)
        config['latitude']=0
        gate.send_beacon_if_due(connection,config,101+BEACON_INTERVAL)
        self.assertEqual(connection.sendall.call_count,3)
        connection.sendall.side_effect=OSError('disconnected')
        with self.assertRaises(OSError):gate.send_beacon_if_due(connection,config,101+2*BEACON_INTERVAL)
        self.assertEqual(gate.snapshot()['beacons_sent'],3)
        connection.sendall.side_effect=None
        gate.send_beacon_if_due(connection,config,101+2*BEACON_INTERVAL)
        self.assertEqual(gate.snapshot()['beacons_sent'],4)

    def test_invalid_qth_position_skips_beacon(self):
        for value in ('null','true','.nan','91','bad'):
            self.qth.write_text(f'my_callsign: WB1BQE\nmy_latitude: {value}\nmy_longitude: 0\n')
            config=self.store.read()
            self.assertIsNone(position_beacon(config))
            gate=IGate(self.store);connection=Mock()
            gate.send_beacon_if_due(connection,config,100)
            connection.sendall.assert_not_called()
            self.assertIn('skipped',gate.snapshot()['beacon_message'])

    def test_stale_packets_are_discarded(self):
        lines=self.fake_server()
        gate=IGate(self.store);self.addCleanup(gate.close)
        line,_=gate_packet(received(),'WB1BQE')
        gate.queue.put((time.monotonic()-20,line,'c'*32))
        gate.start()
        self.wait(lambda:gate.snapshot()['dropped']==1)
        self.assertEqual(lines,[])
        self.assertFalse((self.store.folder/'upload_receipts'/('c'*32+'.json')).exists())

    def test_bounded_queue(self):
        gate=IGate(self.store)
        gate.config=dict(callsign='WB1BQE')
        gate.status['state']='connected'
        for index in range(105):gate.submit(received(f'>test {index}'.encode()))
        self.assertEqual(gate.queue.qsize(),100)
        self.assertEqual(gate.snapshot()['dropped'],5)


if __name__=='__main__':unittest.main()
