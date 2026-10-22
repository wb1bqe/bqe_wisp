# SPDX-License-Identifier: GPL-3.0-or-later
from plugins.bqe_tlm_decoder.service import Session as AudioSession
from .dsp import AudioDecoder
from .protocol import parse_packet
from plugins.bqe_ssdv_decoder.protocol import kiss_packets
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid


class Session(AudioSession):
    file_suffixes = ('.mp3', '.wav', '.flac', '.ogg', '.kiss', '.kss')

    def __init__(self, output):
        super().__init__(output, decoder_factory=AudioDecoder)

    def _run(self, options, file, cleanup):
        if not file or Path(file).suffix.lower() not in ('.kiss', '.kss'):
            return super()._run(options, file, cleanup)
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            log = self.output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                 + '-' + uuid.uuid4().hex[:8] + '.jsonl')
            with self.lock:
                self.status.update(state='running', log=str(log), kiss=None, bauds=[9600],
                                   skipped_bauds=[], packet_input=True)
            count = ignored = 0
            with Path(file).open('rb') as source, log.open('w', encoding='utf-8') as output:
                for frame in kiss_packets(source):
                    if self.stop_event.is_set():
                        break
                    count += 1
                    packet = parse_packet(frame, None, 'KISS import: original CRC not rechecked')
                    if packet is None:
                        ignored += 1
                        continue
                    packet.update(decoded_at=datetime.now(timezone.utc).isoformat(),
                                  input_source=options.get('source_name') or Path(file).name)
                    output.write(json.dumps(packet) + '\n')
                    output.flush()
                    with self.lock:
                        self.packets.append(packet)
                        self.status['packets'] += 1
            with self.lock:
                self.status.update(state='stopped' if self.stop_event.is_set() else 'completed',
                                   diagnostics=dict(imported_packets=count, ignored_packets=ignored))
        except Exception as exc:
            with self.lock:
                self.status.update(state='error', error=str(exc))
        finally:
            self.stop_event.set()
            if cleanup:
                Path(file).unlink(missing_ok=True)
