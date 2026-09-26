# BQE SSTV decoder

Receives SSTV from a sound-card input, virtual audio cable, speaker loopback, or
an MP3/WAV recording. Detects each transmission's mode automatically and saves
JPEGs directly into the existing BQE **SSTV Gallery** folder.

## Windows

Double-click `start_windows.bat`, then open **http://127.0.0.1:8767**.
Choose an input and click **Start live audio**, or select an MP3/WAV file and
click **Decode file**. Stop the current job before starting another. A browser
does not open automatically; add `--open-browser` to request that behavior.

The launcher creates a local `.venv` and installs dependencies on first use
(Python 3.10+ and internet required). An isolated environment is already installed
on this workstation. Linux: run `sh start_linux.sh`; live capture requires a
PulseAudio-compatible audio server. Close the plugin with Ctrl+C.

Windows microphone inputs use `sounddevice`/WASAPI to support USB codecs that
report a plain floating-point audio format; speaker loopback uses SoundCard.
After updating an existing installation, run
`.venv\Scripts\python.exe -m pip install -r requirements.txt` from this plugin
directory and restart the decoder. Windows inputs must have unique device names
so the capture backend can match the selected device without guessing.

The live controls offer 48,000 or 44,100 Hz capture. The audio monitor shows a
sampled 0–4 kHz waterfall and an automatically scaled 20 ms waveform for the
selected channel, even before an SSTV header is detected. Windows microphones
use callback capture to preserve samples across decoder block boundaries.

## Automatic satellite and preset reception

Add this key to a satellite entry in `bqe_config/satellites.yaml`, or at the top
level of a preset YAML under `presets/`:

```yaml
decode_sstv_images: true
```

Set the shared radio input at the top level of `bqe_config/my_rig.yaml`:

```yaml
radio_soundcard: "USB Audio Codec"
```

Boolean `true` uses this rig setting for both satellite and preset reception.
The original `decode_sstv_images: "Other soundcard"` form remains supported as
an explicit per-entry override. A missing/blank `radio_soundcard` is an error
when boolean reception is enabled; the decoder never silently substitutes the
default input. Satellite tracking honors an explicit `--radio_config` path.
Telemetry uses the same rig soundcard when a satellite has `decode_telemetry: true`.

For satellites, reception starts after radio setup when BQE tracks that entry.
For presets, reception starts after tuning when selecting the preset in BQE,
including automatic idle-preset selection. No `program_to_run_during_pass` or
`program_to_run_while_waiting` command is needed. Existing helper commands still
run alongside the decoder. Omit the key, leave it blank, or set it to `false`
to disable automatic reception for that entry.

The receiver automatically opens a browser page, normally at
**http://127.0.0.1:8768**. If that port is occupied it opens an available port
instead, without interrupting another decoder. Standalone controls remain on
port 8767. The automatic receiver uses the plugin's `.venv` when installed;
otherwise the Python running BQE must have the plugin dependencies installed.

The page shows the satellite/preset, detected mode, and **Reception in progress**
image, refreshed about every two seconds. Unreceived rows remain black. Preview
updates stay in memory; final images and partial images saved at shutdown go
into the existing gallery. Switching presets, starting/ending a pass, shutting
down BQE, or losing the owning BQE process closes its receiver and releases the
soundcard. Its browser page is live only while that receiver is running; saved
images remain in the SSTV Gallery. Existing satellite entries are not enabled
automatically.

Use the input's full name, a unique part such as `USB Audio Codec`, or an exact
device ID from `--list-devices`. Recording inputs take precedence over similarly
named speaker loopbacks. Missing or ambiguous devices produce an error on the
receiver page instead of silently recording another soundcard.

## SSTV Gallery

By default, the plugin reads `bqe_wisp.sstv_gallery_location` from
`bqe_config/general_settings.yaml`. Relative paths are resolved from the BQE
program directory, exactly as in the gallery. The current configuration points
to `c:/ham/mmsstv/history`. The folder is created when decoding starts if needed;
the plugin needs write access to it. Existing images are never overwritten.

