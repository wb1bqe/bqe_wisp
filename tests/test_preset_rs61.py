"""RS-61 preset startup, source routing and pass handoff."""
from pathlib import Path
from unittest.mock import patch

from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_rs61_decoder import integration


class PresetRS61Tests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root = Path(self.ns['SCRIPT_DIR'])
        for path in (root / 'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images', 'decode_rs61_telemetry'))
        self.receiver = integration.Receiver(key='decode_rs61_telemetry',
                                            root=root / 'plugins' / 'bqe_rs61_decoder',
                                            port=8780, label='RS-61 telemetry')
        self.stack.enter_context(patch.object(integration, 'RS61_RECEIVER', self.receiver))


del PresetSSTVTests
