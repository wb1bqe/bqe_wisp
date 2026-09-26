"""RTL-SDR C ABI, rtl_tcp wire protocol, and reproducible IQ replay sources."""
import ctypes as C
from ctypes.util import find_library
import os
from pathlib import Path
import queue
import socket
import struct
import threading
import time
import numpy as np

BLOCK = 32768


def load_library(path=''):
    names = [path] if path else [os.environ.get('BQE_RTLSDR_LIBRARY', ''),
        str(Path(__file__).parent / 'drivers' / 'rtlsdr.dll') if os.name == 'nt' else '',
        find_library('rtlsdr'), 'rtlsdr.dll' if os.name == 'nt' else 'librtlsdr.so.0']
    errors = []
    for name in filter(None, names):
        directory = None
        try:
            if os.name == 'nt' and Path(name).is_absolute():
                directory = os.add_dll_directory(str(Path(name).parent))
            lib = C.CDLL(name)
            break
        except OSError as exc:
            errors.append(str(exc))
        finally:
            if directory:
                directory.close()
    else:
        raise RuntimeError('RTL-SDR library unavailable. Install librtlsdr on Linux or matching-architecture '
                           'rtlsdr.dll and dependencies in bqe_sdr/drivers on Windows. '
                           'You can also use rtl_tcp. ' + '; '.join(errors))
    signatures = {
        'get_device_count': (C.c_uint32, []),
        'get_device_name': (C.c_char_p, [C.c_uint32]),
        'get_index_by_serial': (C.c_int, [C.c_char_p]),
        'open': (C.c_int, [C.POINTER(C.c_void_p), C.c_uint32]),
        'close': (C.c_int, [C.c_void_p]),
        'set_center_freq': (C.c_int, [C.c_void_p, C.c_uint32]),
        'set_sample_rate': (C.c_int, [C.c_void_p, C.c_uint32]),
        'set_freq_correction': (C.c_int, [C.c_void_p, C.c_int]),
        'get_freq_correction': (C.c_int, [C.c_void_p]),
        'set_tuner_gain_mode': (C.c_int, [C.c_void_p, C.c_int]),
        'set_tuner_gain': (C.c_int, [C.c_void_p, C.c_int]),
        'get_tuner_gains': (C.c_int, [C.c_void_p, C.POINTER(C.c_int)]),
        'set_agc_mode': (C.c_int, [C.c_void_p, C.c_int]),
        'reset_buffer': (C.c_int, [C.c_void_p]),
        'cancel_async': (C.c_int, [C.c_void_p]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(lib, 'rtlsdr_' + name)
        fn.restype, fn.argtypes = result, args
    return lib


def devices(library=''):
    lib = load_library(library)
    return [dict(index=i, name=lib.rtlsdr_get_device_name(i).decode(errors='replace'))
            for i in range(lib.rtlsdr_get_device_count())]


class USBSource:
    datatype = 'cu8'

    def __init__(self, settings, stop):
        self.s, self.stop = settings, stop
        self.lib = load_library(settings.library)
        self.handle = C.c_void_p()
        self.thread = None
        self.error = None
        self.blocks = queue.Queue(maxsize=32)

    def check(self, name, *args):
        result = getattr(self.lib, 'rtlsdr_' + name)(self.handle, *args)
        if result < 0:
            raise RuntimeError(f'RTL-SDR {name} failed ({result}); check USB driver, device ownership, and settings')

    def __enter__(self):
        key = str(self.s.device)
        index = self.lib.rtlsdr_get_index_by_serial(key[7:].encode()) if key.startswith('serial:') else int(key)
        if index < 0 or index >= self.lib.rtlsdr_get_device_count():
            raise ValueError('RTL-SDR device not found; use --list-devices or device serial:SERIAL')
        if self.lib.rtlsdr_open(C.byref(self.handle), index) < 0:
            raise RuntimeError('Cannot open RTL-SDR; another program may own it, or the USB driver/permissions are missing')
        try:
            self.check('set_sample_rate', self.s.sample_rate)
            if self.lib.rtlsdr_get_freq_correction(self.handle) != self.s.ppm:
                self.check('set_freq_correction', self.s.ppm)
            self.check('set_center_freq', round(self.s.center))
            self.check('set_agc_mode', 0)
            self.check('set_tuner_gain_mode', int(self.s.gain is not None))
            if self.s.gain is not None:
                count = self.lib.rtlsdr_get_tuner_gains(self.handle, None)
                if count <= 0 or count > 256:
                    raise RuntimeError('Driver did not report usable tuner gains')
                gains = (C.c_int * count)()
                self.lib.rtlsdr_get_tuner_gains(self.handle, gains)
                self.check('set_tuner_gain', min(gains, key=lambda x: abs(x-self.s.gain*10)))
            self.check('reset_buffer')
            callback_type = C.CFUNCTYPE(None, C.POINTER(C.c_ubyte), C.c_uint32, C.c_void_p)

            def callback(data, length, context):
                if self.stop.is_set():
                    self.lib.rtlsdr_cancel_async(self.handle)
                    return
                try:
                    self.blocks.put_nowait(C.string_at(data, length))
                except queue.Full:
                    self.error = RuntimeError('IQ processing cannot keep up; capture stopped to avoid silent sample loss')
                    self.stop.set()
                    self.lib.rtlsdr_cancel_async(self.handle)

            self.callback = callback_type(callback)
            self.lib.rtlsdr_read_async.argtypes = [C.c_void_p, callback_type, C.c_void_p, C.c_uint32, C.c_uint32]
            self.lib.rtlsdr_read_async.restype = C.c_int

            def read():
                result = self.lib.rtlsdr_read_async(self.handle, self.callback, None, 8, BLOCK*2)
                if result < 0 and not self.stop.is_set():
                    self.error = RuntimeError(f'RTL-SDR disconnected or capture failed ({result})')
            self.thread = threading.Thread(target=read, daemon=True, name='rtl-usb')
            self.thread.start()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __iter__(self):
        while not self.stop.is_set():
            if self.error:
                raise self.error
            try:
                yield self.blocks.get(timeout=2)
            except queue.Empty:
                if not self.stop.is_set():
                    raise RuntimeError('RTL-SDR stopped delivering IQ samples')
        if self.error:
            raise self.error

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            for _ in range(30):
                self.lib.rtlsdr_cancel_async(self.handle)
                self.thread.join(.1)
                if not self.thread.is_alive():
                    break
            if self.thread.is_alive():
                raise RuntimeError('USB driver did not stop; restart this plugin before opening another receiver')
        if self.handle:
            self.lib.rtlsdr_close(self.handle)
            self.handle = C.c_void_p()


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size-len(data))
        if not block:
            raise RuntimeError('rtl_tcp disconnected before completing an IQ block')
        data.extend(block)
    return bytes(data)


class TCPSource:
    datatype = 'cu8'

    def __init__(self, settings, stop):
        self.s, self.stop, self.socket = settings, stop, None

    def command(self, code, value):
        self.socket.sendall(struct.pack('>BI', code, int(value) & 0xffffffff))

    def __enter__(self):
        self.socket = socket.create_connection((self.s.host, self.s.tcp_port), timeout=3)
        try:
            magic, tuner, gains = struct.unpack('>4sII', recv_exact(self.socket, 12))
            if magic != b'RTL0':
                raise ValueError('Server is not an rtl_tcp IQ source (missing RTL0 header)')
            self.command(2, self.s.sample_rate)
            self.command(5, self.s.ppm)
            self.command(1, round(self.s.center))
            self.command(8, 0)  # Disable RTL2832 digital AGC.
            self.command(3, int(self.s.gain is not None))
            if self.s.gain is not None:
                self.command(4, round(self.s.gain * 10))
            # Discard pre-command data still buffered by the server/TCP stack.
            recv_exact(self.socket, self.s.sample_rate * 2 // 4)
            return self
        except Exception:
            self.__exit__()
            raise

    def __iter__(self):
        while not self.stop.is_set():
            try:
                yield recv_exact(self.socket, BLOCK*2)
            except (OSError, RuntimeError):
                if not self.stop.is_set():
                    raise

    def __exit__(self, *args):
        if self.socket:
            self.socket.close()


class FileSource:
    def __init__(self, settings, stop):
        self.s, self.stop = settings, stop
        self.datatype = settings.iq_format

    def __enter__(self):
        self.file = Path(self.s.iq_file).open('rb')
        return self

    def __iter__(self):
        width = 2 if self.datatype == 'cu8' else 8
        start, count = time.monotonic(), 0
        while not self.stop.is_set():
            raw = self.file.read(BLOCK*width)
            if not raw:
                return
            if len(raw) % width:
                raise ValueError('IQ file ends with an incomplete complex sample')
            yield raw
            count += len(raw)//width
            self.stop.wait(max(0, start + count/self.s.sample_rate - time.monotonic()))

    def __exit__(self, *args):
        self.file.close()


class DemoSource:
    datatype = 'cf32_le'

    def __init__(self, settings, stop):
        self.s, self.stop = settings, stop

    def __enter__(self):
        return self

    def __iter__(self):
        count, start = 0, time.monotonic()
        while not self.stop.is_set():
            t = (np.arange(BLOCK) + count) / self.s.sample_rate
            carrier = 2*np.pi*(self.s.frequency-self.s.center)*t
            if self.s.mode == 'NFM':
                x = .6*np.exp(1j*(carrier - self.s.deviation/1000*.4*np.cos(2*np.pi*1000*t)))
            elif self.s.mode == 'AM':
                x = .5*(1+.5*np.sin(2*np.pi*1000*t))*np.exp(1j*carrier)
            else:
                tone = 0 if self.s.mode == 'CW' else (-1000 if self.s.mode == 'LSB' else 1000)
                x = .5*np.exp(1j*(carrier+2*np.pi*tone*t))
            yield x.astype('<c8').tobytes()
            count += BLOCK
            self.stop.wait(max(0, start + count/self.s.sample_rate - time.monotonic()))

    def __exit__(self, *args):
        pass


def source(settings, stop):
    return {'usb': USBSource, 'tcp': TCPSource, 'file': FileSource, 'demo': DemoSource}[settings.source](settings, stop)
