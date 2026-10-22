"""Verify squelch configuration without connecting to radio hardware."""
import ast
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bqe_hamlib_interface as rig
import bqe_set_radio_from_yaml as presets


class SquelchTests(unittest.TestCase):
    def test_both_transports_and_endpoints(self):
        for value in (0, "0", 0.35, "1.0"):
            with self.subTest(value=value), patch.object(rig, "which_rigctl", return_value=("Windows", "rigctl")), patch.object(rig, "do_hamlib_cmd", return_value=Mock(returncode=0)) as run, patch.object(rig, "send_rig_command", return_value="RPRT 0") as send:
                rig.hamlib_set_squelch_level(123, "COM3", value, 9600)
                run.assert_called_once_with(["rigctl", "-m", "123", "-r", "COM3", "-s", "9600", "L", "SQL", str(float(value))])
                self.assertEqual(rig.rigctld_set_squelch_level(value, 4532), "RPRT 0")
                send.assert_called_once_with(f"L SQL {float(value)}", 4532)

    def test_invalid_and_missing_values_never_send(self):
        with patch.object(rig, "do_hamlib_cmd") as run, patch.object(rig, "send_rig_command") as send:
            for value in (-0.1, 1.1, "bad", "nan", "inf", True):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        rig.hamlib_set_squelch_level(1, "COM3", value)
                    with self.assertRaises(ValueError):
                        rig.rigctld_set_squelch_level(value, 4532)
            for value in (None, ""):
                rig.hamlib_set_squelch_level(1, "COM3", value)
                rig.rigctld_set_squelch_level(value, 4532)
            run.assert_not_called()
            send.assert_not_called()

    def test_interface_yaml_and_cli_override_in_any_mode(self):
        for config, cli, expected in (({"squelch_level": 0}, [], 0.0), ({"squelch_level": 0.8}, ["--squelch_level", "0"], 0.0), ({}, [], None)):
            with self.subTest(config=config, cli=cli), patch("sys.argv", ["interface", *cli]), patch("builtins.open", mock_open()), patch.object(rig.yaml, "safe_load", side_effect=[{"radio_type": 1, "radio_port": "COM3", "radio_baud": 9600}, {"frequency_hz": 145000000, "mode": "USB", **config}]), patch.object(rig, "hamlib_set_frequency"), patch.object(rig, "hamlib_set_mode"), patch.object(rig, "hamlib_set_squelch_level") as setter:
                rig.main()
                if expected is None:
                    setter.assert_not_called()
                else:
                    setter.assert_called_once_with("1", "COM3", expected, "9600")

    def test_preset_yaml_zero_and_omitted(self):
        for extra, expected in (({"squelch_level": 0}, ["L", "SQL", "0.0"]), ({}, ["L", "SQL", "0.25"])):
            with patch.object(presets, "load_yaml", return_value={}), patch.object(presets, "run_rigctl") as run:
                presets.program_preset({"frequency_hz": 145000000, **extra}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                sql_commands = [call.args[2] for call in run.call_args_list if call.args[2][:2] == ["L", "SQL"]]
                self.assertEqual(sql_commands, [] if expected is None else [expected])

    def test_preset_mode_defaults_and_overrides(self):
        settings = {"bqe_set_radio_from_yaml": {
            "default_squelch_level_fm": 0.6,
            "default_squelch_level_ssb": 0.2,
            "default_squelch_level_cw": 0.1,
        }}
        for sections in (settings, [settings]):
            for mode, expected in (("FM", 0.6), ("FMN", 0.6), ("C4FM", 0.6),
                                   ("USB", 0.2), ("LSB", 0.2), ("cw", 0.1),
                                   ("CWR", 0.1), ("AM", None)):
                for extra in ({}, {"squelch_level": None}, {"squelch_level": ""},
                              {"squelch_level": 0}, {"squelch_level": 0.9}):
                    with self.subTest(sections=sections, mode=mode, extra=extra), patch.object(presets, "load_yaml", return_value={"program_settings": sections}), patch.object(presets, "run_rigctl") as run:
                        presets.program_preset({"frequency_hz": 145000000, "mode": mode, **extra}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                        level = extra.get("squelch_level")
                        if level in (None, ""):
                            level = expected
                        commands = [call.args[2] for call in run.call_args_list if call.args[2][:2] == ["L", "SQL"]]
                        self.assertEqual(commands, [] if level is None else [["L", "SQL", str(float(level))]])

    def test_preset_missing_settings_and_invalid_default(self):
        with patch.object(presets, "load_yaml", side_effect=FileNotFoundError):
            for mode, expected in (("FM", 0.25), ("USB", 0.0), ("CW", 0.0)):
                self.assertEqual(presets.preset_squelch_level({}, mode), expected)
        with patch.object(presets, "load_yaml", return_value={"program_settings": {
            "bqe_set_radio_from_yaml": {"default_squelch_level_fm": 1.5}
        }}), patch.object(presets, "run_rigctl") as run:
            with self.assertRaises(ValueError):
                presets.program_preset({"frequency_hz": 145000000}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
            run.assert_not_called()

    def test_tracker_applies_squelch_with_or_without_satellite_setup(self):
        # Extract the real startup block without importing tracking dependencies.
        tree = ast.parse((ROOT / "bqe_track_continuously.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            setup_index = next((i for i, child in enumerate(node.body) if isinstance(child, ast.If) and any(isinstance(term, ast.Name) and term.id == "satellite_mode_required" for term in ast.walk(child.test))), None)
            if setup_index is not None:
                block = ast.Module(body=node.body[setup_index:setup_index + 2], type_ignores=[])
                break
        else:
            self.fail("Tracker startup block not found")
        for satellite_mode in (True, False):
            for level in (0.0, None):
                radio = Mock()
                ns = dict(rig=radio, squelch_level=level, rigctld_port=4532, satellite_mode_required=satellite_mode, sdr_only=False, do_satellite_setup=Mock())
                ns.update(dict.fromkeys(("uplink_frequency_mhz", "uplink_mode", "downlink_frequency_mhz", "downlink_mode", "ctcss_tone", "radio_type", "radio_ft736r_intermediate_frequency_mhz"), 0))
                exec(compile(block, "tracker-startup", "exec"), ns)
                if level is None:
                    radio.rigctld_set_squelch_level.assert_not_called()
                else:
                    radio.rigctld_set_squelch_level.assert_called_once_with(level, 4532)


if __name__ == "__main__":
    unittest.main()
