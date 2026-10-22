#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ ! -x vendor/ssdv/ssdv ]; then
    if command -v gcc >/dev/null; then
        .venv/bin/python vendor/ssdv/build.py --target linux
    else
        echo 'WARNING: SO-127 image rendering needs gcc; RS61 remains available.'
    fi
fi
if [ "$#" -eq 0 ]; then set -- --idle; fi
exec .venv/bin/python bqe_ssdv_decoder.py "$@"
