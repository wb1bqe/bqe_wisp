"""Normalized APRS positions and read-only access to received spot history."""
from pathlib import Path
import json
import re

from plugins.telemetry_view import TelemetryArchive, MAX_FILES, MAX_BYTES

MAX_SPOTS = 200


def packet_report(packet):
    """Keep non-position packets visible alongside mapped reports."""
    aprs = packet.get('aprs') or {}
    if not isinstance(aprs, dict):
        aprs = {}
    position = position_report(packet)
    report = position or dict(key='station:' + str(packet.get('source') or ''),
        name=packet.get('source') or 'Unknown', callsign=packet.get('source') or '',
        active=True, latitude=None, longitude=None, path=packet.get('path') or packet.get('via') or [])
    summary = aprs.get('message_text') or aprs.get('status') or aprs.get('comment') or packet.get('payload_text') or ''
    if aprs.get('format') == 'telemetry':
        summary = f"Sequence {aprs.get('sequence', '?')} · analog {aprs.get('analog', [])} · bits {aprs.get('bits', '')}"
    return dict(report, destination=packet.get('destination') or aprs.get('to') or '',
                format=aprs.get('format') or 'Unparsed', comment=str(summary),
                packet=packet)


def position_report(packet):
    """Use decoded coordinates, never a callsign lookup or inferred location."""
    aprs = packet.get('aprs')
    for _ in range(8):
        if not isinstance(aprs, dict):
            return None
        if aprs.get('format') != 'thirdparty':
            break
        aprs = aprs.get('subpacket')
    else:
        return None
    latitude, longitude = aprs.get('latitude'), aprs.get('longitude')
    if any(type(value) not in (int, float) for value in (latitude, longitude)):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    callsign = aprs.get('from') or packet.get('source') or ''
    if not isinstance(callsign, str) or not callsign.strip():
        return None
    callsign = callsign.strip()
    object_name = aprs.get('object_name') or aprs.get('item_name')
    name = object_name.strip() if isinstance(object_name, str) and object_name.strip() else callsign
    kind = 'object' if object_name else 'station'
    path = aprs.get('path') or packet.get('path') or packet.get('via') or []
    return dict(key=f'{kind}:{callsign}:{name}', name=name, callsign=callsign,
                kind=kind, latitude=latitude, longitude=longitude,
                active=aprs.get('alive') is not False, format=aprs.get('format', ''),
                comment=aprs.get('comment') or aprs.get('status') or '',
                path=path if isinstance(path, list) else [])


class APRSSpotsArchive(TelemetryArchive):
    def snapshot(self, root):
        folder = Path(root).resolve() / 'plugins' / 'bqe_aprs_decoder' / 'decoded'
        spots, packets, warnings, files = [], [], [], []
        try:
            for path in folder.glob('*.jsonl'):
                if path.is_symlink() or path.resolve().parent != folder.resolve():
                    continue
                try:
                    files.append((path.stat().st_mtime_ns, path))
                except OSError:
                    warnings.append('An APRS log changed while loading; refresh to retry.')
            for _, path in sorted(files, reverse=True)[:MAX_FILES]:
                try:
                    for record_id, packet in self._read(path):
                        metadata = dict(id=record_id, decoded_at=packet.get('decoded_at'),
                                        input_source=packet.get('input_source', ''),
                                        audio_seconds=packet.get('audio_seconds'),
                                        tnc2=packet.get('tnc2', ''), log_file=path.name)
                        metadata['satellite_pass'] = packet.get('reception_context') == 'satellite_pass'
                        packets.append(dict(packet_report(packet), **metadata))
                        spot = position_report(packet)
                        if spot is not None:
                            spots.append(dict(spot, id=record_id,
                                              decoded_at=packet.get('decoded_at'),
                                              input_source=packet.get('input_source', ''),
                                              audio_seconds=packet.get('audio_seconds'),
                                              tnc2=packet.get('tnc2', ''),
                                              upload_id=packet.get('upload_id'),
                                              satellite_pass=packet.get('reception_context') == 'satellite_pass',
                                              log_file=path.name))
                except OSError:
                    warnings.append('An APRS log could not be read; refresh to retry.')
        except OSError:
            warnings.append('The APRS log directory could not be read.')
        spots.sort(key=lambda spot: (str(spot['decoded_at'] or ''), spot['id']), reverse=True)
        packets.sort(key=lambda packet: (str(packet['decoded_at'] or ''), packet['id']), reverse=True)
        packets, spots = packets[:MAX_SPOTS], spots[:MAX_SPOTS]
        for report in packets + spots:
            upload_id = report.get('packet', report).get('upload_id')
            uploaded_at = None
            if isinstance(upload_id,str) and re.fullmatch(r'[0-9a-f]{32}',upload_id):
                receipt = folder.parent/'upload_receipts'/f'{upload_id}.json'
                try:
                    value = json.loads(receipt.read_text(encoding='utf-8')).get('uploaded_at')
                    if isinstance(value,str) and value:
                        uploaded_at = value
                except (OSError,ValueError,AttributeError):
                    pass
            report.update(uploaded=bool(uploaded_at), uploaded_at=uploaded_at)
        return dict(ok=True, spots=spots, packets=packets, warnings=sorted(set(warnings)),
                    scope=f'Latest {MAX_SPOTS} decoded packets from the newest {MAX_FILES} APRS logs '
                          f'(last {MAX_BYTES // 1024} KiB of each). '
                          'Times are decoding times; recordings also show their audio offset.')


