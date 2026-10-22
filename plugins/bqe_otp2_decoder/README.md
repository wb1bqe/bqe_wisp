# OTP-2 experimental UHF decoder

Receives 4800-baud GFSK/AX.25 from flat FM discriminator audio, a BQE SDR
audio stream, or WAV/FLAC/OGG/MP3 recordings. Live controls default to
http://127.0.0.1:8779. Run `start_windows.bat` or `start_linux.sh` to open
idle controls; both install their local environment on first use.

This is an experimental **packet decoder**, not a verified OTP-2 engineering
telemetry parser. The satellite team's published UHF parameters are
[400.500 MHz, GFSK, 4800 baud, AX.25](https://db.satnogs.org/transmitter-reviewed-suggestions/10480).
The exact line coding, callsign and payload field layout have not been established.
The receiver tries direct NRZ (both polarities), NRZI, and G3RUH-descrambled
NRZI, with four timing phases. A valid AX.25 header and CRC-16/X-25 are mandatory.
The reported protocol names the successful receive chain; it does not establish
the transmitter's internal processing order or satellite identity.

Accepted frames are saved as JSONL and KISS in `decoded/`. The console shows
addresses, successful encoding, timestamps, raw payload hex and printable text.
It deliberately does not apply SPUTNIX or other satellites' field layouts.
Packet identity remains `unverified`, even when the configured source is OTP-2.
The optional 9600-baud checkbox is a diagnostic fallback for historical reports;
4800 is the published mode and the default. The 50-kbaud S-band link is unsupported.

## BQE configuration

The existing `OTP-2` satellite entry enables `decode_otp2_telemetry: true`.
It starts after tuning, uses the configured sound card or SDR audio source,
and stops at pass end. Presets support the same flag. Selected audio input
is reflected in console controls. Audio recording remains enabled.

The existing 400.502-MHz tuning value is preserved (published nominal frequency
is 400.500 MHz). Squelch is open and the entry overrides SDR audio gain to 0.15
to avoid the severe clipping in the available recording. Adjust tuning/gain
against a real pass if needed. A conventional receiver needs a sufficiently
flat discriminator/data output; a filtered speaker output may lose data.

## Offline use and checks

```
start_windows.bat --file recording.wav --no-web
```

From the repository root, run the plugin tests with
`plugins/bqe_otp2_decoder/.venv/Scripts/python.exe -m unittest plugins.bqe_otp2_decoder.tests.test_decoder plugins.bqe_otp2_decoder.tests.test_integration -v`.
The tests cover synthetic packets, polarity, chunk boundaries, bad CRC, noise,
fallback baud, browser status, selected device metadata, and managed shutdown.
Preset routing tests are in `tests/test_preset_otp2.py` using the main app Python.
Synthetic success does not constitute validation against an OTP-2 transmission.

## Initial recording check

The local `OTP-2-20260929T140536Z-db8e626a.wav` recording was replayed in full
(656 seconds, mono 48 kHz) at 4800 baud. No CRC-valid packets were recovered.
58.8% of samples had absolute amplitude at least 0.99, indicating severe clipping.
This does not prove that clipping was the only obstacle; framing, receiver
filtering, tuning and signal quality remain possible causes. The replay status
is saved in `logs/otp2-replay-status.json` at the repository root.
