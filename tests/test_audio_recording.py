"""Exercise recording without opening audio hardware or starting the scheduler."""
import ast
import os
from pathlib import Path
import shlex
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugins.bqe_sound_recorder.bqe_sound_recorder import (
    LoopbackRecorder, external_recording_status, parse_args, publish_recording_status,
)


def scheduler_functions():
    # The scheduler initializes hardware-related modules at import time.
    # Load its real function bodies independently of that startup code.
    names = {
        "audio_recording_status", "stop_audio_recording", "make_audio_recorder",
        "control_audio_recording", "command_from_yaml_value", "run_pass",
    }
    tree = ast.parse((ROOT / "bqe_wisp.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "os": os, "shlex": shlex, "threading": threading,
        "AUDIO_RECORDING_LOCK": threading.RLock(), "STATE_LOCK": threading.RLock(),
        "PRESET_LIFECYCLE_LOCK": threading.RLock(), "stop_idle_wait_program": Mock(),
        "CURRENT_AUDIO_RECORDER": None, "SHUTDOWN_EVENT": threading.Event(),
        "CURRENT_PASS_END_EVENT": threading.Event(), "APP_STATE": {},
        "satellite_pass_is_active": Mock(return_value=False),
        "_set_status_message": Mock(), "SCRIPT_DIR": str(ROOT),
        "SATELLITE_CONFIG_FILE": "satellites.yaml", "GENERAL_SETTINGS_FILE": "general.yaml",
        "yaml": Mock(), "load_web_console_settings": Mock(),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "bqe_wisp.py"), "exec"), namespace)
    return namespace


