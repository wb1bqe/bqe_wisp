# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture/file lifecycle and durable, bounded packet storage."""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import queue
import threading
import uuid

import numpy as np
import soundfile as sf

from .dsp import AudioDecoder
from .protocols import kiss_encode
import json


def devices():
    import soundcard as sc
    return [dict(id=d.id, name=d.name, loopback=d.isloopback)
            for d in sc.all_microphones(include_loopback=True)]


class Session:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.packets = deque(maxlen=200)
        self.status = dict(state='idle', packets=0, error=None)

    def snapshot(self):
        with self.lock:
            return dict(self.status, recent=list(self.packets))

    def start(self, options, file=None, cleanup=False):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Stop the current decoder before starting another source')
            self.stop_event.clear()
            self.packets.clear()
            self.status = dict(state='starting', packets=0, error=None,
                               source=options.get('source_name', Path(file).name) if file else 'Live audio', audio_seconds=0)
            self.thread = threading.Thread(target=self._run, args=(dict(options), file, cleanup), daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.status['state'] = 'stopping'
                self.stop_event.set()

    def _run(self, options, file, cleanup):
        capture = None
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            log = self.output / (stem + '.jsonl')
            kiss = self.output / (stem + '.kiss')
            with log.open('w', encoding='utf-8') as records, kiss.open('wb') as binary:
                def emit(packet):
                    packet.update(decoded_at=datetime.now(timezone.utc).isoformat(),
                                  input_source=options.get('source_name', Path(file).name) if file else 'live')
                    records.write(json.dumps(packet) + '\n')
                    records.flush()
                    binary.write(kiss_encode(bytes.fromhex(packet['frame_hex'])))
                    binary.flush()
                    with self.lock:
                        self.packets.append(packet)
                        self.status['packets'] += 1

                def consume(blocks, rate, total=None):
                    decoder = AudioDecoder(rate, emit, options.get('bauds', [1200, 2400, 4800, 9600]),
                                           options.get('protocol', 'auto'))
                    channel = int(options.get('channel', 0))
                    count = 0
                    with self.lock:
                        self.status.update(state='running', sample_rate=rate, bauds=decoder.bauds,
                                           skipped_bauds=decoder.skipped_bauds, log=str(log), kiss=str(kiss))
                    for block in blocks:
                        if self.stop_event.is_set():
                            break
                        if channel < 0 or channel >= block.shape[1]:
                            raise ValueError(f'Channel {channel} unavailable; source has {block.shape[1]} channel(s)')
                        audio = block[:, channel]
                        decoder.push(audio)
                        count += len(audio)
                        with self.lock:
                            self.status.update(audio_seconds=count / rate,
                                               progress=count / total if total else None,
                                               level_dbfs=float(20 * np.log10(max(1e-9, np.sqrt(np.mean(audio**2))))),
                                               clipping=bool(np.any(np.abs(audio) >= .999)),
                                               diagnostics=decoder.diagnostics())
                    if not self.stop_event.is_set():
                        decoder.finish()

                if file:
                    with sf.SoundFile(file) as audio:
                        consume(audio.blocks(blocksize=4096, dtype='float64', always_2d=True), audio.samplerate, len(audio))
                else:
                    import soundcard as sc
                    device = options.get('device')
                    source = sc.get_microphone(device, include_loopback=True) if device else sc.default_microphone()
                    if source is None:
                        raise ValueError('No audio input device is available')
                    rate = int(options.get('sample_rate', 48000))
                    chunks = queue.Queue(maxsize=64)
                    failures = []

                    def record():
                        try:
                            # Capture all channels: WASAPI mono capture can return corrupt data.
                            with source.recorder(samplerate=rate) as recorder:
                                while not self.stop_event.is_set():
                                    data = recorder.record(numframes=2048)
                                    try:
                                        chunks.put_nowait(data)
                                    except queue.Full:
                                        raise RuntimeError('Decoder cannot keep up with live audio; select fewer baud rates')
                        except Exception as exc:
                            failures.append(exc)

                    capture = threading.Thread(target=record, daemon=True)
                    capture.start()

                    def blocks():
                        while not self.stop_event.is_set():
                            if failures:
                                raise failures[0]
                            try:
                                yield chunks.get(timeout=.2)
                            except queue.Empty:
                                if not capture.is_alive():
                                    if failures:
                                        raise failures[0]
                                    break
                    consume(blocks(), rate)
            with self.lock:
                self.status['state'] = 'stopped' if self.stop_event.is_set() else 'completed'
        except Exception as exc:
            with self.lock:
                self.status.update(state='error', error=str(exc))
        finally:
            self.stop_event.set()
            if capture:
                capture.join(timeout=5)
                if capture.is_alive():
                    with self.lock:
                        self.status.update(state='error', error='Audio driver did not stop; restart the plugin before capturing again')
                    # Prevent a second job from opening this device while it is still held.
                    capture.join()
            if cleanup and file:
                Path(file).unlink(missing_ok=True)
