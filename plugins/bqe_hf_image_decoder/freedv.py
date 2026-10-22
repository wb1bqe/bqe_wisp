"""Receive xssfox/freedv-tnc framing using the Codec2 raw-data modem API."""
import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path

import numpy as np

SCRAMBLE = bytes.fromhex('bde5a2d7a572023b863ddd7bb5d8c475')


def crc16(data):
    """CRC-16/X-25 used by freedv-tnc (not Codec2's newer data-mode CRC)."""
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc ^ 0xffff


def library():
    local = Path(__file__).with_name('native') / ('libcodec2.dll' if os.name == 'nt' else 'libcodec2.so')
    name = str(local) if local.exists() else find_library('codec2')
    if not name:
        raise RuntimeError('Codec2 library missing; see the HF image plugin README for installation.')
    lib = C.CDLL(name)
    signatures = {
        'freedv_open': (C.c_void_p, [C.c_int]),
        'freedv_close': (None, [C.c_void_p]),
        'freedv_nin': (C.c_int, [C.c_void_p]),
        'freedv_get_sync': (C.c_int, [C.c_void_p]),
        'freedv_get_bits_per_modem_frame': (C.c_int, [C.c_void_p]),
        'freedv_get_n_max_modem_samples': (C.c_int, [C.c_void_p]),
        'freedv_get_n_nom_modem_samples': (C.c_int, [C.c_void_p]),
        'freedv_rawdatarx': (C.c_int, [C.c_void_p, C.POINTER(C.c_ubyte), C.POINTER(C.c_short)]),
        'freedv_rawdatatx': (None, [C.c_void_p, C.POINTER(C.c_short), C.POINTER(C.c_ubyte)]),
    }
    for name, (result, arguments) in signatures.items():
        func = getattr(lib, name)
        func.restype, func.argtypes = result, arguments
    return lib


class Framer:
    """Bounded TNC reassembly, including recovery of one lost payload frame."""
    def __init__(self, size, emit):
        self.size, self.emit = size, emit
        self.state = 'search'
        self.frames = []
        self.missing = None

    def push(self, data):
        if self.state == 'parity':
            self.state = 'search'
            if self.missing is not None and data is not None:
                recovered = bytearray(data)
                for frame in self.frames:
                    if frame is not None:
                        for i, byte in enumerate(frame):
                            recovered[i] ^= byte
                self.frames[self.missing] = bytes(recovered)
                self.emit(b''.join(self.frames)[2:2+self.length])
            return
        if self.state == 'search':
            if data == bytes(self.size):
                self.state = 'header'
            return
        if self.state == 'header':
            if data is None:
                self.state = 'search'
                return
            if data == bytes(self.size):
                return
            self.length = int.from_bytes(data[:2], 'big')
            if not 1 <= self.length <= 2047:
                self.state = 'search'
                return
            self.frames, self.missing = [data], None
            self.state = 'data'
        else:
            if data is None:
                if self.missing is not None:
                    self.state = 'search'
                    return
                self.missing = len(self.frames)
            self.frames.append(data)
        if len(self.frames) * self.size >= self.length + 2:
            if self.missing is None:
                self.emit(b''.join(self.frames)[2:2+self.length])
            self.state = 'parity'


class Modem:
    def __init__(self, mode, emit):
        self.lib = library()
        self.handle = self.lib.freedv_open({'700D': 7}[mode])
        if not self.handle:
            raise RuntimeError(f'Codec2 cannot open FreeDV {mode}')
        self.size = self.lib.freedv_get_bits_per_modem_frame(self.handle) // 8
        self.maximum = self.lib.freedv_get_n_max_modem_samples(self.handle)
        self.framer = Framer(self.size - 2, emit)
        self.pending = np.empty(0, dtype=np.int16)

    def push(self, samples):
        self.pending = np.concatenate((self.pending, np.clip(samples*32767, -32768, 32767).astype(np.int16)))
        while self.handle:
            count = self.lib.freedv_nin(self.handle)
            # Codec2 can process buffered audio without consuming new samples,
            # especially while acquiring sync on a frequency-offset signal.
            if not 0 <= count <= self.maximum:
                raise RuntimeError(f'Invalid Codec2 audio request: {count} samples '
                                   f'(allowed 0..{self.maximum})')
            if len(self.pending) < count:
                break
            audio = np.ascontiguousarray(self.pending[:count])
            self.pending = self.pending[count:]
            output = (C.c_ubyte * self.size)()
            received = self.lib.freedv_rawdatarx(self.handle, output, audio.ctypes.data_as(C.POINTER(C.c_short)))
            if received:
                raw = bytes(output)
                valid = received == self.size and crc16(raw[:-2]) == int.from_bytes(raw[-2:], 'big')
                self.framer.push(bytes(b ^ SCRAMBLE[i % len(SCRAMBLE)] for i, b in enumerate(raw[:-2])) if valid else None)
            elif not self.lib.freedv_get_sync(self.handle):
                self.framer.state = 'search'

    def close(self):
        if self.handle:
            self.lib.freedv_close(self.handle)
            self.handle = None
