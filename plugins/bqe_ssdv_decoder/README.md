# BQE digital image decoder — UMKA-1, Geoscan, RS61 and SO-127


## UMKA-1 and Geoscan profiles

Select a Profile in the decoder console, pass `--profile` on the command line,
or set `ssdv_profile` alongside `decode_ssdv_images: true` in satellites.yaml.

| Profile | Supported digital image transport |
| --- | --- |
| `umka-1` | RS40S USP JPEG file transfer, automatically trying 2400/4800/9600 baud with convolutional and Reed–Solomon error correction |
| `geoscan` | Geoscan v1 (64-byte payload) and v2 (72-byte payload), uncoded 9600-baud GFSK with PN9 and CRC |
| `rs61` | Existing RS61 / 239ALFEROV Geoscan v2 profile (default, unchanged) |
| `hades-sa` | SO-127 SSDV at 200/800/1600 baud |

UMKA-1's digital images use **USP**, not the fsphil SSDV format. They are handled
here so all digital image receptions share the same controls and gallery. The
existing `umka-1` satellite entry now selects this profile automatically at
437.625 MHz; its scheduling and other decoder choices are preserved. Its separate
Robot36 SSTV broadcasts still require `decode_sstv_images: true` when desired.

UMKA-1 KISS imports contain AX.25-encapsulated USP records, without an AX.25 FCS;
live audio is accepted only after USP FEC validation. The receiver handles INIT,
FILESIZE and offset-addressed DATA records, keeps satellite channels separate,
checks bounds, and marks missing/conflicting image data as incomplete. Public
RS40S INIT/DATA session-byte differences are handled. Transmitted filenames are
metadata and never used as local paths. Non-JPEG files such as original telescope
FITS data remain recoverable in the packet log but are not rendered by this plugin.
19200-baud USP is not supported by this profile.

The general Geoscan profile recognizes the published satellite IDs for
Geoscan-Edelveis, StratoSat-TK1 and the v2 family (including RS61), and keeps
satellite/file IDs separate. v1 needs the image-start/JPEG-header packet to turn
memory addresses into file offsets. Packets received before that remain in the
KISS log for replay; v2 fragments can be assembled out of order. Both frame sizes
are tried automatically. **Convolutionally coded Geoscan modes are not supported**.
Select the profile to match the satellite's actual downlink mode.

Both profiles use configured SDR audio or conventional-radio discriminator/DATA
audio on Windows and Linux (Python 3.10+). No external modem is needed. KISS imports
trust the original demodulator's validation, while audio reception validates FEC
or CRC before logging packets. Existing Geoscan KISS logs also replay through the
new profile. No new Python dependencies were added.

Validation includes an independent public RS40S capture (one complete 480×480 JPEG),
synthetic noisy USP audio at all three supported rates, both Geoscan frame sizes,
polarity/sample-rate variations, malformed records, gaps, conflicts and duplicate
packets. All three new format paths also recover pixel-identical complete images
from simulated unsigned 8-bit RTL-SDR IQ at 960 kHz, through BQE's actual tuning,
channel filtering and FM demodulation with the configured 0.15 audio gain. This
offline check is included in Help → Perform environment diagnostics when plugin
self-tests are selected. On-air reception with this workstation has not yet been verified.

```yaml
decode_ssdv_images: true
ssdv_profile: umka-1 # or geoscan, rs61, hades-sa
record_audio: true
```

## SO-127 / HADES-SA / SpinnyONE

The `so-127-ssdv` entry in `bqe_config/satellites.yaml` enables automatic
scheduling for NORAD **68446**, **436.875 MHz**, and selects
`decode_ssdv_images: true` with `ssdv_profile: hades-sa`. Its receive-only
configuration uses 12 kHz FM bandwidth and flat discriminator audio. The shared
`record_audio: true` setting saves lossless WAV audio in the configured sound-recorder
folder for replay (see [pass recording](../PASS_RECORDING.md)).
Normal scheduling priorities, elevation limits, and overlapping passes apply.
Its orbit is already present in `keps.txt` and in the AMSAT TLE feed used by
the normal orbit update.

The HADES profile automatically tries 200, 800, and 1600 baud, both polarities,
validates normal 256-byte SSDV packets using Reed–Solomon correction and CRC32,
and renders JPEGs with Philip Heron's SSDV utility. Validated packets are saved
as KISS files; images and metadata appear in **View → SSTV Gallery**. Reordered
and duplicate packets are supported. Missing packets produce partial images;
only a gap-free sequence with an end packet is marked complete. Partial images
are rendered when reception stops. This profile does not decode CODEC2,
telemetry, no-FEC SSDV, or USB-demodulated audio tones. For conventional radios,
use flat FM discriminator output, not narrow voice audio.

SO-127 telemetry runs alongside this image profile using the dedicated
[`bqe_so127_decoder`](../bqe_so127_decoder/README.md) plugin and
`decode_so127_telemetry: true` in the same satellite entry.

Select **SO-127 / HADES-SA** in the plugin's Profile control for manual file
decoding, or use `--profile hades-sa`. Scheduled passes select it automatically.
The existing RS61 profile remains the default.

Windows includes the source-built `vendor/ssdv/ssdv.exe` (pinned revision and build hashes included). Linux needs
the portable utility compiled for that machine (including ARM Raspberry Pi):

```sh
sudo apt install build-essential
python3 plugins/bqe_ssdv_decoder/vendor/ssdv/build.py --target linux
```

The Linux launcher also builds it if gcc is available. Missing tools
produce an explicit error for HADES image decoding; they do not affect RS61.
See `vendor/ssdv/README-BQE.md` for sources and licensing.

Validated with the operator's reference packet, synthetic noisy audio at all
three rates, the actual BQE SDR IQ-to-audio path, and upstream SSDV encoder /
decoder roundtrips on Windows and Linux. Live SO-127 pass reception has not yet
been verified.

## RS61 / 239ALFEROV

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

The input comes from the SDR audio bridge when using an SDR, otherwise from
`radio_soundcard` in `bqe_config/my_rig.yaml`. The plugin
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
