# AO-123 / ASRTU-1 telemetry

Native Windows/Linux decoder for the 435.400 MHz, 9600-symbol/s BPSK downlink.
No GNU Radio or external decoder is required. It performs carrier/timing recovery,
soft Viterbi decoding, differential decoding, CCSDS descrambling, conventional-basis
RS(255,223) correction, and short-TM KISS reassembly on virtual channels 0 and 2.

## Use with BQE WISP

Restart BQE after installing this update. Select **ao-123-tlm** to track an AO-123
telemetry pass using the configured SDR. This separate entry is not automatically
scheduled; the existing **ao-123** FM voice entry is unchanged. It requires a
working BQE SDR configuration in `bqe_config/my_rig.yaml`. BQE tunes the nominal
435.400 MHz carrier with Doppler correction. Antenna tracking can be enabled in
the telemetry entry if the station has a rotor.

The **ao123-telemetry** preset starts reception at a fixed frequency without
satellite tracking. For a pass, use **ao-123-tlm** instead.

The control page opens automatically for a managed pass/preset (default
`http://127.0.0.1:8783`; a free port is selected if busy). Standalone controls start
without opening a browser. Packets also appear under **View → Telemetry** for the
telemetry satellite entry. All accepted packets are saved in `decoded/*.jsonl`
and standard KISS files; partial transport packets are never published.

## Input requirements

Use the BQE SDR's **BPSK** mode with **16000 Hz** bandwidth. It preserves the whole
complex RF channel and shifts it to a **12000 Hz carrier** in **48000 Hz mono PCM**.
This is a linear frequency conversion, not FM demodulation. The pass recorder
saves this signal as WAV for later decoding. Listen volume does not control decoder
input gain; avoid clipping in the SDR/audio source.

Existing AO-123 FM voice recordings, MP3, and narrow 3 kHz receiver audio are not
suitable for this decoder. Selecting BPSK after recording FM cannot recover the
missing phase information. External wideband linear audio may be used if the
entire BPSK spectrum is preserved; specify its carrier with `--carrier`. Supported
audio rates are 38400–192000 Hz, with at least 6500 Hz of spectrum on either side
of the selected audio carrier. 48000 Hz is recommended. Keep Doppler correction
active; the carrier loop handles small residual offsets, not an entire pass drift.

Stereo WAV/FLAC containing complex baseband I/Q is also supported with
`--input-format iq`: left channel I, right channel Q, carrier near zero. The
sample-rate range is the same. Raw RTL-SDR byte files are not WAV I/Q files.

## What is decoded

The page displays reception time, virtual channel, spacecraft ID, APID, sequence
count, packet type, secondary-header flag, and payload size. Raw packet and payload
hexadecimal bytes are retained. Frame counters detect losses and reset unfinished
KISS packets independently per virtual channel. Invalid lengths/versions, idle
space packets, uncorrectable frames and malformed escapes are rejected.

**Housekeeping payloads remain raw.** The upstream ASRTU-1 reference defines the
CCSDS primary header but provides no voltage, current or temperature conversions.
The plugin reports `header_only` rather than assigning undocumented units. The
AO-123 identity comes from the configured receive channel/protocol; the received
spacecraft ID is retained for inspection. Reed-Solomon verification is error
correction, not authentication or an additional packet CRC.

## Installation and standalone operation

Use Python 3.10+ on Windows or Linux. The main BQE requirements include this plugin.
The launchers create a private environment when needed:

```bat
plugins\bqe_ao123_decoder\start_windows.bat
plugins\bqe_ao123_decoder\start_windows.bat --file "C:\recordings\ao123.wav" --no-web
```

```sh
bash plugins/bqe_ao123_decoder/start_linux.sh
bash plugins/bqe_ao123_decoder/start_linux.sh --file /path/ao123-iq.wav --input-format iq --no-web
```

On Ubuntu install the matching Python venv package if environment creation fails;
Ubuntu 20.04's stock Python 3.8 is too old. Set `BQE_AO123_PYTHON` to an installed
Python 3.10+ executable when using the Linux launcher. Linux live sound-card capture
needs a PulseAudio-compatible server; file decoding and the BQE SDR bridge do not.
Use `--list-devices` to list sound-card inputs, or `--device NAME` for live capture.
Use `--idle` for controls only. `--help` lists all options. Stop before changing input.

For a custom SDR satellite entry/preset use:

```yaml
decode_ao123_telemetry: true
receive_sdr: true
sdr_only: true
downlink_frequency_mhz: '435.400'
downlink_mode: BPSK
sdr:
  bandwidth: 16000
```

Presets use `frequency_hz: 435400000` and `mode: BPSK` instead. The plugin shares
BQE's SDR receiver and never opens a second RTL-SDR device.

## Validation

Synthetic tests cover the full RF I/Q → SDR audio → BPSK → error correction →
CCSDS packet path, reversed polarity, residual frequency/sample-clock offsets,
multiple sample rates, arbitrary chunks, escaped/split packets, frame loss,
noise/corruption, file output and managed start/stop. Windows and Linux are tested.
A real AO-123 telemetry recording is still needed for on-air validation. Live
reception may require further tuning for weak/fading signals.

```sh
python -m unittest plugins.bqe_ao123_decoder.tests.test_decoder plugins.bqe_ao123_decoder.tests.test_integration
```
