import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
import bqe_schedule_passes as scheduler


class SDRSchedulingTests(unittest.TestCase):
    def test_manual_auto_and_direct_requests(self):
        for selection in (['--nickname', 'tlm'], ['--auto_schedule'], ['--sat_name', '12345']):
            for sdr in (False, True):
                with self.subTest(selection=selection, sdr=sdr), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root/'sat.yaml').write_text(yaml.safe_dump({'satellites': [dict(
                        nickname='tlm', satellite_name='Telemetry', catalog_number='12345',
                        auto_schedule=True, sdr_required=True)]}))
                    (root/'rig.yaml').write_text(yaml.safe_dump({'radio_is_sdr': sdr}))
                    (root/'qth.yaml').write_text('my_latitude: 0\nmy_longitude: 0\nmy_altitude: 0\n')
                    argv = ['scheduler', '--satellites_yaml', str(root/'sat.yaml'),
                            '--radio_config', str(root/'rig.yaml'), '--qth_config', str(root/'qth.yaml'), *selection]
                    old = Path.cwd()
                    try:
                        os.chdir(root)
                        output = io.StringIO()
                        with patch('sys.argv', argv), contextlib.redirect_stdout(output), \
                             patch.object(scheduler, 'load_tle_by_name_or_id', return_value=('Telemetry', '1 12345', '2 12345')) as tle, \
                             patch.object(scheduler, 'run_pass_tasks', return_value=[]) as run:
                            scheduler.main()
                        self.assertEqual(len(run.call_args.args[0]), int(sdr))
                        self.assertEqual(tle.call_count, int(sdr))
                        self.assertEqual(json.loads((root/'schedule.json').read_text())['passes'], [])
                        self.assertEqual('sdr_required: true but radio_is_sdr: false' in output.getvalue(), not sdr)
                    finally:
                        os.chdir(old)

    def test_normal_satellites_and_missing_radio(self):
        self.assertFalse(scheduler.skip_sdr_required({'nickname': 'voice'}, False))
        self.assertFalse(scheduler.skip_sdr_required({'sdr_required': False}, False))
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(scheduler.load_radio_is_sdr(Path(temp)/'missing.yaml'))


if __name__ == '__main__':
    unittest.main()
