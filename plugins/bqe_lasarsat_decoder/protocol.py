# SPDX-License-Identifier: GPL-3.0-or-later
"""LASARsat ASCII housekeeping records, as documented by SatNOGS lasarsat.ksy.

Values retain their published wire representation; undocumented units/scales
are not guessed. A valid AX.25 CRC does not imply a valid housekeeping layout.
"""
import re

CALLSIGN = 'OK0LSR'
SATELLITE = dict(name='LASARsat', norad=62391, callsign=CALLSIGN)
SCHEMAS = {
    'OBC': ('On-board computer', 'obc',
            'reset_cnt uptime uptime_tot temp_mcu freemem'),
    'PSU': ('Power supply', 'psu',
            'reset_cnt uptime uptime_tot battery temp_sys temp_bat cur_in cur_out '
            'ch_state_num sys_state gnd_wdt'),
    'MGS': ('Magnetometer and gyroscope', 'mgs',
            'temp_int_mag temp_int_gyr int_mag_x int_mag_y int_mag_z int_gyr_x int_gyr_y int_gyr_z'),
    'SOL': ('Solar panels', 'sol',
            'temp_zp temp_xp temp_yp temp_zn temp_xn temp_yn '
            'diode_zp diode_xp diode_yp diode_zn diode_xn diode_yn'),
    'DOS': ('DoSpace payload', 'dos',
            'mode gyr_x gyr_y gyr_z mag_x mag_y mag_z plasma phd dozi gyr_t mag_t '
            'lppa bus_cur bus_vol uptime'),
    'NAV': ('Navigation', 'nav',
            'week time pos_x pos_y pos_z vel_x vel_y vel_z sats dop ant_cur volt max_snr'),
    'U': ('UHF radio', 'uhf',
          'uptime uptime_tot reset_cnt rf_reset_cnt trx_temp rf_temp pa_temp '
          'digipeater_cnt last_digipeater rx_cnt tx_cnt act_rssi_raw dcd_rssi_raw'),
    'V': ('VHF radio', 'vhf',
          'uptime uptime_tot reset_cnt rf_reset_cnt trx_temp rf_temp pa_temp '
          'digipeater_cnt last_digipeater rx_cnt tx_cnt act_rssi_raw dcd_rssi_raw'),
}


def parse_payload(payload):
    """Return parsed fields or an explicit raw/invalid record, without repair."""
    result = dict(record_type='raw', name='Unrecognized payload', fields={},
                  parse_status='unknown', raw_hex=payload.hex())
    try:
        text = payload.decode('ascii')
    except UnicodeDecodeError:
        return result
    result['text'] = text.rstrip('\x00\r\n')
    values = result['text'].split(',')
    kind = values[0]
    if kind not in SCHEMAS:
        return result
    name, prefix, names = SCHEMAS[kind]
    names = names.split()
    result.update(record_type=kind, name=name, parse_status='invalid')
    if len(values) != len(names) + 1:
        result['error'] = f'{kind}: expected {len(names)} fields, received {len(values)-1}'
        return result
    fields = {}
    for key, raw in zip(names, values[1:]):
        full_key = prefix + '_' + key
        if key == 'last_digipeater':
            if not all(32 <= ord(char) < 127 for char in raw):
                result['error'] = f'{full_key}: invalid text'
                return result
            value = raw
        elif kind == 'SOL' and raw == 'nan':
            value = None
        else:
            hexadecimal = key == 'ch_state_num'
            pattern = r'[0-9a-fA-F]{1,8}' if hexadecimal else r'[+-]?[0-9]{1,20}'
            if not re.fullmatch(pattern, raw):
                result['error'] = f'{full_key}: invalid number {raw!r}'
                return result
            value = int(raw, 16 if hexadecimal else 10)
        fields[full_key] = value
    if kind == 'PSU':
        for channel in range(7):
            fields[f'psu_ch{channel}_state'] = (fields['psu_ch_state_num'] >> channel) & 1
    result.update(parse_status='parsed', fields=fields)
    return result
