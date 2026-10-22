# Standalone Linux build

`BQE_Wisp` is one Linux x86-64 executable containing the application, a private
CPython runtime, Python packages, SSDV, HamDRM, Codec2, FFTW, and PulseAudio client
libraries. Target: glibc 2.31 or newer (Ubuntu 20.04+, Debian 11+, and compatible
distributions). It is not an ARM/Raspberry Pi or Alpine/musl build.

Copy the executable to Linux and run:

```sh
chmod +x BQE_Wisp
./BQE_Wisp --dir "./My BQE Station"
```

Open `http://localhost:8028` in your browser. Ctrl+C shuts down the application.
No Python installation or runtime download is required.

On first launch **on the target computer**, pass `--dir` with a full path
(e.g. `"/home/me/My BQE Station"`) or a relative path (e.g. `"./My BQE Station"`).
Relative paths resolve from the current working directory, not the executable's
folder. The chosen folder must be new or empty. It contains `bqe_wisp/` (settings,
recordings, logs) and `_python/`. After successful extraction the absolute
location is remembered for this build in a small record under
`${XDG_DATA_HOME:-$HOME/.local/share}/BQE_Wisp/installations`.
Later launches can omit `--dir`, including from a different working directory.
Without `--dir` on first run, it reports the required option and exits without
extracting. This is command-line selection; no folder dialog is required.
Allow approximately 750 MB free for the installation and temporary launcher
extraction, in addition to recordings. A writable, executable temporary
filesystem is required by the single-file launcher.

Options:

- `--dir "/path/with spaces/folder"` (also `--data-dir`): choose an initially empty persistent directory on the target computer (full or relative path).
- `--extract-only`: unpack without starting the application.
- `--check`: verify Python imports and report native component availability.
- `--smoke-test`: verify the web console and private helper interpreter offline.
- `--self-test`: run the application's offline decoder diagnostics.

Hamlib (`rigctl`/`rigctld`), SatDump, and RTL-SDR USB libraries/drivers remain
external prerequisites for their respective features. Audio capture requires
a working desktop PulseAudio or PipeWire-Pulse session. Headless/WSL checks
can report unavailable audio even when client libraries are bundled. Hardware
reception is not covered by the offline tests. See the extracted
`bqe_wisp/DEPENDENCIES.md` for feature-specific setup.

Configuration follows `RELEASE.md`: current satellites.yaml is preserved;
general, rig, and QTH settings use release defaults. Configure your station
and update orbital elements before generating a schedule.

## Rebuild on Linux x86-64

The unified entry point is `python3 mkdist.py --output-dir "/path/to/New beta"`.
On Windows, the same command with a Windows output path builds both platforms
using WSL for Linux. See `RELEASE.md` for prepared runtime/tooling options.
The Linux image is written to `bqe_wisp_linux/BQE_Wisp` inside the release directory.
`mkdist.py` runs the native preparation below on private build copies.

Use a relocatable CPython 3.13 runtime from the official
[python-build-standalone releases](https://github.com/astral-sh/python-build-standalone/releases).
This build uses CPython 3.13.16, release 20261003. Keep its runtime notices.
Install the root requirements and `plugins/bqe_sstv_decoder/requirements-test.txt`
into that private runtime. The output build JSON records resolved versions.
Use a separate tooling venv with `pyinstaller==6.22.3`. Native compilation needs
GCC, make, CMake, FFTW development libraries, and Linux PulseAudio client libraries.

1. Run `mkdist.py` to create a manifest-controlled source release.
2. Run `prepare_native.py RELEASE/bqe_wisp --runtime RUNTIME --codec2-source CODEC2_SOURCE --work NEW_NATIVE_WORK`.
   Use Codec2 commit `310777b1c6f1af0bc7c72f5b32f80f6fd9136962` (version 1.2.0).
   It prints a prepared release path and adds native shared libraries to the runtime.
3. With the tooling interpreter run `build.py PREPARED_RELEASE --runtime RUNTIME --work NEW_BUILD_WORK --output OUTPUT`.
4. Run `verify.py OUTPUT/BQE_Wisp --test-dir NEW_TEST_FOLDER`.

Build on the oldest glibc version to support; newer build hosts can increase
the minimum requirement. `BQE_Wisp.build.json` is a build record, not a required
runtime companion. Builds refuse to overwrite an existing executable.


The source inventory was reviewed on 2026-10-22. Run
`python3 check_dependencies.py --files-only` and `python3 mkdist.py --check`
before creating an image. Current UI and backup changes introduce no new
third-party dependencies. Install the root and SSTV diagnostic requirements
in the Linux runtime as above; the Windows lock is platform-specific.

The unified `mkdist.py` build also creates `BQE_Wisp_linux.zip` beside the
image folder. Extracting it places `BQE_Wisp`, `BQE_Wisp.build.json`, and
`BQE_Wisp_Quick_Start_Guide.pdf` together at the top level. This ZIP step runs
in the unified build, after the platform packager completes.
