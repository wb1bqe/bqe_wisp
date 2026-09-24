#!/usr/bin/env python3
"""Record playback-loopback or direct-input audio to MP3 with a web console."""

import argparse
import json
import os
import signal
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_NAME = "PC Audio Recorder"
DEFAULT_RATE = 48_000
DEFAULT_BITRATE = 192
DEFAULT_BLOCKSIZE = 4_096
STATUS_FILE = Path(__file__).resolve().parents[2] / "logs" / "audio_recorder_status.json"


def publish_recording_status(recorder, path=STATUS_FILE):
    """Publish CLI capture state for the main page without opening a browser."""
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        state = recorder.snapshot()
        state.update(updated_at=time.time(), pid=os.getpid())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # A status-display failure must not interrupt audio capture.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def external_recording_status(path=STATUS_FILE):
    """Ignore old heartbeats after a recorder crashes or is forcibly stopped."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(state, dict) and 0 <= time.time() - float(state.get("updated_at", 0)) <= 5:
            return state
    except (OSError, ValueError, TypeError):
        pass
    return {"status": "stopped"}


WEB_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PC Audio Recorder</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui,Segoe UI,Arial,sans-serif; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:#08111f; color:#eaf2ff; }
    main { width:min(760px,calc(100% - 32px)); background:#101d31; border:1px solid #29405f;
           border-radius:18px; box-shadow:0 20px 70px #0008; overflow:hidden; }
    header { padding:24px 28px 18px; background:linear-gradient(135deg,#142b49,#101d31); }
    h1 { margin:0; font-size:1.55rem; } .sub { color:#9fb1ca; margin-top:5px; }
    section { padding:24px 28px 28px; }
    .status { display:flex; gap:12px; align-items:center; margin-bottom:22px; }
    .dot { width:14px; height:14px; border-radius:50%; background:#f5b942; box-shadow:0 0 18px #f5b942; }
    .dot.recording { background:#ff405c; box-shadow:0 0 20px #ff405c; animation:pulse 1.2s infinite; }
    .dot.stopped { background:#31d28a; box-shadow:0 0 16px #31d28a; }
    .dot.error { background:#ff405c; box-shadow:0 0 16px #ff405c; }
    @keyframes pulse { 50% { opacity:.45; transform:scale(.82); } }
    .state { font-size:1.22rem; font-weight:700; text-transform:capitalize; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .card { background:#0b1627; border:1px solid #253955; border-radius:12px; padding:14px; min-width:0; }
    .label { color:#8ea3bf; font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; }
    .value { margin-top:6px; font-size:1.05rem; overflow-wrap:anywhere; }
    .wide { grid-column:1/-1; }
    .error-box { display:none; margin-top:14px; background:#3b1720; color:#ffd7dd; border:1px solid #8a3041;
                 border-radius:10px; padding:12px; white-space:pre-wrap; }
    button { margin-top:22px; width:100%; border:0; border-radius:11px; padding:13px; font:inherit;
             font-weight:750; color:white; background:#d62f49; cursor:pointer; }
    button:hover { background:#eb3d59; } button:disabled { opacity:.5; cursor:default; }
    footer { color:#7489a5; font-size:.82rem; margin-top:15px; text-align:center; }
    @media (max-width:560px) { .grid { grid-template-columns:1fr; } .wide { grid-column:auto; } }
  </style>
</head>
<body><main>
  <header><h1>PC Audio Recorder</h1><div class="sub">Recording audio from the selected source</div></header>
  <section>
    <div class="status"><span id="dot" class="dot"></span><span id="state" class="state">Starting</span></div>
    <div class="grid">
      <div class="card"><div class="label">Elapsed</div><div id="elapsed" class="value">00:00:00</div></div>
      <div class="card"><div class="label">MP3 size</div><div id="size" class="value">0 B</div></div>
      <div class="card wide"><div class="label">Audio source</div><div id="device" class="value">Detecting…</div></div>
      <div class="card wide"><div class="label">Output file</div><div id="file" class="value">Preparing…</div></div>
    </div>
    <div id="error" class="error-box"></div>
    <button id="stop" onclick="stopRecorder()">Stop, save MP3, and exit</button>
    <footer>Closing this browser tab does not stop the recording.</footer>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = String(Math.floor(seconds / 3600)).padStart(2,'0');
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2,'0');
  const s = String(seconds % 60).padStart(2,'0');
  return `${h}:${m}:${s}`;
}
function bytes(value) {
  let n = Number(value || 0), units = ['B','KB','MB','GB'], i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
async function refresh() {
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const s = await r.json();
    $('state').textContent = s.status;
    $('dot').className = `dot ${s.status}`;
    $('elapsed').textContent = duration(s.elapsed_seconds);
    $('size').textContent = bytes(s.bytes_written);
    $('device').textContent = s.device || 'Detecting…';
    $('file').textContent = s.output_file || 'Preparing…';
    $('error').style.display = s.error ? 'block' : 'none';
    $('error').textContent = s.error || '';
    $('stop').disabled = ['stopping','stopped','error'].includes(s.status);
  } catch (_) {
    $('state').textContent = 'Server exited';
    $('dot').className = 'dot stopped';
    $('stop').disabled = true;
  }
}
async function stopRecorder() {
  $('stop').disabled = true;
  $('state').textContent = 'Stopping';
  try { await fetch('/api/stop', {method:'POST'}); } catch (_) {}
  setTimeout(refresh, 350);
}
refresh(); setInterval(refresh, 1000);
</script></body></html>"""


