# SPDX-License-Identifier: GPL-3.0-or-later
"""APRS interpretation of verified AX.25 UI frames; unknown payloads stay raw."""
import re

import aprslib
from plugins.bqe_tlm_decoder.protocols import ax25_header
from plugins.aprs_spots import position_report


def parse_packet(frame):
    header = ax25_header(frame)
    payload = header.pop('payload')
    # Preserve the AX.25 digipeater H bits in TNC2 form (CALL-SSID*).
    path = [call + ('*' if frame[(index + 2) * 7 + 6] & 0x80 else '')
            for index, call in enumerate(header['via'])]
    text = payload.decode('latin-1')
    tnc2 = header['source'] + '>' + header['destination']
    if path:
        tnc2 += ',' + ','.join(path)
    tnc2 += ':' + text
    result = dict(header, path=path, frame_hex=frame.hex(), payload_hex=payload.hex(),
                  payload_text=text, tnc2=tnc2, aprs=None, spot=None, telemetry=[], unparsed_hex=payload.hex())
    if header['control'] & 0xEF != 3 or header['pid'] != 0xF0:
        result['aprs_error'] = 'Not an AX.25 UI frame with APRS PID 0xF0'
        return result
    try:
        if not text or not 32 <= ord(text[0]) < 127:
            raise ValueError('Missing or non-text APRS data type')
        # aprslib does not implement the original T# analog telemetry report.
        # Retain raw counts; engineering units require station-specific EQNS.
        if text.startswith('T#'):
            match = re.fullmatch(r'T#(\d{3}),((?:\d{1,3},){5})([01]{8})(.*)', text, re.DOTALL)
            if not match:
                raise ValueError('Malformed T# telemetry report')
            values = [int(value) for value in match[2].rstrip(',').split(',')]
            if any(value > 255 for value in values):
                raise ValueError('T# analog values must be between 0 and 255')
            parsed = dict(format='telemetry', sequence=int(match[1]), analog=values,
                          bits=match[3], comment=match[4])
        else:
            parsed = aprslib.parse(tnc2)
        result['aprs'] = parsed
        result['unparsed_hex'] = ''
        fields = {key: value for key, value in parsed.items()
                  if key not in ('raw', 'from', 'to', 'path', 'via')}
        result['telemetry'] = [dict(name='APRS ' + parsed.get('format', 'packet'),
                                    fields=fields, raw_hex=payload.hex())]
    except (aprslib.ParseError, aprslib.UnknownFormat, ValueError, TypeError,
            IndexError, KeyError, OverflowError, RecursionError) as exc:
        result['aprs_error'] = str(exc) or type(exc).__name__
    result['spot'] = position_report(result)
    return result
