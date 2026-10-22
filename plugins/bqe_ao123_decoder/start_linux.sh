#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  ao123_python=${BQE_AO123_PYTHON:-python3}
  "$ao123_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Use Python 3.10+; set BQE_AO123_PYTHON")'
  "$ao123_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import numpy, scipy, reedsolo, soundfile' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then set -- --idle; fi
exec .venv/bin/python bqe_ao123_decoder.py "$@"
