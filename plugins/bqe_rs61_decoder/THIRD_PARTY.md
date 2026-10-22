# Protocol sources and attribution

This GPL-3.0-or-later plugin reuses BQE's Geoscan modem, packet framing and CRC
from `bqe_ssdv_decoder`, and the telemetry audio/session/HTTP and managed receiver
infrastructure. Dependencies retain their respective licenses.

Verified on 2026-09-29:

- Geoscan operator, **3U radio protocol v2.7**, Type-I beacon byte-offset table:
  https://download.geoscan.ru/site-files/Protokol_radio_3U.pdf
- Alexander Baskikh's MIT-licensed SatsDecoder implementation, used to cross-check
  field widths, signedness and little-endian representation:
  https://github.com/baskiton/SatsDecoder/blob/master/SatsDecoder/systems/geoscan.py
  Activity flags here follow the operator's LSB-first bit numbering, not the
  MSB-first BitStruct ordering in that implementation. No source code is vendored.
- gr-satellites radio profile:
  https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/239Alferov.yml

`tests/reference.json` contains six beacon packets independently recovered from
dvdiyen's public **Alfrev SSDV 16th Aug 26.mp3** capture:
https://community.libre.space/t/239alferov-image-transmissions/15156/23
The audio is retained locally under `logs/ssdv_reference`, not bundled here.
Original capture validation and modem references: `../bqe_ssdv_decoder/THIRD_PARTY.md`.
