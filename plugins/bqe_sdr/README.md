# BQE SDR

A Windows/Linux RTL-SDR receiver integrated with BQE WISP. It receives IQ directly
over USB or from `rtl_tcp`, selects a channel, and produces 48 kHz mono audio.
No SDR application or virtual audio cable is required. This is a **receive-only**
plugin; an RTL-SDR cannot transmit.

## Included

In BQE's **Configuration → Radio** dialog, **Radio is an SDR** selects receive-only
SDR operation for all passes and idle presets and hides serial/CAT fields. The
USB device, IQ sample rate, gain, PPM correction, recording options and optional
rtl_tcp connection are saved under `sdr` in `bqe_config/my_rig.yaml`. Turning the
checkbox off restores conventional radio operation; its previous settings are
preserved. Per-pass `receive_sdr: true` still supports mixed stations when the
station-wide checkbox is off. Changes take effect on the next receiver start.
The standalone launchers also load this rig file. Select an idle preset and
pass frequencies supported by your receiver before enabling station-wide SDR
operation; an R820T receiver does not provide conventional HF coverage.

- Direct USB via the `librtlsdr` C interface; device index or USB serial selection.
- Network IQ from `rtl_tcp`, including tuner frequency, gain, sample-rate and PPM commands.
- NFM (including `FM` alias), AM, USB, LSB and CW demodulation.
- Channel filtering and continuous digital tuning for Doppler correction.
- Local control page: spectrum, waterfall, signal level, tuning, browser audio monitor.
- 48 kHz mono PCM WAV recording, enabled by default.
- Optional IQ recording with SigMF metadata, and real-time IQ replay.
- Local audio fan-out to the existing SSTV, telemetry, SSDV and MP3 recorder plugins.
- Managed pass/preset startup and orderly shutdown on parent-process EOF.
- A clearly labelled generated test signal for checking the entire receiver without hardware.

The web interface binds to **127.0.0.1:8772**. Closing the page does not stop the
receiver. Stop with **Stop & save**, Ctrl+C, or the owning BQE pass/preset lifecycle.

## Windows installation

1. Use Python 3.10 or newer, preferably 64-bit. Double-click `start_windows.bat`.
   It creates this plugin's `.venv` and installs NumPy, SciPy and PyYAML.
2. For direct USB, install the WinUSB driver for the RTL-SDR's receiver interface
   using the device manufacturer's instructions. The plugin does not replace USB drivers.
3. Place an architecture-matching **rtlsdr.dll and its dependent DLLs** in
   `plugins/bqe_sdr/drivers/`, or specify the library path in Advanced, `--library`,
   or `BQE_RTLSDR_LIBRARY`. Python and the DLL must have the same architecture.
4. Select **RTL-SDR · USB**, click **Find USB receivers**, enter the index, then
   select frequency/mode/bandwidth and start. Close other applications using that dongle.

