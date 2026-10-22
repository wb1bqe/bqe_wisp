"""Dependency-free managed SDR lifecycle and Doppler commands for BQE."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid
from plugins.bqe_sstv_decoder.integration import Receiver

ROOT = Path(__file__).resolve().parent
# Windows cold starts can spend more than 12 seconds loading SciPy and USB
# drivers before the local status server becomes available.
STARTUP_TIMEOUT = 60 if os.name == 'nt' else 12


def effective_config(config, radio_config=None):
    """Apply the station's SDR-only choice to a pass or idle preset."""
    if radio_config is None:
        import yaml
        radio_config = yaml.safe_load((ROOT.parents[1]/'bqe_config'/'my_rig.yaml').read_text(encoding='utf-8')) or {}
    result = dict(config)
    if radio_config.get('radio_is_sdr') is True or config.get('decode_lrpt_images') is True:
        result.update(receive_sdr=True, sdr_only=True)
    return result


def enabled(config):
    value = config.get('receive_sdr', False)
    if not isinstance(value, bool):
        raise ValueError('receive_sdr must be a YAML boolean')
    return value


def rig_settings(radio_config=None):
    if radio_config is None:
        import yaml
        radio_config = yaml.safe_load((ROOT.parents[1]/'bqe_config'/'my_rig.yaml').read_text(encoding='utf-8')) or {}
    settings = radio_config.get('sdr', {})
    if not isinstance(settings, dict):
        raise ValueError('sdr in my_rig.yaml must be a mapping')
    return dict(settings)


def audio_input(radio_config=None):
    return f'bqe-sdr://127.0.0.1:{int(rig_settings(radio_config).get("port", 8772))}'


class SDRReceiver(Receiver):
    def __init__(self):
        super().__init__(key='receive_sdr', root=ROOT, port=8772, label='SDR')

    def start(self, config, source_name, radio_config=None):
        with self.lock:
            self.stop()
            config = effective_config(config, radio_config)
            if not enabled(config):
                return None
            settings = rig_settings(radio_config)
            overrides = config.get('sdr', {})
            if not isinstance(overrides, dict):
                raise ValueError('Per-pass sdr options must be a mapping')
            if 'port' in overrides:
                raise ValueError('Set the SDR port in my_rig.yaml only, so every decoder uses the same audio endpoint')
            settings.update(overrides)
            if 'record_audio' in config:
                from plugins.pass_audio import recording_requested
                recording_requested(config) # Validate before opening the receiver.
                # A single shared recorder owns explicitly requested pass WAVs.
                settings['record_audio'] = False
            frequency = float(config.get('frequency_hz') or float(config['downlink_frequency_mhz'])*1e6)
            mode = str(config.get('mode') or config.get('downlink_mode', 'NFM')).upper()
            settings.update(frequency=frequency, mode=mode, source_name=str(source_name))
            if 'bandwidth' not in settings:
                bandwidth = float(config.get('bandwidth') or (3000 if mode in ('USB', 'LSB') else 16000 if mode == 'BPSK' else 25000))
                # Conventional-radio filter settings can exceed the SDR audio
                # demodulator's SSB limit. Adapt only this inherited setting;
                # an explicit sdr.bandwidth must still be validated as entered.
                if mode in ('USB', 'LSB') and 12000 < bandwidth <= 40000:
                    print(f'[WARNING] {source_name}: using SDR SSB bandwidth 12000 Hz '
                          f'instead of inherited radio bandwidth {bandwidth:g} Hz')
                    bandwidth = 12000
                settings['bandwidth'] = bandwidth
            port = int(settings.pop('port', 8772))
            if not 1 <= port <= 65535:
                raise ValueError('SDR control port must be 1–65535')
            from .config import Settings
            try:
                Settings.from_dict(settings)
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Invalid SDR settings for {source_name}: {exc}') from exc
            local = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            command = [str(local) if local.exists() else sys.executable, str(ROOT/'bqe_sdr.py'),
                       '--managed', '--port', str(port)]
            instance = uuid.uuid4().hex
            command += ['--instance-id', instance]
            for key, value in settings.items():
                if key == 'record_audio':
                    if value is False:
                        command.append('--no-record-audio')
                elif key == 'record_iq':
                    if value is True:
                        command.append('--record-iq')
                elif key == 'open_browser':
                    if value is True:
                        command.append('--open-browser')
                elif value is not None:
                    command.extend(['--'+key.replace('_', '-'), str(value)])
            kwargs = dict(cwd=str(ROOT.parents[1]), stdin=subprocess.PIPE)
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(command, **kwargs)
            try:
                deadline = time.monotonic()+STARTUP_TIMEOUT
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError('SDR process exited; run its launcher to inspect driver/settings errors')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/status', timeout=.5) as response:
                            state = json.load(response)
                    except OSError:
                        time.sleep(.1)
                        continue
                    if state.get('instance') != instance:
                        raise RuntimeError(f'SDR port {port} belongs to another receiver; stop it before starting a managed pass')
                    if state['state'] == 'running':
                        print(f'[INFO] SDR reception started for {source_name}: {frequency/1e6:.6f} MHz')
                        return self.process
                    if state['state'] == 'error':
                        raise RuntimeError(state['error'])
                    time.sleep(.1)
                raise RuntimeError(f'SDR startup timed out after {STARTUP_TIMEOUT} seconds; '
                                   'check the SDR launcher for driver/settings errors')
            except Exception:
                self.stop()
                raise

    def tune(self, frequency):
        with self.lock:
            if self.process is None:
                return
            if self.process.poll() is not None:
                raise RuntimeError('SDR receiver exited during the pass')
            self.process.stdin.write((json.dumps({'frequency': frequency})+'\n').encode())
            self.process.stdin.flush()


SDR_RECEIVER = SDRReceiver()
