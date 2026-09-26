#!/usr/bin/env python3
"""BQE Meteor LRPT: full-IQ capture, automatic SatDump decoding and image gallery."""
import argparse
import json
import mimetypes
from pathlib import Path
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from plugins.decoder_http import DecoderServer
from plugins.bqe_lrpt_decoder.config import Options
from plugins.bqe_lrpt_decoder.engine import decode_job, executable
from plugins.bqe_lrpt_decoder.service import Session


def load_defaults(path):
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    # Share hardware identity, not the audio receiver's channel settings.
    hardware = {k:v for k,v in data.get('sdr', {}).items()
                if k in ('source','device','gain','ppm','host','tcp_port','library')}
    hardware.update(data.get('lrpt', {}))
    return hardware


def handler(session, defaults, instance=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            host = self.headers.get('Host', '')
            return host in (f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}') and self.headers.get('Origin', 'http://'+host) == 'http://'+host

        def reply(self, code, value, mime='application/json'):
            raw = json.dumps(value, allow_nan=False).encode() if mime == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.allowed():
                self.reply(403, {'error':'Local access only'}); return
            path = unquote(urlparse(self.path).path)
            try:
                if path == '/':
                    self.reply(200, (ROOT/'index.html').read_bytes(), 'text/html; charset=utf-8')
                elif path == '/status':
                    self.reply(200, dict(session.snapshot(), instance=instance, jobs=session.jobs()))
                elif path == '/defaults':
                    self.reply(200, defaults)
                elif path == '/engine':
                    self.reply(200, {'path':executable(defaults.get('satdump',''))})
                elif path.startswith('/files/'):
                    file = (session.output/path[len('/files/'):]).resolve()
                    if session.output not in file.parents or file.suffix.lower() not in ('.png','.jpg','.jpeg','.json','.log'):
                        raise ValueError('Not a viewable product')
                    self.reply(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'text/plain')
                else:
                    self.reply(404, {'error':'Not found'})
            except (OSError, ValueError) as exc:
                self.reply(400, {'error':str(exc)})

        def do_POST(self):
            if not self.allowed():
                self.reply(403, {'error':'Local access only'}); return
            try:
                self.connection.settimeout(3)
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 16384:
                    raise ValueError('Expected a small JSON request')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                values = dict(defaults, **data)
                if self.path == '/start':
                    session.start(values)
                elif self.path == '/stop':
                    session.stop()
                elif self.path == '/replay':
                    session.replay(data['iq_file'], values)
                else:
                    self.reply(404, {'error':'Not found'}); return
                self.reply(200, {'ok':True})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.reply(400, {'error':str(exc)})
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT.parents[1]/'bqe_config'/'my_rig.yaml')
    p.add_argument('--options-json', default='{}', help='Override receiver settings with a JSON mapping')
    p.add_argument('--output', type=Path)
    p.add_argument('--port', type=int, default=8773)
    p.add_argument('--idle', action='store_true')
    p.add_argument('--managed', action='store_true')
    p.add_argument('--instance-id')
    p.add_argument('--open-browser', action='store_true')
    p.add_argument('--decode-job', type=Path)
    p.add_argument('--replay', type=Path, help='Single-center SigMF data or metadata')
    args = p.parse_args(argv)
    if args.decode_job:
        result = decode_job(args.decode_job)
        return int(result['state'] == 'error')
    defaults = Options().as_dict()
    configured = load_defaults(args.config) if args.config.exists() else {}
    defaults.update(configured)
    defaults.update(json.loads(args.options_json))
    session = Session(args.output or configured.get('output', ROOT/'recordings'))
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *unused: done.set())
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda *unused: done.set())
    server = DecoderServer(('127.0.0.1', args.port), handler(session, defaults, args.instance_id))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if args.replay:
            session.replay(args.replay, defaults)
        elif not args.idle:
            session.start(defaults)
        if args.open_browser:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{server.server_port}')
        print(f'BQE LRPT controls: http://127.0.0.1:{server.server_port}', flush=True)
        if args.managed:
            def owner():
                for unused in sys.stdin:
                    pass
                done.set()
            threading.Thread(target=owner, daemon=True).start()
        while not done.wait(.1):
            if args.managed and session.snapshot()['state'] in ('queued','error'):
                break
    finally:
        session.stop()
        if session.thread:
            session.thread.join(8)
            if session.thread.is_alive():
                session.update(state='error', error='Capture did not stop within 8 seconds')
        server.shutdown()
        server.server_close()
    print(json.dumps(session.snapshot(), indent=2), flush=True)
    return int(session.snapshot()['state'] == 'error')


if __name__ == '__main__':
    raise SystemExit(main())