For an RTL-SDR Blog V4, use a current V4-compatible library. Older libraries can
open the dongle yet tune it incorrectly. See the official
[RTL-SDR quick-start guide](https://www.rtl-sdr.com/rtl-sdr-quick-start-guide/) and
[V4 driver instructions](https://www.rtl-sdr.com/V4/).
The plugin does not enable a bias tee or alter EEPROM settings.

From PowerShell, after the first launch:

```powershell
cd C:\bqe\bqe_wisp\plugins\bqe_sdr
.\.venv\Scripts\python.exe bqe_sdr.py --list-devices
.\.venv\Scripts\python.exe bqe_sdr.py --source usb --frequency 145800000 --mode NFM --open-browser
```

## Linux installation

Use Python 3.10+ for consistency with BQE. The standalone SDR suite also passes
on Python 3.8 / Ubuntu 20.04 with NumPy 1.24.4 and SciPy 1.10.1.

On Debian/Ubuntu, install the Python venv support and RTL-SDR driver/tools:

```sh
sudo apt install python3-venv rtl-sdr librtlsdr0
cd /path/to/bqe_wisp/plugins/bqe_sdr
sh start_linux.sh
```

The launcher creates a local virtual environment and opens the controls. Ensure
your user has USB access through the distribution's RTL-SDR udev rules, and that
the DVB TV driver is not claiming the device. `rtl_test -t` can help diagnose
driver/device problems; stop it before starting BQE SDR. Follow the manufacturer
instructions for V4-compatible packages if your distribution ships an old library.

```sh
.venv/bin/python bqe_sdr.py --list-devices
.venv/bin/python bqe_sdr.py --source usb --frequency 145800000 --mode NFM
```

The SDR receiver itself does not depend on ALSA, PulseAudio, SoundCard or a local
sound card. Browser listening and the local PCM bridge are optional. Existing
decoder plugins retain their own Python requirements.

## Try without a receiver

Choose **Demo signal · no hardware**, then **Start receiver**, or run:

```sh
python bqe_sdr.py --source demo --duration 10 --record-iq --open-browser
```

The generated signal is a 1 kHz tone (700 Hz after CW demodulation). The page
labels it as simulated. Recording output defaults to this plugin's `recordings/`
directory. Use `--output /path/to/folder` to change it.

## Automatic satellite passes and presets

1. Run the launcher once to install this plugin's dependencies.
2. Merge the `sdr:` mapping in [example_rig.yaml](example_rig.yaml) into
   `bqe_config/my_rig.yaml`. It is separate from physical-radio settings.
3. Add the keys in [example_satellite.yaml](example_satellite.yaml) to the
   satellite entries you want to receive with SDR:

```yaml
receive_sdr: true
sdr_only: true
enable_tuning: true
decode_sstv_images: true
```

`receive_sdr` starts the SDR before the decoders, using the entry's downlink
frequency, mode and bandwidth. Boolean decoder flags automatically select its
audio stream. SSTV's older explicit soundcard-name override remains an override.
Set `decode_telemetry` and/or `decode_ssdv_images` as appropriate for the signal;
the available protocols remain those supported by those plugins.

`enable_tuning: true` sends the tracker's Doppler-corrected downlink to the SDR
while the satellite is above the configured horizon. It uses a digital frequency
shift within the captured band, preserving the IQ RF center and avoiding USB
retuning gaps. A requested frequency outside that band is rejected; restart the
receiver with a new RF center for a large frequency change.

`sdr_only: true` skips physical-radio setup and CAT tuning for that pass/preset.
Omit it or use false to retain normal transceiver control alongside SDR reception.
The existing FDT/manual CAT controls still address the physical radio; use the
SDR page for manual receiver tuning. SDR Doppler follows `enable_tuning`, including
linear satellite passes, independently of the physical radio's FDT enable switch.

Per-entry `sdr:` mappings can override gain, sample rate, recording options and
other receiver CLI options. Set the control `port` only in `my_rig.yaml` so every
decoder agrees on the audio endpoint. `open_browser: true` is optional. A busy
control port is an error; managed passes never attach to an unrelated receiver.

For presets, copy/adapt [example_preset.yaml](example_preset.yaml) into `presets/`.
The BQE scheduler launches the receiver when that preset becomes active and stops
it before switching presets or starting a pass. The standalone radio-preset helper
does not own SDR reception; use the main BQE application for this lifecycle.

Restart BQE after installing these integration changes. Existing satellite and
radio configuration files are not automatically switched to SDR. A legacy
`program_to_run_during_pass` command that explicitly records USB Audio CODEC still
does exactly that; remove it for an SDR-only recording, or point it at the bridge
below. The SDR plugin already records its own demodulated WAV by default.

## Connect a decoder or the MP3 recorder manually

Start SDR reception first, then use this input-device name:

```text
bqe-sdr://127.0.0.1:8772
```

For example, from the BQE project directory, using each plugin's Python environment:

```sh
python plugins/bqe_sstv_decoder/bqe_sstv_decoder.py --device bqe-sdr://127.0.0.1:8772
python plugins/bqe_tlm_decoder/bqe_tlm_decoder.py --device bqe-sdr://127.0.0.1:8772
python plugins/bqe_sound_recorder/bqe_sound_recorder.py --input-device bqe-sdr://127.0.0.1:8772 --sample-rate 48000
```

Audio is mono, so select channel 0. Every consumer receives its own continuous
stream. Up to 16 consumers are supported. A consumer that falls behind is
disconnected with an error rather than silently dropping samples. Stopping the
SDR ends its streams; manually launched consumers need restarting for a new
reception. Managed consumers stop before the managed SDR shuts down.

## rtl_tcp

Start `rtl_tcp` on the computer connected to the receiver, then:

```sh
python bqe_sdr.py --source tcp --host 192.168.1.50 --tcp-port 1234 --frequency 435800000 --mode NFM
```

The network server carries IQ, and BQE SDR performs the demodulation. Use a
trusted network or a tunnel for remote access: the rtl_tcp protocol has no
authentication or encryption. This plugin's control/audio server remains local.

## IQ storage and replay

Enable **Record wideband IQ** or `--record-iq`. Each capture saves:

- `.sigmf-data`: interleaved IQ (`cu8` for USB/rtl_tcp; original supported format for replay).
- `.sigmf-meta`: sample rate, datatype, RF center, UTC start, and annotations for digital tuning changes.
- `.wav`: demodulated audio, unless `--no-record-audio` is supplied.

At 960 kS/s, `cu8` IQ uses about **1.92 MB/s / 115 MB/minute**; `cf32_le` uses four
times as much. IQ recording is disabled by default. Recordings use unique names,
and file/device errors are shown in the control page. Standard WAV has a roughly
4 GiB size limit; split exceptionally long sessions before that limit.

```sh
python bqe_sdr.py --iq-file recordings/capture.sigmf-data --frequency 145800000 --mode NFM
python bqe_sdr.py --source file --iq-file input.iq --iq-format cu8 --sample-rate 960000 --center 146040000 --frequency 145800000
```

SigMF metadata supplies RF center, rate and datatype automatically. Set the
desired receive frequency and demodulation settings yourself; replay does not
automatically follow the original tuning annotations. Raw files require the
correct rate/center/format to be supplied. Supported replay formats are `cu8` and
`cf32_le`, with one continuous center frequency per capture. MP3 is an audio format,
not an IQ storage format.

## Limits and verification

This version supports one receiver and one demodulated channel per instance.
Supported IQ rates are 240 kS/s, 960 kS/s, 1.2, 1.44, 1.92 and 2.4 MS/s.
Channel bandwidth is 500–40000 Hz (SSB at most 12000 Hz). Use NFM for narrowband
FM voice/SSTV and compatible digital audio. Broadcast WFM/stereo, direct IQ packet
decoders, automatic multi-channel reception, bias tee control and legacy HF
direct-sampling selection are not implemented. HF coverage depends on the dongle;
V4 uses its supported driver rather than the older V3 direct-sampling setting.

USB backpressure, TCP disconnection, malformed IQ, failed opens and decoder
backpressure are explicit errors. RTL-SDR/rtl_tcp do not provide sample sequence
numbers, so this does not guarantee detection of every hardware/network sample loss.

Run the portable suite from the project root:

```sh
python -m unittest discover -s plugins/bqe_sdr/tests -v
```

The suite covers all demodulators, arbitrary block continuity, sideband rejection,
Doppler shifts, gain/driver calls with a mock USB device, a real local rtl_tcp
test server, WAV/SigMF replay, multiple HTTP audio clients, request-origin checks,
managed ownership and decoder routing. Tested on Windows/Python 3.12 and
Ubuntu 20.04/Python 3.8 (30 tests on each platform). Windows USB capture was also
verified with the user's RTL2832U/R820T receiver after installing WinUSB on
interface 0: 7,700,480 complex samples and 8.02 seconds of non-silent WAV audio,
with no capture error or clipping. No antenna was attached, so the signal was
background noise. An audio_gain of 0.15 was used for this unsquelched NFM test;
adjust it for actual reception. On-air decoding and physical USB capture on
Linux remain unverified.

API references: [librtlsdr](https://github.com/osmocom/rtl-sdr/blob/master/include/rtl-sdr.h),
[rtl_tcp](https://github.com/osmocom/rtl-sdr/blob/master/src/rtl_tcp.c),
[SigMF](https://github.com/sigmf/SigMF).
