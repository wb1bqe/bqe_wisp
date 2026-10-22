# Native receiver components

`libcodec2.dll` is a Windows x64 build of unmodified
[Codec2](https://github.com/drowe67/codec2) commit
`310777b1c6f1af0bc7c72f5b32f80f6fd9136962`. License: LGPL-2.1,
see `COPYING-codec2`. Python accesses only the public FreeDV raw-data API.

Rebuild from that checkout on Linux/WSL with CMake and MinGW-w64:

```sh
cmake -S codec2 -B codec2-win -DCMAKE_SYSTEM_NAME=Windows \
  -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON -DCMAKE_SHARED_LINKER_FLAGS=-static-libgcc
cmake --build codec2-win --target codec2 -j4
```

Copy `codec2-win/src/libcodec2.dll` here. On Linux, install the system Codec2
library or build the same source without the Windows cross-compilation options.

`hamdrm.exe` is built from the sources in `rxamadrm/`, derived from M. Bos
PA0MBO's [RXAMADRM 3.6 source archive](https://pa0mbo.nl/ties/public_html/hamradio/rxamadrm/trxamadrmv3_6_18nov14_64b.tgz).
Upstream copyright headers are retained; see `rxamadrm/COPYING` and individual
files. The archive contains the original receiver and supporting RS utilities.

BQE adaptations: remove X11/ALSA control code; read mono 48 kHz float32 audio
from stdin in PCM16 amplitude units; retain FFT arrays for plan lifetime; use
FFTW_ESTIMATE; correct a rate-table declaration; allocate unique received-file
names with per-file original-name metadata; use binary file output; defer gzip
and image parsing to Python; and flush receiver progress on stdout. No radio
or GUI is opened by the native helper. It runs in a temporary directory.

Build Windows x64 from Linux/WSL:

```sh
CC=x86_64-w64-mingw32-gcc sh rxamadrm/build.sh
```

Build Linux (requires FFTW development libraries):

```sh
sh rxamadrm/build.sh
```

On 2026-10-12, BQE removed the Numerical Recipes `ludcmp.c`, `lubksb.c`,
`nrutil.c`, `nrutil.h`, and `nr.h` files from this source tree and the release
allowlist. Their only active use was inversion of the channel estimator's
covariance matrix. The newly authored `bqe_matrix.c` / `bqe_matrix.h` implement
standard Gauss-Jordan inversion under GPL-3.0-or-later, using zero-based arrays,
row scaling, partial pivoting and double-precision working storage. This is a
new implementation, not a port of the removed routines. Other upstream notices
are unchanged. Both native HamDRM helpers were rebuilt after this replacement.

`tests/test_matrix.py` compares native results against NumPy, including 208x208
matrices, Wiener covariance matrices, row pivoting, scaling and invalid inputs.
Failed inversion leaves the result untouched and causes the native receiver to
exit with an error. An independent over-the-air HamDRM image decode remains
unverified; numerical and synthetic/noise tests do not establish that result.

Run the matrix tests on Linux with a C compiler and NumPy installed:

```sh
python3 -m unittest plugins.bqe_hf_image_decoder.tests.test_matrix -v
```

For Windows testing, compile `bqe_matrix.c` as a DLL and set
`BQE_MATRIX_LIBRARY` to that DLL before running the same tests.

`libfftw3-3.dll`, its `.def`, and `fftw3.h` come from the official
[FFTW 3.3.5 Windows x64 archive](https://fftw.org/pub/fftw/fftw-3.3.5-dll64.zip).
License: GPL-2.0-or-later, see `COPYING-fftw`. Corresponding source:
<https://fftw.org/pub/fftw/fftw-3.3.5.tar.gz>.

SSDV image rendering reuses `../bqe_ssdv_decoder/vendor/ssdv` (relative to the
plugin directory); it is not duplicated in this plugin.
