# PC Audio Recorder

A small Python application that immediately records the sound being played by
the computer. It uses Windows WASAPI loopback or the Linux PulseAudio/PipeWire
monitor source, encodes continuously to MP3, and provides a local web console.

## What it does

- Starts recording as soon as the program launches.
- Opens a web console at <http://127.0.0.1:8765>.
- Captures playback audio, not the microphone.
- Shows recording state, elapsed time, selected output device, filename, and
  current file size.
- Saves recordings in the `recordings` subfolder with names such as
  `pc-audio_2026-08-31_17-42-10.mp3`.
- Finalizes the MP3 when you click **Stop, save MP3, and exit**, press Ctrl+C,
  or send the process a normal termination signal.

Closing the browser tab does not stop the recording.

## Requirements

- Python 3.10 or newer (64-bit Python is recommended on Windows).
- Windows 10/11, or Linux with PulseAudio/PipeWire PulseAudio compatibility.
- A playback device visible to the operating system.

The Python dependencies are `SoundCard`, `numpy`, and `lameenc`. The MP3 encoder
is included in the `lameenc` wheel, so a separate FFmpeg installation is not
needed.

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
