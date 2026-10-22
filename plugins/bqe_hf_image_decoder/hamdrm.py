"""Isolated RXAMADRM process receiving 48 kHz mono float audio on stdin."""
from collections import deque
import gzip
import hashlib
from io import BytesIO
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading

import numpy as np
from PIL import Image
import reedsolo

NATIVE = Path(__file__).with_name('native')


def unpack_rs(data, level):
    """HamDRM RS1..RS4: column-interleaved RS(255,224/192/160/128)."""
    size = {1: 224, 2: 192, 3: 160, 4: 128}[level]
    if not data or len(data) > 408000 or len(data) % 255:
        raise ValueError('Invalid HamDRM Reed-Solomon object length')
    rows = len(data) // 255
    blocks = np.frombuffer(data, dtype=np.uint8).reshape(255, rows).T
    rs = reedsolo.RSCodec(255-size, fcr=1, prim=0x11d)
    # RXAMADRM's Phil Karn implementation numbers coefficients low to high;
    # reedsolo uses high to low. Retain the corrected full codeword, reverse
    # it back, then take the leading data bytes (parity follows on the wire).
    decoded = b''.join(bytes(rs.decode(bytes(block)[::-1])[1])[::-1][:size] for block in blocks)
    if int.from_bytes(decoded[1:3], 'little') != rows or decoded[0] >= size:
        raise ValueError('Invalid HamDRM Reed-Solomon file header')
    return decoded[7:len(decoded)-decoded[0]]


def open_image(data, name=''):
    suffix = Path(name).suffix.lower()
    if suffix in ('.rs1', '.rs2', '.rs3', '.rs4'):
        data = unpack_rs(data, int(suffix[-1]))
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=BytesIO(data)) as stream:
            data = stream.read(8*1024*1024+1)
        if len(data) > 8*1024*1024:
            raise ValueError('HamDRM decompressed object too large')
    with Image.open(BytesIO(data)) as source:
        if source.width*source.height > 16_000_000:
            raise ValueError('HamDRM image too large')
        return source.convert('RGB')


class Modem:
    def __init__(self, emit, report):
        engine = NATIVE / ('hamdrm.exe' if os.name == 'nt' else 'hamdrm')
        if not engine.is_file():
            raise RuntimeError('HamDRM engine missing; build native/rxamadrm as described in README.')
        self.emit, self.report = emit, report
        self.folder = tempfile.TemporaryDirectory(prefix='bqe-hamdrm-')
        self.root = Path(self.folder.name)
        self.closed = False
        self.queue = queue.Queue(maxsize=64)
        self.events = queue.Queue(maxsize=64)
        self.errors = []
        self.seen = deque(maxlen=128)
        shutil.copy(NATIVE/'rxamadrm/lowpass.dat', self.root/'lowpass.dat')
        for sub in ('picsrx', 'incomplete'):
            (self.root/sub).mkdir()
        self.process = subprocess.Popen([str(engine.resolve())], cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.reader.start()
        self.writer.start()

    def _read(self):
        try:
            for raw in self.process.stdout:
                line = raw.decode('utf-8', errors='replace').strip()
                if line.startswith('Fileclosed'):
                    parts = line.split(maxsplit=1)
                    if len(parts) != 2:
                        continue
                    path = (self.root/parts[1]).resolve()
                    if path.parent not in ((self.root/'picsrx').resolve(), (self.root/'incomplete').resolve()):
                        continue
                    if not path.is_file() or path.stat().st_size > 8*1024*1024:
                        continue
                    name = path.with_suffix('.bin.name')
                    original = name.read_text(errors='replace')[:200] if name.exists() else ''
                    self.events.put((path.read_bytes(), original, parts[0].startswith('FileclosedOK')), timeout=5)
                    path.unlink(missing_ok=True)
                    name.unlink(missing_ok=True)
        except Exception as exc:
            self.errors.append(exc)

    def _write(self):
        try:
            while True:
                data = self.queue.get()
                if data is None:
                    break
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except Exception as exc:
            self.errors.append(exc)
        finally:
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def drain(self):
        while True:
            try:
                data, name, complete = self.events.get_nowait()
            except queue.Empty:
                break
            digest = hashlib.sha256(data).digest()
            if digest in self.seen:
                continue
            try:
                image = open_image(data, name)
            except (OSError, ValueError, reedsolo.ReedSolomonError) as exc:
                self.report(warning=f'HamDRM object received but not a decodable image: {exc}')
                continue
            self.seen.append(digest)
            image.info.update(ssdv_mode='HamDRM', ssdv_complete=complete)
            self.report(mode='HamDRM', reception='HamDRM image received')
            self.emit(image, None)

    def push(self, samples):
        if self.errors:
            raise RuntimeError(f'HamDRM receiver failed: {self.errors[0]}')
        if self.process.poll() is not None:
            raise RuntimeError(f'HamDRM receiver exited unexpectedly ({self.process.returncode})')
        self.drain()
        try:
            self.queue.put((np.asarray(samples, dtype='<f4')*32767).tobytes(), timeout=2)
        except queue.Full:
            raise RuntimeError('HamDRM receiver cannot keep up with audio')

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.queue.put(None, timeout=1)
            self.writer.join(timeout=5)
            self.process.wait(timeout=5)
        except (queue.Full, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=3)
        finally:
            self.reader.join(timeout=3)
            self.writer.join(timeout=3)
            try:
                self.drain()
            finally:
                self.process.stdout.close()
                self.folder.cleanup()
        if self.errors or self.process.returncode != 0:
            detail = str(self.errors[0]) if self.errors else f'exit code {self.process.returncode}'
            raise RuntimeError(f'HamDRM receiver failed while finishing: {detail}')
