# Third-party dependencies

Installed by pip; source is not vendored here:

- `sstv==0.1.0`: https://github.com/unexcellent/sstv-py (MIT), wrapping
  https://github.com/unexcellent/sstv (MIT). Performs mode detection and decoding.
- NumPy: BSD-3-Clause. Audio buffers.
- Pillow: HPND. JPEG output.
- SoundFile: BSD-3-Clause, using libsndfile (LGPL-2.1-or-later). MP3/WAV input.
- SoundCard: BSD-3-Clause. Audio input and loopback capture.
- PyYAML: MIT. Existing BQE configuration.
- PySSTV: MIT. Test-only independent signal generation.

The plugin entry point and HTTP controls adapt the repository's existing
GPL-3.0-or-later BQE telemetry plugin. See `LICENSE` for GPLv3 terms.
