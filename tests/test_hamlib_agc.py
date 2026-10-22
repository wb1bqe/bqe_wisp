"""Exercise AGC setup without connecting to radio hardware."""
import ast
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bqe_hamlib_interface as rig
import bqe_set_radio_from_yaml as presets


class AgcTests(unittest.TestCase):
    def test_satellite_pass_defaults_and_overrides(self):
        tree = ast.parse((ROOT / "bqe_track_continuously.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        override = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "cli_override")
        assignments = [node for node in main.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in ("agc", "squelch_level") for target in node.targets)]
        startup = next(node for node in ast.walk(main) if isinstance(node, ast.Try) and any(isinstance(child, ast.If) and any(isinstance(term, ast.Name) and term.id == "satellite_mode_required" for term in ast.walk(child.test)) for child in node.body))
        index = next(i for i, node in enumerate(startup.body) if isinstance(node, ast.If) and any(isinstance(term, ast.Name) and term.id == "satellite_mode_required" for term in ast.walk(node.test)))
        block = ast.Module(body=[override, *assignments, *startup.body[index:index + 3]], type_ignores=[])
        cases = [
            ({}, None, None, 0.0, "disabled"),
            ({"squelch_level": None, "agc": None}, None, None, 0.0, "disabled"),
            ({"squelch_level": "", "agc": ""}, None, None, 0.0, "disabled"),
            ({"squelch_level": 0.4, "agc": "medium"}, None, None, 0.4, "medium"),
            ({"squelch_level": 0}, None, None, 0.0, "disabled"),
            ({"agc": "fast"}, None, None, 0.0, "fast"),
            ({"squelch_level": 0.4, "agc": "slow"}, "0", "disabled", 0.0, "disabled"),
            # A later unconfigured pass must reset the preceding pass's levels.
            ({}, None, None, 0.0, "disabled"),
        ]
        for satellite_mode in (True, False):
            with patch.object(rig, "send_rig_command", return_value="RPRT 0") as send:
                for config, cli_sql, cli_agc, expected_sql, expected_agc in cases:
                    with self.subTest(satellite_mode=satellite_mode, config=config):
                        send.reset_mock()
                        ns = dict(rig=rig, args=SimpleNamespace(squelch_level=cli_sql, agc=cli_agc), satellite_config=config, rigctld_port=4532, satellite_mode_required=satellite_mode, sdr_only=False, do_satellite_setup=Mock())
                        ns.update(dict.fromkeys(("uplink_frequency_mhz", "uplink_mode", "downlink_frequency_mhz", "downlink_mode", "ctcss_tone", "radio_type", "radio_ft736r_intermediate_frequency_mhz"), 0))
                        exec(compile(block, "satellite-receiver-defaults", "exec"), ns)
                        self.assertEqual([call.args for call in send.call_args_list], [(f"L SQL {expected_sql}", 4532), (f"L AGC {rig.AGC_LEVELS[expected_agc]}", 4532)])

    def test_both_transports(self):
        for value, code in (("fast", 2), ("medium", 5), ("slow", 3), ("disabled", 0), (" DISABLED ", 0), (" FAST ", 2), ("Medium", 5)):
            with self.subTest(value=value), ExitStack() as stack:
                stack.enter_context(patch.object(rig, "which_rigctl", return_value=("Windows", "rigctl")))
                run = stack.enter_context(patch.object(rig, "do_hamlib_cmd", return_value=Mock(returncode=0)))
                send = stack.enter_context(patch.object(rig, "send_rig_command", return_value="RPRT 0"))
                rig.hamlib_set_agc(123, "COM3", value, 9600)
                run.assert_called_once_with(["rigctl", "-m", "123", "-r", "COM3", "-s", "9600", "L", "AGC", str(code)])
                self.assertEqual(rig.rigctld_set_agc(value, 4532), "RPRT 0")
                send.assert_called_once_with(f"L AGC {code}", 4532)

    def test_invalid_and_missing_values_never_send(self):
        with patch.object(rig, "do_hamlib_cmd") as run, patch.object(rig, "send_rig_command") as send:
            for value in ("auto", "bad", 2, True, [], "fast\nF 123"):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        rig.hamlib_set_agc(1, "COM3", value)
                    with self.assertRaises(ValueError):
                        rig.rigctld_set_agc(value, 4532)
            for value in (None, ""):
                rig.hamlib_set_agc(1, "COM3", value)
                rig.rigctld_set_agc(value, 4532)
            run.assert_not_called()
            send.assert_not_called()

    def test_interface_yaml_and_cli_override(self):
        for value, cli, expected in (("fast", [], "fast"), ("medium", [], "medium"), ("slow", [], "slow"), ("disabled", [], "disabled"), ("slow", ["--agc", "DISABLED"], "disabled"), ("slow", ["--agc", "FAST"], "fast"), (None, [], None)):
            with self.subTest(value=value, cli=cli), ExitStack() as stack:
                stack.enter_context(patch("sys.argv", ["interface", *cli]))
                stack.enter_context(patch("builtins.open", mock_open(read_data=rig.yaml.safe_dump({"radio_type": 1, "radio_port": "COM3", "radio_baud": 9600, "frequency_hz": 145000000, "mode": "USB", "agc": value}))))
                stack.enter_context(patch.object(rig, "hamlib_set_frequency"))
                stack.enter_context(patch.object(rig, "hamlib_set_mode"))
                setter = stack.enter_context(patch.object(rig, "hamlib_set_agc"))
                rig.main()
                if expected is None:
                    setter.assert_not_called()
                else:
                    setter.assert_called_once_with("1", "COM3", expected, "9600")

    def test_preset_yaml_and_invalid_input(self):
        for value, code in (("fast", 2), ("medium", 5), ("slow", 3), ("disabled", 0), (None, 2)):
            with self.subTest(value=value), patch.object(presets, "load_yaml", return_value={}), patch.object(presets, "run_rigctl") as run:
                presets.program_preset({"frequency_hz": 145000000, "agc": value}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                commands = [call.args[2] for call in run.call_args_list if call.args[2][:2] == ["L", "AGC"]]
                self.assertEqual(commands, [] if code is None else [["L", "AGC", str(code)]])
        with patch.object(presets, "run_rigctl") as run:
            with self.assertRaises(ValueError):
                presets.program_preset({"frequency_hz": 145000000, "agc": "bad"}, {"radio_type": 1, "radio_port": "COM3", "radio_baud": 9600})
            run.assert_not_called()

    def test_preset_agc_mode_defaults(self):
        for mode, code in (("FM", 2), ("C4FM", 2), ("FMN", 2), ("USB", 5),
                           ("LSB", 5), ("cw", 3), ("CWR", 3), ("AM", None)):
            for extra in ({}, {"agc": None}, {"agc": ""}, {"agc": "disabled"}):
                with self.subTest(mode=mode, extra=extra), patch.object(presets, "load_yaml", return_value={}), patch.object(presets, "run_rigctl") as run:
                    presets.program_preset({"frequency_hz": 145000000, "mode": mode, **extra}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                    expected = 0 if extra.get("agc") == "disabled" else code
                    commands = [call.args[2] for call in run.call_args_list if call.args[2][:2] == ["L", "AGC"]]
                    self.assertEqual(commands, [] if expected is None else [["L", "AGC", str(expected)]])

    def test_preset_agc_configured_defaults(self):
        section = {"bqe_set_radio_from_yaml": {"default_agc_fm": "slow", "default_agc_ssb": "fast", "default_agc_cw": "medium"}}
        for sections in (section, [section]):
            with patch.object(presets, "load_yaml", return_value={"program_settings": sections}):
                for mode, expected in (("FM", "slow"), ("C4FM", "slow"), ("USB", "fast"), ("CW", "medium")):
                    self.assertEqual(presets.preset_agc({}, mode), expected)
                    self.assertEqual(presets.preset_agc({"agc": "disabled"}, mode), "disabled")
        with patch.object(presets, "load_yaml", side_effect=FileNotFoundError):
            self.assertEqual(presets.preset_agc({}, "CW"), "slow")
        with patch.object(presets, "load_yaml", return_value={"program_settings": {
            "bqe_set_radio_from_yaml": {"default_agc_fm": "bad"}
        }}), patch.object(presets, "run_rigctl") as run:
            with self.assertRaises(ValueError):
                presets.program_preset({"frequency_hz": 145000000}, {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
            run.assert_not_called()

    def test_tracker_yaml_override_and_receive_only_setup(self):
        # Execute the actual AGC config and startup statements without importing
        # the tracker's astronomy and hardware dependencies.
        tree = ast.parse((ROOT / "bqe_track_continuously.py").read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        resolve = next(node for node in main.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "agc" for target in node.targets))
        override = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "cli_override")
        for node in ast.walk(main):
            if isinstance(node, ast.Try):
                index = next((i for i, child in enumerate(node.body) if isinstance(child, ast.If) and any(isinstance(term, ast.Name) and term.id == "satellite_mode_required" for term in ast.walk(child.test))), None)
                if index is not None:
                    startup = node.body[index:index + 3]
                    break
        else:
            self.fail("Tracker startup block not found")
        block = ast.Module(body=[override, resolve, *startup], type_ignores=[])
        for satellite_mode in (True, False):
            for value, cli, expected in (("fast", None, "fast"), ("medium", None, "medium"), ("slow", None, "slow"), ("disabled", None, "disabled"), ("slow", "DISABLED", "disabled"), ("slow", "FAST", "fast"), (None, None, "disabled")):
                with self.subTest(satellite_mode=satellite_mode, value=value, cli=cli), patch.object(rig, "rigctld_set_agc") as setter:
                    radio = Mock(wraps=rig)
                    ns = dict(rig=radio, args=SimpleNamespace(agc=cli), satellite_config={"agc": value}, squelch_level=None, rigctld_port=4532, satellite_mode_required=satellite_mode, sdr_only=False, do_satellite_setup=Mock())
                    ns.update(dict.fromkeys(("uplink_frequency_mhz", "uplink_mode", "downlink_frequency_mhz", "downlink_mode", "ctcss_tone", "radio_type", "radio_ft736r_intermediate_frequency_mhz"), 0))
                    exec(compile(block, "tracker-agc", "exec"), ns)
                    if expected is None:
                        setter.assert_not_called()
                    else:
                        setter.assert_called_once_with(expected, 4532)


if __name__ == "__main__":
    unittest.main()