class RecordingControlTests(unittest.TestCase):
    def setUp(self):
        self.ns = scheduler_functions()
        self.recorder = Mock()
        self.recorder.snapshot.return_value = {"status": "recording", "output_file": "audio.mp3"}
        self.recorder.thread.is_alive.return_value = True
        self.ns["make_audio_recorder"] = Mock(return_value=self.recorder)

    def test_start_is_idempotent_and_stop_finalizes(self):
        control = self.ns["control_audio_recording"]
        self.assertTrue(control("start_audio_recording")["ok"])
        self.assertTrue(control("start_audio_recording")["ok"])
        self.recorder.start.assert_called_once()
        self.assertTrue(self.ns["audio_recording_status"]()["active"])
        self.recorder.join.side_effect = lambda: setattr(self.recorder.thread.is_alive, "return_value", False)
        self.assertIn("audio.mp3", control("stop_audio_recording")["message"])
        self.recorder.stop.assert_called_once()
        self.recorder.join.assert_called_once()
        self.assertFalse(self.ns["audio_recording_status"]()["active"])

    def test_active_pass_or_shutdown_rejects_start(self):
        self.ns["satellite_pass_is_active"].return_value = True
        self.assertFalse(self.ns["control_audio_recording"]("start_audio_recording")["ok"])
        self.ns["satellite_pass_is_active"].return_value = False
        self.ns["SHUTDOWN_EVENT"].set()
        self.assertFalse(self.ns["control_audio_recording"]("start_audio_recording")["ok"])
        self.recorder.start.assert_not_called()

    def test_failed_capture_is_reported_and_can_be_restarted(self):
        self.ns["CURRENT_AUDIO_RECORDER"] = self.recorder
        self.recorder.thread.is_alive.return_value = False
        self.recorder.snapshot.return_value = {"status": "error", "error": "device unavailable"}
        self.assertEqual(self.ns["audio_recording_status"]()["error"], "device unavailable")
        self.assertTrue(self.ns["control_audio_recording"]("start_audio_recording")["ok"])
        self.recorder.start.assert_called_once()

    def test_pass_handoff_stops_recording_before_tracking_setup(self):
        self.ns["CURRENT_AUDIO_RECORDER"] = self.recorder
        events = []
        self.recorder.stop.side_effect = lambda: events.append("stop")
        self.recorder.join.side_effect = lambda: events.append("finalized")
        class HandoffComplete(Exception):
            pass
        def tracking_setup():
            events.append("tracking setup")
            self.assertTrue(self.ns["APP_STATE"]["tracking_running"])
            raise HandoffComplete()
        self.ns["CURRENT_PASS_END_EVENT"] = Mock()
        self.ns["CURRENT_PASS_END_EVENT"].clear.side_effect = tracking_setup
        with self.assertRaises(HandoffComplete):
            self.ns["run_pass"]({}, None, None)
        self.assertEqual(events, ["stop", "finalized", "tracking setup"])

    def test_pass_capture_settings_and_recordings_directory_are_reused(self):
        ns = scheduler_functions()
        ns["yaml"].safe_load.return_value = {"satellites": [
            {"program_to_run_during_pass": ["other-helper"]},
            {"program_to_run_during_pass": ["python", "plugins/bqe_sound_recorder/bqe_sound_recorder.py",
              "--input-device", "USB Audio CODEC", "--sample-rate", "44100", "--bitrate", "128",
              "--output", "recordings/satellite"]},
        ]}
        ns["load_web_console_settings"].return_value.recordings_location = "custom-recordings"
        with patch("builtins.open", mock_open()):
            recorder = ns["make_audio_recorder"]()
        self.assertEqual(recorder.input_device_name, "USB Audio CODEC")
        self.assertEqual((recorder.samplerate, recorder.bitrate), (44100, 128))
        self.assertEqual(recorder.output_prefix, ROOT / "custom-recordings" / "Audio Recording")
        self.assertEqual(recorder.timestamp_separator, " ")

    def test_sdr_station_overrides_legacy_soundcard_and_sample_rate(self):
        ns = scheduler_functions()
        ns['yaml'].safe_load.side_effect = [
            {'satellites': [{'program_to_run_during_pass': [
                'python', 'plugins/bqe_sound_recorder/bqe_sound_recorder.py',
                '--input-device', 'USB Audio CODEC', '--sample-rate', '44100', '--bitrate', '128']}]},
            {'radio_is_sdr': True, 'sdr': {'port': 8872}},
        ]
        ns['load_web_console_settings'].return_value.recordings_location = 'custom-recordings'
        with patch('builtins.open', mock_open()):
            recorder = ns['make_audio_recorder']()
        self.assertEqual(recorder.input_device_name, 'bqe-sdr://127.0.0.1:8872')
        self.assertIsNone(recorder.device_name)
        self.assertEqual((recorder.samplerate, recorder.bitrate), (48000, 128))
        self.assertEqual(recorder.output_prefix, ROOT / 'custom-recordings' / 'Audio Recording')

    def test_shared_pass_flag_leaves_casual_recording_on_configured_radio_input(self):
        ns = scheduler_functions()
        ns['yaml'].safe_load.side_effect = [
            {'satellites': [{'record_audio': True}]},
            {'radio_soundcard': 'My radio input'},
        ]
        ns['load_web_console_settings'].return_value.recordings_location = 'recordings'
        with patch('builtins.open', mock_open()):
            recorder = ns['make_audio_recorder']()
        self.assertEqual(recorder.input_device_name, 'My radio input')
        self.assertIsNone(recorder.device_name)


