"""The radio editor must round-trip typed SDR settings without losing CAT data."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
import bqe_wisp_web as web
from plugins.bqe_sdr.integration import effective_config


class RigEditorTests(unittest.TestCase):
    def test_round_trip_and_switch_back_preserve_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'my_rig.yaml'
            path.write_text(yaml.safe_dump(dict(radio_type=1035, radio_port='com4',
                sdr=dict(device='serial:001', custom_option='preserved'))), encoding='utf-8')
            with patch.object(web, 'RIG_CONFIG_PATH', path):
                result = web.write_radio_config_payload(dict(data=dict(radio_is_sdr=True,
                    sdr=dict(source='usb', device='0', sample_rate='960000', gain='',
                             audio_gain='0.15', ppm='-2', port='8772', record_iq=False))))
                self.assertTrue(result['ok'], result)
                data = yaml.safe_load(path.read_text())
                self.assertEqual(data['radio_port'], 'com4')
                self.assertIs(data['radio_is_sdr'], True)
                self.assertEqual(data['sdr']['device'], '0')
                self.assertIsNone(data['sdr']['gain'])
                self.assertEqual(data['sdr']['sample_rate'], 960000)
                self.assertEqual(data['sdr']['custom_option'], 'preserved')
                self.assertIsInstance(result['data']['sdr'], dict)
                self.assertTrue(web.write_radio_config_payload(dict(data=dict(radio_is_sdr=False)))['ok'])
                saved = yaml.safe_load(path.read_text())
                self.assertEqual(saved['sdr'], data['sdr'])
                self.assertEqual(saved['radio_type'], 1035)

    def test_invalid_settings_do_not_overwrite_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'my_rig.yaml'
            path.write_text('radio_type: 1035\n', encoding='utf-8')
            with patch.object(web, 'RIG_CONFIG_PATH', path):
                for data in [dict(sdr=dict(sample_rate='123')), dict(sdr=dict(port='70000')),
                             dict(sdr=dict(gain='nan')), dict(radio_is_sdr='true')]:
                    result = web.write_radio_config_payload(dict(data=data))
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(path.read_text(), 'radio_type: 1035\n')

    def test_station_switch_controls_pass_and_decoder_routing(self):
        original = dict(downlink_frequency_mhz=145.8)
        selected = effective_config(original, dict(radio_is_sdr=True))
        self.assertIs(selected['receive_sdr'], True)
        self.assertIs(selected['sdr_only'], True)
        self.assertNotIn('receive_sdr', original)
        self.assertEqual(effective_config(original, dict(radio_is_sdr=False)), original)
        from plugins.bqe_sstv_decoder.integration import Receiver
        with patch('plugins.bqe_sstv_decoder.integration.subprocess.Popen') as popen:
            Receiver().start(dict(decode_sstv_images=True), 'test', dict(radio_is_sdr=True))
            self.assertIn('bqe-sdr://127.0.0.1:8772', popen.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
