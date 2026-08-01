@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo BQE WISP Python dependency installer for Windows
echo ============================================================
echo.

set "PYTHON_CMD="

where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo ERROR: Python 3 was not found.
    echo Install Python 3.9 or newer, enable "Add Python to PATH",
    echo and then run this file again.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 (
    echo ERROR: BQE WISP requires Python 3.9 or newer.
    %PYTHON_CMD% --version
    pause
    exit /b 1
)

if not exist "%~dp0requirements.txt" (
    echo ERROR: requirements.txt was not found beside this installer.
    pause
    exit /b 1
)

echo Creating or updating the virtual environment...
if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo ERROR: Could not create the .venv virtual environment.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo ERROR: Could not activate the .venv virtual environment.
    pause
    exit /b 1
)

echo Updating pip, setuptools, and wheel...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :install_error

echo Installing BQE WISP Python libraries...
python -m pip install -r requirements.txt
if errorlevel 1 goto :install_error

echo Verifying Python imports...
python -c "import numpy, requests, skyfield, yaml; print('Python dependency check passed.')"
if errorlevel 1 goto :install_error

echo.
echo Checking Hamlib command-line programs...
where rigctl.exe >nul 2>&1
if errorlevel 1 (
    echo WARNING: rigctl.exe was not found.
    echo Install Hamlib for Windows and add its executable directory to PATH,
    echo or copy the Hamlib executables and required DLL files into this project directory.
) else (
    for /f "delims=" %%I in ('where rigctl.exe') do echo Found: %%I
)

where rigctld.exe >nul 2>&1
if errorlevel 1 (
    echo WARNING: rigctld.exe was not found.
    echo Radio tracking will not work until Hamlib is installed.
) else (
    for /f "delims=" %%I in ('where rigctld.exe') do echo Found: %%I
)

echo.
echo Installation complete.
echo.
echo To activate this environment later:
echo     call .venv\Scripts\activate.bat
echo.
echo To start BQE WISP after the project files have their normal names:
echo     .venv\Scripts\python.exe bqe_wisp.py
echo.
pause
exit /b 0

:install_error
echo.
echo ERROR: Dependency installation failed.
echo Review the messages above, verify Internet access, and run this installer again.
pause
exit /b 1