def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def channel_count(device):
    channels = getattr(device, "channels", 2)
    if isinstance(channels, int):
        return max(1, channels)
    try:
        return max(1, len(channels))
    except TypeError:
        return 2


class LoopbackRecorder:
    def __init__(self, output_prefix, device_name=None, input_device_name=None,
                 samplerate=DEFAULT_RATE, bitrate=DEFAULT_BITRATE,
                 blocksize=DEFAULT_BLOCKSIZE, timestamp_separator="_"):
        self.output_prefix = Path(output_prefix).expanduser().resolve()
        self.device_name = device_name
        self.input_device_name = input_device_name
        self.samplerate = samplerate
        self.bitrate = bitrate
        self.blocksize = blocksize
        self.timestamp_separator = timestamp_separator
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.state = {
            "status": "starting",
            "started_at": None,
            "finished_at": None,
            "device": None,
            "output_file": None,
            "bytes_written": 0,
            "frames_recorded": 0,
            "error": None,
        }

    def start(self):
        self.thread = threading.Thread(target=self._record, name="audio-recorder", daemon=False)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.state["status"] in ("starting", "recording"):
                self.state["status"] = "stopping"

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        if result["started_at"] and result["status"] not in ("stopped", "error"):
            result["elapsed_seconds"] = time.monotonic() - result.get("_started_monotonic", time.monotonic())
        else:
            result["elapsed_seconds"] = result.get("_elapsed_seconds", 0.0)
        result.pop("_started_monotonic", None)
        result.pop("_elapsed_seconds", None)
        if result["output_file"]:
            result["output_file"] = str(result["output_file"])
        return result

    def _set_error(self, message):
        with self.lock:
            self.state["status"] = "error"
            self.state["error"] = message
            self.state["finished_at"] = iso_now()
            started = self.state.get("_started_monotonic")
            self.state["_elapsed_seconds"] = time.monotonic() - started if started else 0.0

    def _resolve_loopback(self, soundcard):
        speaker = soundcard.get_speaker(self.device_name) if self.device_name else soundcard.default_speaker()
        if speaker is None:
            raise RuntimeError("No playback device was found.")

        # Matching by backend ID is exact on most systems. Name matching is a
        # useful fallback for Linux PulseAudio/PipeWire and some WASAPI drivers.
        candidates = (getattr(speaker, "id", None), str(speaker.name))
        last_error = None
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                loopback = soundcard.get_microphone(candidate, include_loopback=True)
                if loopback is not None and getattr(loopback, "isloopback", True):
                    return speaker, loopback
            except Exception as exc:
                last_error = exc
        detail = " ({})".format(last_error) if last_error else ""
        raise RuntimeError("The playback device does not expose a loopback input{}".format(detail))

    def _resolve_source(self, soundcard):
        if self.input_device_name:
            source = soundcard.get_microphone(
                self.input_device_name,
                include_loopback=False,
            )
            if source is None:
                raise RuntimeError(
                    "No direct input device matching {!r} was found."
                    .format(self.input_device_name)
                )
            return source, "{} (direct input)".format(source.name)

        speaker, loopback = self._resolve_loopback(soundcard)
        return loopback, "{} (playback loopback)".format(speaker.name)

    def _record(self):
        try:
            import lameenc
            import numpy as np
            import soundcard as sc
        except Exception as exc:
            self._set_error(
                "The audio libraries could not be initialized: {}: {}. "
                "Install the requirements and make sure the operating-system audio service is running."
                .format(type(exc).__name__, exc)
            )
            return

        final_path = None
        part_path = None
        output_handle = None
        encoder = None
        finalized = False

        try:
            output_dir = self.output_prefix.parent
            output_name = self.output_prefix.name
            if not output_name:
                raise ValueError("--output must include a filename prefix, not only a directory")
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            filename = output_name + self.timestamp_separator + stamp
            final_path = output_dir / (filename + ".mp3")
            suffix = 1
            while final_path.exists() or Path(str(final_path) + ".part").exists():
                final_path = output_dir / "{}_{}.mp3".format(filename, suffix)
                suffix += 1
            part_path = Path(str(final_path) + ".part")

            audio_source, source_label = self._resolve_source(sc)
            available_channels = channel_count(audio_source)
            channels = 2 if available_channels >= 2 else 1
            channel_map = [0, 1] if channels == 2 else [0]

            encoder = lameenc.Encoder()
            encoder.set_bit_rate(self.bitrate)
            encoder.set_in_sample_rate(self.samplerate)
            encoder.set_channels(channels)
            encoder.set_quality(2)

            output_handle = open(part_path, "wb")
            started_mono = time.monotonic()
            with self.lock:
                self.state.update({
                    "status": "recording",
                    "started_at": iso_now(),
                    "_started_monotonic": started_mono,
                    "device": source_label,
                    "output_file": str(final_path),
                })

            with audio_source.recorder(
                samplerate=self.samplerate,
                channels=channel_map,
                blocksize=self.blocksize,
            ) as source:
                while not self.stop_event.is_set():
                    samples = source.record(numframes=self.blocksize)
                    samples = np.asarray(samples, dtype=np.float32)
                    if samples.size == 0:
                        time.sleep(0.01)
                        continue
                    if samples.ndim == 1:
                        samples = samples.reshape(-1, 1)
                    if samples.shape[1] < channels:
                        samples = np.repeat(samples[:, :1], channels, axis=1)
                    elif samples.shape[1] > channels:
                        samples = samples[:, :channels]

                    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
                    encoded = encoder.encode(pcm.tobytes(order="C"))
                    if encoded:
                        output_handle.write(encoded)
                    with self.lock:
                        self.state["frames_recorded"] += int(samples.shape[0])
                        self.state["bytes_written"] = output_handle.tell()

            tail = encoder.flush()
            if tail:
                output_handle.write(tail)
            output_handle.flush()
            os.fsync(output_handle.fileno())
            output_handle.close()
            output_handle = None
            os.replace(str(part_path), str(final_path))
            finalized = True

            with self.lock:
                self.state["status"] = "stopped"
                self.state["finished_at"] = iso_now()
                self.state["bytes_written"] = final_path.stat().st_size
                self.state["_elapsed_seconds"] = time.monotonic() - started_mono
        except Exception as exc:
            self._set_error(
                "{}: {}\n\nTry --list-devices, then select a playback source with "
                "--device or a recording source with --input-device."
                .format(type(exc).__name__, exc)
            )
        finally:
            if output_handle is not None:
                try:
                    if encoder is not None:
                        tail = encoder.flush()
                        if tail:
                            output_handle.write(tail)
                    output_handle.flush()
                    os.fsync(output_handle.fileno())
                    output_handle.close()
                except Exception:
                    pass
            # Preserve usable audio even if a device error occurs during recording.
            if not finalized and part_path and final_path and part_path.exists() and part_path.stat().st_size:
                try:
                    os.replace(str(part_path), str(final_path))
                    with self.lock:
                        self.state["output_file"] = str(final_path)
                        self.state["bytes_written"] = final_path.stat().st_size
                except OSError:
                    pass


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, recorder, shutdown_event):
        self.recorder = recorder
        self.shutdown_event = shutdown_event
        super().__init__(address, ConsoleHandler)


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "PCAudioRecorder/1.0"

    def log_message(self, fmt, *args):
        return

    def _send(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, WEB_PAGE, "text/html; charset=utf-8")
        elif path == "/api/status":
            payload = json.dumps(self.server.recorder.snapshot()).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")
        else:
            self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/stop":
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        self.server.recorder.stop()
        self.server.shutdown_event.set()
        self._send(202, '{"ok":true}', "application/json; charset=utf-8")