ARCHIVE = APRSSpotsArchive()


def build_html(theme='classic'):
    theme = 'modern' if theme == 'modern' else 'classic'
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BQE WISP APRS Spots</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="/aprs-spots.css"></head>
<body class="__THEME__-ui aprs-standalone">
<main id="aprsSpotsModal" data-standalone="true">
<header><div><h1>APRS Spots</h1><p>Decoded packets from live reception and recordings</p></div>
<button type="button" id="aprsSpotsClose">Close window</button></header>
<aside class="aprs-upload-legend" aria-label="Upload color legend">
<span class="aprs-preset-received">Preset / other reception: yellow when received</span> ·
<span class="aprs-preset-uploaded">bold blue when uploaded</span><br>
<span class="aprs-satellite-received">Satellite pass: green when received</span> ·
<span class="aprs-satellite-uploaded">bold green when uploaded</span>
<small>Bold means sent on a verified APRS-IS connection; individual packets are not acknowledged by the server. Regular text means not uploaded / no upload recorded.</small>
</aside>
<label for="aprsSpotsFilter">Search callsign, packet type, source, or packet text</label>
<input id="aprsSpotsFilter" type="search" placeholder="Search decoded packets…">
<div class="aprs-actions"><button type="button" id="aprsSpotsRefresh">Refresh</button>
<button type="button" id="aprsSpotsFit">Fit positions on map</button></div>
<p id="aprsSpotsMessage" role="status" aria-live="polite"></p>
<aside class="aprs-map-legend" aria-label="Map markers">
<strong>Map markers:</strong>
<span><i class="aprs-map-satellite" aria-hidden="true"></i>Satellite icon: satellite pass</span>
<span><i class="aprs-map-dot terrestrial" aria-hidden="true"></i>Blue: preset / other reception</span>
</aside>
<div id="aprsSpotsMap" aria-label="Decoded APRS positions"></div>
<div class="aprs-spots-table-wrap"><table id="aprsSpotsTable"><thead><tr>
<th>Decoded at</th><th>Station / object</th><th>Type</th><th>Destination</th><th>Position</th><th>Reception source</th><th>Message / summary</th>
</tr></thead><tbody id="aprsSpotsRows"></tbody></table></div>
<section aria-labelledby="aprsDetailHeading"><h2 id="aprsDetailHeading">Packet details</h2>
<p>Select a station in the table to see its packet, path, and decoded fields.</p>
<pre id="aprsSpotsDetail" tabindex="0"></pre></section>
<p id="aprsSpotsScope"></p><button type="button" id="aprsSpotsDone">Close window</button>
</main><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="/aprs-spots.js"></script></body></html>'''.replace('__THEME__', theme)
