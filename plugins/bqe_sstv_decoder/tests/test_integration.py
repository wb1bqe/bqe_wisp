import ast
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
import sstv

from plugins.bqe_sstv_decoder import integration
from plugins.bqe_sstv_decoder.decoder import AudioDecoder
from plugins.bqe_sstv_decoder.service import Session, input_device

ROOT = integration.ROOT.parents[1]


class IntegrationTests(unittest.TestCase):
    def test_command_device_name_spaces_and_clean_stop(self):
        receiver = integration.Receiver()
        child = Mock()
        with patch.object(integration.subprocess, 'Popen', return_value=child) as popen:
            receiver.start({'decode_sstv_images': 'USB Audio Codec'}, 'iss-sstv')
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--device')+1], 'USB Audio Codec')
            self.assertIn('--managed', command)
            self.assertIn('--open-browser', command)
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.PIPE)
            receiver.stop()
            child.stdin.close.assert_called_once()
            child.wait.assert_called_once_with(timeout=12)
            child.terminate.assert_not_called()
            receiver.stop()
            child.stdin.close.assert_called_once()

    def test_disabled_invalid_and_replaced_config(self):
        receiver = integration.Receiver()
        with patch.object(integration.subprocess, 'Popen') as popen:
            for value in (None, '', False):
                receiver.start({'decode_sstv_images': value}, 'test')
            popen.assert_not_called()
            with self.assertRaises(ValueError):
                receiver.start({'decode_sstv_images': True}, 'test', radio_config={})
            receiver.start({'decode_sstv_images':'A'}, 'first')
            receiver.start({}, 'second')
            popen.return_value.stdin.close.assert_called_once()

    def test_boolean_uses_rig_soundcard_and_legacy_override_wins(self):
        receiver = integration.Receiver()
        with patch.object(integration.subprocess, 'Popen') as popen:
            receiver.start({'decode_sstv_images': True}, 'iss', {'radio_soundcard': 'USB Audio Codec'})
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--device')+1], 'USB Audio Codec')
            receiver.start({'decode_sstv_images': 'Other input'}, 'hf_sstv', {'radio_soundcard': 'USB Audio Codec'})
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('--device')+1], 'Other input')

    def test_preset_boolean_reads_rig_file(self):
        from unittest.mock import mock_open
        receiver = integration.Receiver()
        with patch.object(Path, 'open', mock_open(read_data='radio_soundcard: USB Audio Codec\n')) as opened, \
                patch.object(integration.subprocess, 'Popen') as popen:
            receiver.start({'decode_sstv_images': True}, 'hf_sstv')
            opened.assert_called_once_with(encoding='utf-8')
            self.assertIn('USB Audio Codec', popen.call_args.args[0])

    def test_device_selection_prefers_input_and_rejects_ambiguity(self):
        sc = Mock()
        def device(name, key, loopback=False):
            d = Mock(id=key, isloopback=loopback)
            d.name = name
            return d
        mic = device('Microphone (USB Audio Codec)', 'mic')
        speaker = device('Speakers (USB Audio Codec)', 'speaker', True)
        sc.all_microphones.return_value = [speaker, mic]
        self.assertIs(input_device(sc, 'USB Audio Codec'), mic)
        self.assertIs(input_device(sc, 'speaker'), speaker)
        with self.assertRaisesRegex(ValueError, 'not found'):
            input_device(sc, 'missing')
        sc.all_microphones.return_value.append(device('Other USB Audio Codec', 'other'))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            input_device(sc, 'USB Audio Codec')

    def test_preview_arrives_before_image_is_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(folder)
            audio = sstv.encode(Image.new('RGB',(320,240),'red'), sstv.Mode.ROBOT_36,12000).astype(np.float32)/32768
            decoder = AudioDecoder(12000, session._save, session.update, session.preview)
            decoder.push(audio[:12000*8])
            first = session.snapshot()
            self.assertEqual(first['images'], 0)
            self.assertTrue(first['has_preview'])
            self.assertFalse(first['preview_complete'])
            self.assertGreater(first['preview_version'], 1)
            with Image.open(io.BytesIO(session.get_preview())) as image:
                self.assertEqual(image.size, (320,240))
            decoder.push(audio[12000*8:12000*12])
            self.assertGreater(session.snapshot()['preview_version'], first['preview_version'])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_preset_only_decoder_and_stop_without_external_helper(self):
        # Import just the real lifecycle functions; BQE's module startup reads
        # radio and astronomy configuration not relevant to this test.
        tree = ast.parse((ROOT/'bqe_wisp.py').read_text(encoding='utf-8'))
        names = {'start_idle_wait_program', 'stop_idle_wait_program'}
        nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        namespace = dict(os=os, SCRIPT_DIR=str(ROOT), yaml=Mock(), SHUTDOWN_EVENT=threading.Event(),
                         CURRENT_IDLE_PROCESS=None, IDLE_WAIT_PROGRAM_PID_FILE='unused',
                         load_preset_config_by_nickname=Mock(return_value={'decode_sstv_images':'USB Audio Codec'}))
        exec(compile(ast.Module(body=nodes,type_ignores=[]), '<preset functions>', 'exec'), namespace)
        with patch.object(integration, 'SSTV_RECEIVER') as receiver:
            self.assertIsNone(namespace['start_idle_wait_program']('hf_sstv'))
            receiver.start.assert_called_once_with({'decode_sstv_images':'USB Audio Codec'},'hf_sstv')
            namespace['stop_idle_wait_program']()
            receiver.stop.assert_called_once()

    def test_managed_process_exits_on_owner_pipe_eof(self):
        # No capture or browser is opened: test the real parent-death handshake.
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([str(integration.ROOT/'.venv'/'Scripts'/'python.exe') if os.name=='nt'
                                      else str(integration.ROOT/'.venv'/'bin'/'python'),
                                      str(integration.ROOT/'bqe_sstv_decoder.py'), '--idle','--managed',
                                      '--port','0','--output',folder], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, stderr.decode())
                self.assertIn(b'SSTV controls:', stdout)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()


if __name__ == '__main__':
    unittest.main()
