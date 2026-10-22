"""Received positions, growing history, invalid data and web integration."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from plugins.aprs_spots import APRSSpotsArchive, position_report
import bqe_wisp_web as web


def packet(**changes):
    result = dict(frame_hex='0011', source='N0CALL-7', input_source='iss-aprs',
                  decoded_at='2026-10-02T22:00:00Z', audio_seconds=1.5,
                  aprs=dict(format='uncompressed', latitude=49.05, longitude=-72.02))
    result.update(changes)
    return result


class APRSSpotsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root/'plugins'/'bqe_aprs_decoder'/'decoded'
        self.folder.mkdir(parents=True)
        self.archive = APRSSpotsArchive()

    def write(self, packets):
        path = self.folder/'capture.jsonl'
        path.write_text(''.join(json.dumps(p)+'\n' for p in packets), encoding='utf-8')
        return path

    def test_formats_thirdparty_and_removed_objects(self):
        for kind in ('uncompressed', 'compressed', 'mic-e'):
            report = position_report(packet(aprs=dict(format=kind,latitude=0,longitude=0)))
            self.assertEqual(report['latitude'],0)
            self.assertEqual(report['callsign'],'N0CALL-7')
        inner=dict(format='object', object_name='BALLOON  ', alive=False,
                   latitude=45,longitude=-70,path=['WIDE1-1*'], **{'from':'N1TEST'})
        report=position_report(packet(aprs=dict(format='thirdparty',subpacket=inner)))
        self.assertEqual(report['callsign'],'N1TEST')
        self.assertEqual(report['name'],'BALLOON')
        self.assertEqual(report['kind'],'object')
        self.assertFalse(report['active'])
        self.assertEqual(report['path'],['WIDE1-1*'])

    def test_invalid_or_missing_coordinates(self):
        for bad in (None, '42', True, float('nan'), float('inf'), 91, -91, 10**400):
            self.assertIsNone(position_report(packet(aprs=dict(latitude=bad,longitude=0))))
        for longitude in (-181, 181):
            self.assertIsNone(position_report(packet(aprs=dict(latitude=0,longitude=longitude))))
        self.assertIsNone(position_report(packet(aprs=dict(format='message',message_text='Hello'))))
        self.assertIsNone(position_report(packet(aprs=None)))

    def test_history_growth_limits_and_existing_logs(self):
        self.assertEqual(self.archive.snapshot(self.root)['spots'],[])
        # Existing logs have no normalized 'spot' field; parsed APRS is sufficient.
        path=self.write([packet(),packet(aprs=dict(format='status',status='Ready'))])
        first=self.archive.snapshot(self.root)['spots']
        self.assertEqual(len(first),1)
        new=packet(decoded_at='2026-10-02T22:01:00Z')
        with path.open('a') as stream:stream.write('bad json\n'+json.dumps(new))
        self.assertEqual(self.archive.snapshot(self.root)['spots'],first)
        with path.open('a') as stream:stream.write('\n')
        result=self.archive.snapshot(self.root)['spots']
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['decoded_at'],new['decoded_at'])
        self.assertEqual(result[1]['id'],first[0]['id'])
        with patch('plugins.aprs_spots.MAX_SPOTS',1):
            self.assertEqual(len(self.archive.snapshot(self.root)['spots']),1)
        self.write([])
        self.assertEqual(self.archive.snapshot(self.root)['spots'],[])

    def test_http_and_assets(self):
        self.write([packet()])
        for name in ('aprs_spots.js','aprs_spots.css'):
            (self.root/'plugins'/name).write_bytes((web.SCRIPT_DIR/'plugins'/name).read_bytes())
        with patch.object(web,'SCRIPT_DIR',self.root), patch.object(web,'APRS_SPOTS_ARCHIVE',self.archive):
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(web.WebConsoleHandler,status_payload_func=lambda:{}))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url+'/api/aprs/spots') as response:
                    data=json.load(response)
                self.assertTrue(data['ok'])
                self.assertEqual(data['spots'][0]['callsign'],'N0CALL-7')
                for path in ('/aprs-spots','/aprs-spots.js','/aprs-spots.css'):
                    with urlopen(url+path) as response:self.assertEqual(response.status,200)
            finally:
                server.shutdown();server.server_close();worker.join(3)

    def test_upload_receipts_refresh_and_survive_archive_restart(self):
        self.write([packet(upload_id='a'*32,reception_context='satellite_pass'),
                    packet(upload_id='b'*32,reception_context='idle_preset'),packet()])
        self.assertTrue(all(not p['uploaded'] for p in self.archive.snapshot(self.root)['packets']))
        receipts=self.root/'plugins/bqe_aprs_decoder/upload_receipts'
        receipts.mkdir()
        (receipts/('a'*32+'.json')).write_text(json.dumps({'uploaded_at':'2026-10-08T12:00:00Z'}))
        for archive in (self.archive, type(self.archive)()):
            data=archive.snapshot(self.root)
            self.assertEqual(sum(p['uploaded'] for p in data['packets']),1)
            self.assertEqual(sum(p['uploaded'] for p in data['spots']),1)
            self.assertEqual(sum(p['satellite_pass'] for p in data['packets']),1)
            self.assertEqual(sum(p['satellite_pass'] for p in data['spots']),1)

    def test_packet_history_includes_non_positions_and_raw_errors(self):
        self.write([packet(), packet(aprs=dict(format='message',message_text='Hello'), destination='APRS'),
                    packet(aprs=dict(format='status',status='Ready')),
                    packet(aprs=dict(format='telemetry',sequence=1,analog=[1,2,3,4,5],bits='01010101')),
                    packet(aprs=None,aprs_error='Unknown format',payload_text='raw report')])
        data = self.archive.snapshot(self.root)
        self.assertEqual(len(data['spots']), 1)
        self.assertEqual(len(data['packets']), 5)
        reports = {p['format']: p for p in data['packets']}
        self.assertEqual(reports['message']['comment'], 'Hello')
        self.assertIsNone(reports['message']['latitude'])
        self.assertEqual(reports['status']['comment'], 'Ready')
        self.assertIn('analog [1, 2, 3, 4, 5]', reports['telemetry']['comment'])
        self.assertEqual(reports['Unparsed']['packet']['aprs_error'], 'Unknown format')


if __name__=='__main__':unittest.main()
