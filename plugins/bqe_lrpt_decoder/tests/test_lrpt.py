import ast
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from plugins.bqe_lrpt_decoder.config import Options
from plugins.bqe_lrpt_decoder import engine, service, integration
from plugins.bqe_lrpt_decoder.bqe_lrpt_decoder import handler, load_defaults
from plugins.decoder_http import DecoderServer


class LRPTTests(unittest.TestCase):
    def test_settings_validate_and_keep_full_iq(self):
        o = Options.from_dict(dict(symbol_rate=80000, sample_rate=240000))
        self.assertEqual(o.source_settings().center, o.frequency)
        self.assertEqual(o.source_settings().sample_rate, 240000)
        for bad in [dict(symbol_rate=9600), dict(frequency=145800000), dict(duration=0),
                    dict(satellite='NOAA-19'), dict(cli_version='3'), dict(source='demo')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Options.from_dict(bad)

    def test_command_72_80_and_cli_versions(self):
        job = dict(options=Options().as_dict(), iq_file='a space/input.iq', products='out',
                   sample_rate=240000, format='cu8')
        with patch.object(engine,'executable',return_value='/path/satdump'):
            cmd = engine.command(job)
            self.assertEqual(cmd[1:4], ['meteor_m2-x_lrpt','baseband','a space/input.iq'])
            self.assertIn('--rs_usecheck',cmd)
            job['captured_at']='2026-09-26T12:00:00Z'
            self.assertIn('--start_timestamp',engine.command(job))
            job['options'].update(symbol_rate=80000, cli_version='2')
            self.assertEqual(engine.command(job)[1:3], ['pipeline','meteor_m2-x_lrpt_80k'])

    def test_capture_retains_exact_iq_and_metadata_then_queues(self):
        raw = bytes(range(256))*32
        stream = Mock(datatype='cu8', error=None)
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.__iter__ = Mock(return_value=iter([raw,raw]))
        with tempfile.TemporaryDirectory() as directory, patch.object(service,'executable'), \
             patch.object(service,'source',return_value=stream), patch.object(service,'launch_job') as launch:
            session=service.Session(directory)
            session.start(dict(duration=1))
            session.thread.join(5)
            self.assertEqual(session.snapshot()['state'],'queued',session.snapshot())
            job=session.jobs()[0]
            self.assertEqual(Path(job['iq_file']).read_bytes(),raw+raw)
            meta=json.loads(Path(job['iq_file']).with_suffix('.sigmf-meta').read_text())
            self.assertEqual(meta['captures'][0]['core:frequency'],137100000)
            self.assertEqual(meta['global']['core:sample_rate'],240000)
            launch.assert_called_once()

    def test_capture_error_does_not_queue_corrupt_data(self):
        stream=Mock(datatype='cu8',error=RuntimeError('USB overrun'))
        stream.__enter__=Mock(return_value=stream); stream.__exit__=Mock(return_value=False)
        stream.__iter__=Mock(return_value=iter([bytes(2048)]))
        with tempfile.TemporaryDirectory() as directory, patch.object(service,'executable'), \
             patch.object(service,'source',return_value=stream), patch.object(service,'launch_job') as launch:
            s=service.Session(directory);s.start({});s.thread.join(5)
            self.assertEqual(s.snapshot()['state'],'error')
            self.assertIn('overrun',s.jobs()[0]['error']);launch.assert_not_called()

    def test_missing_engine_and_disk_reserve_fail_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            s=service.Session(directory)
            with self.assertRaises(ValueError):
                s.start(dict(satdump=str(Path(directory)/'missing')))
            with patch.object(service,'executable'), patch.object(service.shutil,'disk_usage',return_value=Mock(free=0)), \
                 patch.object(service,'source') as source:
                s.start({});s.thread.join(5)
                self.assertEqual(s.snapshot()['state'],'error');source.assert_not_called()

    def test_replay_formats_and_reject_wrong_center(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(service,'executable'), patch.object(service,'launch_job'):
            path=Path(directory)/'test.sigmf-data';path.write_bytes(bytes(2048))
            meta={'global':{'core:datatype':'cu8','core:sample_rate':240000},
                  'captures':[{'core:sample_start':0,'core:frequency':137100000}]}
            path.with_suffix('.sigmf-meta').write_text(json.dumps(meta))
            s=service.Session(Path(directory)/'jobs')
            job=s.replay(path,{})
            self.assertEqual(job['samples'],1024)
            self.assertEqual(job['iq_file'],str(path.resolve()))
            with self.assertRaisesRegex(ValueError,'centered'):
                s.replay(path,dict(frequency=137900000))

    def test_worker_distinguishes_images_noise_and_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(engine,'executable',return_value='/bin/satdump'):
            folder=Path(directory);path=folder/'job.json'
            base=dict(state='queued',options=Options().as_dict(),iq_file=str(folder/'raw'),
                      products=str(folder/'products'),sample_rate=240000,format='cu8')
            for code, expected in [(0,'no_images'),(3,'error')]:
                engine.write_json(path,base)
                with patch.object(engine.subprocess,'run',return_value=Mock(returncode=code)):
                    self.assertEqual(engine.decode_job(path)['state'],expected)
            (folder/'products'/'image.png').write_bytes(b'test image artifact')
            engine.write_json(path,base)
            with patch.object(engine.subprocess,'run',return_value=Mock(returncode=0)):
                self.assertEqual(engine.decode_job(path)['state'],'completed')
            with self.assertRaises(ValueError):
                engine.decode_job(path) # Cannot duplicate a running/completed job.

    def test_http_origin_and_file_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            s=service.Session(directory)
            server=DecoderServer(('127.0.0.1',0),handler(s,{}))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url+'/status') as response:
                    self.assertEqual(json.load(response)['state'],'idle')
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url+'/start',data=b'{}',headers={'Origin':'https://example.com'}))
                self.assertEqual(error.exception.code,403)
                with self.assertRaises(HTTPError):
                    urlopen(url+'/files/../outside.json')
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_managed_launch_uses_iq_settings_and_releases_owner(self):
        child=Mock();child.poll.return_value=None
        response=io.BytesIO(json.dumps(dict(state='capturing',instance='test')).encode())
        with patch.object(integration.subprocess,'Popen',return_value=child) as popen, \
             patch.object(integration.uuid,'uuid4',return_value=Mock(hex='test')), \
             patch.object(integration,'urlopen',return_value=response):
            receiver=integration.LRPTReceiver()
            receiver.start(dict(decode_lrpt_images=True,downlink_frequency_mhz='137.9',
                lrpt=dict(satellite='M2-3')), 'Meteor', dict(sdr=dict(device='serial:123',audio_gain=.15)))
            args=popen.call_args.args[0]
            settings=json.loads(args[args.index('--options-json')+1])
            self.assertEqual(settings['frequency'],137900000)
            self.assertEqual(settings['device'],'serial:123')
            self.assertNotIn('audio_gain',settings)
            receiver.stop();child.stdin.close.assert_called_once()

    def test_disabled_and_conflicting_decoders(self):
        receiver=integration.LRPTReceiver()
        with patch.object(integration.subprocess,'Popen') as popen:
            self.assertIsNone(receiver.start({},'Meteor',{}));popen.assert_not_called()
            with self.assertRaises(ValueError):
                receiver.start(dict(decode_lrpt_images=True,decode_telemetry=True),'Meteor',{})

    def test_rig_defaults_share_hardware_not_audio_bandwidth(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'rig.yaml'
            path.write_text('sdr:\n  device: "0"\n  sample_rate: 960000\n  bandwidth: 3000\nlrpt:\n  symbol_rate: 80000\n')
            result=load_defaults(path)
            self.assertNotIn('bandwidth',result);self.assertNotIn('sample_rate',result)
            self.assertEqual(result['symbol_rate'],80000)

    def test_tracker_exclusive_receiver_branch(self):
        root=Path(__file__).resolve().parents[3]
        tree=ast.parse((root/'bqe_track_continuously.py').read_text(encoding='utf-8'))
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                    isinstance(n.test,ast.Name) and n.test.id=='use_lrpt')
        sdr, lrpt=Mock(),Mock()
        ns=dict(use_lrpt=True,LRPT_RECEIVER=lrpt,SDR_RECEIVER=sdr,sdr_pass_config={},satellite_name='Meteor',radio_config={})
        exec(compile(ast.Module(body=[branch],type_ignores=[]),'tracker','exec'),ns)
        lrpt.start.assert_called_once();sdr.start.assert_not_called()

    def test_scheduled_profile_has_current_tle_and_receive_only_lifecycle(self):
        import yaml
        from plugins.bqe_sdr.integration import effective_config
        root=Path(__file__).resolve().parents[3]
        entries=yaml.safe_load((root/'bqe_config'/'satellites.yaml').read_text())['satellites']
        meteor=next(x for x in entries if x.get('nickname')=='meteor-m2-4-lrpt')
        self.assertIs(meteor['auto_schedule'],True)
        self.assertIs(meteor['decode_lrpt_images'],True)
        self.assertEqual(str(meteor['catalog_number']),'59051')
        self.assertIs(effective_config(meteor,{})['sdr_only'],True)
        self.assertTrue(any(line.startswith('1 59051') for line in (root/'keps.txt').read_text().splitlines()))
        self.assertFalse(any(meteor.get(x) for x in ('decode_telemetry','decode_sstv_images','decode_ssdv_images')))


if __name__ == '__main__': unittest.main()
