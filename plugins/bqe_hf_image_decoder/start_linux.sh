#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -x ../bqe_ssdv_decoder/vendor/ssdv/ssdv ] || .venv/bin/python ../bqe_ssdv_decoder/vendor/ssdv/build.py --target linux
[ -x native/hamdrm ] || sh native/rxamadrm/build.sh
if [ "$#" -eq 0 ]; then set -- --idle; fi
exec .venv/bin/python bqe_hf_image_decoder.py "$@"
