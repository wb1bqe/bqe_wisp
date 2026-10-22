#!/bin/sh
set -eu
cd "$(dirname "$0")"
# Linux: install a C compiler and libfftw3-dev. For Windows cross-builds set
# CC=x86_64-w64-mingw32-gcc; the bundled FFTW DLL supplies the import library.
cc=${CC:-cc}
case "$cc" in
  *mingw*) fftw=../libfftw3-3.dll; output=../hamdrm.exe; extra="-Wl,--stack,67108864 -static-libgcc" ;;
  *) fftw=-lfftw3; output=../hamdrm; extra="" ;;
esac
# sources.txt is a checked-in list of C files, intentionally word-split here.
$cc -O2 -fcommon -include stdint.h $extra -I.. main.c monorec.c stubs.c $(cat sources.txt) $fftw -lm -o "$output"
