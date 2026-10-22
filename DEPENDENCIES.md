# BQE WISP installation and beta distribution

## Supported environment

Use Python 3.10 or newer. Python 3.13 is the recommended beta baseline;
Windows requires x64 Python to match the supplied native engines. The SSTV
package also requires Python >=3.10. Ubuntu/Debian Linux is supported by the
shell installer; it is not a macOS installer. Run BQE as your regular desktop
user, not root.

Run `install_dependencies.bat` on Windows or `bash install_dependencies.sh`
on Ubuntu/Debian. Both create/update the project `.venv`, install the root
`requirements.txt`, run `pip check`, and run `check_dependencies.py`.
Both installers check the complete dependency file list with
`check_dependencies.py --files-only` before creating the virtual environment
or installing Python packages. This preflight uses only the standard library.
The Windows installer also runs `patch_soundcard.py` after pip. This applies
the reviewed SoundCard 0.4.6 COM initialization fix without importing audio
backends. It is safe to rerun and rejects other versions for review. After
manual pip installs/upgrades (including plugin environments), run
`<environment-python> patch_soundcard.py` from the project root on Windows.
The Windows builder applies the same fix to its copied private runtime before
validation and hashing; prepared input runtimes are left unchanged.
Start BQE with `.venv\Scripts\python.exe bqe_wisp.py` on Windows or
`.venv/bin/python bqe_wisp.py` on Linux. No separate plugin setup is required
for managed launches when plugin-local virtual environments are absent.

The root requirements include every plugin's requirements file, so distribute
those files too. Shared constraints are resolved together. Python packages are
installed from PyPI for source installations. Single-file executables bundle
the resolved packages in their private Python runtime. The installers require a binary
wheel for `sstv==0.1.0` (the unexcellent/sstv-py Rust-backed library), avoiding an
unexpected Rust compiler requirement. Its published wheels cover Windows x64
and Linux x64/ARM64. Other architectures need a separate source-build setup.

## Python package inventory

| Packages | Used by |
| --- | --- |
| requests, skyfield, PyYAML, numpy, packaging | Core tracking, configuration, scheduling |
| SoundCard, lameenc; sounddevice on Windows | Audio capture, playback/streaming and MP3 recording |
| scipy, soundfile, reedsolo | Telemetry and image DSP, recording input and error correction |
| Pillow, sstv==0.1.0 | Image handling and analog SSTV decoding |
| aprslib==0.7.2 | APRS packet fields; Bell 202 audio decoding uses NumPy/SciPy |
| soxr | HF image audio resampling |
| piper-tts==1.8.0, ONNX Runtime | Local pass announcements with the bundled Cori voice |
| faster-whisper, CTranslate2, PyAV, ONNX Runtime | Local multilingual recording transcription |

Speech transcription defaults to CPU on Windows and Linux. Its first use downloads
`small.en` for English or the multilingual `small` model for other languages into `plugins/bqe_speech_decoder/models`; no audio is uploaded.
Use that plugin's Windows/Linux launcher with `--check` or `--download-model` to
prepare an existing installation. See `plugins/bqe_speech_decoder/README.md` for
offline use, Python requirements, optional NVIDIA GPU support and radio accuracy.

`argparse` and `datetime` are standard-library modules and must not be installed
as third-party dependencies. Transitive packages such as CFFI are resolved by pip.
For tests only, analog SSTV has an additional `requirements-test.txt`.

Existing plugin `.venv` directories take precedence over the root environment.
Do not distribute them: they contain machine-specific interpreter paths.
For an existing installation, update a plugin environment with its own Python
and `-m pip install -r <plugin>/requirements.txt`, or rename that environment to
let managed launches use the root environment. The checker reports these overrides.

## Native components: what must ship?

None of these components downloads itself during normal decoding.

| Component | What to distribute / install | Download behavior |
| --- | --- | --- |
| `plugins/bqe_ssdv_decoder/vendor/ssdv/` | Keep the pinned C sources, headers, GPL/LGPL notices, SOURCE.json, build.py and README-BQE.md. Windows ships the source-built `ssdv.exe` with BUILD-WINDOWS.json. Linux installer verifies sources and builds `ssdv` locally. Used by SO-127 and HF FreeDV image rendering. | No automatic download; can also find `ssdv` on PATH. |
| `plugins/bqe_hf_image_decoder/native/` | Include `hamdrm.exe`, `libcodec2.dll`, `libfftw3-3.dll` for Windows; keep `rxamadrm/` sources/build files and attribution/license files. Linux installer builds HamDRM and installs system Codec2/FFTW. | No automatic download of these Windows binaries. |
| `plugins/bqe_lrpt_decoder/vendor/satdump/` | Optional to bundle. If included, keep the entire portable distribution including DLLs, resources and pipelines. Alternatively have testers run `plugins/bqe_lrpt_decoder/install_engine_windows.ps1`, or configure an installed SatDump CLI. | That explicit PowerShell installer downloads pinned SatDump 1.2.2 and verifies its SHA-256. Neither BQE nor the root installers download SatDump automatically. Linux needs a separate SatDump installation. |
| `plugins/bqe_sdr/drivers/` | Windows USB reception needs architecture-matched `rtlsdr.dll` and its dependencies (the current local set also has `pthreadVC2.dll` and `msvcr100.dll`). Linux installer installs librtlsdr and rtl-sdr. | No automatic Windows download or driver installation. |
| `plugins/bqe_sdr/drivers/vendor/` | This is an extracted driver tools/archive staging area, not a runtime search path. It may be omitted if the required DLLs are supplied in `drivers/` or installed separately. | No automatic download. |
| Hamlib | Windows users install rigctl/rigctld and all required DLLs separately or receive a complete compatible distribution. Linux installer installs libhamlib-utils and a local rigctl compatibility link. | Only the Linux installer installs it via apt. |