class RecordingStatusTests(unittest.TestCase):
    def test_browser_is_opt_in_and_legacy_flag_still_works(self):
        self.assertTrue(parse_args([]).no_browser)
        self.assertTrue(parse_args(["--no-browser"]).no_browser)
        self.assertFalse(parse_args(["--open-browser"]).no_browser)

    def test_external_capture_heartbeat_and_terminal_states(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            self.assertEqual(external_recording_status(path)["status"], "stopped")
            recorder = Mock()
            for state in ("starting", "recording", "stopping", "stopped", "error"):
                recorder.snapshot.return_value = {"status": state, "output_file": "test.mp3"}
                publish_recording_status(recorder, path)
                self.assertEqual(external_recording_status(path)["status"], state)
            recorder.snapshot.return_value = {"status": "recording"}
            with patch("plugins.bqe_sound_recorder.bqe_sound_recorder.time.time", return_value=100):
                publish_recording_status(recorder, path)
            with patch("plugins.bqe_sound_recorder.bqe_sound_recorder.time.time", return_value=106):
                self.assertEqual(external_recording_status(path)["status"], "stopped")
            path.write_text("broken json", encoding="utf-8")
            self.assertEqual(external_recording_status(path)["status"], "stopped")

    def test_status_write_failure_does_not_stop_capture(self):
        recorder = Mock()
        recorder.snapshot.return_value = {"status": "recording"}
        with patch.object(Path, "mkdir", side_effect=PermissionError):
            publish_recording_status(recorder)
        recorder.stop.assert_not_called()


class RecordingFileTests(unittest.TestCase):
    def test_direct_input_uses_shared_backend_and_encodes_signal(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            recorder = LoopbackRecorder(Path(folder) / 'radio', input_device_name='USB Audio CODEC')
            source = Mock(channels=2)
            recorder._resolve_source = Mock(return_value=(source, 'USB input'))
            capture = Mock()
            def block(numframes):
                recorder.stop()
                return np.full((numframes, 2), 0.25, dtype=np.float32)
            capture.record.side_effect = block
            encoder = Mock()
            encoder.encode.return_value = b'mp3 data'
            encoder.flush.return_value = b'tail'
            module = 'plugins.bqe_sound_recorder.bqe_sound_recorder'
            with patch(module + '.input_recorder') as backend, patch.dict(
                    sys.modules, {'lameenc': Mock(Encoder=Mock(return_value=encoder)),
                                  'soundcard': Mock()}):
                backend.return_value.__enter__.return_value = capture
                recorder._record()
            state = recorder.snapshot()
            self.assertEqual(state['status'], 'stopped', state.get('error'))
            backend.assert_called_once_with(source, samplerate=48000)
            source.recorder.assert_not_called()
            self.assertEqual(state['frames_recorded'], recorder.blocksize)
            self.assertAlmostEqual(state['level_dbfs'], -12.0412, places=3)
            pcm = np.frombuffer(encoder.encode.call_args.args[0], dtype='<i2')
            self.assertTrue(np.all(pcm == 8191))

    def test_finalization_names_and_collision_handling(self):
        with tempfile.TemporaryDirectory() as folder:
            for prefix, separator in [("Audio Recording", " "), ("satellite", "_")]:
                for _ in range(2):
                    recorder = LoopbackRecorder(Path(folder) / prefix, timestamp_separator=separator)
                    recorder.stop()  # Exercise encoder flush and rename without reading hardware.
                    source = Mock(channels=1)
                    source.recorder.return_value.__enter__ = Mock(return_value=source)
                    source.recorder.return_value.__exit__ = Mock(return_value=False)
                    recorder._resolve_source = Mock(return_value=(source, "test input"))
                    encoder = Mock()
                    encoder.flush.return_value = b"test mp3 tail"
                    with patch.dict(sys.modules, {"numpy": Mock(), "soundcard": Mock(),
                                                 "lameenc": Mock(Encoder=Mock(return_value=encoder))}):
                        recorder._record()
                    status = recorder.snapshot()
                    self.assertEqual(status["status"], "stopped", status.get("error"))
                    path = Path(status["output_file"])
                    self.assertTrue(path.name.startswith(prefix + separator))
                    self.assertEqual(path.suffix, ".mp3")
                    self.assertEqual(path.read_bytes(), b"test mp3 tail")
                    self.assertFalse(Path(str(path) + ".part").exists())
            self.assertEqual(len(list(Path(folder).glob("*.mp3"))), 4)


if __name__ == "__main__":
    unittest.main()
