# BQE telemetry decoder

Receives FM-discriminator audio from a live sound-card input (including virtual
audio cables and speaker loopback) or MP3, WAV, FLAC and OGG recordings. No I/Q
data or GNU Radio installation is required. Runs independently alongside BQE.

## Start on Windows

Double-click `start_windows.bat`, then visit **http://127.0.0.1:8766**.
No browser tab opens automatically. Choose an audio device and click **Start live
audio**, or select a recording and click **Decode file**. Stop the current job
before starting another. Channel numbers start at zero; stereo is not averaged.
The launcher installs dependencies into a local `.venv` on first use (requires
Python 3.10+ and internet). Dependencies are already installed on this workstation.
On Linux run `sh start_linux.sh`; SoundCard requires a working PulseAudio-compatible
server. Close with Ctrl+C.

For command-line use, from this plugin directory:

```powershell
.venv\Scripts\python.exe bqe_tlm_decoder.py --list-devices
.venv\Scripts\python.exe bqe_tlm_decoder.py --input-device "Your audio device"
.venv\Scripts\python.exe bqe_tlm_decoder.py --file "C:\recordings\satellite.mp3" --no-web
.venv\Scripts\python.exe bqe_tlm_decoder.py --device "Your audio device" --bauds 9600 --protocol usp --no-web
```

Direct invocation starts capture by default. `--idle` starts only the controls;
`--open-browser` explicitly opens a browser. `--channel 1` selects the second
channel. `--sample-rate 48000` applies to live capture; files use their original
sample rate. `--output PATH` chooses a log directory. Web uploads are limited to
512 MiB; the command line streams larger recordings. The web controls bind only
to localhost. A file job stays visible at EOF unless `--no-web` is used.

## Supported signals

Automatic parallel decoding at 1200, 2400, 4800 and 9600 baud:

- G3RUH scrambled, NRZI AX.25 with HDLC and CRC-16/X-25 validation.
- SPUTNIX USP short/long frames with polarity detection, soft Viterbi decoding,
  CCSDS descrambling and Reed-Solomon error correction, containing AX.25.

These are the modes listed for UMKA-1 in gr-satellites. This is not a universal
CubeSat decoder: Bell 202 AFSK, BPSK, SSTV, CW and I/Q inputs are not implemented.
The source/framing separation permits adding an I/Q demodulator in the future.
Continuous clock recovery tracks fractional samples and small sample-clock errors.
Retuning glitches can destroy a packet; the decoder searches for subsequent frames.

**Receiver audio matters:** select wide FM, disable squelch, noise suppression,
audio enhancements and AGC/compression where possible. Prefer a flat DATA/9600-baud
or discriminator audio output. Wide RF bandwidth alone does not make an ordinary
3 kHz speaker output suitable for 9600-baud telemetry. Avoid clipping. Rates with
fewer than three samples/symbol are skipped and reported. MP3 compression may
erase data; WAV/FLAC are preferable for future recordings.

## Results and telemetry

Each session writes unique `.jsonl` and `.kiss` files under `decoded/`, flushing
every validated packet. The page shows the newest 200 packets. JSON includes
callsigns, baud, validation method, file/capture-relative audio time, raw frame and
payload bytes, and any recognized fields. `decoded_at` is processing time, not
the original reception time of a recording. KISS contains AX.25 without FCS.

Supported engineering records: SPUTNIX common/X/Y/Z power (IDs 000E–0011), and
25-byte UHF beacon 4246 (temperatures, RSSI, power, uptime, voltage/current).
Firmware layouts vary. Unknown or differently sized records remain raw; the
plugin does not invent values or identify satellites from tuning frequency.
CRC failures and uncorrectable USP frames are not reported as valid packets.
Capture overruns stop the job with an error; select fewer rates on slower machines.

## BQE pass program

Set a satellite's existing `program_to_run_during_pass` command to the full path of
this plugin's `.venv/Scripts/python.exe`, followed by the full path of
`bqe_tlm_decoder.py`, `--device "Your device" --no-web` and optional rate/protocol
arguments. BQE supplies `BQE_PASS_STOP_FILE` for graceful shutdown at pass end.
Existing satellite configurations are not changed automatically. `--stop-file`
also supports an external stop request (create that file to stop).

## Verification

```powershell
# Run from C:\bqe\bqe_wisp
plugins\bqe_tlm_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_tlm_decoder/tests -v
```

Tests include independent gr-satellites USP reference packets, damaged symbols,
all four baud rates, polarity reversal, fractional timing, noise, bad CRC rejection,
MP3/WAV file decoding, and saved JSONL/KISS. Actual UMKA-1 reception has not yet
been validated with this plugin; a telemetry recording is needed for that check.

GPL-3.0-or-later. See LICENSE and THIRD_PARTY.md.
