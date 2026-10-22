"""OTP-2 preset startup, source routing and pass handoff."""
from pathlib import Path
from unittest.mock import patch

from tests.test_preset_sstv import PresetSSTVTests
from plugins.bqe_otp2_decoder import integration


class PresetOTP2Tests(PresetSSTVTests):
    def setUp(self):
        super().setUp()
        root = Path(self.ns['SCRIPT_DIR'])
        for path in (root / 'presets').glob('*.yaml'):
            path.write_text(path.read_text().replace('decode_sstv_images', 'decode_otp2_telemetry'))
        self.receiver = integration.Receiver(key='decode_otp2_telemetry',
                                            root=root / 'plugins' / 'bqe_otp2_decoder',
                                            port=8779, label='OTP-2 telemetry')
        self.stack.enter_context(patch.object(integration, 'OTP2_RECEIVER', self.receiver))


del PresetSSTVTests
