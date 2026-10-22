"""Use the existing preset/pass handoff suite with HF digital reception."""
from pathlib import Path
from unittest.mock import patch

from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_hf_image_decoder import integration


class PresetHFTests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root = Path(self.ns['SCRIPT_DIR'])
        for path in (root/'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images', 'decode_hf_images'))
        self.receiver = integration.Receiver(root/'plugins'/'bqe_hf_image_decoder')
        self.stack.enter_context(patch.object(integration, 'HF_IMAGE_RECEIVER', self.receiver))

    def test_profile_and_control_port(self):
        self.select('sstv')
        command = self.popen.call_args.args[0]
        self.assertEqual(command[command.index('--port')+1], '8776')
        self.assertEqual(command[command.index('--profile')+1], 'auto')
        with self.assertRaises(ValueError):
            self.receiver.decoder_args({'hf_image_profile': 'invalid'})


# Avoid running the imported base class a second time during discovery.
del PresetSSTVTests
