"""APRS preset startup, SDR routing and orderly pass handoff."""
from pathlib import Path
from unittest.mock import patch

from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_aprs_decoder import integration


class PresetAPRSTests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root = Path(self.ns['SCRIPT_DIR'])
        for path in (root / 'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images', 'decode_aprs'))
        self.receiver = integration.APRSReceiver(key='decode_aprs',
                    root=root / 'plugins' / 'bqe_aprs_decoder', port=8781, label='APRS')
        self.stack.enter_context(patch.object(integration, 'APRS_RECEIVER', self.receiver))


del PresetSSTVTests
