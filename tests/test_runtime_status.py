import unittest
from unittest.mock import Mock, patch
from weakref import WeakSet

from plugins import runtime_status
from plugins.bqe_sstv_decoder.integration import Receiver


class RuntimeStatusTests(unittest.TestCase):
    def test_only_live_processes_and_stop(self):
        with patch.object(runtime_status, '_receivers', WeakSet()):
            live = Receiver(key='decode_sstv_images', label='SSTV')
            stopped = Receiver(key='decode_telemetry', label='Telemetry')
            Receiver(key='never_started', label='Never started')
            live.process = Mock(pid=101, poll=Mock(return_value=None))
            stopped.process = Mock(pid=102, poll=Mock(return_value=1))
            self.assertEqual(runtime_status.running_plugins(), [
                dict(id='decode_sstv_images', label='SSTV', pid=101)])
            live.stop()
            self.assertEqual(runtime_status.running_plugins(), [])

    def test_extra_helpers_and_safe_labels(self):
        with patch.object(runtime_status, '_receivers', WeakSet()):
            child = Mock(pid=103, poll=Mock(return_value=None),
                         args=['python', r'C:\bqe\plugins\bqe_speech_decoder.py', '--secret', 'value'])
            label = runtime_status.program_label(child)
            self.assertEqual(label, 'SPEECH')
            self.assertEqual(runtime_status.running_plugins([('pass_program', label, child)]),
                             [dict(id='pass_program', label='SPEECH', pid=103)])
            child.poll.return_value = 0
            self.assertEqual(runtime_status.running_plugins([('pass_program', label, child)]), [])


if __name__ == '__main__':
    unittest.main()
