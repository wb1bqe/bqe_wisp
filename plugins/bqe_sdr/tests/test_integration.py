import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from plugins.bqe_sdr import integration
from plugins.bqe_sstv_decoder.integration import Receiver

ROOT=Path(__file__).resolve().parents[3]


class IntegrationTests(unittest.TestCase):
    def test_disabled_sdr_does_not_open_hardware_or_read_config(self):
        receiver=integration.SDRReceiver()
        with patch.object(integration.subprocess,'Popen') as popen, patch.object(integration,'rig_settings') as settings:
            self.assertIsNone(receiver.start({},'satellite',{}))
            popen.assert_not_called();settings.assert_not_called()

    def test_managed_start_arguments_tune_and_stop(self):
        child=Mock();child.poll.return_value=None
        receiver=integration.SDRReceiver()
        response=Mock()
        response.__enter__=Mock(return_value=io.BytesIO(json.dumps({'state':'running','instance':'test-id'}).encode()))
        response.__exit__=Mock(return_value=False)
        with patch.object(integration.subprocess,'Popen',return_value=child) as popen, \
             patch.object(integration,'urlopen',return_value=response), \
             patch.object(integration.uuid,'uuid4',return_value=Mock(hex='test-id')):
            receiver.start(dict(receive_sdr=True,downlink_frequency_mhz='145.8',downlink_mode='FM'),
                           'ISS',dict(sdr=dict(source='tcp',host='radio.local',record_iq=True)))
            cmd=popen.call_args.args[0]
            self.assertEqual(cmd[cmd.index('--frequency')+1],'145800000.0')
            self.assertIn('--managed',cmd);self.assertIn('--record-iq',cmd)
            self.assertIn('radio.local',cmd)
            receiver.tune(145803000)
            child.stdin.write.assert_called_once_with(b'{"frequency": 145803000}\n')
            child.stdin.flush.assert_called_once()
            receiver.stop()
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once_with(timeout=12)

    def test_decoder_selects_sdr_audio_without_soundcard_config(self):
        receiver=Receiver()
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen') as popen:
            receiver.start(dict(receive_sdr=True,decode_sstv_images=True),'ISS',dict(sdr=dict(port=8775)))
            self.assertIn('bqe-sdr://127.0.0.1:8775',popen.call_args.args[0])

    def test_preset_sdr_only_does_not_issue_rig_commands(self):
        tree=ast.parse((ROOT/'bqe_set_radio_from_yaml.py').read_text(encoding='utf-8'))
        function=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='program_preset')
        namespace={'get_radio_settings':Mock(side_effect=AssertionError('Physical radio must not be read'))}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<preset>','exec'),namespace)
        message=namespace['program_preset'](dict(receive_sdr=True,sdr_only=True),{})
        self.assertIn('SDR',message)

    def test_cli_managed_owner_eof_exits_and_releases_port(self):
        with tempfile.TemporaryDirectory() as folder:
            child=subprocess.Popen([sys.executable,str(ROOT/'plugins/bqe_sdr/bqe_sdr.py'),
                '--idle','--managed','--port','0','--output',folder],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                out,err=child.communicate(timeout=10)
                self.assertEqual(child.returncode,0,err.decode())
                self.assertIn(b'BQE SDR controls:',out)
            finally:
                if child.poll() is None:
                    child.kill();child.communicate()

    def test_plugin_import_is_dependency_free_for_scheduler(self):
        script='''
import sys
from plugins.bqe_sdr.integration import SDR_RECEIVER
assert 'numpy' not in sys.modules
assert 'scipy' not in sys.modules
assert 'soundcard' not in sys.modules
'''
        result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
