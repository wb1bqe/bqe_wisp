"""Receive-only APRS-IS IGate: fresh CRC-checked RF packets, never file history."""
from collections import OrderedDict
import queue
import os
import re
import select
import socket
import threading
import time
from datetime import datetime, timezone

from .igate_settings import CALL, atomic_json

BEACON_INTERVAL = 30 * 60


def position_beacon(config):
    latitude, longitude = config.get('latitude'), config.get('longitude')
    if latitude is None or longitude is None:
        return None
    def coordinate(value, width, positive, negative):
        # Round total hundredths of minutes first, carrying 60 minutes correctly.
        hundredths = round(abs(value) * 6000)
        degrees, minutes = divmod(hundredths,6000)
        return f'{degrees:0{width}d}{minutes//100:02d}.{minutes%100:02d}{negative if value<0 else positive}'
    lat = coordinate(latitude,2,'N','S')
    lon = coordinate(longitude,3,'E','W')
    # Client-originated packets use TCPIP*; the server supplies the q construct.
    # I& is the IGate symbol. This is an Internet beacon, never an RF transmission.
    return f"{config['callsign']}>APRS,TCPIP*:!{lat}I{lon}&BQE WISP receive-only IGate\r\n".encode('ascii')


class GateRejected(ValueError):
    pass


def gate_packet(packet, callsign):
    if packet.get('validation') != 'CRC-16/X-25' or packet.get('control') != 3 or packet.get('pid') != 240:
        raise GateRejected('Only CRC-verified AX.25 UI APRS packets are eligible')
    text=packet.get('tnc2')
    if not isinstance(text,str):
        raise GateRejected('Invalid packet text')
    # Some RF senders include terminal CR/LF bytes in the information field.
    # Normalize only those suffix bytes; preserve spaces and binary Mic-E data
    # (including 0x1c..0x1f and DEL). Header validation remains ASCII-only below.
    text=text.rstrip('\r\n')
    if any(c in text for c in ('\r','\n','\x00')):
        raise GateRejected('Embedded line break or NUL in packet text')
    for depth in range(5):
        header, sep, payload = text.partition(':')
        source, arrow, path = header.partition('>')
        parts=path.split(',')
        if not sep or not arrow or not payload or not CALL.fullmatch(source) or not parts[0]:
            raise GateRejected('Invalid APRS header')
        if len(parts)>10 or not all(re.fullmatch(r'[A-Za-z0-9-]{1,9}\*?',p) for p in parts):
            raise GateRejected('Invalid APRS path')
        prohibited={'TCPIP','TCPXX','NOGATE','RFONLY'}
        if any(p.rstrip('*').upper() in prohibited or re.fullmatch(r'qa[a-z]',p.lower()) for p in parts[1:]):
            raise GateRejected('Packet requests no gating or has already traversed the Internet')
        if payload.startswith('?'):
            raise GateRejected('Generic APRS query')
        if payload.startswith('}'):
            text=payload[1:]
            continue
        line=f'{header},qAO,{callsign}:{payload}\r\n'.encode('latin-1')
        if len(line)>512:
            raise GateRejected('APRS-IS packet exceeds 512 bytes')
        # Digipeated copies may have different paths; do not upload them again.
        return line, (source, parts[0], payload)
    raise GateRejected('Excessive third-party nesting')


