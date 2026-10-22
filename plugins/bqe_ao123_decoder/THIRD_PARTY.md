# Protocol references and licensing

This plugin is GPL-3.0-or-later, consistent with the reused BQE telemetry DSP.

The protocol configuration and layouts follow Daniel Estevez's gr-satellites
(GPL-3.0-or-later):

- https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/ASRTU-1.yml
- https://github.com/daniestevez/gr-satellites/blob/main/python/hier/ccsds_viterbi.py
- https://github.com/daniestevez/gr-satellites/blob/main/python/components/deframers/ccsds_rs_deframer.py
- https://github.com/daniestevez/gr-satellites/blob/main/python/components/transports/tm_kiss_transport.py
- https://github.com/daniestevez/gr-satellites/blob/main/python/telemetry/by02.py
- https://github.com/daniestevez/gr-satellites/blob/main/python/telemetry/asrtu.py

The implementation reuses BQE's SymbolClock, USPFEC soft Viterbi implementation,
CCSDS randomizer, KISS writer, audio/device management, and HTTP/lifecycle service.
See ../bqe_tlm_decoder/THIRD_PARTY.md for their attribution and dependencies.
NumPy, SciPy, reedsolo, SoundCard, sounddevice and soundfile retain their licenses.
No upstream housekeeping conversion table or real satellite recording is bundled.
