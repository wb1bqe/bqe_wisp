# BQE Monitor telemetry decoder

Receive MONITOR-3 (RS58S, NORAD 57180, 435.290 MHz) and MONITOR-4
(RS57S, NORAD 57182, 436.080 MHz) USP FSK telemetry at 1200, 2400,
4800 and 9600 baud. Uses BQE's shared USP modem: polarity detection,
soft Viterbi decoding, CCSDS descrambling, Reed-Solomon verification and
AX.25 extraction. This is a receive-only plugin.

## Start

Run `start_windows.bat`, or `sh start_linux.sh` on Linux, then open
http://127.0.0.1:8777. The launchers install dependencies in a local `.venv`
on first use (Python 3.10+ and internet required). Keep this directory in the
BQE repository because it uses the shared telemetry and audio components.

Select an input and **Start live audio**, or choose a WAV, FLAC, OGG or MP3
recording and **Decode file**. WAV/FLAC are preferable to lossy MP3.
Select only the baud rates you need if the computer cannot keep up.
Channel numbers start at zero. Stop a running job before changing sources.

From this directory on Windows:

```powershell
.venv\Scripts\python.exe bqe_monitor_decoder.py --list-devices
.venv\Scripts\python.exe bqe_monitor_decoder.py --device "USB Audio CODEC"
.venv\Scripts\python.exe bqe_monitor_decoder.py --file "C:\recordings\monitor.wav" --no-web
```

Use `--bauds 9600` to select one rate, `--channel 1` for the second channel,
`--output PATH` for another output directory and `--open-browser` to open
the controls. Browser uploads are limited to 512 MiB; CLI file input streams
larger recordings. Raw I/Q, SSTV and CW are not inputs to this telemetry modem.

## BQE integration

The existing MONITOR3 and MONITOR4 entries in `bqe_config/satellites.yaml`
now use `decode_monitor_telemetry: true` and `decode_telemetry: false` to
run one telemetry receiver per pass. Restart BQE to load the integration.
The plugin also works in idle presets with the same boolean setting.
It uses `radio_soundcard` from the selected rig configuration or the BQE
SDR audio bridge, and closes capture and flushes output when the pass ends,
the preset changes, or the owning process exits. No manual helper command
is needed. SSTV reception, when desired, uses the separate SSTV plugin.

Use FM with roughly 25 kHz RF bandwidth and flat DATA/9600-baud or
discriminator audio, preferably sampled at 48 kHz. Disable squelch and
speech processing. A normal narrow speaker output can remove the signal
needed for high-rate decoding even with wide RF bandwidth. Rates with fewer
than three samples per symbol are skipped and reported.

## Results and limits

All verified packets are written to unique JSONL and KISS files in `decoded/`.
The controls show the newest 200 packets; click one for fields and raw bytes.
Satellite identity comes from the AX.25 source callsign, including SSIDs.
Other callsigns remain unidentified and are retained. A filename or selected
frequency never establishes identity.

The shared telemetry parser provides common/X/Y/Z power records (000E–0011)
and the 25-byte UHF beacon (4246). Unknown records, unsupported firmware
layouts and trailing bytes remain raw. Monitor science payload layouts are
not claimed decoded. `decoded_at` is processing time; `audio_seconds` is
relative to the recording/capture. KISS contains AX.25 frames without FCS.

Protocol references: gr-satellites
[MONITOR-3](https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/MONITOR-3.yml)
and [MONITOR-4](https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/MONITOR-4.yml).
See the shared decoder's `THIRD_PARTY.md` and `LICENSE` for attribution.
GPL-3.0-or-later.

## Verification

From the repository root:

```powershell
plugins\bqe_monitor_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_monitor_decoder/tests -v
```

Tests exercise independent gr-satellites USP reference vectors through noisy
audio at all four rates and both polarities, identity for both Monitor
callsigns, raw data preservation, file output, upload controls and managed
pass shutdown. Reference vectors validate the common USP transport; they
are not Monitor reception recordings. Actual over-the-air Monitor reception
still requires validation with a suitable recording or live pass.
