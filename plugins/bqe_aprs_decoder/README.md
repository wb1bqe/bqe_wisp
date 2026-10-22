# BQE APRS decoder

Receive APRS from FM radio audio, the BQE SDR audio bridge, or WAV/FLAC/OGG/MP3
recordings. Includes a streaming 1200-baud Bell 202 AFSK modem (1200/2200 Hz
tones) and optional 9600-baud G3RUH reception. Optional APRS-IS uploading forwards
live received packets to the Internet. The plugin never transmits over radio
or digipeats packets.

## Start

Run `start_windows.bat` or `sh start_linux.sh`, then open
http://127.0.0.1:8781. The launchers install requirements into a plugin-local
`.venv` on first use (Python 3.10+). Keep the plugin in the BQE repository;
it reuses the common audio, telemetry and decoder HTTP components.

Choose a sound-card input or decode a recording. The selected input belongs
to the server computer. Channel numbers start at zero. Stop before switching
sources. All verified packets are saved; the newest 200 appear in the controls.
Select a packet for its full APRS fields, TNC2 text, path and raw bytes.

```powershell
.venv\Scripts\python.exe bqe_aprs_decoder.py --list-devices
.venv\Scripts\python.exe bqe_aprs_decoder.py --device "USB Audio CODEC"
.venv\Scripts\python.exe bqe_aprs_decoder.py --file "C:\recordings\aprs.wav" --no-web
.venv\Scripts\python.exe bqe_aprs_decoder.py --device "USB Audio CODEC" --bauds 1200 9600
```

`--channel 1` selects the second channel. `--output PATH` overrides `decoded/`.
`--open-browser` opens the controls; `--idle` opens controls without capture.
Uploads are limited to 512 MiB; CLI files are streamed. Controls use localhost,
matching the other packet plugins. Saved satellite packets are also available
through BQE's main web console under its Telemetry menu.

**View → APRS Spots** shows received position reports across satellites,
presets and decoded recordings. It refreshes every three seconds while open,
with a searchable history, station/object coordinates, comments, reception
source, packet details and a map of each station/object's latest active position.
Older logs work too. Messages without coordinates are retained in packet logs
but do not become spots. The view reads the newest 50 default `decoded/` logs
(last 256 KiB of each) and displays up to 200 position reports. Custom `--output`
directories are not included. Decoding times are shown; for recordings these
are processing times, with the original audio offset available in details.
New position packets also include a normalized `spot` field in their JSONL.
Packet rows and details are green after a completed send on a verified APRS-IS
connection, and yellow otherwise. The legend explains both colors. Upload
receipts persist across restarts; older packets without receipts remain yellow.
Packets received by the satellite pass tracker use bold text in their upload
color. Idle preset packets retain regular weight. This reception context is
saved with new packets; recordings and older logs without context remain regular.
Map markers use green for satellite-pass reception and blue for preset or other
reception, independently of upload status. The map legend and marker tooltips
explain the colors. Each station/object marker represents its latest visible
position packet, so its color follows that packet's reception context.
APRS-IS does not acknowledge individual packets, so green does not guarantee
that a packet has appeared on aprs.fi. Receipt files are kept locally in
`upload_receipts/` and excluded from Git and release packages.

## APRS-IS uploading (Windows and Linux)

Open **Config → APRS Upload**, or the same settings in
the APRS decoder window. The callsign comes from `bqe_config/my_qth.yaml`:
only `my_callsign` is used. There is no separate callsign setting to maintain.

Enter your numeric **APRS-IS passcode**, check Enable, and save. An aprs.fi
website password cannot authenticate APRS-IS; browser saved credentials are
not accessed. The default server is `rotate.aprs2.net:14580`. Start live APRS
reception and check for a verified connection and the Sent counter. Settings
changes take effect during live reception. Uploading is disabled by default.

After verified login, the live uploader sends a station position beacon using
`my_callsign`, `my_latitude` and `my_longitude` from `my_qth.yaml`, then repeats
every 30 minutes. It uses the IGate map symbol and an Internet-only TCPIP path.
Beacon count and last-send time appear in Config → APRS Upload, separately from
forwarded packet counts. Reconnecting does not restart the interval; a changed
QTH position triggers a fresh beacon. A new decoder session sends an initial
beacon. Missing or invalid coordinates skip beacons with a status explanation
while packet forwarding continues. Disabled uploading, recorded-file decoding,
SDR replay/demo, and unverified logins send no station beacon. No beacon is
sent after live decoding stops. As with forwarded packets, a completed send
does not guarantee immediate display on aprs.fi.

