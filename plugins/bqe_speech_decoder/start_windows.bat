@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 'Use Python 3.10 or newer')"
  if errorlevel 1 exit /b 1
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -c "import faster_whisper, av, onnxruntime, yaml" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" bqe_speech_decoder.py --check
) else (
  ".venv\Scripts\python.exe" bqe_speech_decoder.py %*
)
exit /b %errorlevel%
