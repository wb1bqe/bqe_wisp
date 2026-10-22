# SSDV utility provenance and builds

Philip Heron's SSDV encoder/decoder is pinned to upstream commit
`d1ceda81b69f88741396f3e052b50c8ae40efb76`:
https://github.com/fsphil/ssdv/tree/d1ceda81b69f88741396f3e052b50c8ae40efb76

Upstream has moved to https://codeberg.org/fsphil/ssdv. The pinned GitHub
archive is retained as a verifiable reference. On 2026-10-12 all seven source,
header, Makefile and COPYING files were compared with that revision and matched.
`SOURCE.json` records their SHA-256 hashes after CRLF-to-LF normalization.
The upstream files and their copyright/license notices are unmodified.

`main.c`, `ssdv.c` and `ssdv.h` are GPL-3.0-or-later; see COPYING.
`rs8.c` and `rs8.h` carry Phil Karn's LGPL notices; see COPYING.LGPL-2.1.
These component notices are preserved independently of BQE Wisp's GPL license.

The shipped `ssdv.exe` is now compiled from these exact sources, replacing the
previous operator-supplied binary. `BUILD-WINDOWS.json` records the compiler,
flags, source hashes, revision and resulting binary hash. It is a Windows x64
build with static compiler runtime linkage and no PE build timestamp.

Build Windows using MinGW-w64 (on Windows or a Linux cross-build host):

```sh
python3 build.py --target windows --cc x86_64-w64-mingw32-gcc
```

For native MinGW on Windows, use `python build.py --target windows --cc gcc`.
Build Linux natively with `python3 build.py --target linux --cc gcc`.
Linux installers and release preparation use this same source-verifying script;
they build for the target machine rather than using the Windows executable.
Each build emits a platform-specific build record alongside the executable.

Verify the pinned sources and shipped Windows binary without a compiler:

```sh
python3 build.py --check
```

BQE invokes `ssdv -d` with an argument list, temporary packet files, and a
bounded timeout. SO-127/HADES-SA and HF FreeDV image reception remain supported.

Protocol and reference packet credit: AMSAT-EA, CC BY 4.0.
https://github.com/AMSAT-EA/HADES-SA_SpinnyONE
`tests/hades_sample.txt` comes from `byte_version/sample_type_10.txt`.
The on-air five-byte prefix is restored for the reference test.

Operator transmission specification:
https://www.amsat-ea.org/app/download/14191564/AMSAT%2BEA%2B-%2BHADES-SA_SpinnyONE%2BTransmissions%2BDescription.pdf
