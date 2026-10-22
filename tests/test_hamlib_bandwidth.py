"""Verify preset bandwidth defaults without connecting to radio hardware."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bqe_set_radio_from_yaml as presets


class BandwidthTests(unittest.TestCase):
    def test_mode_commands_and_explicit_overrides(self):
        for mode, default in (("FM", 15000), ("C4FM", 15000), ("FMN", 15000),
                              ("USB", 3000), ("LSB", 3000), ("cw", 1500),
                              ("CWR", 1500), ("AM", 0)):
            for extra in ({}, {"bandwidth": None}, {"bandwidth": ""},
                          {"bandwidth": 0}, {"bandwidth": "8000"}):
                with self.subTest(mode=mode, extra=extra), patch.object(presets, "load_yaml", return_value={}), patch.object(presets, "run_rigctl") as run:
                    presets.program_preset({"frequency_hz": 145000000, "mode": mode, **extra},
                                           {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                    value = extra.get("bandwidth")
                    expected = default if value in (None, "") else int(value)
                    commands = [call.args[2] for call in run.call_args_list if call.args[2][0] == "M"]
                    self.assertEqual(commands, [["M", mode.upper(), str(expected)]])

    def test_configured_defaults_and_missing_file(self):
        section = {"bqe_set_radio_from_yaml": {
            "default_bandwidth_fm": 16000, "default_bandwidth_ssb": 2400,
            "default_bandwidth_cw": 500}}
        for sections in (section, [section]):
            with patch.object(presets, "load_yaml", return_value={"program_settings": sections}):
                for mode, expected in (("FM", 16000), ("C4FM", 16000), ("USB", 2400), ("CW", 500)):
                    self.assertEqual(presets.preset_bandwidth({}, mode), expected)
                    self.assertEqual(presets.preset_bandwidth({"bandwidth": 0}, mode), 0)
        with patch.object(presets, "load_yaml", side_effect=FileNotFoundError):
            for mode, expected in (("FM", 15000), ("USB", 3000), ("CW", 1500)):
                self.assertEqual(presets.preset_bandwidth({}, mode), expected)

    def test_invalid_bandwidth_fails_before_radio_commands(self):
        for config, extra in (({}, {"bandwidth": "bad"}),
                              ({"program_settings": {"bqe_set_radio_from_yaml": {
                                  "default_bandwidth_fm": "bad"}}}, {})):
            with patch.object(presets, "load_yaml", return_value=config), patch.object(presets, "run_rigctl") as run:
                with self.assertRaises(ValueError):
                    presets.program_preset({"frequency_hz": 145000000, **extra},
                                           {"radio_type": 2, "radio_port": "localhost:4532", "radio_baud": 9600})
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