Only fresh CRC-verified APRS UI frames from live radio capture are eligible.
Recording decoding and BQE SDR file/demo sources never upload. Sound-card
capture must be connected to a live receiver; BQE cannot identify recordings
played into a sound-card input. Duplicate copies are suppressed for 30 seconds.
NOGATE/RFONLY, Internet-origin paths and generic queries are excluded. Eligible
third-party packets are unwrapped, and receive-only `qAO` routing is added.
Packets received while disconnected are discarded rather than replayed later.
One decoder per installation owns the uploader; stop it before switching to
another live decoder. Reception and local saving continue if uploading fails.

The passcode is stored locally in `plugins/bqe_aprs_decoder/aprs-is.private.json`
and is never returned by the settings API. This file is excluded from Git and
release packages. Linux creation permissions are owner-only; Windows uses the
folder's access permissions. It is not encrypted at rest, and standard APRS-IS
TCP login is not encrypted in transit. Settings can be changed only through
localhost. The server receives the decoded packet (including any position or
message it contains) and your IGate callsign. No APRS-IS traffic is sent to RF.

Protocol references: [APRS-IS connection requirements](https://www.aprs-is.net/Connecting.aspx)
and [IGate requirements](https://www.aprs-is.net/IGateDetails.aspx).

## Automatic reception

In a satellite entry or idle preset:

```yaml
decode_aprs: true
aprs_bauds: [1200]  # optionally [1200, 9600]
```

The existing `iss-aprs` entry is enabled for decoding; its frequency, scheduling
and antenna settings are retained. Confirm the active transmitter's frequency
before reception. A decoder setting does not enable automatic scheduling.
Use the same configuration for other APRS satellites or terrestrial presets.
No helper-program command is needed.

The plugin uses `radio_soundcard` from the selected rig configuration, or the
BQE SDR bridge for an SDR receiver. It starts after tuning and stops when a
pass ends, a preset changes, or the owner exits. Restart BQE after installing
to load the new lifecycle hooks. Launchers and BQE prefer the plugin's `.venv`.

1200 baud works with ordinary FM speaker audio; keep squelch open and disable
speech noise suppression. 9600 baud needs flat discriminator/DATA audio,
preferably at 48 kHz. Rates below three samples per symbol are skipped and
reported. Tune/Doppler-correct the radio normally; this plugin consumes audio,
not raw I/Q. HF 300-baud AFSK, LoRa APRS and APRS-IS input are not supported.

## Packets and storage

AX.25 frames must pass CRC-16/X-25 verification. Several symbol-clock hypotheses
are deduplicated without discarding later retransmissions. JSONL contains the
decoded fields and complete frame/payload hex; KISS retains AX.25 bytes without
FCS. Files are uniquely named and flushed as packets arrive.

APRS UI/PID 0xF0 frames are interpreted with aprslib: normal and compressed
positions, Mic-E, objects, messages/acknowledgments, status, weather, telemetry
definitions and third-party reports. Original `T#` telemetry reports are also
parsed as raw analog counts and digital bits. Station-specific equations are
not applied; configuration packets remain available. Parsed aprslib numeric
fields use its metric conventions; raw packet text is always retained.
Unsupported formats (including item reports and raw GPS) or malformed payloads
remain raw AX.25 packets with a parse explanation instead of being discarded.
Digipeater repeated flags are retained as `*` in the path/TNC2 representation.

The source callsign is the packet originator, which can be a ground station
relayed by a satellite. No satellite identity is inferred from that callsign
or from a recording name. BQE's archive associates packets with the configured
input source. `audio_seconds` marks packet reception within the audio, while
`decoded_at` is the processing timestamp.

## Verification

From the repository root:

```powershell
plugins\bqe_aprs_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_aprs_decoder/tests -v
plugins\bqe_aprs_decoder\.venv\Scripts\python.exe -m unittest tests.test_preset_aprs -v
```

Tests generate independent Bell 202 audio and CRCs, with noise, frequency/sample
clock offsets, both polarities and arbitrary block boundaries; check optional
G3RUH, parser formats, rejected CRCs, repeats, uploads, stereo selection,
JSONL/KISS output, archive lookup, managed shutdown and preset/pass handoff.
Help → Environment Diagnostics includes offline APRS self-tests. These tests
do not replace validation against an actual on-air recording.

See [THIRD_PARTY.md](THIRD_PARTY.md) for protocol and dependency references.
