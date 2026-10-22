# Building and installing BQE Wisp v0.7beta5

Run `python mkdist.py --output-dir "C:\BQE Releases\Beta 1"` from the Windows source
checkout to create both single-file executables using the prepared Windows
toolchain and Linux toolchain in WSL. The quoted `--output-dir` is the exact **new**
release directory; spaces are supported. Outputs are `bqe_wisp_windows/BQE_Wisp.exe`
and `bqe_wisp_linux/BQE_Wisp`, plus the source copy in `bqe_wisp/` and build records.
The build also creates `BQE_Wisp_windows.zip` and `BQE_Wisp_linux.zip` in the
release directory. Each ZIP contains its image folder's files (including the
executable and build JSON) and `BQE_Wisp_Quick_Start_Guide.pdf` from the source
release's `documentation/` folder. All ZIP entries are at the top level, with
no directory structure. Duplicate filenames are rejected rather than silently
overwritten. The Linux executable remains `BQE_Wisp` and carries executable
permissions in the ZIP; Windows uses `BQE_Wisp.exe`. Existing ZIPs are never
overwritten. Source-only builds do not create image ZIPs.
Windows build work is in `.build/`; Linux scratch builds stay on the Linux
filesystem for reliable Unix permissions and faster copying. Only the
executable needs to be given to an end user.

On Linux, `python3 mkdist.py --output-dir "/home/me/BQE Releases/Beta 1"` builds the
Linux executable. Windows binaries require Windows; Linux binaries require
x86-64 Linux (or WSL). No cross-compilation is attempted. On Windows the
default target is `both`; on Linux it is `linux`. Use `--target windows`,
`--target linux`, or `--target both` to choose explicitly.

Use `--source-only` for the previous source-release workflow (Python 3.10+,
stdlib only). Without `--output-dir`, output goes to `dist_YYYYMMDDTHHMMSSZ/` in the
checkout, using UTC time.
`python mkdist.py --check` checks all inputs without copying anything.
`--datestamp YYYYMMDD` selects a shorter date explicitly. Existing output is
never overwritten. A failed build removes only its own temporary staging tree.
If executable packaging fails, the published source release and build work
are retained for diagnosis; the command exits nonzero. Use a fresh output
directory for another complete build.

### Prepared build tools

The executable workflow reuses the existing builders and does not download or
install packages automatically. Prepare dependencies as described in
`packaging_windows/README.md` and `packaging_linux/README.md` before a fresh setup.
Refresh an existing runtime whenever requirements or the dependency lock change;
otherwise a rebuild still packages its old installed dependencies. The Windows
README includes the refresh command. For Linux, install the current root
requirements and SSTV test requirements into the selected private runtime.
Build preflight validates all plugin requirement versions, transitive
dependencies, and decoder imports (including speech and Piper). The platform
packagers repeat this check in the copied runtime before creating an executable.
Piper must be installed in each platform's private runtime; its Cori model and
configuration are included by the release manifest.
The current workstation's defaults are:

- Windows tooling: `.build_windows/tooling/Scripts/python.exe` with PyInstaller;
  override using `--windows-python "PATH"`.
- Windows runtime: `.build_windows/payload/_python`; override using
  `--windows-runtime "PATH"`.
- Linux tooling/runtime: `/tmp/bqe-linux-build/tooling/bin/python` and
  `/tmp/bqe-linux-build/runtime/python`. Set `--linux-build-root` for another
  prepared tree, or `--linux-python` and `--linux-runtime` individually.
- Linux scratch work: unique folders under `<linux-build-root>/work`; override
  using `--linux-work-root`. The build prints the exact path and retains it
  for diagnosis. Use a Linux filesystem here when building through WSL.
- Codec2 source: `logs/hf-modem-research/codec2` in the checkout; override with
  `--codec2-source "/linux/path/to/codec2"`.
- WSL: the default distribution, or select `--wsl-distro Ubuntu-20.04`.

Linux toolchain and Codec2 arguments are Linux paths, including when invoked
from Windows. Release/output paths are translated with `wslpath`. All subprocess
arguments are passed directly without shell interpolation. Toolchain checks run
before the source release is created. Linux native preparation compiles SSDV,
HamDRM and Codec2 in build copies and copies shared libraries into a private
runtime copy; the input runtime and source release are not edited.

### Choosing the installed files directory on the target computer

Choose the installation folder **when running the executable on the target
computer**, using its `--dir` argument. No target location is embedded at build
time. This is the command-line option, not a graphical folder dialog.

Windows (PowerShell):

```powershell
.\BQE_Wisp.exe --dir "C:\My BQE Station"
.\BQE_Wisp.exe --dir ".\My BQE Station"
```

Linux:

```sh
./BQE_Wisp --dir "/home/me/My BQE Station"
./BQE_Wisp --dir "./My BQE Station"
```

Both full and relative paths are accepted. A relative path is resolved against
**the current working directory from which the executable was launched**,
not against the executable's folder. Quote paths containing spaces.
`--data-dir` remains a compatible alias.

The chosen folder must be new or empty. It will contain `bqe_wisp/` (settings,
recordings, logs) and `_python/`. The location is remembered after successful
extraction; later launches of the same build can omit `--dir`, even when run
from a different working directory. Existing settings are preserved.
A small location record is kept under the user's application-data
`BQE_Wisp/installations` folder. Nothing is written into `satellites.yaml`.
If no location is supplied on first run, the executable explains how to use
`--dir` and exits without extracting. If a saved installation becomes
unavailable, specify `--dir` to select a location explicitly. Existing matching
installations at the old default location are recognized.

