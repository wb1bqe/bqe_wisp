@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -c "import numpy, scipy, yaml" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" bqe_sdr.py --config ../../bqe_config/my_rig.yaml --idle --open-browser
) else (
  ".venv\Scripts\python.exe" bqe_sdr.py %*
)
if errorlevel 1 pause
