#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then set -- --idle; fi
exec .venv/bin/python bqe_sstv_decoder.py "$@"

