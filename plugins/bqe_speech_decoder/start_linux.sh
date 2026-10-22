#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  speech_python=${BQE_SPEECH_PYTHON:-python3}
  "$speech_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Use Python 3.10+; set BQE_SPEECH_PYTHON to its executable")'
  "$speech_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import faster_whisper, av, onnxruntime, yaml' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ "$#" -eq 0 ]; then set -- --check; fi
exec .venv/bin/python bqe_speech_decoder.py "$@"