class IGate:
    def __init__(self, store, connector=socket.create_connection):
        self.store, self.connector = store, connector
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.thread=None
        self.sock=None
        self.config=None
        self.queue=queue.Queue(maxsize=100)
        self.recent=OrderedDict()
        self.next_beacon=0
        self.beacon_position=None
        self.status=dict(state='waiting',message='Waiting for a live radio source',sent=0,filtered=0,dropped=0,
                         beacons_sent=0,last_beacon_at=None,beacon_message='Waiting for verified live connection')

    def send_beacon_if_due(self, connection, config, now):
        line=position_beacon(config)
        if line is None:
            with self.lock:
                self.status['beacon_message']='Beacon skipped: set valid my_latitude and my_longitude in my_qth.yaml'
            return
        if now < self.next_beacon and line == self.beacon_position:
            return
        connection.sendall(line)
        self.next_beacon=now+BEACON_INTERVAL
        self.beacon_position=line
        with self.lock:
            self.status.update(beacons_sent=self.status['beacons_sent']+1,
                               last_beacon_at=datetime.now(timezone.utc).isoformat(),
                               beacon_message='Station position sent; repeats every 30 minutes during live uploading')

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def update(self, **values):
        with self.lock:
            self.status.update(values)
            data=dict(self.status,updated=time.time())
        try:
            atomic_json(self.store.status_path,data)
        except OSError:
            pass  # Publishing UI status must not interrupt decoding.

    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,daemon=True,name='aprs-is')
            self.thread.start()

    def submit(self, packet):
        with self.lock:
            if self.status['state']!='connected' or not self.config:
                self.status['dropped']+=1
                return
            try:
                line,key=gate_packet(packet,self.config['callsign'])
            except (ValueError,UnicodeError):
                self.status['filtered']+=1
                return
            now=time.monotonic()
            while self.recent and (now-next(iter(self.recent.values()))>30 or len(self.recent)>2000):
                self.recent.popitem(last=False)
            if key in self.recent:
                self.status['filtered']+=1
                return
            try:
                self.queue.put_nowait((now,line,packet.get('upload_id')))
                self.recent[key]=now
            except queue.Full:
                self.status['dropped']+=1

    def close(self):
        self.stop_event.set()
        with self.lock:
            connection=self.sock
        if connection:
            try: connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.thread:
            self.thread.join(timeout=7)
        self.update(state='stopped',message='Live reception stopped; uploads stopped')

    def read_line(self, connection, buffer, deadline):
        while b'\n' not in buffer:
            if self.stop_event.is_set() or time.monotonic()>deadline:
                raise OSError('Connection timed out')
            try:
                part=connection.recv(2048)
            except socket.timeout:
                continue
            if not part:
                raise OSError('Connection closed')
            buffer+=part
            if len(buffer)>8192:
                raise OSError('Server response too long')
        line,buffer=buffer.split(b'\n',1)
        return line.decode('ascii',errors='replace').strip(),buffer

    def run(self):
        # One network sender per installation, even with multiple decoder windows.
        self.store.folder.mkdir(parents=True, exist_ok=True)
        with (self.store.folder/'aprs-is.lock').open('a+b') as guard:
            guard.seek(0, 2)
            if guard.tell() == 0:
                guard.write(b'0'); guard.flush()
            guard.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.update(state='blocked',message='Another live APRS decoder owns the uploader')
                return
            try:
                self._run()
            finally:
                guard.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(guard, fcntl.LOCK_UN)

    def _run(self):
        retry=1
        while not self.stop_event.is_set():
            connection=None
            try:
                config=self.store.read()
                if not config['enabled']:
                    self.update(state='disabled',message='APRS-IS upload is disabled')
                    self.stop_event.wait(1)
                    continue
                self.update(state='connecting',message='Connecting to APRS-IS')
                connection=self.connector((config['host'],config['port']),timeout=5)
                connection.settimeout(1)
                connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                with self.lock: self.sock=connection
                line,buffer=self.read_line(connection,b'',time.monotonic()+8)
                if not line.startswith('#'):
                    raise OSError('Unexpected APRS-IS greeting')
                login=f"user {config['callsign']} pass {config['passcode']} vers BQEWISP 1.0\r\n"
                connection.sendall(login.encode('ascii'))
                deadline=time.monotonic()+10
                while True:
                    line,buffer=self.read_line(connection,buffer,deadline)
                    match=re.match(r'#\s*logresp\s+(\S+)\s+(verified|unverified)\b',line,re.I)
                    if match:
                        if match[1].upper()!=config['callsign'] or match[2].lower()!='verified':
                            raise PermissionError('APRS-IS login was not verified; check the passcode')
                        break
                with self.lock: self.config=config
                self.update(state='connected',message='Verified APRS-IS connection; forwarding eligible live packets')
                retry=1
                heard=time.monotonic()
                checked=0
                while not self.stop_event.is_set():
                    if time.monotonic()-checked >= 1:
                        if self.store.read()!=config:
                            break
                        self.send_beacon_if_due(connection,config,time.monotonic())
                        self.update()
                        checked=time.monotonic()
                    # Always drain server traffic; no APRS-IS packet is sent to RF.
                    if select.select([connection],[],[],.1)[0]:
                        data=connection.recv(8192)
                        if not data: raise OSError('Server disconnected')
                        heard=time.monotonic()
                    if time.monotonic()-heard>120:
                        raise OSError('Server stopped responding')
                    for _ in range(20):
                        try: received,line,upload_id=self.queue.get_nowait()
                        except queue.Empty: break
                        if time.monotonic()-received>15:
                            with self.lock:self.status['dropped']+=1
                            continue
                        connection.sendall(line)
                        with self.lock:self.status['sent']+=1
                        # Record only completed writes on a verified connection.
                        # APRS-IS has no per-packet delivery acknowledgment.
                        if isinstance(upload_id,str) and re.fullmatch(r'[0-9a-f]{32}',upload_id):
                            try:
                                atomic_json(self.store.folder/'upload_receipts'/f'{upload_id}.json',
                                            dict(uploaded_at=datetime.now(timezone.utc).isoformat()))
                            except OSError:
                                pass  # A missing receipt must never trigger a retransmission.
            except PermissionError:
                self.update(state='error',message='APRS-IS login was not verified; check the passcode')
                self.stop_event.wait(15)
            except (OSError,ValueError,TypeError,KeyError):
                if not self.stop_event.is_set():
                    self.update(state='error',message='Cannot connect to APRS-IS; check server, QTH callsign, passcode and network')
                    self.stop_event.wait(retry)
                    retry=min(30,retry*2)
            finally:
                with self.lock:
                    self.sock=None
                    self.config=None
                    if self.status['state']=='connected':
                        self.status.update(state='reconnecting',message='Reconnecting with current settings')
                if connection: connection.close()
                while True:
                    try:self.queue.get_nowait()
                    except queue.Empty:break