`mkdist.py` uses `--output-dir` only for build artifacts on the build computer.
It no longer accepts `--dir`; that option belongs to the target executable.

`release_manifest.json` is the authoritative list of exact files and empty
directories. There are no recursive directory or wildcard copy rules. Add an
entry whenever adding a runtime module, UI asset, dependency file, or diagnostic
fixture. Entries can map a source to a different destination.
`manifest.txt` is a short guide to this manifest, not a second file allowlist.

The builder validates all inputs first, rejects external/linked paths and
duplicate destinations, verifies each copied file with SHA-256, and publishes
the completed directory. `DISTRIBUTION.json`, beside `bqe_wisp/`, records the
size and SHA-256 of every shipped file. Nothing else from the checkout is copied.

## Contents and external prerequisites

The release contains core code, plugin code/UI, their requirements and launchers,
documentation, offline diagnostic tests/fixtures, and the SSDV/HF native source,
Windows binaries and license notices. Tests with small public reference packets
are intentionally included; private recordings and decoded outputs are not.

SSDV is pinned to upstream commit `d1ceda81b69f88741396f3e052b50c8ae40efb76`.
Its Windows binary is built from the shipped sources, with hashes and compiler
flags in `plugins/bqe_ssdv_decoder/vendor/ssdv/BUILD-WINDOWS.json`.
Run `python plugins/bqe_ssdv_decoder/vendor/ssdv/build.py --check` to verify it.
`mkdist.py --check` and release creation also reject source or binary hash mismatches.
Linux builds those same verified sources for the target machine. The 2m SSB
preset (`presets/2m_ssb_preset.yaml`) is included.

SatDump's large portable folder and Windows RTL-SDR/Hamlib drivers are excluded.
Install those separately as described in DEPENDENCIES.md. For Windows LRPT,
run `plugins/bqe_lrpt_decoder/install_engine_windows.ps1`; this is an explicit
download, not an automatic action on first decode. Linux builds the included
SSDV/HamDRM sources through `install_dependencies.sh`.

## Packaged configuration

The release copies the working `bqe_config/satellites.yaml` byte for byte,
including enabled scheduling, tracking, tuning, recording, decoder settings,
paths, and comments. It does not sanitize or substitute this file. The working
`templates/satellites_template.yaml` is also copied unchanged. The obsolete
neutral satellite copy under `release_defaults/` is not packaged or used.

General settings, QTH, and rig configuration still come from
`release_defaults/bqe_config/`; local presets are not copied. The general
settings template also uses its neutral default. The example HF image preset
is provided as a template, not activated automatically.

- Callsign is NOCALL, coordinates/altitude are zero placeholders.
- Conventional radio uses Hamlib's dummy model 1; select your real radio, port,
  audio device or SDR before use. The initial SDR source is a demo signal.
- No idle preset is selected. Satellite flags retain their current working
  values; they are not disabled during packaging.
- Gallery output uses the local `images/` directory.
- No TLE cache or schedule is shipped; update orbital elements and generate a
  schedule after setting your location and choosing satellites.

Telemetry and LRPT entries are marked `sdr_required: true`. Pass generation
skips these with a warning if `radio_is_sdr` is false or absent in
`bqe_config/my_rig.yaml`. An omitted `sdr_required` flag means no SDR restriction.
Manual nickname scheduling uses the same check. Direct spacecraft name/NORAD
requests are skipped when all matching configured profiles require an SDR.
The scheduler accepts `--radio_config PATH` for an alternate rig configuration;
place it before `--auto_schedule` if using that option.

Run the platform dependency installer, then use the project `.venv` to run
`bqe_wisp.py`. Configure your station before enabling reception/tracking. The
general/QTH/rig defaults are also retained under `release_defaults/` for
rebuilding. Rebuilding copies the satellite configuration as it exists in that
checkout at build time.

The current allowlist includes the CW SDR filtering changes and regression
tests, SSTV recovery, APRS decoder and spots UI, weather gallery, telemetry
charts, and SDR scheduling support. Existing entries always copy the latest
source contents; a separate manifest entry is needed only for a new file.


### Source inventory refresh — 2026-10-22

The release allowlist includes the current telemetry dropdown and charts, log
viewer with return navigation, schedule emphasis/next-pass indicator and
centering after pass completion, and Update Keps running-state feedback.
Settings and downloaded-data backup tools, the SoundCard COM patch, and
`tests/test_dependency_requirements.py` are included. Personal APRS-IS
credentials remain excluded from the release; settings backups include them.

Before creating a new single-file image, run `python check_dependencies.py --files-only`, `python mkdist.py --check`, and the packaging/dependency tests.
These checks do not create an executable or update the prepared runtime.
The 46-package Windows lock was checked against current requirements on
2026-10-22; no package-version upgrades are required by these UI changes.

Downloaded-data backup/restore now includes pass recordings and transcript
sidecars (including the configured recording location), standard SDR WAV/IQ
recordings and metadata, and APRS upload receipts. LRPT remains excluded.
These changes use the existing files and standard-library ZIP support.
