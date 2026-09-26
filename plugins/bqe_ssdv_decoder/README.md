# BQE SSDV decoder — RS61 / 239ALFEROV

Receives RS61 Geoscan digital-image packets from live radio audio or a recording.
It provides the same capture controls, channel selection, 44.1/48 kHz options,
waterfall, waveform, image preview, and saved-image gallery as the SSTV plugin.
The waterfall covers 0–12 kHz for the wider 9600-baud signal.

## Windows startup

Run `start_windows.bat` and open **http://127.0.0.1:8770**. The launcher creates
an isolated `.venv` and installs its dependencies (Python 3.10+ and internet on
first installation). This workstation's environment is already installed.
Choose the radio's **microphone/input**, channel 0 or 1, and Start live audio.
Speaker loopback is available if radio audio is routed through that output.
Close the plugin with Ctrl+C. Linux: `sh start_linux.sh` with PulseAudio capture.

Use FM-demodulated **discriminator / 9600-baud DATA audio**, with squelch open.
A conventional narrow voice-audio path can remove the frequencies needed for
9600 baud. Selecting a wider RF filter does not restore bandwidth already lost
in the radio's audio output. This receiver does not consume I/Q recordings.

The supported profile is **RS61 / 239ALFEROV, 9600-baud Geoscan NRZ/GFSK**:
sync `930B51DE`, 74-byte whitened frames, PN9, CC11xx CRC-16, and Geoscan v2
image fragments. Both signal polarities are supported. Packet CRC errors and
valid-packet counts appear in status. No GNU Radio or external soundmodem is
required. This is not a universal fsphil SSDV/RTTY/LoRa/IL2P decoder; those
transports and Geoscan convolutionally coded modes are outside this profile.

## Recordings and packet recovery

Upload WAV, MP3, OGG, FLAC, or KISS (`.kiss` / `.kss`) files. Lossless audio is
preferred. KISS files must contain already-demodulated 72-byte Geoscan frames;
they are not AX.25 frames and do not include the radio CRC. A KISS import trusts
the original demodulator's CRC validation and validates the image packet layout.
Use `--file` for recordings larger than the 512 MiB upload limit.

Every session saves validated packets to a unique `ssdv-packets-*.kiss` log,
including telemetry frames that are not image fragments. Replaying that log
reassembles images without repeating audio demodulation. The session page lists
packet counts and the packet-log path. Nothing is uploaded to an external service.

Fragments are placed by file number and byte offset, with duplicate rejection,
out-of-order assembly, gap accounting, and bounded storage (four image assemblies,
8 MiB each). Preview is attempted as fragments arrive. A JPEG header must be
received before an image can be displayed. Missing or conflicting fragments
prevent an image from being marked complete. On stop or EOF, renderable partial
images are saved; unusable fragments remain available in the packet log.

JPEGs and JSON metadata use unique `ssdv-...` names in the configured
`sstv_gallery_location`, so **View → SSTV Gallery** shows these images too.
`--output` selects a different folder. Metadata includes the file ID, fragment
count, missing bytes, conflicting packets, and completion status.

## Automatic passes and presets

Add to a supported satellite entry or preset:

```yaml
decode_ssdv_images: true
```

The input comes from `radio_soundcard` in `bqe_config/my_rig.yaml`. The plugin
starts after tuning, opens its controls at **http://127.0.0.1:8771** (or a free
port if occupied), and stops when the pass/preset ends. It can run alongside
the SSTV and telemetry receivers, using their shared-mode audio capture.
Its owner EOF handshake also stops it when the parent BQE process exits.

The existing **rs61** entry is enabled for this receiver. Its `auto_schedule`
setting is unchanged; schedule or select an RS61 pass in BQE to start it.
No existing HF preset is enabled for this RS61-specific radio protocol.

## Command line

```bat
.venv\Scripts\python.exe bqe_ssdv_decoder.py --list-devices
.venv\Scripts\python.exe bqe_ssdv_decoder.py --device "USB Audio Codec"
.venv\Scripts\python.exe bqe_ssdv_decoder.py --file "C:\recordings\rs61.wav" --no-web
.venv\Scripts\python.exe bqe_ssdv_decoder.py --file "C:\recordings\rs61.kiss"
```

Tests, from the BQE project root:
`plugins\bqe_ssdv_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_ssdv_decoder/tests`

Validation on this workstation: complete generated image transmissions decoded
at 44.1 and 48 kHz with both polarities; live USB Audio CODEC capture kept real
time and stopped cleanly. The public 607-second RS61 recording yielded 625
CRC-valid packets, including 296 unique image fragments. No starting JPEG
fragment was recovered from that recording, so it did not produce a displayable
image. Real-pass complete-image reception remains to be verified here.

See `THIRD_PARTY.md` for the protocol references and validation recording.