Each received image gets a unique UTC timestamp/mode `.jpg` filename and a JSON
sidecar with source, mode, dimensions, and completion status. JPEGs become visible
only after writing finishes. Open or refresh **Files → SSTV Gallery** in BQE to
see them; the decoder also shows its latest 100 saved images and a gallery link.
The gallery link requires BQE's web console to be running. Both the decoder and
gallery retain all saved JPEGs on disk.

## Supported modes

- Robot 36 and 72
- Martin 1 and 2
- Scottie 1, 2 and DX
- PD 50, 90, 120, 160, 180, 240 and 290 (including common ISS modes)
- Pasokon P3, P5 and P7
- Wraase SC2-180

Automatic recognition requires a recognizable VIS header. Unsupported modes,
missing/damaged headers, digital SSTV/SSDV, and severely degraded signals may not
decode. This version does not offer manual headerless reception. File extension
does not determine the SSTV mode; the received signal does.

An image is saved within roughly two seconds of completion. Stopping reception,
reaching end of file, or receiving a new header saves a recognized unfinished
image with `-partial` in its filename. Missing rows may be black. “Complete” means
the decoder received all rows, not that the picture is free of noise or errors.
No detected image produces an explicit status message instead of a blank JPEG.

Keep squelch open, disable noise suppression/audio enhancements, and avoid
clipping. WAV preserves more signal detail than MP3. Channel numbers begin at
zero; stereo channels are not averaged. The selected input is on the computer
running the plugin, not a browser microphone. Capture overflow stops the job
with an error instead of silently dropping samples.

## Command line

From the plugin directory:

```powershell
.venv\Scripts\python.exe bqe_sstv_decoder.py --list-devices
.venv\Scripts\python.exe bqe_sstv_decoder.py --idle --open-browser
.venv\Scripts\python.exe bqe_sstv_decoder.py --input-device "Your radio audio device"
.venv\Scripts\python.exe bqe_sstv_decoder.py --file "C:\recordings\iss.wav" --no-web
.venv\Scripts\python.exe bqe_sstv_decoder.py --file "C:\recordings\sstv.mp3" --channel 1 --no-web
```

Direct invocation starts live capture unless `--idle` or `--file` is supplied.
`--sample-rate 48000` applies to capture; files use their actual sample rate.
Supported input rates are 8000–192000 Hz. `--port` changes the control port.
`--output PATH` overrides the save folder; such images appear in BQE's gallery
only if it is configured to use that same folder. The plugin does not change BQE
settings or existing radio presets. Missing gallery configuration is an error
unless an output override is supplied.

Web uploads are limited to 512 MiB. Command-line file decoding reads blocks and
can handle larger recordings. Reception retains at most about 450 seconds of
audio (long enough for Pasokon P7), plus temporary decode buffers. Multiple
transmissions in a file or a live session are handled automatically. Metadata's
`audio_seconds` is the approximate beginning of the detection buffer, within
about three seconds of the header; `decoded_at` is the processing time.

For a BQE pass program, use the full path to `.venv/Scripts/python.exe`, followed
by the full path to `bqe_sstv_decoder.py`, `--device "Your device" --no-web`.
The plugin honors `BQE_PASS_STOP_FILE` and `--stop-file PATH` to stop gracefully
at pass end, including saving any recognized partial image. It is independent
of the telemetry plugin and uses a different port. No existing pass programs
are changed automatically.

## Tests

From the BQE program directory:

```powershell
plugins\bqe_sstv_decoder\.venv\Scripts\python.exe -m pip install -r plugins/bqe_sstv_decoder/requirements-test.txt
plugins\bqe_sstv_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_sstv_decoder/tests -v
```

Tests exercise every supported mode, independently generated PySSTV signals for
Robot/Martin/Scottie/PD, MP3 and stereo WAV input, consecutive transmissions,
arbitrary block boundaries, partial images, silence/noise, saved JPEG colors and
metadata, configuration, HTTP controls, and mocked capture lifecycle. Physical
radio reception and sound-driver behavior require an on-air check.

## Decoder dependency

Uses the MIT-licensed [sstv Python package](https://github.com/unexcellent/sstv-py)
and its [Rust decoding engine](https://github.com/unexcellent/sstv), pinned to
Python package version 0.1.0. These dependencies are installed, not vendored.
PySSTV is an independent encoder used only by tests. See `THIRD_PARTY.md`.
