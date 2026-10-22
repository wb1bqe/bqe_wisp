# References and dependencies

- APRS payload specification: [APRS 1.0.1](https://www.aprs.org/doc/APRS101.PDF).
- [aprslib 0.7.2](https://github.com/rossengeorgiev/aprs-python), Rossen Georgiev,
  GPL-2.0-or-later (see source license headers). Installed as a dependency,
  not vendored. Uses its parser only, not its APRS-IS client.
- [Supported parser formats](https://aprs-python.readthedocs.io/en/stable/parse_formats.html).
- Bell 202 AFSK uses noncoherent quadrature tone correlations, NRZI and AX.25
  HDLC framing. The modem is implemented here; no external TNC executable is
  required. Independent tests construct continuous-phase audio and reflected
  CCITT CRCs using Python's `binascii`.
- HDLC/FCS, G3RUH, symbol timing, capture and HTTP lifecycle reuse BQE's
  `bqe_tlm_decoder`; see that plugin's THIRD_PARTY.md and LICENSE.
- NumPy and SciPy are installed dependencies under their respective BSD licenses.

This plugin is GPL-3.0-or-later; see LICENSE.
