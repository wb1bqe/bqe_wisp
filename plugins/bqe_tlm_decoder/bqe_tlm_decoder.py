#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""BQE telemetry decoder entry point; live sound card and recorded audio."""
import argparse
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.service import Session, devices
from plugins.decoder_http import DecoderServer, decoder_page


def handler(session, defaults, root=ROOT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data, content_type='application/json'):
            data = json.dumps(data).encode() if content_type == 'application/json' else data
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            return host in (expected, f'localhost:{self.server.server_port}') and self.headers.get('Origin', f'http://{host}') == f'http://{host}'

        def do_GET(self):
            if not self.allowed():
                self.reply(403, {'error': 'Local access only'})
                return
            try:
                if self.path == '/':
                    self.reply(200, decoder_page((root / 'index.html')), 'text/html; charset=utf-8')
                elif self.path == '/igate-settings' and hasattr(session, 'upload_settings'):
                    self.reply(200, session.upload_settings())
                elif self.path == '/igate-controls.js' and hasattr(session, 'upload_settings'):
                    self.reply(200, (root/'igate_controls.js').read_bytes(), 'application/javascript; charset=utf-8')
                elif self.path == '/status':
                    self.reply(200, session.snapshot())
                elif self.path == '/devices':
                    self.reply(200, devices())
                else:
                    self.reply(404, {'error': 'Not found'})
            except Exception as exc:
                self.reply(500, {'error': str(exc)})

        def do_POST(self):
            uploaded = None
            try:
                self.connection.settimeout(30)
                length = int(self.headers.get('Content-Length', '0'))
                incoming = self.rfile
                # Consume small bodies before rejecting a request. Closing a
                # Windows socket with unread body bytes can reset the connection
                # before the client receives our 400/403 response.
                if 0 < length <= 8192:
                    incoming = io.BytesIO(self.rfile.read(length))
                if not self.allowed():
                    self.reply(403, {'error': 'Local access only'})
                    return
                if self.path == '/file':
                    if self.headers.get('Content-Type') != 'application/octet-stream' or not 0 < length <= 512 * 1024 * 1024:
                        raise ValueError('Upload MP3/WAV audio, at most 512 MiB; use --file for larger files')
                    filename = Path(unquote(self.headers.get('X-Filename', 'audio.mp3'))).name
                    suffix = Path(filename).suffix.lower()
                    allowed_suffixes = getattr(session, 'file_suffixes', ('.mp3', '.wav', '.flac', '.ogg'))
                    if suffix not in allowed_suffixes:
                        raise ValueError('Supported files: ' + ', '.join(allowed_suffixes))
                    options = dict(defaults, **json.loads(self.headers.get('X-Options', '{}')))
                    options['source_name'] = filename
                    folder = root / '.uploads'
                    folder.mkdir(exist_ok=True)
                    uploaded = folder / (uuid.uuid4().hex + suffix)
                    with uploaded.open('wb') as stream:
                        remaining = length
                        while remaining:
                            block = incoming.read(min(65536, remaining))
                            if not block:
                                raise ValueError('Incomplete upload')
                            stream.write(block)
                            remaining -= len(block)
                    session.start(options, uploaded, cleanup=True)
                    uploaded = None
                else:
                    if self.headers.get('Content-Type') != 'application/json' or not 0 < length <= 8192:
                        raise ValueError('Expected a small JSON request')
                    options = json.loads(incoming.read(length))
                    if self.path == '/igate-settings' and hasattr(session, 'upload_settings'):
                        self.reply(200, session.upload_settings(options))
                        return
                    if self.path == '/live':
                        session.start(dict(defaults, **options))
                    elif self.path == '/stop':
                        session.stop()
                    else:
                        self.reply(404, {'error': 'Not found'})
                        return
                self.reply(200, {'ok': True})
            except Exception as exc:
                self.reply(400, {'error': str(exc)})
            finally:
                if uploaded:
                    uploaded.unlink(missing_ok=True)
    return Handler


def main(*, root=ROOT, session_factory=Session, port=8766,
         label='Telemetry', protocol='auto', protocols=('auto', 'ax25', 'usp'),
         bauds=(1200, 2400, 4800, 9600), configure_parser=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Decode an MP3/WAV/FLAC/OGG recording')
    parser.add_argument('--device', '--input-device', help='SoundCard input or loopback device ID/name')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--sample-rate', type=int, default=48000)
    parser.add_argument('--channel', type=int, default=0, help='Zero-based input channel (default: 0)')
    parser.add_argument('--bauds', type=int, nargs='+', choices=list(bauds), default=list(bauds))
    parser.add_argument('--protocol', choices=protocols, default=protocol)
    parser.add_argument('--output', type=Path, default=root / 'decoded')
    parser.add_argument('--port', type=int, default=port)
    parser.add_argument('--source-name', help='Satellite name for live status and saved packet metadata')
    parser.add_argument('--managed', action='store_true', help='Stop cleanly when the owning BQE process closes stdin')
    parser.add_argument('--fallback-port', action='store_true', help='Use an available port when the requested port is busy')
    parser.add_argument('--idle', action='store_true', help='Open controls without starting capture')
    parser.add_argument('--no-web', action='store_true', help='Headless capture; files exit at EOF')
    parser.add_argument('--open-browser', action='store_true', help='Explicitly open the controls in a browser')
    parser.add_argument('--stop-file', default=os.environ.get('BQE_PASS_STOP_FILE'))
    if configure_parser:
        configure_parser(parser)
    args = parser.parse_args()
    if args.list_devices:
        print(json.dumps(devices(), indent=2))
        return 0
    if args.no_web and args.idle:
        parser.error('--idle requires web controls')
    session = session_factory(args.output)
    server = None
    if not args.no_web:
        try:
            server = DecoderServer(('127.0.0.1', args.port), handler(session, vars(args), root=root))
        except OSError:
            if not args.fallback_port:
                raise
            server = DecoderServer(('127.0.0.1', 0), handler(session, vars(args), root=root))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}'
        print(label + ' controls: ' + url, flush=True)
        if args.open_browser:
            import webbrowser
            webbrowser.open(url)
    owner_closed = threading.Event()
    if args.managed:
        def watch_owner():
            try:
                while os.read(sys.stdin.fileno(), 1024):
                    pass
            finally:
                owner_closed.set()
        threading.Thread(target=watch_owner, daemon=True).start()
    try:
        if not args.idle:
            session.start(vars(args), args.file)
        while True:
            if owner_closed.is_set():
                break
            if args.stop_file and Path(args.stop_file).exists():
                break
            status = session.snapshot()
            if args.no_web and status['state'] in ('completed', 'stopped', 'error'):
                print(json.dumps(status, indent=2))
                return 1 if status['state'] == 'error' else 0
            time.sleep(.2)
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        if session.thread:
            session.thread.join(timeout=10)
        if server:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
