# SO-127 / HADES-SA telemetry

Receives SO-127 (HADES-SA / SpinnyONE, NORAD 68446) telemetry from flat FM
discriminator audio at 200, 800 and 1600 baud, trying both polarities. Supports
live conventional-radio input, the BQE SDR audio bridge, and WAV/FLAC/MP3/OGG
recordings. Use WAV/FLAC where possible, at 8 kHz or higher; 48 kHz is recommended.
USB-demodulated tones and filtered speaker audio are not supported.

## Scheduled passes

The existing `so-127-ssdv` entry in `bqe_config/satellites.yaml` uses:

```yaml
decode_so127_telemetry: true
decode_ssdv_images: true
ssdv_profile: hades-sa
squelch_level: 0
```

The generic `decode_telemetry` setting is replaced by the SO-127 setting.
Telemetry and SSDV start together on 436.875 MHz FM using the configured
radio/SDR input and stop at the end of the pass. The existing scheduling,
tracking, recording and SSDV settings still apply. The release-default entry
also selects the decoder, without enabling scheduling or tracking by default.

Live controls use http://127.0.0.1:8782 (an available fallback port is selected
when BQE launches it and that port is occupied). Validated packets are written
to `decoded/*.jsonl` and appear in BQE's **Telemetry** menu under SO-127.
Each record retains the original scrambled frame, decoded payload, CRC
validation, satellite identity, timestamps, baud and engineering fields.
Packets are not mislabeled as AX.25/KISS. Repeated transmissions are retained;
only simultaneous duplicates from the clock-recovery branches are suppressed.

## Supported packet contents

- Types 1–5: power, temperatures, status and power/temperature ranges.
- Types 8–9: antenna deployment and ten extended-power channels.
- Type 12: CRC-validated ephemeris payload retained as raw bytes.
- Type 14: system clock, variable ID and raw time-series samples; sample units
  depend on the variable ID and are not inferred.
- Type 15: BBS callsigns, messages and voice-frame counts.

Temperature sensor errors are null values; saturation limits are explicitly
marked. Uncalibrated radio signal codes remain raw. The plugin ignores SSDV,
CODEC2 audio and the type-13 PN9 link-test stream, which has no telemetry CRC.
SSDV continues in the existing image plugin. There is no voice playback.

## Manual use

Run `start_windows.bat` or `sh start_linux.sh` to install dependencies in a
plugin-local environment and start idle controls. Open the URL printed by the
launcher. BQE uses that environment when it exists; packaged installations can
instead use the application's environment with the root requirements installed.

```sh
python bqe_so127_decoder.py --file recording.wav --bauds 200 800 1600 --no-web
python bqe_so127_decoder.py --device "Radio audio" --source-name SO-127
```

## Validation and protocol details

The operator's ten published telemetry fixtures reconstruct their original
on-air CRCs exactly. HADES scrambles only bits 7 through 1 of each payload byte,
leaving bit 0 alone. CRC-16/CCITT-FALSE is checked on scrambled bytes from
type/address through payload, before descrambling; CRC bytes are big endian.
The size excludes the size byte itself. Core telemetry integers are packed
little endian, even though each byte is transmitted MSB first. The operator's
source and fixtures resolve ambiguities in the prose specification.

Tests cover those fixtures, corruption rejection, resynchronization, repeated
packets, noise, fractional sample clocks, both polarities at all rates, the
BQE SDR IQ-to-audio path, recording uploads, JSONL/Telemetry integration,
receiver configuration, CLI rate selection and managed shutdown.

From the repository root, using an environment with these requirements:

```sh
python -m unittest plugins.bqe_so127_decoder.tests.test_decoder -v
```

Selected offline checks are also available through BQE's plugin self-tests.
Live SO-127 RF reception has not yet been verified with this decoder.
See [THIRD_PARTY.md](THIRD_PARTY.md) for protocol sources and fixture attribution.
