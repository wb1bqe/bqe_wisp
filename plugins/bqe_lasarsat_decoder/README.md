# BQE LASARsat telemetry decoder

Receives LASARsat (NORAD 62391, OK0LSR) digital telemetry at **436.925 MHz,
9600 baud GFSK, AX.25/G3RUH**, from live FM discriminator audio or WAV,
FLAC, OGG and MP3 recordings. This plugin stays inside the BQE repository
and reuses its streaming modem, audio-device selection and managed lifecycle.

## Use

Run `start_windows.bat` or `sh start_linux.sh`. The first launch installs
dependencies into a local `.venv` (Python 3.10+). Open
http://127.0.0.1:8778, select the radio input, then start live audio or
decode a recording. Select a packet row for named fields and raw data.
The preset/pass input is reflected in the dropdown and retained on restart.

CLI examples from this directory:

```powershell
.venv\Scripts\python.exe bqe_lasarsat_decoder.py --device "USB Audio Codec" --open-browser
.venv\Scripts\python.exe bqe_lasarsat_decoder.py --file "C:\recordings\lasarsat.wav" --no-web
```

The default is 9600 baud; `--bauds` allows another supported rate when
deliberately testing recordings. `--channel 1` selects the second channel.
`--idle` opens controls without capture. `--output` changes the local log folder.
JSONL contains all displayed frames, parsed fields and raw payloads. KISS
contains the CRC-checked AX.25 frames without FCS. No data is uploaded.

## Pass configuration

`bqe_config/satellites.yaml` includes `lasarsat-tlm` with
`decode_lasarsat_telemetry: true`, FM at 436.925 MHz, 25 kHz bandwidth,
open squelch, antenna tracking, tuning, recording and automatic scheduling.
It reads `radio_soundcard` from the rig configuration (USB Audio Codec here).
SDR rigs use BQE's audio bridge automatically. The plugin opens its console
at pass start and shuts down at pass end or when its owning process exits.
Port conflicts fall back to an available localhost port.

The existing LASARSAT-beacon entry remains available for manual selection;
its auto_schedule flag is disabled to avoid two automatic entries for NORAD
62391. Restart BQE and regenerate the schedule to use the new automatic entry.
The plugin key also works in presets.

## Decoded records and limits

Supported published CSV layouts: OBC (computer), PSU (power), MGS (magnetometer
and gyroscope), SOL (solar panels), DOS (DoSpace payload), NAV (navigation),
U (UHF radio), and V (VHF radio). PSU channel state is expanded into seven bits.
SOL `nan` is displayed as unavailable, never as a real sensor measurement.
Fields retain the published integer representation: undocumented scales and
units are not guessed. See THIRD_PARTY.md for the field-layout reference.

Only CRC-checked AX.25 frames whose source callsign is OK0LSR are accepted.
Unknown payloads and malformed known layouts are saved as raw records with
an explicit parse status; CRC success alone does not validate payload fields.
Other stations and digipeated amateur sources are not labeled as LASARsat
housekeeping. This is a digital packet decoder; use BQE's separate CW decoder
for the Morse beacon.

A non-SDR radio is supported, but its USB input must provide flat 9600-baud
DATA/discriminator audio. Ordinary filtered speaker audio may lose the
required modulation even when the audio level is healthy. Disable squelch
and audio noise suppression; WAV/FLAC are preferable to MP3.

## Verification

From the BQE repository root:

```powershell
plugins\bqe_lasarsat_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_lasarsat_decoder/tests
python -m unittest tests.test_preset_lasarsat
```

Tests cover all field layouts, malformed/unknown records, CRC rejection,
foreign callsign rejection, polarity, 44.1/48 kHz audio, arbitrary chunk
boundaries, silence/noise, file decoding and logs, managed CLI startup,
HTTP status and owner shutdown. Audio test vectors are synthetic; they
do not establish successful on-air reception. Offline checks are also
registered in BQE's plugin self-test menu.

Workstation validation on 2026-09-29: all seven plugin tests and six
LASARsat preset lifecycle tests passed. Live USB Audio Codec capture ran
without errors at approximately -38 dBFS. Full digital decoding of the
three local LASARSAT-beacon WAV recordings from 2026-09-27 (114204Z,
114733Z and 131915Z; about 21 minutes total) produced no CRC-valid frames.
Therefore on-air packet reception remains unverified; audible CW or a
healthy input level does not establish that 9600-baud data is recoverable.
