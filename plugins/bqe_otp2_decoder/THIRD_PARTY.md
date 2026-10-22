# Sources and reuse

This plugin reuses BQE's GPL-3.0-or-later telemetry modem, HDLC/CRC framing,
AX.25 address parsing, audio capture, HTTP server and managed receiver lifecycle.
See `../bqe_tlm_decoder/THIRD_PARTY.md` and the repository license.

Mission mode reference (consulted 2026-09-29):
https://db.satnogs.org/transmitter-reviewed-suggestions/10480
Satellite-team submission: 400.500 MHz, GFSK 4800 baud, AX.25.
No mission-specific payload specification or packet fixtures were copied.
Test addresses and payloads are synthetic and do not identify OTP-2.
