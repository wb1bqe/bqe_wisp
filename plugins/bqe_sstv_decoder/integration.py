"""Dependency-free lifecycle bridge used by the scheduler and pass tracker."""
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent


class Receiver:
    def __init__(self, key='decode_sstv_images', root=ROOT, port=8768, label='SSTV'):
        self.process = None
        self.lock = threading.RLock()
        self.key, self.root, self.port, self.label = key, Path(root), port, label

    def start(self, config, source_name, radio_config=None):
        """Resolve an enabled decoder's input from the rig or legacy SSTV key."""
        with self.lock:
            self.stop()
            enabled = config.get(self.key)
            if enabled is None or enabled is False or enabled == '':
                return None
            if enabled is True:
                if radio_config is None:
                    import yaml
                    rig_path = self.root.parents[1] / 'bqe_config' / 'my_rig.yaml'
                    with rig_path.open(encoding='utf-8') as stream:
                        radio_config = yaml.safe_load(stream) or {}
                if not isinstance(radio_config, dict):
                    raise ValueError('my_rig.yaml must contain a YAML mapping')
                if config.get('receive_sdr') is True or radio_config.get('radio_is_sdr') is True:
                    from plugins.bqe_sdr.integration import audio_input
                    device = audio_input(radio_config)
                else:
                    device = radio_config.get('radio_soundcard')
                if not isinstance(device, str) or not device.strip():
                    raise ValueError(f'{self.key}: true requires radio_soundcard in my_rig.yaml')
            elif self.key == 'decode_sstv_images' and isinstance(enabled, str) and enabled.strip():
                device = enabled  # Preserve per-satellite/preset soundcard overrides.
            else:
                raise ValueError(f'{self.key} must be a YAML boolean (true or false)'
                                 + (' or a soundcard name' if self.key == 'decode_sstv_images' else ''))
            local_python = self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            command = [str(local_python) if local_python.exists() else sys.executable,
                       str(self.root / (self.root.name + '.py')), '--device', device.strip(),
                       '--source-name', str(source_name), '--managed', '--port', str(self.port),
                       '--fallback-port', '--open-browser']
            kwargs = dict(cwd=str(self.root.parents[1]), stdin=subprocess.PIPE)
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(command, **kwargs)
            print(f'[INFO] {self.label} reception started for {source_name}: {device.strip()} (PID {self.process.pid})')
            return self.process

    def stop(self):
        with self.lock:
            process, self.process = self.process, None
            if process is None:
                return
            # EOF is also delivered if the owning scheduler/tracker crashes.
            # This lets the child save a partial image before releasing audio.
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                print(f'[WARNING] {self.label} receiver did not stop cleanly; terminating it')
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


SSTV_RECEIVER = Receiver()
