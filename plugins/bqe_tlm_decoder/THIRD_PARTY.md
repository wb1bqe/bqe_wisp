# Protocol references and attribution

This plugin is distributed under GPL-3.0-or-later (see LICENSE).

- USP framing, PLS constants, and independent test vectors:
  gr-satellites, Copyright 2021 Daniel Estevez, GPL-3.0-or-later.
  https://github.com/daniestevez/gr-satellites/blob/main/python/components/deframers/usp_deframer.py
  https://github.com/daniestevez/gr-satellites/blob/main/python/qa_usp_deframer.py
  `tests/usp_vectors.json` is extracted from that test file.
- UMKA-1 supported modes:
  https://github.com/daniestevez/gr-satellites/blob/main/python/satyaml/UmKA-1.yml
- CCSDS dual-basis conversion constants: Phil Karn, KA9Q, 2002, libfec,
  GNU LGPL (compatible with this plugin's GPL distribution).
  https://github.com/quiet/libfec/blob/master/gen_ccsds_tal.c
- SPUTNIX telemetry field descriptions: SatsDecoder, Alexander Baskikh, MIT.
  https://github.com/baskiton/SatsDecoder/blob/master/SatsDecoder/systems/usp.py

The software dependencies are installed separately under their respective licenses.

MIT permission notice for SatsDecoder-derived field descriptions:

Copyright (c) Alexander Baskikh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
