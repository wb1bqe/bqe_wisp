# Protocol references and attribution

The new receiver code is GPL-3.0-or-later and reuses this repository's
GPL-3.0-or-later telemetry `SymbolClock` and KISS encoder. It also reuses the
SSTV session lifecycle and shared `plugins/audio_devices.py` capture support.
The SO-127 profile bundles the attributed SSDV utility (see vendor/ssdv/README-BQE.md).
No GNU Radio runtime is bundled.

Protocol parameters and field layouts were checked on 2026-09-26 against:

- Geoscan, **3U radio protocol v2.7**, operator documentation:
  https://download.geoscan.ru/site-files/Protokol_radio_3U.pdf
- Daniel Estevez, **gr-satellites** (GPL-3.0-or-later), RS61 radio profile,
  synchronization, PN9 whitening, and CRC parameters:
  https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/239Alferov.yml
  https://github.com/daniestevez/gr-satellites/blob/main/python/components/deframers/geoscan_deframer.py
  https://github.com/daniestevez/gr-satellites/blob/main/python/hier/pn9_scrambler.py
  https://github.com/daniestevez/gr-satellites/blob/main/python/crcs.py
- GNU Radio (GPL-3.0), LFSR and additive scrambler bit-order reference:
  https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/lfsr.h
  https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/additive_scrambler_impl.cc
- Alexander Baskikh, **SatsDecoder**, `systems/geoscan.py` (MIT), satellite ID 9,
  image marker, file number, offset, and 54-byte image-fragment layout:
  https://github.com/baskiton/SatsDecoder/blob/main/SatsDecoder/systems/geoscan.py

Validation recording: dvdiyen's publicly shared **Alfrev SSDV 16th Aug 26.mp3**,
linked from https://community.libre.space/t/239alferov-image-transmissions/15156/23.
The recording is downloaded locally under `logs/ssdv_reference` for validation;
it is not bundled as a plugin asset. Small decoded packet fixtures cite that source.
Synthetic tests additionally verify complete JPEG recovery, both polarities,
sample rates, malformed frames, missing data, and lifecycle behavior.

Dependencies retain their respective licenses; see installed package metadata.


## UMKA-1 / USP and general Geoscan (2026-09-27)

The USP modem reuses `plugins/bqe_tlm_decoder/dsp.py` and its FEC implementation;
see that plugin's THIRD_PARTY.md for the gr-satellites/libfec references.
File-transfer layouts were checked against SPUTNIX's operator tooling and
Alexander Baskikh's MIT-licensed SatsDecoder implementation:

- https://github.com/sputnixru/SX-Files
- https://github.com/baskiton/SatsDecoder/blob/master/SatsDecoder/systems/usp.py
- Operator description of UHF image transfers:
  https://community.libre.space/t/umka-1-telescope-satellite-mission/10572/546
- `tests/umka_reference.kss`: DL7NDR's public RS40S packet capture, 2025-04-14
  19:18:35 UTC, from post 521 (the attachment's .zip suffix is misleading: it is KISS).
  https://community.libre.space/t/umka-1-telescope-satellite-mission/10572/521
  https://community.libre.space/uploads/short-url/xrUoJ1bDDl6YlsE2ebIGUD6pknZ.zip
  It reconstructs UMK_6.jpg, 27,377 bytes, 480×480 pixels, from 147 image fragments.

Geoscan v1 layouts, command IDs, 24-bit base addressing and v2 satellite IDs were
checked against the same SatsDecoder geoscan.py source linked above. Frame sizes
66/74 (including the radio CRC), synchronization and whitening follow the
referenced gr-satellites geoscan_deframer. The new general profile does not claim
support for Geoscan convolutionally coded modes.
