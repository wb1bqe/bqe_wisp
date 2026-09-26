# PC Audio Recorder

A small Python application that immediately records the sound being played by
the computer. It uses Windows WASAPI loopback or the Linux PulseAudio/PipeWire
monitor source, encodes continuously to MP3, and provides a local web console.

## What it does

- Starts recording as soon as the program launches.
- Provides an optional web console at <http://127.0.0.1:8765> without opening
  a browser tab automatically. Use `--open-browser` to open it on launch;
  the existing `--no-browser` option remains supported.
- Captures playback audio by default, or a microphone/radio input with `--input-device`.
- Shows recording state, elapsed time, selected output device, filename, and
  current file size.
- Saves recordings in the `recordings` subfolder with names such as
  `pc-audio_2026-08-31_17-42-10.mp3`.
- Finalizes the MP3 when you click **Stop, save MP3, and exit**, press Ctrl+C,
  or send the process a normal termination signal.

Closing the browser tab does not stop the recording.

## Recording from BQE WISP

Select **Audio > Record Audio** between passes. The item changes to **Stop
Recording** while capture is starting or running. Stopping saves an MP3 named
`Audio Recording 2026-09-22_14-30-00.mp3` in the recordings directory configured
under `plugins.bqe_sound_recorder.recordings` in `general_settings.yaml`.

This uses the same recorder and encoder as pass recording, with the source,
sample rate, and bitrate from the first `bqe_sound_recorder.py` command in
`satellites.yaml`. If no such command is configured, the recorder uses its
default playback source and encoding settings. The audio dependencies must
be installed in the Python environment running BQE WISP.

A scheduled or test pass stops and finalizes this recording before starting
tracking. The menu cannot start another recording while a pass is active;
existing recording configured for satellite passes continues to work as before.
Exit and Restart Server also stop and save menu recordings.

The main BQE WISP status panel shows a flashing blue light and **Recording**
label while capture is in progress. This covers menu recordings and the
bundled recorder running during passes. The light clears when capture stops
or fails; a forcibly terminated pass recorder's status expires within five
seconds, plus the main page's refresh interval. With reduced-motion enabled
in the browser or operating system, the light stays steadily blue instead.
Pass recorders publish a small heartbeat in `logs/audio_recorder_status.json`;
the indicator does not assume that a scheduled pass is necessarily recording.

## Requirements

- Python 3.10 or newer (64-bit Python is recommended on Windows).
- Windows 10/11, or Linux with PulseAudio/PipeWire PulseAudio compatibility.
- A playback device visible to the operating system.

The Python dependencies are `SoundCard`, `numpy`, `lameenc`, and (on Windows)
`sounddevice`. The MP3 encoder
is included in the `lameenc` wheel, so a separate FFmpeg installation is not
needed.

Windows direct input uses callback-based WASAPI capture in raw shared mode.
This bypasses Windows noise suppression that can erase radio static and digital
signals, while allowing the decoders to capture the same device simultaneously.
The recorder console shows the input level in dBFS and flags a missing signal.
After updating an existing installation, install `requirements.txt` in the
Python environment running BQE and restart BQE to load the capture changes.

## Windows quick start

1. Install Python from <https://www.python.org/downloads/> if needed. During
   installation, enable the option that adds the Python launcher.
2. Double-click `start_windows.bat`.

The first run creates a private `.venv` folder and installs the dependencies.
Later runs start directly. Recording uses the Windows WASAPI loopback for the
default playback device.

## Linux quick start

On Debian/Ubuntu, first ensure the virtual-environment and PulseAudio client
libraries are available:

```bash
sudo apt install python3-venv libpulse0
```

Then run:

```bash
chmod +x start_linux.sh
./start_linux.sh
```

PipeWire installations normally work through `pipewire-pulse`. A minimal/headless
system must have a running PulseAudio-compatible session and a monitor source;
otherwise there is no desktop playback stream to record.

## Choosing a different playback device

List the playback devices:

```bash
python pc_audio_recorder.py --list-devices
```

Select one using a unique part of its displayed name:

```bash
python pc_audio_recorder.py --device "Speakers (Realtek"
```

You can also choose a different port or output folder:

```bash
python pc_audio_recorder.py --port 8080 --output-dir my_recordings
```

Run `python pc_audio_recorder.py --help` for all options.

## Notes and troubleshooting

- The web server listens on `127.0.0.1` by default, so other computers cannot
  access it.
- Bluetooth headsets sometimes switch between a high-quality playback profile
  and a hands-free profile. Select the high-quality playback device if both are
  shown.
- On Linux, `pactl list short sinks` and `pactl list short sources` can help
  confirm that the selected sink has a corresponding `.monitor` source.
- If the machine loses its audio device during a recording, the app preserves
  any MP3 data already encoded and reports the device error in the console.
- An abrupt power loss or forced process kill cannot run the normal finalization
  code. Stop from the web console or with Ctrl+C whenever possible.

## Direct installation (without the launcher)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   . .venv/bin/activate
python -m pip install -r requirements.txt
python pc_audio_recorder.py
```
# BQE Audio menu with an SDR

When `radio_is_sdr: true` is set in `bqe_config/my_rig.yaml`, **Audio → Record
audio** subscribes to the running BQE SDR receiver at its configured port using
48 kHz mono audio. It does not require USB Audio Codec or open the dongle again.
Start an SDR idle preset first. The menu recording retains its configured MP3
bitrate and recordings folder, and stops automatically when a pass starts.
Conventional radio stations continue using the configured pass recorder input.
