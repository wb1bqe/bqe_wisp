"""Read-only, bounded access to saved telemetry. No radio/DSP dependencies."""
from collections import OrderedDict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import threading

import yaml


DECODERS = {
    'decode_ao123_telemetry': ('bqe_ao123_decoder', 'AO-123 / ASRTU-1'),
    'decode_so127_telemetry': ('bqe_so127_decoder', 'SO-127 / HADES-SA'),
    'decode_aprs': ('bqe_aprs_decoder', 'APRS'),
    'decode_telemetry': ('bqe_tlm_decoder', 'AX.25 / USP'),
    'decode_monitor_telemetry': ('bqe_monitor_decoder', 'MONITOR'),
    'decode_marmotsat_telemetry': ('bqe_marmotsat_decoder', 'MARMOTSat'),
    'decode_lasarsat_telemetry': ('bqe_lasarsat_decoder', 'LASARsat'),
    'decode_otp2_telemetry': ('bqe_otp2_decoder', 'OTP-2 (raw packets)'),
    'decode_rs61_telemetry': ('bqe_rs61_decoder', 'RS-61'),
}
MAX_FILES = 50
MAX_BYTES = 256 * 1024
MAX_PACKETS = 100


def invalid_number(value):
    raise ValueError('Non-finite JSON number: ' + value)


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def catalog(path):
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict) or not isinstance(data.get('satellites', []), list):
        raise ValueError('Invalid satellites configuration')
    entries = []
    for config in data.get('satellites', []):
        if not isinstance(config, dict):
            continue
        decoders = [dict(plugin=plugin, label=label) for key, (plugin, label) in DECODERS.items()
                    if config.get(key) is True]
        nickname = str(config.get('nickname') or '').strip()
        if not nickname or not decoders:
            continue
        name = str(config.get('satellite_name') or nickname)
        entries.append(dict(id=nickname, name=name, nickname=nickname,
                            norad=str(config.get('catalog_number') or ''),
                            frequency=str(config.get('downlink_frequency_mhz') or ''),
                            decoders=decoders))
    return sorted(entries, key=lambda entry: entry['nickname'].casefold())


def belongs(packet, satellite):
    """Explicit spacecraft identity wins over the tuned input's label."""
    aliases = {normalized(satellite['name']), normalized(satellite['nickname'])} - {''}
    identity = packet.get('satellite')
    if isinstance(identity, dict):
        if identity.get('norad') and satellite['norad']:
            return str(identity['norad']) == satellite['norad']
        if identity.get('name'):
            return normalized(identity['name']) in aliases
    callsigns = set(re.findall(r'RS\d+S', (satellite['name'] + ' ' + satellite['nickname']).upper()))
    if callsigns and packet.get('source'):
        return str(packet['source']).upper().split('-')[0] in callsigns
    if normalized(packet.get('source')) in aliases:
        return True
    # Older generic logs carry the scheduled satellite's name, even without a
    # mission parser. Keep raw/unknown payloads visibly distinct in the viewer.
    return normalized(packet.get('input_source')) in aliases


class TelemetryArchive:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache = OrderedDict()

    def _read(self, path):
        stat = path.stat()
        key = str(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] == stamp:
                self.cache.move_to_end(key)
                return cached[1]
        records = []
        with path.open('rb') as stream:
            start = max(0, stat.st_size - MAX_BYTES)
            stream.seek(start)
            if start:
                stream.readline(MAX_BYTES)  # Discard a potentially partial first record.
            raw = stream.read(MAX_BYTES)
        # An active writer may have flushed only part of its last record.
        lines = raw.split(b'\n')[:-1]
        for line in lines:
            if len(line) > 65536:
                continue
            try:
                packet = json.loads(line, parse_constant=invalid_number)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if isinstance(packet, dict) and isinstance(packet.get('frame_hex'), str):
                records.append((sha256(key.encode() + b'\0' + line).hexdigest()[:24], packet))
        with self.lock:
            self.cache[key] = stamp, records
            self.cache.move_to_end(key)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
        return records

    def snapshot(self, root, satellite):
        root = Path(root).resolve()
        plugins = {d['plugin'] for d in satellite['decoders']}
        plugins.add('bqe_tlm_decoder')  # Include historical generic-decoder logs.
        packets, locations, warnings = [], [], []
        for plugin in sorted(plugins):
            folder = root / 'plugins' / plugin / 'decoded'
            locations.append(str(folder))
            try:
                files = []
                for path in folder.glob('*.jsonl'):
                    # Requests never select filesystem paths; skip linked files too.
                    if path.resolve().parent != folder.resolve() or path.is_symlink():
                        continue
                    files.append((path.stat().st_mtime_ns, path))
                for _, path in sorted(files, reverse=True)[:MAX_FILES]:
                    try:
                        for record_id, packet in self._read(path):
                            if belongs(packet, satellite):
                                packets.append(dict(id=record_id, packet=packet,
                                                    decoder=plugin, log_file=str(path)))
                    except OSError:
                        warnings.append('A telemetry log changed or could not be read; refresh to retry.')
            except OSError:
                warnings.append('A telemetry output directory could not be read.')
        packets.sort(key=lambda record: (str(record['packet'].get('decoded_at') or ''), record['id']), reverse=True)
        return dict(ok=True, satellite=satellite, packets=packets[:MAX_PACKETS],
                    locations=locations, warnings=sorted(set(warnings)),
                    scope=f'Latest {MAX_PACKETS} matching packets from the newest {MAX_FILES} logs per decoder '
                          f'(last {MAX_BYTES // 1024} KiB of each log).')


def has_recent_telemetry(records, now=None):
    """True for a decoded timestamp aged >= 0 and strictly less than 24 hours."""
    now = now or datetime.now(timezone.utc)
    for record in records:
        value = record['packet'].get('decoded_at')
        if not isinstance(value, str):
            continue
        try:
            decoded = datetime.fromisoformat(value.replace('Z', '+00:00'))
            # Historical decoder timestamps without an offset are UTC.
            if decoded.tzinfo is None:
                decoded = decoded.replace(tzinfo=timezone.utc)
            age = (now - decoded).total_seconds()
        except (ValueError, OverflowError):
            continue
        if 0 <= age < 24 * 60 * 60:
            return True
    return False


ARCHIVE = TelemetryArchive()
