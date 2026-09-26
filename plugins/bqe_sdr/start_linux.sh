#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import numpy, scipy, yaml' >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then set -- --config ../../bqe_config/my_rig.yaml --idle --open-browser; fi
exec .venv/bin/python bqe_sdr.py "$@"
