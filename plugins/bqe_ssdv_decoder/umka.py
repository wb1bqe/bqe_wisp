# SPDX-License-Identifier: GPL-3.0-or-later
"""UMKA-1 USP file transfer over the shared, FEC-validated telemetry modem.

Record layouts follow SPUTNIX/SatsDecoder; see THIRD_PARTY.md. This is USP
JPEG file transfer, not the fsphil SSDV packet format.
"""
from collections import OrderedDict
import struct

from plugins.bqe_tlm_decoder.dsp import AudioDecoder as USPModem
from plugins.bqe_tlm_decoder.protocols import ax25_header
from .images import Assembly, MAX_ACTIVE_IMAGES, MAX_IMAGE_BYTES


class FileAssembly(Assembly):
    def __init__(self, number, when):
        super().__init__(number, when)
        self.expected_size = None

    def render(self):
        image = super().render()
        if image is None:
            return None
        length = self.expected_size or len(self.data)
        missing = length - sum(self.covered[:length])
        image.info.update(ssdv_mode='UMKA1_USP', missing_bytes=missing,
                          expected_bytes=self.expected_size,
                          ssdv_complete=(image.info['ssdv_complete'] and not missing
                                         and len(self.data) == length))
        return image


def records(frame):
    """Validate the entire envelope before allowing any image mutations."""
    header = ax25_header(frame)
    if header['source'].split('-')[0] != 'RS40S' or header['pid'] != 0xf0:
        return []
    payload = header['payload']
    parsed = []
    offset = 0
    while offset < len(payload):
        if len(payload)-offset < 8:
            raise ValueError('Truncated USP record header')
        message, sender, receiver, size = struct.unpack_from('<4H', payload, offset)
        offset += 8
        if size > len(payload)-offset:
            raise ValueError('Truncated USP record body')
        body = payload[offset:offset+size]
        offset += size
        if ((message == 0xc20 and size < 10) or
                (message == 0xc24 and size < 6) or
                (message == 0xc2b and size != 4)):
            raise ValueError('Invalid USP file-transfer record')
        parsed.append(((header['source'], sender, receiver), message, body))
    return parsed


class ImageReceiver:
    def __init__(self, emit, report, preview):
        self.emit, self.report, self.preview = emit, report, preview
        self.images = OrderedDict()
        self.current = {}
        self.image_packets = self.rejected = 0

    def publish(self, assembly, final=False):
        if assembly.saved:
            return
        image = assembly.render()
        if image is not None:
            self.preview(image)
            if final or image.info['ssdv_complete']:
                self.emit(image, assembly.started)
                assembly.saved = True

    def get(self, key, when, name=None):
        if key not in self.images:
            if len(self.images) >= MAX_ACTIVE_IMAGES:
                old_key, old = self.images.popitem(last=False)
                self.publish(old, final=True)
                self.current = {channel: session for channel, session in self.current.items()
                                if (channel, session) != old_key}
            self.images[key] = FileAssembly(name or f'USP-session-{key[1]}', when)
        self.images.move_to_end(key)
        return self.images[key]

    def packet(self, frame, when):
        try:
            items = records(frame)
        except ValueError:
            self.rejected += 1
            return
        for channel, message, body in items:
            if message == 0xc20:
                session = body[1]
                # The transmitted name is metadata only, never a filesystem path.
                name = body[10:].split(b'\x00', 1)[0].decode('utf-8', errors='replace')[:128]
                key = (channel, session)
                old = self.images.get(key)
                if old and (old.saved or (name and old.number != name and not str(old.number).startswith('USP-session-'))):
                    self.publish(old, final=True)
                    del self.images[key]
                self.get(key, when, name).number = name or f'USP-session-{session}'
                self.current[channel] = session
            elif message == 0xc2b:
                size = int.from_bytes(body, 'little')
                if channel not in self.current or not 0 < size <= MAX_IMAGE_BYTES:
                    self.rejected += 1
                    continue
                assembly = self.get((channel, self.current[channel]), when)
                if size < len(assembly.data):
                    self.rejected += 1
                    continue
                assembly.expected_size = size
                self.publish(assembly)
            elif message == 0xc24:
                session, offset = struct.unpack_from('<BI', body)
                fragment = body[5:]
                if offset+len(fragment) > MAX_IMAGE_BYTES:
                    self.rejected += 1
                    continue
                key = (channel, session)
                # The independent RS40S capture uses different INIT and DATA
                # session bytes. Bind the first data block to its pending INIT
                # on this exact source/sender/receiver channel.
                pending_key = (channel, self.current.get(channel))
                pending = self.images.get(pending_key)
                if (pending_key != key and pending is not None and not pending.received
                        and (key not in self.images or self.images[key].saved)):
                    self.images.pop(key, None)
                    self.images[key] = self.images.pop(pending_key)
                assembly = self.get(key, when)
                # Reused session numbers can carry a new photograph.
                if (offset == 0 and fragment.startswith(b'\xff\xd8') and assembly.saved
                        and assembly.data[:len(fragment)] != fragment):
                    name = assembly.number
                    del self.images[key]
                    assembly = self.get(key, when, name)
                self.current[channel] = session
                if assembly.expected_size and offset+len(fragment) > assembly.expected_size:
                    self.rejected += 1
                    continue
                if not assembly.add(offset, fragment):
                    continue
                self.image_packets += 1
                self.report(mode='UMKA1_USP', file_id=assembly.number,
                            image_packets=self.image_packets, received_bytes=assembly.received,
                            missing_bytes=(assembly.expected_size or len(assembly.data))-assembly.received,
                            conflicting_packets=assembly.conflicts,
                            reception=f'Receiving UMKA-1 file {assembly.number}')
                if when-assembly.last_preview >= 2 or b'\xff\xd9' in assembly.data[max(0, offset-1):offset+len(fragment)+1]:
                    self.publish(assembly)
                    assembly.last_preview = when

    def finish(self):
        for assembly in self.images.values():
            self.publish(assembly, final=True)


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **fields: None, preview=lambda image: None,
                 packet_sink=lambda packet: None):
        self.images = ImageReceiver(emit, report, preview)
        self.report, self.packet_sink = report, packet_sink
        self.packets = 0
        self.modem = USPModem(rate, self.received, bauds=(2400, 4800, 9600), protocol='usp')

    def received(self, packet):
        self.packet(bytes.fromhex(packet['frame_hex']), packet['audio_seconds'])
        self.report(baud=packet['baud'])

    def packet(self, frame, when):
        self.packet_sink(frame)
        self.packets += 1
        self.images.packet(frame, when)
        self.report(packets=self.packets, rejected_packets=self.images.rejected)

    def push(self, samples):
        self.modem.push(samples)
        self.report(**self.modem.diagnostics())

    def finish(self):
        self.modem.finish()
        self.images.finish()
