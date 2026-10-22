# Standalone Windows build

`BQE_Wisp.exe` is a single-file, 64-bit Windows launcher containing the exact
selected source release, CPython 3.13.5, and all resolved Python dependencies.
Python does not need to be installed on the destination computer. PySSTV is
included for the application's offline SSTV diagnostic fixtures as well.
Piper and the Cori British English voice are included when the private runtime
has been prepared with the current dependency lock. The Quick Start Guide is
extracted to `bqe_wisp/documentation/BQE_Wisp_Quick_Start_Guide.pdf`.

On first run **on the target computer**, running `BQE_Wisp.exe` without
`--dir` installs to `C:\bqe` (`C:\bqe\bqe_wisp` plus `C:\bqe\_python`).
The successful installation location is remembered for later launches.
To choose a different parent folder, use:

```powershell
.\BQE_Wisp.exe --dir "C:\My BQE Station"
# Or a relative path, resolved from the current working directory:
.\BQE_Wisp.exe --dir ".\My BQE Station"
```

The parent folder may already contain unrelated files and folders. For example,
`BQE_Wisp.exe --dir "C:\bqe"` creates `C:\bqe\bqe_wisp` and `C:\bqe\_python`
without changing other contents. If `bqe_wisp` already exists outside a recognized
installation of this build, rename the old folder (for example `bqe_wisp_old`)
before rerunning. An existing `_python` or another build's bundle metadata must
also be moved aside, or choose a different parent. Valid installations of the
same build reopen normally. The executable verifies and extracts there, then
remembers the absolute location for this build. Later launches can omit
`--dir`, even from a different working directory. The chosen folder contains
`bqe_wisp/` (editable configuration, recordings, logs, assets) and `_python/`.
A small location record is stored under `%LOCALAPPDATA%\BQE_Wisp\installations`.
Without `--dir`, a remembered installation takes priority over `C:\bqe`.
A missing or invalid remembered installation produces an error instead of
silently creating another installation. Existing-folder protections also apply
to the first-run default. No folder dialog is required.
Subsequent runs preserve existing files, including user changes to satellites.yaml.
Keep the extracted folder if you want to keep your settings and recordings.
Run the executable normally and use the console's displayed web address
(the supplied default is `http://localhost:8028`). Closing the app's console
stops the application; Ctrl+C lets it perform its normal shutdown.

Options:

- `--dir "C:\path\to\new-folder"` (also `--data-dir`): choose a persistent installation folder on the target computer (full or relative path).
  Unrelated parent-folder contents are preserved; existing application/runtime paths are protected.
- `--check`: verify Python imports and report native component availability.
- `--self-test`: run the application's offline decoder diagnostic suites.
- `--smoke-test`: test dependency imports, web serving on a temporary port, and
  helper interpreter selection without opening hardware or starting a schedule.
- `--extract-only`: prepare files without launching the application.

This preserves the selected release's native-component scope: SSDV, HamDRM,
Codec2, and FFTW are included. Hamlib, SatDump, and RTL-SDR USB libraries/OS
drivers remain separate prerequisites for their respective features, as
described in the release's DEPENDENCIES.md. No runtime dependencies are downloaded
automatically. The bundled interpreter is placed first on the child process
PATH so the release's `python` setting and plugin subprocesses use it.

## Rebuild

The unified entry point is `python mkdist.py --output-dir "C:\BQE Releases\New beta"`.
It builds Windows and Linux (via WSL) by default; add `--target windows` for
Windows alone. See `RELEASE.md` for toolchain paths and overrides. The manual
steps below describe preparing the runtime and invoking the lower-level builder.

Run from the repository using 64-bit Python 3.13. Use fresh work/output folders
for a new build; existing output is never overwritten. The builder does not
modify the selected source release. `BQE_Wisp.build.json` records the packaged
file hashes, exact Python package versions, and executable SHA-256. It is a
build record, not a file the executable needs alongside it.

1. Create build tooling with `python -m venv .build_windows/tooling` and install
   `pyinstaller==6.22.3` in that environment.
2. Download the official
   [Python 3.13.5 x64 embeddable ZIP](https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-amd64.zip)
   and extract it to `.build_windows/payload/_python`. The published archive
   MD5 is `370a345dbea8bbc1830a2385f24632d2`.
3. Use the tooling interpreter's pip with `--no-compile --only-binary=sstv
   --target .build_windows/payload/_python/Lib/site-packages`, installing the
   selected release's `requirements.txt` and its
   `plugins/bqe_sstv_decoder/requirements-test.txt`. Use `--report` to retain
   the package download URLs/hashes. For repeatable dependency versions, pin
   the versions recorded in the prior build JSON. The current
   `requirements-lock-20261003.txt` was refreshed on 2026-10-11 and contains
   46 exact versions, including Piper, ONNX Runtime, and speech transcription
   dependencies. It can replace the two requirements inputs when recreating
   the private runtime; its filename is retained for existing build scripts.

   Refresh an existing prepared Windows runtime from the checkout root before
   rebuilding (the builder only copies it; editing the lock alone does not
   update installed packages):

   ```powershell
   .build_windows/tooling/Scripts/python.exe -m pip install --upgrade --no-compile --only-binary=:all: --target .build_windows/payload/_python/Lib/site-packages -r packaging_windows/requirements-lock-20261003.txt
   ```

   If using a custom runtime, substitute its `Lib/site-packages` directory.
   The builder applies `patch_soundcard.py` to the copied runtime before
   validation and hashing. After manual dependency updates in an environment
   used directly, run its Python with `patch_soundcard.py` as well.
4. Run the tooling interpreter with:

   ```text
   packaging_windows/build.py <release>/bqe_wisp --runtime .build_windows/payload/_python --work .build_windows/new-build --output <release>/bqe_wisp_windows
   ```

5. Run the resulting executable's `--smoke-test` and `--self-test` with a new
   data directory and a PATH containing only Windows system directories.
   Inspect diagnostic results as well as the exit status: the application's
   existing `--self-test` report can contain warnings without a failing exit.
   `verify.py <exe> --test-dir <new-folder> --source <release>/bqe_wisp`
   automates these checks, verifies the absence of host package search paths,
   compares the shipped source hashes, and exercises normal startup, graceful
   shutdown, and preservation of edited settings. It edits only its disposable
   extracted test copy.

The private interpreter avoids frozen `sys.executable` recursion for plugin
workers. Extraction persists outside PyInstaller's temporary directory so
configuration and output survive exit. The launcher clears PyInstaller's DLL
search override before invoking the real bundled interpreter; see
[PyInstaller's subprocess guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)
and [Python's embeddable runtime documentation](https://docs.python.org/3.13/using/windows.html#the-embeddable-package).


Before the next image build, run `python check_dependencies.py --files-only`
and `python mkdist.py --check` from the source checkout. The 2026-10-22 review
confirmed all 46 existing lock pins satisfy the current requirements, including
PyAV <17. The release includes the dependency regression test. Recent UI and
backup changes need no additional packages; the builder still validates the
actual copied runtime before packaging.

The unified `mkdist.py` build also creates `BQE_Wisp_windows.zip` beside the
image folder. Extracting it places `BQE_Wisp.exe`, `BQE_Wisp.build.json`, and
`BQE_Wisp_Quick_Start_Guide.pdf` together at the top level. This ZIP step runs
in the unified build, after the platform packager completes.