def list_devices():
    try:
        import soundcard as sc
    except Exception as exc:
        print("Cannot initialize audio: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        print("Install the requirements and make sure the operating-system audio service is running.", file=sys.stderr)
        return 2
    speakers = sc.all_speakers()
    default = sc.default_speaker()
    default_id = getattr(default, "id", None) if default else None
    print("Playback devices (these are the available loopback sources):")
    for speaker in speakers:
        marker = " [default]" if getattr(speaker, "id", None) == default_id else ""
        print("  {}{}".format(speaker.name, marker))

    microphones = sc.all_microphones(include_loopback=False)
    try:
        default_microphone = sc.default_microphone()
    except Exception:
        default_microphone = None
    default_microphone_id = getattr(default_microphone, "id", None) if default_microphone else None
    print("\nInput devices (use one of these with --input-device):")
    for microphone in microphones:
        marker = " [default]" if getattr(microphone, "id", None) == default_microphone_id else ""
        print("  {}{}".format(microphone.name, marker))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Web console address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Web console port (default: 8765)")
    parser.add_argument(
        "--output",
        default=os.path.join("recordings", "pc-audio"),
        help="MP3 path and filename prefix (default: recordings/pc-audio)",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--device",
        help="Playback-device name or unique substring (records its loopback)",
    )
    source_group.add_argument(
        "--input-device",
        help="Input-device name or unique substring (records it directly)",
    )
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_RATE, help="Sample rate in Hz")
    parser.add_argument("--bitrate", type=int, default=DEFAULT_BITRATE, help="MP3 bitrate in kbps")
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument("--open-browser", dest="no_browser", action="store_false",
                               help="Open the optional recorder web console automatically")
    browser_group.add_argument("--no-browser", dest="no_browser", action="store_true",
                               help="Do not open the web console (the default)")
    parser.set_defaults(no_browser=True)
    parser.add_argument(
        "--stop-file",
        default=os.environ.get("BQE_PASS_STOP_FILE"),
        help=(
            "Optional file whose creation requests a clean stop. "
            "bqe_track_continuously.py supplies this automatically during satellite passes."
        ),
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List playback and direct-input devices, then exit",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if args.list_devices:
        return list_devices()
    if not 1 <= args.port <= 65535:
        print("Port must be between 1 and 65535.", file=sys.stderr)
        return 2
    if args.sample_rate < 8_000 or args.bitrate < 32:
        print("Sample rate or bitrate is unreasonably low.", file=sys.stderr)
        return 2

    shutdown_event = threading.Event()
    recorder = LoopbackRecorder(
        output_prefix=args.output,
        device_name=args.device,
        input_device_name=args.input_device,
        samplerate=args.sample_rate,
        bitrate=args.bitrate,
    )

    def request_shutdown(_signum=None, _frame=None):
        recorder.stop()
        shutdown_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)
    if hasattr(signal, "SIGBREAK"):
        # Windows Popen.terminate() cannot be caught, but CTRL_BREAK can be
        # caught when the recorder is launched in a separate process group.
        signal.signal(signal.SIGBREAK, request_shutdown)

    try:
        server = ConsoleServer((args.host, args.port), recorder, shutdown_event)
    except OSError as exc:
        print("Could not start web console on {}:{}: {}".format(args.host, args.port, exc), file=sys.stderr)
        return 1

    recorder.start()
    publish_recording_status(recorder)
    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = "http://{}:{}".format(url_host, args.port)
    print("{} is starting immediately.".format(APP_NAME))
    print("Web console: {}".format(url))
    print("Press Ctrl+C or use the web console to stop and save the MP3.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    server.timeout = 0.5
    try:
        while not shutdown_event.is_set():
            server.handle_request()
            publish_recording_status(recorder)
            if args.stop_file and os.path.exists(args.stop_file):
                print("Clean stop requested by the satellite pass controller.")
                request_shutdown()
            if recorder.snapshot()["status"] == "error":
                # Keep the console available so the error can be read.
                continue
    finally:
        recorder.stop()
        recorder.join()
        publish_recording_status(recorder)
        server.server_close()
        if args.stop_file:
            try:
                os.remove(args.stop_file)
            except FileNotFoundError:
                pass
            except OSError as exc:
                print("Warning: could not remove stop request file: {}".format(exc), file=sys.stderr)

    state = recorder.snapshot()
    if state["output_file"] and state["bytes_written"]:
        print("Saved: {}".format(state["output_file"]))
    if state["error"]:
        print(state["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
