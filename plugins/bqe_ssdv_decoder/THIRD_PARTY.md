# Protocol references and attribution

The new receiver code is GPL-3.0-or-later and reuses this repository's
GPL-3.0-or-later telemetry `SymbolClock` and KISS encoder. It also reuses the
SSTV session lifecycle and shared `plugins/audio_devices.py` capture support.
No upstream executable or GNU Radio runtime is bundled.

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
