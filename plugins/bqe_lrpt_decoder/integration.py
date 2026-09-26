"""Lightweight lifecycle adapter: LRPT exclusively owns IQ during its pass."""
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


def enabled(config):
    value = config.get('decode_lrpt_images', False)
    if not isinstance(value, bool):
        raise ValueError('decode_lrpt_images must be a YAML boolean')
    return value


class LRPTReceiver(Receiver):
    def __init__(self):
        super().__init__(key='decode_lrpt_images', root=ROOT, port=8773, label='LRPT')

    def start(self, config, source_name, radio_config=None):
        with self.lock:
            self.stop()
            if not enabled(config):
                return None
            if any(config.get(key) for key in ('decode_sstv_images','decode_telemetry','decode_ssdv_images')):
                raise ValueError('An LRPT pass uses raw IQ; disable audio decoders for this entry')
            if radio_config is None:
                import yaml
                radio_config = yaml.safe_load((ROOT.parents[1]/'bqe_config'/'my_rig.yaml').read_text(encoding='utf-8')) or {}
            settings = {k:v for k,v in radio_config.get('sdr',{}).items()
                        if k in ('source','device','gain','ppm','host','tcp_port','library')}
            settings.update(radio_config.get('lrpt',{}))
            settings.update(config.get('lrpt',{}))
            settings.update(frequency=float(config.get('frequency_hz') or float(config['downlink_frequency_mhz'])*1e6),
                            source_name=str(source_name))
            port = int(settings.pop('port',8773))
            output = settings.pop('output',str(ROOT/'recordings'))
            if not 1 <= port <= 65535:
                raise ValueError('Invalid LRPT port')
            python = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            instance = uuid.uuid4().hex
            args = [str(python) if python.exists() else sys.executable, str(ROOT/'bqe_lrpt_decoder.py'),
                    '--managed','--instance-id',instance,'--port',str(port),
                    '--output',str(output),'--options-json',json.dumps(settings)]
            self.process = subprocess.Popen(args, cwd=str(ROOT.parents[1]), stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic()+15
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError('LRPT receiver exited; check SatDump installation and USB ownership')
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/status',timeout=.5) as response:
                            state = json.load(response)
                    except OSError:
                        time.sleep(.1); continue
                    if state.get('instance') != instance:
                        raise RuntimeError('LRPT port is occupied; close the standalone LRPT control window/server')
                    if state['state'] == 'capturing':
                        return self.process
                    if state['state'] == 'error':
                        raise RuntimeError(state['error'])
                    time.sleep(.1)
                raise RuntimeError('LRPT startup timed out')
            except Exception:
                self.stop()
                raise


LRPT_RECEIVER = LRPTReceiver()
