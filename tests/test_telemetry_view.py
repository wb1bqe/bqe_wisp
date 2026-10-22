"""Archive isolation, growing JSONL files and read-only web integration."""
from datetime import datetime, timezone, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import yaml
from plugins.telemetry_view import TelemetryArchive, catalog, belongs, has_recent_telemetry
import bqe_wisp_web as web


class TelemetryViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'satellites.yaml'
        self.config.write_text(yaml.safe_dump({'satellites': [
            dict(nickname='rs61', satellite_name='239ALFEROV (RS61S)', catalog_number='64881', decode_rs61_telemetry=True),
            dict(nickname='monitor4', satellite_name='Monitor-4 (RS57S)', catalog_number='57182', decode_monitor_telemetry=True),
            dict(nickname='otp2', satellite_name='OTP-2', catalog_number='63235', decode_otp2_telemetry=True),
            dict(nickname='off', decode_telemetry=False),
            dict(nickname='image-only', decode_ssdv_images=True),
        ]}), encoding='utf-8')
        self.satellites = {s['id']: s for s in catalog(self.config)}
        self.archive = TelemetryArchive()

    def log(self, plugin, name, records):
        folder = self.root / 'plugins' / plugin / 'decoded'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
        return path

    def test_recent_telemetry_boundary_and_timestamps(self):
        now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        def records(value):
            return [dict(packet=dict(decoded_at=value))]
        for age, expected in [(0, True), (86399, True), (86400, False), (86401, False), (-1, False)]:
            with self.subTest(age=age):
                self.assertEqual(has_recent_telemetry(records((now - timedelta(seconds=age)).isoformat()), now), expected)
        self.assertTrue(has_recent_telemetry(records('2026-10-18T07:00:00-04:00'), now))
        self.assertTrue(has_recent_telemetry(records('2026-10-18T11:00:00Z'), now))
        self.assertTrue(has_recent_telemetry(records('2026-10-18T11:00:00'), now))
        for invalid in [None, '', 'invalid', 123]:
            self.assertFalse(has_recent_telemetry(records(invalid), now))
        self.assertFalse(has_recent_telemetry([], now))
        self.assertTrue(has_recent_telemetry(records('invalid') + records(now.isoformat()), now))

    def test_catalog_and_packet_identity(self):
        self.assertEqual(set(self.satellites), {'rs61', 'monitor4', 'otp2'})
        rs61 = self.satellites['rs61']
        self.assertTrue(belongs({'source': 'RS61S'}, rs61))
        self.assertFalse(belongs({'source': 'RS57S', 'input_source': rs61['name']}, rs61))
        self.assertFalse(belongs({'satellite': {'norad': 57182}, 'input_source': rs61['name']}, rs61))
        self.assertTrue(belongs({'satellite': {'norad': 64881}, 'input_source': 'uploaded.wav'}, rs61))
        self.assertTrue(belongs({'input_source': 'OTP-2', 'parse_status': 'raw'}, self.satellites['otp2']))

    def test_growing_logs_and_legacy_decoder(self):
        packet = dict(frame_hex='0011', source='RS57S-1', decoded_at='2026-09-29T10:00:00Z')
        path = self.log('bqe_tlm_decoder', 'old.jsonl', [packet, dict(packet, source='RS61S')])
        first = self.archive.snapshot(self.root, self.satellites['monitor4'])
        self.assertEqual(len(first['packets']), 1)
        stable_id = first['packets'][0]['id']
        with path.open('a', encoding='utf-8') as stream:
            stream.write('bad json\n' + json.dumps(dict(packet, decoded_at='2026-09-29T11:00:00Z')))
        partial_result = self.archive.snapshot(self.root, self.satellites['monitor4'])
        self.assertEqual([p['id'] for p in partial_result['packets']], [stable_id])
        with path.open('a', encoding='utf-8') as stream: stream.write('\n')
        complete = self.archive.snapshot(self.root, self.satellites['monitor4'])
        self.assertEqual(len(complete['packets']), 2)
        self.assertEqual(complete['packets'][0]['packet']['decoded_at'], '2026-09-29T11:00:00Z')
        self.assertEqual(complete['packets'][1]['id'], stable_id)

    def test_http_catalog_empty_state_and_unknown_satellite(self):
        with patch.object(web, 'SATELLITES_CONFIG_PATH', self.config), patch.object(web, 'SCRIPT_DIR', self.root):
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(web.WebConsoleHandler, status_payload_func=lambda: {}))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/api/telemetry/satellites') as response:
                    entries = json.load(response)['satellites']
                    self.assertEqual(len(entries), 3)
                    self.assertTrue(all(s['has_telemetry'] is False for s in entries))
                    self.assertTrue(all(s['has_recent_telemetry'] is False for s in entries))
                # Live reception changes availability without restarting the server.
                self.log('bqe_tlm_decoder', 'new.jsonl', [dict(frame_hex='0011', source='RS57S-1')])
                with urlopen(base + '/api/telemetry/satellites') as response:
                    availability = {s['id']: s['has_telemetry'] for s in json.load(response)['satellites']}
                    self.assertEqual(availability, {'monitor4': True, 'rs61': False, 'otp2': False})
                self.log('bqe_tlm_decoder', 'fresh.jsonl', [dict(frame_hex='0022', source='RS61S',
                    decoded_at=datetime.now(timezone.utc).isoformat())])
                with urlopen(base + '/api/telemetry/satellites') as response:
                    recent = {s['id']: s['has_recent_telemetry'] for s in json.load(response)['satellites']}
                    self.assertEqual(recent, {'monitor4': False, 'rs61': True, 'otp2': False})
                with urlopen(base + '/api/telemetry?satellite=otp2') as response:
                    result = json.load(response)
                    self.assertEqual(result['packets'], [])
                    self.assertTrue(result['ok'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/api/telemetry?satellite=..%2F..%2Fmy_rig.yaml')
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_history_limit_and_truncated_tail(self):
        packets = [dict(frame_hex=f'{i:04x}', source='RS61S', decoded_at=f'2026-09-29T10:{i // 60:02d}:{i % 60:02d}Z')
                   for i in range(150)]
        path = self.log('bqe_rs61_decoder', 'capture.jsonl', packets)
        result = self.archive.snapshot(self.root, self.satellites['rs61'])
        self.assertEqual(len(result['packets']), 100)
        self.assertEqual(result['packets'][0]['packet']['frame_hex'], '0095')
        # A tail starting mid-record must not generate a spurious partial packet.
        with patch('plugins.telemetry_view.MAX_BYTES', 300):
            small = TelemetryArchive().snapshot(self.root, self.satellites['rs61'])
        self.assertGreater(len(small['packets']), 0)
        self.assertLess(len(small['packets']), 4)
        self.assertEqual(small['packets'][0]['packet']['frame_hex'], '0095')
        # Truncating/replacing an existing log must invalidate the cache.
        path.write_text(json.dumps(packets[0]) + '\n', encoding='utf-8')
        self.assertEqual(len(self.archive.snapshot(self.root, self.satellites['rs61'])['packets']), 1)


if __name__ == '__main__':
    unittest.main()
