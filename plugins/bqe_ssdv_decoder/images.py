# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, offset-aware Geoscan JPEG assembly with honest gap accounting."""
from collections import OrderedDict
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .protocol import image_fragment

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_IMAGES = 4


class Assembly:
    def __init__(self, number, when):
        self.number, self.started = number, when
        self.data = bytearray()
        self.covered = bytearray()
        self.received = 0
        self.packets = 0
        self.conflicts = 0
        self.saved = False
        self.last_preview = -10

    def add(self, offset, fragment):
        end = offset + len(fragment)
        if end > MAX_IMAGE_BYTES:
            return False
        if end > len(self.data):
            self.data.extend(bytes(end-len(self.data)))
            self.covered.extend(bytes(end-len(self.covered)))
        if any(self.covered[i] and self.data[i] != byte for i, byte in enumerate(fragment, offset)):
            self.conflicts += 1
            return False
        new = sum(not self.covered[i] for i in range(offset, end))
        self.data[offset:end] = fragment
        self.covered[offset:end] = b'\x01' * len(fragment)
        self.received += new
        if new:
            self.packets += 1
        return bool(new)

    def render(self):
        if self.data[:2] != b'\xff\xd8' or not all(self.covered[:2]):
            return None
        end = self.data.rfind(b'\xff\xd9')
        length = end + 2 if end >= 0 else len(self.data)
        missing = length - sum(self.covered[:length])
        complete = end >= 0 and not missing and not self.conflicts
        jpeg = bytes(self.data[:length])
        if end < 0:
            jpeg += b'\xff\xd9'
        try:
            with Image.open(BytesIO(jpeg)) as original:
                if original.format != 'JPEG' or original.width * original.height > 4096 * 4096:
                    return None
                original.load()
                image = original.convert('RGB')
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
        image.info.update(ssdv_mode='RS61_GEOSCAN', ssdv_complete=complete,
                          file_id=self.number, packets=self.packets,
                          received_bytes=self.received, missing_bytes=missing,
                          conflicting_packets=self.conflicts, end_received=end >= 0)
        return image


class ImageReceiver:
    def __init__(self, emit, report, preview):
        self.emit, self.report, self.preview = emit, report, preview
        self.images = OrderedDict()
        self.image_packets = 0
        self.rejected = 0

    def _publish(self, assembly, final=False):
        if assembly.saved:
            return
        image = assembly.render()
        if image is None:
            return
        self.preview(image)
        if image.info['ssdv_complete'] or final:
            self.emit(image, assembly.started)
            assembly.saved = True

    def packet(self, packet, when):
        fragment = image_fragment(packet)
        if fragment is None:
            return
        number, offset, data = fragment
        if offset + len(data) > MAX_IMAGE_BYTES:
            self.rejected += 1
            return
        old = self.images.get(number)
        if (old and offset == 0 and all(old.covered[:len(data)])
                and old.data[:len(data)] != data):
            self._publish(old, final=True)
            del self.images[number]
        if number not in self.images:
            if len(self.images) >= MAX_ACTIVE_IMAGES:
                _, evicted = self.images.popitem(last=False)
                self._publish(evicted, final=True)
            self.images[number] = Assembly(number, when)
        current = self.images[number]
        self.images.move_to_end(number)
        if not current.add(offset, data):
            return
        self.image_packets += 1
        self.report(mode='RS61_GEOSCAN', file_id=number, image_packets=self.image_packets,
                    received_bytes=current.received, missing_bytes=len(current.data)-current.received,
                    has_start=current.data[:2] == b'\xff\xd8',
                    conflicting_packets=current.conflicts,
                    reception=f'Receiving RS61 image {number} · {current.packets} fragments')
        if when - current.last_preview >= 2 or b'\xff\xd9' in current.data[max(0, offset-1):offset+len(data)+1]:
            self._publish(current)
            current.last_preview = when

    def finish(self):
        for assembly in self.images.values():
            self._publish(assembly, final=True)
