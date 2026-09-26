#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ "$#" -eq 0 ]; then set -- --idle; fi
exec .venv/bin/python bqe_ssdv_decoder.py "$@"
