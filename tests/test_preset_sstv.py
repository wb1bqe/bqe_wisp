"""Preset SSTV lifecycle without a radio, audio capture, or scheduler startup."""
import ast
from contextlib import ExitStack
from datetime import datetime, timezone
import glob
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import yaml

from plugins.bqe_sstv_decoder import integration

ROOT = Path(__file__).resolve().parents[1]


class HandoffComplete(Exception):
    pass


class PresetSSTVTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (root / 'presets').mkdir()
        (root / 'bqe_config').mkdir()
        (root / 'bqe_config' / 'my_rig.yaml').write_text(
            'radio_soundcard: Test USB Audio\n', encoding='utf-8')
        for name, setting in [('sstv', 'decode_sstv_images: true\n'),
                              ('off', 'decode_sstv_images: false\n'), ('plain', '')]:
            (root / 'presets' / (name + '.yaml')).write_text(
                f'nickname: "{name}"\nfrequency_hz: 14230000\n{setting}', encoding='utf-8')
        self.receiver = integration.Receiver(root=root / 'plugins' / 'bqe_sstv_decoder')
        self.stack.enter_context(patch.object(integration, 'SSTV_RECEIVER', self.receiver))
        for module, receiver in [('bqe_sdr', 'SDR_RECEIVER'), ('bqe_lrpt_decoder', 'LRPT_RECEIVER'),
                                 ('bqe_ssdv_decoder', 'SSDV_RECEIVER'),
                                 ('bqe_marmotsat_decoder', 'MARMOTSAT_RECEIVER'),
                                 ('bqe_so127_decoder', 'SO127_RECEIVER'),
                                 ('bqe_lasarsat_decoder', 'LASARSAT_RECEIVER'),
                                 ('bqe_otp2_decoder', 'OTP2_RECEIVER'),
                                 ('bqe_rs61_decoder', 'RS61_RECEIVER'),
                                 ('bqe_aprs_decoder', 'APRS_RECEIVER'),
                                 ('bqe_monitor_decoder', 'MONITOR_RECEIVER')]:
            self.stack.enter_context(patch(f'plugins.{module}.integration.{receiver}'))
        self.popen = self.stack.enter_context(patch.object(integration.subprocess, 'Popen'))
        self.child = self.popen.return_value
        names = {'program_preset_from_web', 'load_preset_config_by_nickname',
                 'start_idle_wait_program', 'stop_idle_wait_program', 'run_pass'}
        tree = ast.parse((ROOT / 'bqe_wisp.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.ns = dict(os=os, glob=glob, yaml=yaml, SCRIPT_DIR=str(root),
                       PRESET_LIFECYCLE_LOCK=threading.RLock(), STATE_LOCK=threading.RLock(),
                       AUDIO_RECORDING_LOCK=threading.RLock(), SHUTDOWN_EVENT=threading.Event(),
                       CURRENT_IDLE_PROCESS=None, IDLE_WAIT_PROGRAM_PID_FILE=str(root / 'idle.pid'),
                       APP_STATE={'presets': [{'nickname': n} for n in ('sstv', 'off', 'plain')]},
                       utc_now=lambda: datetime.now(timezone.utc), idle_task_ctl=Mock(),
                       update_idle_task_status_for_preset=Mock(return_value=False),
                       stop_audio_recording=Mock(), CURRENT_PASS_END_EVENT=Mock())
        self.ns['satellite_pass_is_active'] = lambda: self.ns['APP_STATE'].get('tracking_running', False)
        self.ns['CURRENT_PASS_END_EVENT'].clear.side_effect = HandoffComplete
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'bqe_wisp.py'), 'exec'), self.ns)

    def select(self, nickname):
        self.assertTrue(self.ns['program_preset_from_web'](nickname)['ok'])

    def test_boolean_yaml_starts_after_tuning_without_helper(self):
        self.popen.side_effect = lambda *a, **k: (
            self.ns['idle_task_ctl'].program_preset_by_nickname.assert_called_once_with('sstv')
            or self.child)
        self.select('sstv')
        command = self.popen.call_args.args[0]
        self.assertEqual(command[command.index('--device') + 1], 'Test USB Audio')
        self.assertEqual(command[command.index('--source-name') + 1], 'sstv')
        self.assertIs(self.receiver.process, self.child)

    def test_switching_to_disabled_or_plain_preset_stops_before_tuning(self):
        for nickname in ('off', 'plain'):
            with self.subTest(nickname=nickname):
                self.select('sstv')
                self.child.reset_mock()
                def tune(_):
                    self.child.stdin.close.assert_called_once()
                    self.child.wait.assert_called_once_with(timeout=12)
                    self.assertIsNone(self.receiver.process)
                self.ns['idle_task_ctl'].program_preset_by_nickname.side_effect = tune
                self.select(nickname)
                self.ns['idle_task_ctl'].program_preset_by_nickname.side_effect = None

    def test_sstv_preset_reselection_replaces_receiver(self):
        self.select('sstv')
        self.select('sstv')
        self.assertEqual(self.popen.call_count, 2)
        self.child.stdin.close.assert_called_once()
        self.child.wait.assert_called_once_with(timeout=12)
        self.assertIs(self.receiver.process, self.child)

    def test_boolean_preset_uses_sdr_audio_for_sdr_rig(self):
        rig = Path(self.ns['SCRIPT_DIR']) / 'bqe_config' / 'my_rig.yaml'
        rig.write_text('radio_is_sdr: true\nsdr:\n  port: 8775\n', encoding='utf-8')
        self.select('sstv')
        command = self.popen.call_args.args[0]
        self.assertEqual(command[command.index('--device') + 1], 'bqe-sdr://127.0.0.1:8775')

    def test_pass_stops_decoder_and_blocks_late_idle_start(self):
        self.select('sstv')
        with self.assertRaises(HandoffComplete):
            self.ns['run_pass']({}, None, None)
        self.child.stdin.close.assert_called_once()
        self.assertIsNone(self.receiver.process)
        self.ns['start_idle_wait_program']('sstv')
        self.popen.assert_called_once()
        self.assertFalse(self.ns['program_preset_from_web']('sstv')['ok'])

    def test_pass_waits_for_inflight_preset_then_stops_its_decoder(self):
        tuning = threading.Event()
        finish_tuning = threading.Event()
        pass_requested = threading.Event()
        failures = []
        def tune(_):
            tuning.set()
            if not finish_tuning.wait(5):
                raise RuntimeError('test tuning timed out')
        def select():
            try:
                self.select('sstv')
            except BaseException as exc:
                failures.append(exc)
        def run_pass():
            pass_requested.set()
            try:
                self.ns['run_pass']({}, None, None)
            except HandoffComplete:
                pass
            except BaseException as exc:
                failures.append(exc)
        self.ns['idle_task_ctl'].program_preset_by_nickname.side_effect = tune
        preset_thread = threading.Thread(target=select)
        pass_thread = threading.Thread(target=run_pass)
        preset_thread.start()
        try:
            self.assertTrue(tuning.wait(5))
            pass_thread.start()
            self.assertTrue(pass_requested.wait(5))
        finally:
            finish_tuning.set()
            preset_thread.join(5)
            if pass_thread.ident is not None:
                pass_thread.join(5)
        self.assertFalse(preset_thread.is_alive())
        self.assertFalse(pass_thread.is_alive())
        self.assertEqual(failures, [])
        self.popen.assert_called_once()
        self.child.stdin.close.assert_called_once()
        self.assertIsNone(self.receiver.process)


if __name__ == '__main__':
    unittest.main()
