# SPDX-License-Identifier: GPL-3.0-or-later
"""Reuse the proven audio lifecycle, monitor, preview, and atomic gallery saves."""
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from plugins.bqe_sstv_decoder.service import Session as ImageSession, devices, gallery_settings
from plugins.bqe_tlm_decoder.protocols import kiss_encode
from .decoder import AudioDecoder
from .protocol import kiss_packets


class Session(ImageSession):
    image_prefix = 'ssdv'
    spectrum_limit = 12000
    mode_key = 'ssdv_mode'
    complete_key = 'ssdv_complete'
    audio_suffixes = ('.wav', '.mp3', '.ogg', '.flac', '.kiss', '.kss')
    waiting_message = 'Waiting for RS61 / Geoscan image packets (9600 baud)'
    no_image_message = 'No displayable RS61 image yet. Valid packets are retained in the KISS log.'

    def _save(self, image, seconds):
        # KISS files carry packet bytes without audio timestamps.
        super()._save(image, None if self.status.get('packet_input') else seconds)

    def image_metadata(self, image):
        return {key: image.info.get(key) for key in
                ('file_id', 'packets', 'received_bytes', 'missing_bytes', 'conflicting_packets', 'end_received')}

    def new_decoder(self, rate, options):
        self.output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        log = self.output / ('ssdv-packets-' + stamp + '-' + uuid.uuid4().hex[:8] + '.kiss')
        log.touch(exist_ok=False)
        self.update(packet_log=str(log), baud=9600, packets=0, image_packets=0)

        def save_packet(packet):
            with log.open('ab') as stream:
                stream.write(kiss_encode(packet))

        return AudioDecoder(rate, self._save, self.update, self.preview, packet_sink=save_packet)

    def _run(self, options, file, cleanup):
        if not file or Path(file).suffix.lower() not in ('.kiss', '.kss'):
            return super()._run(options, file, cleanup)
        try:
            decoder = self.new_decoder(48000, options)
            self.update(state='running', packet_input=True,
                        reception='Reading previously demodulated Geoscan packets')
            with Path(file).open('rb') as stream:
                for packet in kiss_packets(stream):
                    if self.stop_event.is_set():
                        break
                    if len(packet) == 72:
                        decoder.packet(packet, time.monotonic())
            decoder.images.finish()
            self.update(state='stopped' if self.stop_event.is_set() else 'completed',
                        reception='Images saved' if self.status['images'] else self.no_image_message)
        except Exception as exc:
            self.update(state='error', error=str(exc) or type(exc).__name__)
        finally:
            self.stop_event.set()
            if cleanup:
                Path(file).unlink(missing_ok=True)
