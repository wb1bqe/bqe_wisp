#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""MARMOTSat UHF receive-only OpenLST decoder; live discriminator audio and recordings."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
from plugins.bqe_tlm_decoder.service import devices
from plugins.bqe_tlm_decoder.bqe_tlm_decoder import handler
from plugins.bqe_marmotsat_decoder.service import Session
from plugins.bqe_marmotsat_decoder.protocol import BAUD, SYNC_WORD
from plugins.decoder_http import DecoderServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, help='Decode an MP3/WAV/FLAC/OGG recording')
    parser.add_argument('--device', '--input-device', help='SoundCard input or loopback device ID/name')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--sample-rate', type=int, default=48000)
    parser.add_argument('--channel', type=int, default=0, help='Zero-based input channel (default: 0)')
    parser.add_argument('--baud', type=float, default=BAUD, help='Symbol rate (default 10170)')
    parser.add_argument('--sync-word', type=lambda value: int(value, 16), default=SYNC_WORD, help='32-bit hex sync (default D391D391)')
    parser.add_argument('--output', type=Path, default=ROOT / 'decoded')
    parser.add_argument('--port', type=int, default=8774)
    parser.add_argument('--source-name', help='Satellite name for live status and saved packet metadata')
    parser.add_argument('--managed', action='store_true', help='Stop cleanly when the owning BQE process closes stdin')
    parser.add_argument('--fallback-port', action='store_true', help='Use an available port when the requested port is busy')
    parser.add_argument('--idle', action='store_true', help='Open controls without starting capture')
    parser.add_argument('--no-web', action='store_true', help='Headless capture; files exit at EOF')
    parser.add_argument('--open-browser', action='store_true', help='Explicitly open the controls in a browser')
    parser.add_argument('--stop-file', default=os.environ.get('BQE_PASS_STOP_FILE'))
    args = parser.parse_args()
    if args.list_devices:
        print(json.dumps(devices(), indent=2))
        return 0
    if args.no_web and args.idle:
        parser.error('--idle requires web controls')
    session = Session(args.output)
    server = None
    if not args.no_web:
        try:
            server = DecoderServer(('127.0.0.1', args.port), handler(session, vars(args), root=ROOT))
        except OSError:
            if not args.fallback_port:
                raise
            server = DecoderServer(('127.0.0.1', 0), handler(session, vars(args), root=ROOT))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}'
        print('MARMOTSat controls: ' + url, flush=True)
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
