# SPDX-License-Identifier: GPL-3.0-or-later
"""Reuse the CRC-validated RS61 Geoscan audio modem without assembling images."""
from plugins.bqe_ssdv_decoder.decoder import AudioDecoder as GeoscanDecoder
from .protocol import parse_packet


class AudioDecoder:
    def __init__(self, rate, emit, options):
        if options.get('protocol', 'geoscan') != 'geoscan' or options.get('bauds', [9600]) != [9600]:
            raise ValueError('RS61 telemetry requires Geoscan framing at 9600 baud')
        self.bauds, self.skipped_bauds = [9600], []
        self.ignored_packets = self.parsed_packets = self.unknown_packets = 0
        owner = self

        class BeaconSink:
            image_packets = 1  # Suppress the image modem's waiting-for-images message.

            def __init__(self, *args):
                pass

            def packet(self, frame, when):
                packet = parse_packet(frame, round(when, 6))
                if packet is None:
                    owner.ignored_packets += 1
                    return
                owner.parsed_packets += packet['parse_status'] == 'parsed'
                owner.unknown_packets += packet['parse_status'] == 'unknown'
                emit(packet)

            def finish(self):
                pass

        self.modem = GeoscanDecoder(rate, lambda *args: None, image_receiver=BeaconSink)

    def push(self, samples):
        self.modem.push(samples)

    def finish(self):
        self.modem.finish()

    def diagnostics(self):
        return dict(valid_geoscan_packets=self.modem.packets,
                    parsed_beacons=self.parsed_packets, unknown_beacons=self.unknown_packets,
                    ignored_image_or_foreign_packets=self.ignored_packets,
                    bad_crc=sum(f.bad_crc for f in self.modem.framers),
                    duplicate_candidates=self.modem.duplicates)
