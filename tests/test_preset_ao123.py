"""AO-123 presets obey the shared start/stop and pass handoff lifecycle."""
from pathlib import Path
from unittest.mock import patch
from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_ao123_decoder import integration


class PresetAO123Tests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root=Path(self.ns['SCRIPT_DIR'])
        for path in (root/'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images','decode_ao123_telemetry')+'\nmode: USB\n')
        self.receiver=integration.AO123Receiver(key='decode_ao123_telemetry',
            root=root/'plugins'/'bqe_ao123_decoder',port=8783,label='AO-123')
        self.stack.enter_context(patch.object(integration,'AO123_RECEIVER',self.receiver))


del PresetSSTVTests
