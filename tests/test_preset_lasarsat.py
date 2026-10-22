"""LASARsat preset startup, source routing and pass handoff."""
from pathlib import Path
from unittest.mock import patch

from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_lasarsat_decoder import integration


class PresetLASARSATTests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root = Path(self.ns['SCRIPT_DIR'])
        for path in (root / 'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images', 'decode_lasarsat_telemetry'))
        self.receiver = integration.Receiver(key='decode_lasarsat_telemetry',
                                            root=root / 'plugins' / 'bqe_lasarsat_decoder',
                                            port=8778, label='LASARsat telemetry')
        self.stack.enter_context(patch.object(integration, 'LASARSAT_RECEIVER', self.receiver))


del PresetSSTVTests
