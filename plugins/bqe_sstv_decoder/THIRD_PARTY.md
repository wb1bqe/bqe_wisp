# Third-party dependencies

Installed by pip; source is not vendored here:

- `sstv==0.1.0`: https://github.com/unexcellent/sstv-py (MIT), wrapping
  https://github.com/unexcellent/sstv (MIT). Performs mode detection and decoding.
- NumPy: BSD-3-Clause. Audio buffers.
- SciPy: BSD-3-Clause. Audio filtering, spectral header detection, resampling,
  and phase demodulation for fade recovery.
- Pillow: HPND. JPEG output.
- SoundFile: BSD-3-Clause, using libsndfile (LGPL-2.1-or-later). MP3/WAV input.
- SoundCard: BSD-3-Clause. Audio input and loopback capture.
- PyYAML: MIT. Existing BQE configuration.
- PySSTV: MIT. Test-only independent signal generation.

The plugin entry point and HTTP controls adapt the repository's existing
GPL-3.0-or-later BQE telemetry plugin. See `LICENSE` for GPLv3 terms.

The mode timing tables and colour conversion in `recovery.py` adapt the
`sstv` crate v0.1.2's mode tables and `image.rs` conversion formulas.
Source: https://github.com/unexcellent/sstv/tree/v0.1.2/src
Their upstream license notice follows:

MIT License

Copyright (c) 2026 Tobias Klockau

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
