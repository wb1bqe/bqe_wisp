#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "BQE WISP dependency installer for Ubuntu Linux"
echo "============================================================"
echo

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    echo "ERROR: This installer needs root privileges for apt packages."
    echo "Install sudo or run this script as root."
    exit 1
fi

echo "Installing Ubuntu system prerequisites and Hamlib..."
$SUDO apt-get update
$SUDO apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    libhamlib-utils \
    build-essential python3-dev libffi-dev pkg-config \
    libpulse0 libsndfile1 libfftw3-dev libcodec2-dev \
    librtlsdr0 rtl-sdr

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "ERROR: BQE WISP requires Python 3.10 or newer (3.13 recommended)."
    python3 --version || true
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
    echo "ERROR: requirements.txt was not found beside this installer."
    exit 1
fi

echo "Checking the dependency file list..."
python3 "$SCRIPT_DIR/check_dependencies.py" --files-only

echo "Creating or updating the virtual environment..."
if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "ERROR: Existing .venv is too old. Rename it and rerun this installer."
    exit 1
fi

echo "Updating pip, setuptools, and wheel..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "Installing BQE WISP Python libraries..."
"$PYTHON" -m pip install --only-binary=sstv -r "$SCRIPT_DIR/requirements.txt"

echo "Building the bundled SSDV and HamDRM engines for this machine..."
# Force rebuilding: a copied release folder may contain foreign-architecture objects.
python3 "$SCRIPT_DIR/plugins/bqe_ssdv_decoder/vendor/ssdv/build.py" --target linux
sh "$SCRIPT_DIR/plugins/bqe_hf_image_decoder/native/rxamadrm/build.sh"

echo "Verifying Python dependencies and reporting native components..."
"$PYTHON" -m pip check
"$PYTHON" "$SCRIPT_DIR/check_dependencies.py"

echo "Checking Hamlib command-line programs..."
RIGCTL_PATH="$(command -v rigctl || true)"
RIGCTLD_PATH="$(command -v rigctld || true)"

if [[ -z "$RIGCTL_PATH" || -z "$RIGCTLD_PATH" ]]; then
    echo "ERROR: Hamlib was installed, but rigctl or rigctld is not available in PATH."
    exit 1
fi

echo "Found rigctl:  $RIGCTL_PATH"
echo "Found rigctld: $RIGCTLD_PATH"

# bqe_hamlib_interface.py currently calls ./rigctl on POSIX systems.
# Create a local compatibility link without replacing an existing file.
if [[ ! -e "$SCRIPT_DIR/rigctl" ]]; then
    ln -s "$RIGCTL_PATH" "$SCRIPT_DIR/rigctl"
    echo "Created compatibility link: $SCRIPT_DIR/rigctl -> $RIGCTL_PATH"
elif [[ -L "$SCRIPT_DIR/rigctl" ]]; then
    ln -sfn "$RIGCTL_PATH" "$SCRIPT_DIR/rigctl"
    echo "Updated compatibility link: $SCRIPT_DIR/rigctl -> $RIGCTL_PATH"
else
    echo "NOTICE: $SCRIPT_DIR/rigctl already exists and was not changed."
fi

echo
echo "Python and bundled native builds complete. Review component warnings above."
echo "SatDump is installed separately; see DEPENDENCIES.md."
echo "Run BQE as your regular desktop user with PulseAudio or PipeWire-Pulse available."
echo
echo "To activate this environment later:"
echo "    source \"$SCRIPT_DIR/.venv/bin/activate\""
echo
echo "To start BQE WISP:"
echo "    \"$SCRIPT_DIR/.venv/bin/python\" \"$SCRIPT_DIR/bqe_wisp.py\""