The Windows SDR USB driver must be configured separately (typically WinUSB).
BQE does not run Zadig or replace USB drivers. `rtl_tcp` reception does not need
a local RTL-SDR USB library. The root checker loads Codec2/RTL-SDR libraries but
does not open a receiver; executable presence does not prove all native DLLs work.

Linux audio uses libpulse and a working PulseAudio or PipeWire-Pulse desktop
session. The installer also installs libsndfile, build tools and FFTW/Codec2
development packages. An import check may warn about audio when run headless or
as root; verify capture in the regular user's desktop session.

Keep all COPYING/LICENSE, THIRD_PARTY and provenance/build documentation with
bundled components. See each vendor/native README for exact upstream versions
and source locations; a runtime binary-only folder is not a complete source
release. Check the third-party redistribution terms before packaging binaries.

## Packaging checklist

- Include application Python files, all plugin source/UI/requirements files,
  configuration/templates, these installers and `check_dependencies.py`.
- Include the native/vendor components above for features promised in the beta.
  Do not assume `git archive` contains local files: the LRPT vendor directory is
  ignored, and other native/vendor files may be untracked. Verify the final ZIP.
- Exclude `.venv`, `__pycache__`, `.uploads`, logs, recordings, personal decoded
  images/telemetry, downloaded ZIPs/installers, and native `.o` build intermediates.
  Linux native executables should be built for the target machine.
- Use sample configuration appropriate for testers. Local hardware names and
  output paths (including your SSTV gallery) need adjustment on their machines.
- Test the release in a clean folder using the root environment. Help > Environment
  Diagnostics provides additional plugin self-tests without starting a pass.

The main web map uses Leaflet from unpkg.com and OpenStreetMap tiles. Map display,
TLE updates and initial dependency installation need network access. These are
separate from the locally supplied decoder engines.

AO-123 telemetry reuses the native telemetry dependencies (NumPy, SciPy, reedsolo
and the audio libraries). GNU Radio is not required. See
`plugins/bqe_ao123_decoder/README.md` for BPSK input requirements.

## Spoken pass announcements

`program_settings` > `bqe_wisp` > `pass_announcements_enabled` defaults to
`true`. Set it to `false` and restart BQE to silence pass-start and pass-end
announcements. Scheduled passes and test passes use the same announcements;
expired/skipped passes are not announced. Speech runs in the background and
speech failures do not interrupt tracking or decoding.

Announcements use **Piper 1.8.0 with Cori (British female)** locally. Install the
root requirements, or use the isolated environment described in
`plugins/bqe_pass_voice/README.md`. The Cori model and its configuration ship in
`plugins/bqe_pass_voice/models`. No voice downloads happen during passes.

`program_settings` > `bqe_wisp` > `pass_announcements_windows_fallback` defaults
to `true`. If Piper synthesis or playback fails on Windows, BQE tries the
previous Windows System.Speech behavior (an installed, enabled en-GB female
voice). Set this flag to `false` to use Piper only. Restart BQE after changes.
If neither backend works, a warning is logged without interrupting the pass.

Windows playback uses the default system playback device, never the configured
radio soundcard. Keep the Windows default set to speakers/headphones rather
than the radio codec. Linux playback requires `paplay` or `aplay`.

Release builders must install the updated root requirements into the prepared
private Python runtime before rebuilding the executable. The release manifest
includes the voice model and attribution; project-local virtual environments
are excluded. See the voice README for licensing and verification details.

Piper keeps up to `pass_announcements_cache_size: 20` recordings under
`plugins/bqe_pass_voice/cache`, reusing them across server restarts. Set this
nonnegative integer under `program_settings` > `bqe_wisp` in general_settings.yaml.
Set it to `0` to disable caching and clear cached recordings on the next
announcement. Restart BQE after changing it. The least recently used recordings
are removed first; changed voice model/config files cause fresh synthesis.
Beginning and ending messages each count as one recording.

## Release dependency review — 2026-10-22

The current UI, settings backup, and downloaded-data backup changes require no
additional third-party packages. Core NumPy and PyYAML requirements are now
explicit in the root list as well as in the plugins that use them. All plugin
requirements are included. The existing 46 Windows package pins satisfy the
current application and SSTV diagnostic requirements; pins were not upgraded.
The dependency regression tests are included in the release allowlist.
