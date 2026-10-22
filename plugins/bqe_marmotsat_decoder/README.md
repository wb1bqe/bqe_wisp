# BQE MARMOTSat UHF decoder

Receive-only decoder for the published MARMOTSat / ORCASat UHF TT&C scheme:
**436.125 MHz, uncoded 2-FSK, 10.17 kbps, 6.18 kHz deviation, PN9 whitening**.
Supports live sound-card / BQE SDR discriminator audio and WAV, FLAC, OGG or MP3
recordings. WAV/FLAC are preferred. This plugin does not decode the VHF CW beacon,
Greencube-style digipeater, DVB-S2 or I/Q files.

## Start

On Windows, run `start_windows.bat` in this directory and open
http://127.0.0.1:8774. On Linux, run `sh start_linux.sh`.
The launchers install dependencies into a local `.venv` on first use; Python
3.10+ and internet are required for that installation. Windows dependencies
have already been installed on this workstation. Keep this plugin inside the
BQE repository: it reuses BQE's audio lifecycle, HTTP handler and symbol clock.

Choose an input and **Start live audio**, or select a recording and **Decode file**.
The channel number starts at zero. Stop a running job before changing sources.
The controls show CRC-validated packets and diagnostics. Click a packet to see
its header, raw frame and payload. All validated packets are saved in `decoded/`
as JSONL, including source label, reception-relative audio time, flags, sequence,
system, command, hardware ID and CRC result. Nothing is transmitted.

From this plugin directory on Windows:

```powershell
.venv\Scripts\python.exe bqe_marmotsat_decoder.py --list-devices
.venv\Scripts\python.exe bqe_marmotsat_decoder.py --device "USB Audio CODEC"
.venv\Scripts\python.exe bqe_marmotsat_decoder.py --file "C:\recordings\marmotsat.wav" --no-web
```

`--channel 1` selects the second channel. `--output PATH` chooses the JSONL
directory. `--baud` allows adjustment within 10000–10350 symbols/s; default 10170.
`--sync-word D391D391` changes the 32-bit sync if a verified radio configuration
requires it. The receiver handles either discriminator polarity automatically.
Web uploads are limited to 512 MiB; command-line file decoding streams larger files.
`--idle` starts only the controls. `--open-browser` explicitly opens them.

## Audio and availability

Use **flat FM discriminator or DATA/9600-baud audio**, preferably sampled at
48 kHz or higher. Set RF bandwidth to approximately 25 kHz or wider as needed
for frequency error; disable squelch, de-emphasis, noise suppression and speech
processing. Ordinary narrow speaker audio is unsuitable, even if packets are
audible. Rates below three samples per symbol are rejected.

UVic says UHF traffic is normally active only during contacts with the dedicated
Victoria, BC ground station. The currently advertised amateur CW beacon uses
145.875 MHz; this UHF decoder does not apply to that signal. Neither 437.443 MHz
nor 447.443 MHz is the mission's published UHF TT&C frequency.

## Automatic BQE reception

After installing the plugin dependencies, add these keys to a suitable satellite
entry or idle preset (retain its other required BQE settings):

```yaml
downlink_frequency_mhz: '436.125'
downlink_mode: 'FM'
bandwidth: '25000'
decode_marmotsat_telemetry: true
decode_telemetry: false
decode_sstv_images: false
```

The plugin takes its input from `radio_soundcard` in `bqe_config/my_rig.yaml`, or
the BQE SDR bridge for SDR reception. Start/stop and parent-process shutdown use
the same managed lifecycle as the other decoders. Automatic reception is opt-in;
existing satellite/preset entries have not been changed. Restart BQE to load the
new integration. The chosen receiver must actually support the requested bandwidth.

## Implemented protocol and validation limits

Bits are packed MSB-first following sync `D3 91 D3 91`. PN9 resets to all ones
at the length byte and covers length, header, payload, hardware ID and CRC.
The one-byte length excludes itself. Frames contain:

`length | flags | sequence (2 LE) | system | command | payload | HWID (2 LE) | CRC (2 LE)`

The software CRC includes the length byte, uses polynomial `0x8005`, initial
value `0xFFFF`, no reflection and no final XOR. A packet is emitted only when
its length and CRC are valid. This is OpenLST software CRC, not the CC1110
hardware CRC framing mode. No FEC is applied, matching UVic's uncoded mode.
Candidates are bounded; a damaged length does not prevent searching for later
sync words. Multiple symbol-clock phases improve acquisition, with duplicates
removed only within the same packet time window.

**Tested with independent reference vectors and synthetic discriminator audio;
not yet verified against an over-the-air MARMOTSat UHF capture.** The sync and
CRC details follow the publicly available ORCASat/OpenLST implementation, which
UVic identifies as the basis of the shared scheme. No MARMOTSat-specific payload
schema is published on the referenced mission page. Payloads therefore remain
raw instead of being presented as guessed temperatures, voltages or other
engineering values. A CRC-valid OpenLST frame alone does not prove satellite
identity; source labels describe the selected receiver configuration.

Run tests from the BQE repository root:

```powershell
plugins\bqe_marmotsat_decoder\.venv\Scripts\python.exe -m unittest discover -s plugins/bqe_marmotsat_decoder/tests -v
```

## Sources and attribution

- [UVic mission frequencies, emission and packet structure](https://www.propagationlab.ca/satellite/).
- [ORCASat SDR ground-station decoder](https://github.com/ethan-ac/ORCASat-TTC-SDR-ground-station/blob/master/gr-pduencode/examples/decoder_hier.grc), GPL-3.0: sync and dewhitening flow, OpenLST CRC byte handling.
- [OpenLST radio firmware](https://github.com/OpenLST/openlst/blob/master/open-lst/common/radio.c), Planet Labs, GPL-3.0-or-later: length and software CRC coverage.
- [gr-openlst](https://github.com/rzimmerman/gr-openlst), Robert Zimmerman, GPL-3.0-or-later: reference CRC, PN9 and RF framing algorithms used to cross-check the implementation and generate fixed synthetic vectors.
- [TI CC1110 datasheet](https://www.ti.com/lit/gpn/cc1110-cc1111): packet engine and whitening.

New decoder code is GPL-3.0-or-later. Test vectors are synthetic, not spacecraft telemetry.
