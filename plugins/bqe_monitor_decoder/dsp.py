# SPDX-License-Identifier: GPL-3.0-or-later
"""Monitor mission profile over the shared, validated USP audio modem."""
from plugins.bqe_tlm_decoder.dsp import AudioDecoder as USPDecoder


SATELLITES = {
    'RS58S': {'name': 'MONITOR-3', 'norad': 57180, 'downlink_mhz': 435.290},
    'RS57S': {'name': 'MONITOR-4', 'norad': 57182, 'downlink_mhz': 436.080},
}


class AudioDecoder(USPDecoder):
    def __init__(self, sample_rate, emit, options):
        if options.get('protocol', 'usp') != 'usp':
            raise ValueError('Monitor telemetry uses USP framing')

        def identified(packet):
            # Identify from the decoded source, never the filename or tuning.
            callsign = packet['source'].split('-')[0]
            satellite = SATELLITES.get(callsign)
            packet['satellite'] = dict(satellite, callsign=callsign) if satellite else None
            emit(packet)

        super().__init__(sample_rate, identified,
                         options.get('bauds', [1200, 2400, 4800, 9600]), 'usp')
