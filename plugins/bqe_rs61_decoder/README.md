# RS-61 / 239ALFEROV telemetry

Decodes RS61S Type-I housekeeping beacons on 436.270 MHz using the existing
9600-baud Geoscan modem. The `rs61` satellite entry enables
`decode_rs61_telemetry: true` alongside `decode_ssdv_images: true`.
The telemetry plugin starts after tuning and stops at pass end. It uses the
configured radio sound card or BQE SDR audio bridge. Restart BQE after installation
to load the new preset lifecycle integration.

The console defaults to http://127.0.0.1:8780 and shows the selected input,
audio level, packet counts, decoded fields and raw data. `start_windows.bat`
or `start_linux.sh` opens idle controls and installs dependencies on first use.

## Decoded fields

The parser follows Geoscan 3U radio protocol v2.7: spacecraft UTC time, raw EPS
mode, platform and solar currents (mA), battery and VBUS voltages (mV), signed
battery/panel temperatures (C), subsystem activity flags, GNSS satellite count,
camera file count, signed RSSI (dBm), transmitted packet count and received QSOs.
Counter rollover, reserved bytes and undocumented enum meanings are not guessed.
RSSI is reported by the spacecraft; it is not your ground receiver's signal level.

The operator assigns activity bit 0 to flywheels and bit 7 to panel Y-; this
implementation follows that order rather than the reversed ordering in some
third-party parsers. Raw bitfields and complete raw packets remain available.

Only 72-byte BEACON packets from RS61S with the published UI/PID header are
accepted. Both the null padding observed in real RS61S packets and standard
space padding are supported. Beacon ID 1 is parsed; other IDs remain raw.
Image and foreign packets are counted but not presented as RS61 telemetry.
The modem supports the uncoded Geoscan mode, not convolutionally coded variants.

## Storage and replay

Each session writes a unique timestamped JSONL file under
`plugins/bqe_rs61_decoder/decoded/`, with decoded fields and full raw packet hex.
Live and audio-file sessions also write a KISS log of accepted beacons.
The image plugin independently retains its complete Geoscan packet log.
The console displays the output paths. `--output` selects another directory.

WAV, FLAC, OGG, MP3, KISS and KSS files work from the console or CLI:

```
start_windows.bat --file recording.wav --no-web
start_windows.bat --file ssdv-packets-example.kiss --no-web
```

KISS imports do not contain the original radio CRC or audio timestamps. Their
records explicitly say `KISS import: original CRC not rechecked`, and the console
shows `Imported` instead of an audio time. Audio packets require a valid radio CRC.

Use flat FM discriminator/9600 DATA audio with squelch open. The configured
RS61 SDR audio gain is 0.15 to avoid clipping. The image decoder remains enabled.

## Verification

Tests cover six independently recorded beacon fixtures, identities, null/space
padding, unknown beacon IDs, signed fields, activity bit order, CRC rejection,
both audio polarities, 44.1/48 kHz input, chunk boundaries, KISS upload, selected
device metadata, managed shutdown and preset source routing.

From the repository root:

```
plugins/bqe_rs61_decoder/.venv/Scripts/python.exe -m unittest plugins.bqe_rs61_decoder.tests.test_decoder plugins.bqe_rs61_decoder.tests.test_integration -v
python -m unittest tests.test_preset_rs61 -v
```

Protocol and reference capture attribution are in `THIRD_PARTY.md`.

Full reference audio replay (607.478 seconds) recovered 625 CRC-valid Geoscan
packets, including six Type-I RS61S beacons. KISS replay recovered the same six
beacons. Verification outputs are in `logs/rs61-verification/`.
