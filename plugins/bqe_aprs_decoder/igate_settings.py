"""Local APRS-IS settings; the web API never returns the passcode."""
import json
import math
import os
from pathlib import Path
import re
import time
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[2]
CALL = re.compile(r'[A-Z0-9]{3,6}(?:-[A-Z0-9]{1,2})?\Z')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.folder = self.root/'plugins/bqe_aprs_decoder'
        self.path = self.folder/'aprs-is.private.json'
        self.status_path = self.folder/'aprs-is.status.json'

    def read(self):
        values = dict(enabled=False, host='rotate.aprs2.net', port=14580, passcode='')
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('Invalid APRS-IS settings file')
            values.update({k: saved[k] for k in values if k in saved})
        try:
            qth = yaml.safe_load((self.root/'bqe_config/my_qth.yaml').read_text(encoding='utf-8')) or {}
        except yaml.YAMLError:
            raise ValueError('Invalid QTH configuration') from None
        if not isinstance(qth, dict):
            raise ValueError('Invalid QTH configuration')
        values['callsign'] = str(qth.get('my_callsign', '')).strip().upper()
        for key, limit in (('latitude',90),('longitude',180)):
            raw = qth.get('my_' + key)
            try:
                coordinate = float(raw)
                values[key] = coordinate if not isinstance(raw,bool) and math.isfinite(coordinate) and abs(coordinate)<=limit else None
            except (TypeError,ValueError,OverflowError):
                values[key] = None
        return self.validate(values)

    @staticmethod
    def validate(values):
        if type(values.get('enabled')) is not bool:
            raise ValueError('Enabled must be true or false')
        call = values.get('callsign', '')
        if values['enabled'] and (not CALL.fullmatch(call) or len(call) > 9 or not any(c.isdigit() for c in call)):
            raise ValueError('Set a valid my_callsign in my_qth.yaml before enabling uploads')
        host = values.get('host')
        if not isinstance(host, str) or len(host)>253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
            raise ValueError('Enter an APRS-IS server hostname')
        if type(values.get('port')) is not int or not 1 <= values['port'] <= 65535:
            raise ValueError('Server port must be 1–65535')
        code = values.get('passcode')
        if not isinstance(code,str) or (code and (not re.fullmatch(r'[0-9]{1,5}',code) or int(code)>32767)):
            raise ValueError('Enter the numeric APRS-IS passcode, not an aprs.fi website password')
        if values['enabled'] and not code:
            raise ValueError('An APRS-IS passcode is required to enable uploads')
        return values

    def public(self):
        values = self.read()
        result = {k:values[k] for k in ('enabled','host','port','callsign')}
        result['has_passcode'] = bool(values['passcode'])
        result['upload'] = dict(state='inactive', message='Start live APRS reception to upload')
        try:
            status = json.loads(self.status_path.read_text(encoding='utf-8'))
            if isinstance(status,dict) and 0 <= time.time() - status.get('updated',0) < 10:
                result['upload'] = status
        except (OSError,ValueError,TypeError):
            pass
        return result

    def save(self, changes):
        if not isinstance(changes,dict) or set(changes) - {'enabled','host','port','passcode','clear_passcode'}:
            raise ValueError('Unsupported APRS-IS settings')
        values = self.read()
        for key in ('enabled','host','port'):
            if key in changes:
                values[key]=changes[key]
        if changes.get('clear_passcode'):
            values['passcode']=''
        elif changes.get('passcode'):
            values['passcode']=changes['passcode']
        self.validate(values)
        # The callsign always comes from QTH, never a second stored copy.
        atomic_json(self.path,{k:values[k] for k in ('enabled','host','port','passcode')})
        return self.public()


def build_html(theme='classic'):
    theme = 'modern' if theme == 'modern' else 'classic'
    form = (Path(__file__).parent/'igate_form.html').read_text(encoding='utf-8')
    form = form.replace('__ENDPOINT__', '/api/aprs-is/settings').replace('<details ', '<details open ', 1)
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BQE WISP APRS Upload</title><link rel="stylesheet" href="/aprs-spots.css"></head>
<body class="__THEME__-ui aprs-standalone"><main style="max-width:720px">
<header><h1>APRS Upload</h1><button type="button" onclick="window.close()">Close window</button></header>
__FORM__
</main><script src="/igate-controls.js"></script></body></html>'''.replace('__THEME__', theme).replace('__FORM__', form)


def serve_settings(handler, write=False):
    """Local-only main-console endpoint; passcodes never appear in responses."""
    expected = (f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}')
    host = handler.headers.get('Host', '')
    origin = handler.headers.get('Origin')
    if handler.client_address[0] not in ('127.0.0.1', '::1') or host not in expected or (origin and origin != 'http://' + host):
        handler.send_bytes(403, 'application/json', b'{"error":"Upload settings are available only on localhost"}')
        return
    try:
        store = SettingsStore()
        if write:
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected JSON settings')
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Invalid settings size')
            handler.connection.settimeout(10)
            result = store.save(json.loads(handler.rfile.read(length)))
        else:
            result = store.public()
        handler.send_bytes(200, 'application/json', json.dumps(result).encode('utf-8'))
    except (OSError,ValueError,TypeError):
        handler.send_bytes(400, 'application/json', b'{"error":"Check the QTH callsign, numeric APRS-IS passcode, hostname and port"}')
