import ast
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import yaml

from plugins.bqe_sstv_decoder.integration import Receiver

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / 'plugins' / 'bqe_ssdv_decoder'


class IntegrationTests(unittest.TestCase):
    def test_managed_server_exits_on_owner_eof(self):
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([sys.executable, str(PLUGIN / 'bqe_ssdv_decoder.py'),
                                      '--managed', '--idle', '--port', '0', '--output', folder],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                output, errors = child.communicate(input=b'', timeout=20)
                self.assertEqual(child.returncode, 0, errors.decode())
                self.assertIn(b'SSDV controls:', output)
            finally:
                if child.poll() is None:
                    child.kill(); child.communicate()

    def test_opt_in_uses_rig_device_and_managed_port(self):
        receiver = Receiver(key='decode_ssdv_images', root=PLUGIN, port=8771, label='SSDV')
        child = Mock(pid=42)
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen', return_value=child) as launch:
            receiver.start({'decode_ssdv_images': True}, 'rs61', {'radio_soundcard': 'USB Audio Codec'})
            args = launch.call_args.args[0]
            self.assertEqual(args[1], str(PLUGIN / 'bqe_ssdv_decoder.py'))
            self.assertEqual(args[args.index('--device')+1], 'USB Audio Codec')
            self.assertEqual(args[args.index('--port')+1], '8771')
            self.assertIn('--managed', args)
            receiver.stop()
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once()

    def test_disabled_and_missing_device_never_launch(self):
        receiver = Receiver(key='decode_ssdv_images', root=PLUGIN)
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen') as launch:
            receiver.start({}, 'rs61', {})
            with self.assertRaisesRegex(ValueError, 'radio_soundcard'):
                receiver.start({'decode_ssdv_images': True}, 'rs61', {})
            launch.assert_not_called()

    def test_pass_and_preset_hooks_and_rs61_configuration(self):
        # Inspect AST calls so unrelated imports do not start BQE or require Hamlib.
        tracker = ast.parse((ROOT / 'bqe_track_continuously.py').read_text(encoding='utf-8'))
        self.assertTrue(any(isinstance(node, ast.Tuple) and any(isinstance(item, ast.Name) and
                             item.id == 'SSDV_RECEIVER' for item in node.elts) for node in ast.walk(tracker)))
        for path in ('bqe_wisp.py', 'bqe_track_continuously.py'):
            tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
            self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'SSDV_RECEIVER'
                                and node.func.attr == 'stop' for node in ast.walk(tree)))
        config = yaml.safe_load((ROOT / 'bqe_config/satellites.yaml').read_text(encoding='utf-8'))
        rs61 = next(s for s in config['satellites'] if s['nickname'] == 'rs61')
        self.assertTrue(rs61['decode_ssdv_images'])
        self.assertNotEqual(rs61.get('program_to_run_during_pass'), 'tbd')
