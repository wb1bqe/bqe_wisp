"""Exercise cold SoundCard startup; importing it in the test runner hides this bug."""
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows audio startup regression')
class WindowsAudioStartupTests(unittest.TestCase):
    def test_cold_import_and_worker_device_listing(self):
        script = '''
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from plugins.bqe_sstv_decoder.service import devices
with patch('soundcard.all_microphones', return_value=[]):
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(devices).result(timeout=10) == []
'''
        result = subprocess.run([sys.executable, '-c', script],
                                cwd=Path(__file__).resolve().parents[3],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Exception ignored', result.stderr)
