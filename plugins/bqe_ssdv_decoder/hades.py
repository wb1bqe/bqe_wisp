"""SO-127: flat FM discriminator samples -> FSK -> standard SSDV images.

HADES replaces the SSDV callsign with BF35/FB/A3 on air. The original RS
parity repairs those four bytes, as in the operator's fsphil SSDV utility.
"""
from collections import OrderedDict, deque
from io import BytesIO
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import zlib

import numpy as np
import reedsolo
from PIL import Image
from scipy.signal import firwin, lfilter
from plugins.bqe_tlm_decoder.dsp import SymbolClock

VENDOR = Path(__file__).with_name('vendor') / 'ssdv'


def executable():
    path = VENDOR / ('ssdv.exe' if os.name == 'nt' else 'ssdv')
    result = str(path) if path.is_file() else shutil.which('ssdv')
    if not result:
        raise RuntimeError('SO-127 needs the SSDV utility: run make in plugins/bqe_ssdv_decoder/vendor/ssdv (gcc and make required).')
    return result


class Validator:
    def __init__(self):
        self.rs = reedsolo.RSCodec(32, nsize=255, fcr=112, prim=0x187, generator=173)

    def packet(self, raw):
        if len(raw) != 256 or raw[:6] != bytes.fromhex('5566bf35fba3'):
            return None
        try:
            _, corrected, _ = self.rs.decode(raw[1:])
        except reedsolo.ReedSolomonError:
            return None
        packet = b'\x55' + bytes(corrected)
        if packet[1] != 0x66 or zlib.crc32(packet[1:220]) != int.from_bytes(packet[220:224], 'big'):
            return None
        if not packet[9] or not packet[10]:
            return None
        mcu = int.from_bytes(packet[13:15], 'big')
        if mcu != 65535 and (mcu >= packet[9]*packet[10] or packet[12] >= 205):
            return None
        return packet


class Framer:
    def __init__(self, emit):
        self.emit = emit
        self.shift = 0
        self.candidates = []

    def feed(self, value, when):
        bit = int(value >= 0)
        remaining = []
        for polarity, bits in self.candidates:
            bits.append(bit ^ polarity)
            if len(bits) == 252*8:
                tail = np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()
                self.emit(bytes.fromhex('5566bf35') + tail, when)
            else:
                remaining.append((polarity, bits))
        self.candidates = remaining
        self.shift = ((self.shift << 1) | bit) & 0xffffffff
        if self.shift in (0x5566bf35, 0xaa9940ca) and len(self.candidates) < 4:
            self.candidates.append((int(self.shift == 0xaa9940ca), []))


class Images:
    def __init__(self, emit, report, preview):
        self.engine = executable()
        self.emit, self.report, self.preview = emit, report, preview
        self.groups = OrderedDict()
        self.image_packets = 0

    def packet(self, packet, when):
        key = (packet[2:7], packet[9:12][0:2], packet[11] & 0xfb)
        if key not in self.groups:
            if len(self.groups) == 4:
                _, old = self.groups.popitem(last=False)
                self.save(old, when)
            self.groups[key] = {'packets': {}, 'saved': False, 'end': None, 'conflicts': 0}
        group = self.groups[key]
        number = int.from_bytes(packet[7:9], 'big')
        old = group['packets'].get(number)
        if old is not None:
            if old != packet:
                group['conflicts'] += 1
            return
        if len(group['packets']) >= 8192:
            self.report(warning='SO-127 image packet limit reached; packet remains in KISS log')
            return
        group['packets'][number] = packet
        group['saved'] = False
        self.image_packets += 1
        if packet[11] & 4:
            group['end'] = number
        self.report(image_packets=self.image_packets, reception='Receiving SO-127 SSDV image packets')
        if self.complete(group):
            self.save(group, when)

    @staticmethod
    def complete(group):
        end = group['end']
        return end is not None and not group['conflicts'] and all(i in group['packets'] for i in range(end+1))

    def save(self, group, when):
        if group['saved'] or not group['packets']:
            return
        with tempfile.TemporaryDirectory(prefix='bqe-hades-') as folder:
            src, dst = Path(folder)/'image.ssdv', Path(folder)/'image.jpg'
            src.write_bytes(b''.join(p for _, p in sorted(group['packets'].items())))
            result = subprocess.run([self.engine, '-d', str(src), str(dst)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode or not dst.exists() or not dst.stat().st_size:
                self.report(warning='SO-127 packets retained; SSDV utility could not render this image')
                return
            with Image.open(BytesIO(dst.read_bytes())) as decoded:
                image = decoded.convert('RGB')
        packets = group['packets']
        first = next(iter(packets.values()))
        image.info.update(ssdv_mode='SO-127', ssdv_complete=self.complete(group),
                          file_id=first[6], packets=len(packets), end_received=group['end'] is not None,
                          conflicting_packets=group['conflicts'],
                          missing_packets=sum(i not in packets for i in range((group['end'] if group['end'] is not None else max(packets))+1)))
        self.preview(image)
        self.emit(image, when)
        group['saved'] = True

    def finish(self):
        for group in self.groups.values():
            self.save(group, None)


class AudioDecoder:
    def __init__(self, rate, emit, report=lambda **kw: None, preview=lambda image: None,
                 packet_sink=lambda packet: None):
        if rate < 8000:
            raise ValueError('SO-127 needs at least 8000 audio samples per second')
        self.report, self.packet_sink = report, packet_sink
        self.images = Images(emit, report, preview)
        self.validator = Validator()
        self.recent = deque(maxlen=128)
        self.packets = self.bad_crc = 0
        self.chains = []
        self.samples = 0
        self.rate = rate
        self.dc = np.zeros(1)
        for baud in (200, 800, 1600):
            taps = firwin(int(6*rate/baud) | 1, .75*baud, fs=rate)
            framers = [Framer(self.packet) for _ in range(4)]
            clocks = [SymbolClock(rate, baud, i/4, f.feed, (len(taps)-1)/2) for i,f in enumerate(framers)]
            self.chains.append([taps, np.zeros(len(taps)-1), clocks])

    def packet(self, raw, when):
        packet = self.validator.packet(raw)
        if packet is None:
            self.bad_crc += 1
            return
        if packet in self.recent:
            return
        self.recent.append(packet)
        self.packets += 1
        # Retain corrected payload bytes with the HADES on-air header, so
        # KISS replay follows the identical validated path.
        self.packet_sink(packet[:2] + bytes.fromhex('bf35fba3') + packet[6:])
        self.images.packet(packet, when)
        self.report(packets=self.packets, bad_crc=self.bad_crc)

    def push(self, samples):
        samples = np.asarray(samples, dtype=float)
        if samples.ndim != 1 or not np.all(np.isfinite(samples)):
            raise ValueError('Expected finite mono FM-discriminator samples')
        if not len(samples):
            return
        samples, self.dc = lfilter([1,-1], [1,-np.exp(-2*np.pi*.5/self.rate)], samples, zi=self.dc)
        for chain in self.chains:
            taps, state, clocks = chain
            filtered, chain[1] = lfilter(taps, [1], samples, zi=state)
            for clock in clocks:
                clock.push(filtered)
        self.samples += len(samples)

    def finish(self):
        self.push(np.zeros(int(self.rate*.1)))
        self.images.finish()
        self.report(packets=self.packets, bad_crc=self.bad_crc)
