#!/usr/bin/env python3
"""BQE SDR: cross-platform RTL-SDR receiver, IQ replay and audio bridge."""
import argparse
import json
from pathlib import Path
import queue
import signal
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from plugins.bqe_sdr.config import Settings, AUDIO_RATE
from plugins.bqe_sdr.service import Session, replay_options
from plugins.bqe_sdr.sources import devices
from plugins.decoder_http import DecoderServer


def handler(session, defaults):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            host = self.headers.get('Host', '')
            return host in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') and self.headers.get('Origin', f'http://{host}') == f'http://{host}'

        def reply(self, code, value, mime='application/json'):
            data = json.dumps(value, allow_nan=False).encode() if mime == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def stream(self, wav=False):
            client = session.hub.subscribe()
            try:
                self.connection.settimeout(3)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav' if wav else 'application/octet-stream')
                self.send_header('X-Audio-Format', 's16le' if wav else 'f32le')
                self.send_header('X-Audio-Rate', str(AUDIO_RATE))
                self.send_header('X-Audio-Channels', '1')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                if wav:
                    self.wfile.write(struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 0xffffffff,
                        b'WAVE', b'fmt ', 16, 1, 1, AUDIO_RATE, AUDIO_RATE*2, 2, 16, b'data', 0xffffffff))
                while True:
                    try:
                        data = client.get(timeout=2)
                    except queue.Empty:
                        break
                    if data is None:
                        break
                    if wav:
                        import numpy as np
                        data = (np.frombuffer(data, '<f4')*32767).astype('<i2').tobytes()
                    self.wfile.write(data)
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                session.hub.unsubscribe(client)

        def do_GET(self):
            if not self.allowed():
                self.reply(403, {'error': 'Local access only'})
                return
            path = urlparse(self.path).path
            try:
                if path == '/':
                    self.reply(200, (ROOT/'index.html').read_bytes(), 'text/html; charset=utf-8')
                elif path == '/status':
                    self.reply(200, dict(session.snapshot(), instance=defaults.get('_instance')))
                elif path == '/defaults':
                    self.reply(200, defaults)
                elif path == '/devices':
                    self.reply(200, devices(defaults.get('library', '')))
                elif path in ('/audio', '/listen.wav'):
                    self.stream(path == '/listen.wav')
                else:
                    self.reply(404, {'error': 'Not found'})
            except (ValueError, RuntimeError, OSError) as exc:
                self.reply(400, {'error': str(exc)})

        def do_POST(self):
            try:
                self.connection.settimeout(3)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192:
                    raise ValueError('Expected a small JSON request')
                raw = self.rfile.read(length)
                if not self.allowed():
                    self.reply(403, {'error': 'Local access only'})
                    return
                if self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('Expected application/json')
                options = json.loads(raw)
                if not isinstance(options, dict):
                    raise ValueError('Expected a JSON object')
                if self.path == '/start':
                    values = dict(defaults, **options)
                    if values.get('source') == 'file':
                        values.update(replay_options(values['iq_file']))
                    session.start(values)
                elif self.path == '/stop':
                    session.stop()
                elif self.path == '/tune':
                    session.tune(options['frequency'])
                else:
                    self.reply(404, {'error': 'Not found'})
                    return
                self.reply(200, {'ok': True})
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.reply(400, {'error': str(exc)})
    return Handler


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('usb', 'tcp', 'file', 'demo'), default=None)
    parser.add_argument('--frequency', type=float, help='Desired receive frequency in Hz')
    parser.add_argument('--center', type=float, help='RF center frequency in Hz; normally chosen automatically')
    parser.add_argument('--sample-rate', type=int)
    parser.add_argument('--mode', choices=('FM', 'NFM', 'AM', 'USB', 'LSB', 'CW'))
    parser.add_argument('--bandwidth', type=float)
    parser.add_argument('--deviation', type=float)
    parser.add_argument('--audio-gain', type=float)
    parser.add_argument('--gain', type=float, help='Tuner gain dB; omitted means automatic')
    parser.add_argument('--ppm', type=int)
    parser.add_argument('--device', help='USB index or serial:SERIAL')
    parser.add_argument('--library', help='Absolute RTL-SDR shared library path')
    parser.add_argument('--host', help='rtl_tcp source hostname (web controls always bind to localhost)')
    parser.add_argument('--tcp-port', type=int)
    parser.add_argument('--iq-file', help='IQ data or SigMF metadata file')
    parser.add_argument('--iq-format', choices=('cu8', 'cf32_le'))
    parser.add_argument('--record-iq', action='store_true', default=None)
    parser.add_argument('--no-record-audio', dest='record_audio', action='store_false', default=None)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--source-name')
    parser.add_argument('--config', type=Path, help='YAML containing SDR options (or a rig file with an sdr mapping)')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--idle', action='store_true')
    parser.add_argument('--no-web', action='store_true')
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--managed', action='store_true', help='Read tune commands from stdin; stop on owner EOF')
    parser.add_argument('--instance-id', help=argparse.SUPPRESS)
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args(argv)
    if args.idle and args.no_web:
        parser.error('--idle requires web controls')
    return args


def main(argv=None):
    args = parse_args(argv)
    options = Settings().as_dict()
    configured = {}
    if args.config:
        import yaml
        data = yaml.safe_load(args.config.read_text(encoding='utf-8')) or {}
        if not isinstance(data, dict) or not isinstance(data.get('sdr', data), dict):
            raise ValueError('SDR configuration must be a YAML mapping')
        configured = data.get('sdr', data)
        options.update(configured)
    args.port = args.port if args.port is not None else int(configured.get('port', 8772))
    args.output = args.output or Path(configured.get('output', ROOT/'recordings'))
    options.update({k: v for k, v in vars(args).items() if k in options and v is not None})
    options['_instance'] = args.instance_id
    if args.iq_file:
        options.update(replay_options(args.iq_file))
    if args.list_devices:
        print(json.dumps(devices(options.get('library', '')), indent=2))
        return 0
    session = Session(args.output)
    server = None
    closed = threading.Event()
    signal.signal(signal.SIGINT, lambda *unused: closed.set())
    signal.signal(signal.SIGTERM, lambda *unused: closed.set())
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda *unused: closed.set())
    try:
        if not args.no_web:
            server = DecoderServer(('127.0.0.1', args.port), handler(session, options))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}'
            print('BQE SDR controls: ' + url, flush=True)
            if args.open_browser:
                import webbrowser
                webbrowser.open(url)
        if not args.idle:
            session.start(options)
        if args.managed:
            def owner():
                try:
                    for line in sys.stdin:
                        try:
                            message = json.loads(line)
                            if 'frequency' in message:
                                session.tune(message['frequency'])
                        except (ValueError, TypeError) as exc:
                            print(f'SDR tune rejected: {exc}', file=sys.stderr, flush=True)
                finally:
                    closed.set()
            threading.Thread(target=owner, daemon=True).start()
        while not closed.wait(.1):
            if args.stop_file and args.stop_file.exists():
                break
            if (args.no_web or args.managed) and session.snapshot()['state'] in ('error', 'completed', 'stopped'):
                break
    finally:
        session.stop()
        if session.thread:
            session.thread.join(8)
        if server:
            server.shutdown()
            server.server_close()
    state = session.snapshot()
    state.pop('spectrum', None)
    print(json.dumps(state, indent=2), flush=True)
    return int(state['state'] == 'error')


if __name__ == '__main__':
    raise SystemExit(main())
