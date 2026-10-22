import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bqe_instance import SchedulerLock


ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import sys
from bqe_instance import SchedulerLock, SchedulerAlreadyRunning
try:
    with SchedulerLock(sys.argv[1]):
        print('owned', flush=True)
        if len(sys.argv)>2:
            sys.stdin.read()
except SchedulerAlreadyRunning:
    sys.exit(23)
'''


class SchedulerInstanceTests(unittest.TestCase):
    def probe(self, path):
        return subprocess.run([sys.executable, '-B', '-c', CHILD, str(path)],
                              cwd=ROOT, capture_output=True, timeout=10)

    def test_second_process_rejected_then_lock_reusable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'scheduler.lock'
            with SchedulerLock(path):
                self.assertEqual(self.probe(path).returncode, 23)
            self.assertEqual(self.probe(path).returncode, 0)

    def test_crashed_owner_does_not_leave_stale_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'scheduler.lock'
            child = subprocess.Popen([sys.executable, '-B', '-c', CHILD, str(path), 'wait'],
                                     cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b'owned')
                self.assertEqual(self.probe(path).returncode, 23)
            finally:
                child.kill()
                child.communicate(timeout=10)
            self.assertEqual(self.probe(path).returncode, 0)


if __name__ == '__main__':
    unittest.main()
