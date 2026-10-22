# SPDX-License-Identifier: GPL-3.0-or-later
"""Geoscan v1/v2 9600-baud uncoded image formats (64/72-byte payloads)."""
import struct
from .decoder import AudioDecoder as GeoscanModem
from .images import ImageReceiver, MAX_IMAGE_BYTES
from .protocol import IMAGE_MARKER

SATELLITES = {1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}


class Fragments:
    def __init__(self):
        self.starts = {}
        self.sequence = 0

    def __call__(self, packet):
        if len(packet) not in (64, 72) or packet[0] not in SATELLITES:
            return None
        satellite = packet[0]
        if len(packet) == 72 and satellite not in (1, 2):
            marker, offset, number = struct.unpack_from('<IIH', packet, 5)
            if marker == IMAGE_MARKER:
                return f'{satellite}:{number}', offset, packet[15:69]
            if packet[1] != 0x98:
                return None
        command, low = struct.unpack_from('<HH', packet, 3)
        if command not in (0x0901, 0x0905, 0x0920, 0x9820, 0x411c, 0x4150):
            return None
        size = packet[2]-6
        if not 0 < size <= len(packet)-8:
            return None
        address = (packet[7] << 16) | low
        data = packet[8:8+size]
        channel = (satellite, command in (0x9820, 0x411c))
        previous = self.starts.get(channel)
        if command == 0x0901 or data.startswith(b'\xff\xd8'):
            signature = (address, data)
            if previous is None or previous[2] != signature or previous[3]:
                self.sequence += 1
                previous = [address, f'{satellite}:v1:{self.sequence}', signature, False]
                self.starts[channel] = previous
        if previous is None:
            # A v1 memory address is not a JPEG offset until the start arrives.
            return None
        offset = address-previous[0]
        if not 0 <= offset < MAX_IMAGE_BYTES:
            return None
        if b'\xff\xd9' in data:
            previous[3] = True
        return previous[1], offset, data


class AudioDecoder(GeoscanModem):
    def __init__(self, rate, emit, report=lambda **fields: None, preview=lambda image: None,
                 packet_sink=lambda packet: None):
        fragments = Fragments()
        def receiver(emit, report, preview):
            return ImageReceiver(emit, report, preview, fragments, 'GEOSCAN', 'Geoscan')
        super().__init__(rate, emit, report, preview, packet_sink=packet_sink,
                         frame_sizes=(66, 74), image_receiver=receiver, label='Geoscan')
