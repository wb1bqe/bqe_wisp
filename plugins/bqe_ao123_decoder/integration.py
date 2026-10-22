"""Dependency-free AO-123 lifecycle, with explicit BPSK source requirements."""
from pathlib import Path
from plugins.bqe_sstv_decoder.integration import Receiver


class AO123Receiver(Receiver):
    def start(self, config, source_name, radio_config=None):
        if config.get(self.key) is True:
            if radio_config is None:
                import yaml
                radio_config = yaml.safe_load((self.root.parents[1]/'bqe_config'/'my_rig.yaml').read_text(encoding='utf-8')) or {}
            if not isinstance(radio_config, dict):
                raise ValueError('my_rig.yaml must contain a YAML mapping')
            mode = str(config.get('mode') or config.get('downlink_mode', '')).upper()
            if mode not in ('BPSK', 'USB', 'LSB'):
                raise ValueError('AO-123 needs BPSK-preserving input, not FM voice; use the AO-123 telemetry SDR entry')
            if mode == 'BPSK' and not (config.get('receive_sdr') is True or (radio_config or {}).get('radio_is_sdr') is True):
                raise ValueError('BPSK mode requires receive_sdr: true; for external wideband linear audio use USB/LSB')
        return super().start(config, source_name, radio_config)

    def decoder_args(self, config):
        carrier = float(config.get('ao123_audio_carrier_hz', 12000))
        if not 6500 <= carrier <= 17500:
            raise ValueError('ao123_audio_carrier_hz must be 6500-17500 Hz for 48000 Hz audio')
        return ['--carrier', str(carrier)]


AO123_RECEIVER = AO123Receiver(key='decode_ao123_telemetry',
    root=Path(__file__).resolve().parent, port=8783, label='AO-123 telemetry')
