#!/usr/bin/env python3
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

"""
bqe_wisp.py

Reads 'schedule.json' and automatically runs 'bqe_track_continuously.py <object_name>'
for each scheduled satellite pass, starting and stopping at the times specified.

Also starts a small built-in Python web console using settings from
bqe_config/general_settings.yaml.  The console shows the schedule and highlights passes in red
while they are active.
"""

import glob
import json
import math
import os
import re
import subprocess
import sys
import signal
import shlex
import shutil
import threading
import time
from datetime import datetime, timezone, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
from bqe_wisp_web import (
    WebConsoleHandler,
    build_index_html,
    get_web_console_port,
    load_web_console_settings,
    resolve_sstv_images_directory,
)

try:
    import yaml
except ImportError:  # Keep the scheduler usable even if PyYAML is not installed.
    yaml = None

try:
    import bqe_hamlib_interface as rig
except ImportError:
    rig = None

try:
    import bqe_set_radio_from_yaml as idle_task_ctl
except ImportError:
    idle_task_ctl = None


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERAL_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "bqe_config", "general_settings.yaml")
FDT_RECALCULATION_INTERVAL_MIN = 4.0
FDT_RECALCULATION_INTERVAL_MAX = 30.0

DEFAULT_GENERAL_SETTINGS = {
    "python_path": sys.executable or "python",
    # Scheduler/web-server polling cadence. This is intentionally separate from
    # the satellite tracking/Doppler cadence in bqe_track_continuously.
    "scheduler_sleep_interval_seconds": 30.0,
    "idle_tuning_delay_seconds": 3.0,
    # Tracking-loop cadence defaults. These are read from the
    # bqe_track_continuously section and also shown by the FDT Console.
    "tracking_sleep_interval_seconds": 10.0,
    "tracking_sleep_interval_high_elevation_seconds": 3.0,
    "tracking_high_pass_elevation": 65.0,
    "horizon_threshold_elevation": 0.1,
    "web_command_timeout_seconds": 300,
    "test_pass_azimuth": 45.0,
    "test_pass_elevation": 45.0,
    "test_pass_doppler": 3028.0,
    "fdt_recalculation_interval": 10.0,
    "rigctld_port": 4532,
    "rigctl_path": "rigctl",
    "rigctld_path": "rigctld",
}


def _settings_section(program_settings, name):
    """Return a named settings section from general_settings.yaml."""
    if isinstance(program_settings, dict):
        value = program_settings.get(name)
        return value if isinstance(value, dict) else {}

    if isinstance(program_settings, list):
        for item in program_settings:
            if not isinstance(item, dict):
                continue
            value = item.get(name)
            if isinstance(value, dict):
                return value

    return {}


def _coerce_int(value, default, setting_name):
    """Convert a YAML value to int, falling back to default if invalid."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        print(f"Warning: general_settings.yaml setting {setting_name!r}={value!r} is invalid; using {default!r}.")
        return default


def _coerce_float(value, default, setting_name):
    """Convert a YAML value to float, falling back to default if invalid."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        print(f"Warning: general_settings.yaml setting {setting_name!r}={value!r} is invalid; using {default!r}.")
        return default


def _coerce_seconds(value, default, setting_name):
    """Convert a numeric or text duration, such as '5 seconds', to seconds."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
        if not match:
            print(f"Warning: general_settings.yaml setting {setting_name!r}={value!r} is invalid; using {default!r}.")
            return default
        seconds = float(match.group(0))
    if seconds <= 0:
        print(f"Warning: general_settings.yaml setting {setting_name!r} must be positive; using {default!r}.")
        return default
    return seconds


def load_general_settings(filename=GENERAL_SETTINGS_FILE):
    """Load general_settings.yaml and return normalized settings for this script."""
    settings = dict(DEFAULT_GENERAL_SETTINGS)

    if yaml is None:
        print("Warning: PyYAML not available; using built-in defaults for general settings.")
        return settings

    if not os.path.exists(filename):
        print(f"Warning: {filename} not found; using built-in defaults for general settings.")
        return settings

    try:
        with open(filename, "r", encoding="utf-8") as f:
            root = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: could not read {filename}: {e}; using built-in defaults for general settings.")
        return settings

    if not isinstance(root, dict):
        print(f"Warning: {filename} did not contain a YAML mapping; using built-in defaults for general settings.")
        return settings

    program_settings = root.get("program_settings") or {}
    common_settings = _settings_section(program_settings, "common")
    wisp_settings = _settings_section(program_settings, "bqe_wisp")
    track_settings = _settings_section(program_settings, "bqe_track_continuously")
    third_party_settings = _settings_section(program_settings, "third_party")

    python_path = common_settings.get("python_path")
    if python_path is not None and str(python_path).strip():
        settings["python_path"] = str(python_path).strip()

    settings["scheduler_sleep_interval_seconds"] = _coerce_seconds(
        wisp_settings.get("scheduler_sleep_interval"),
        settings["scheduler_sleep_interval_seconds"],
        "program_settings.bqe_wisp.scheduler_sleep_interval",
    )

    settings["horizon_threshold_elevation"] = _coerce_float(
        wisp_settings.get("horizon_threshold_elevation"),
        settings["horizon_threshold_elevation"],
        "program_settings.bqe_wisp.horizon_threshold_elevation",
    )

    settings["web_command_timeout_seconds"] = _coerce_seconds(
        wisp_settings.get("web_command_timeout_seconds"),
        settings["web_command_timeout_seconds"],
        "program_settings.bqe_wisp.web_command_timeout_seconds",
    )

    tuning_delay = _coerce_float(
        wisp_settings.get("idle_tuning_delay_seconds"),
        settings["idle_tuning_delay_seconds"],
        "program_settings.bqe_wisp.idle_tuning_delay_seconds",
    )
    if not 0 <= tuning_delay < float("inf"):
        print("Warning: idle_tuning_delay_seconds must be finite and nonnegative; using 3 seconds.")
        tuning_delay = 3.0
    settings["idle_tuning_delay_seconds"] = tuning_delay

    settings["tracking_sleep_interval_seconds"] = _coerce_seconds(
        track_settings.get("sleep_interval"),
        settings["tracking_sleep_interval_seconds"],
        "program_settings.bqe_track_continuously.sleep_interval",
    )
    settings["tracking_sleep_interval_high_elevation_seconds"] = _coerce_seconds(
        track_settings.get("sleep_interval_high_elevation"),
        settings["tracking_sleep_interval_high_elevation_seconds"],
        "program_settings.bqe_track_continuously.sleep_interval_high_elevation",
    )
    settings["tracking_high_pass_elevation"] = _coerce_float(
        track_settings.get("high_pass_elevation"),
        settings["tracking_high_pass_elevation"],
        "program_settings.bqe_track_continuously.high_pass_elevation",
    )

    settings["test_pass_azimuth"] = _coerce_float(
        track_settings.get("test_pass_azimuth"),
        settings["test_pass_azimuth"],
        "program_settings.bqe_track_continuously.test_pass_azimuth",
    )
    settings["test_pass_elevation"] = _coerce_float(
        track_settings.get("test_pass_elevation"),
        settings["test_pass_elevation"],
        "program_settings.bqe_track_continuously.test_pass_elevation",
    )
    settings["test_pass_doppler"] = _coerce_float(
        track_settings.get("test_pass_doppler"),
        settings["test_pass_doppler"],
        "program_settings.bqe_track_continuously.test_pass_doppler",
    )
    settings["fdt_recalculation_interval"] = _coerce_seconds(
        track_settings.get("fdt_recalculation_interval"),
        settings["fdt_recalculation_interval"],
        "program_settings.bqe_track_continuously.fdt_recalculation_interval",
    )
    if not (
            FDT_RECALCULATION_INTERVAL_MIN
            <= settings["fdt_recalculation_interval"]
            <= FDT_RECALCULATION_INTERVAL_MAX):
        original_interval = settings["fdt_recalculation_interval"]
        settings["fdt_recalculation_interval"] = min(
            FDT_RECALCULATION_INTERVAL_MAX,
            max(FDT_RECALCULATION_INTERVAL_MIN, original_interval),
        )
        print(
            "Warning: general_settings.yaml setting "
            "'program_settings.bqe_track_continuously.fdt_recalculation_interval' "
            f"must be between {FDT_RECALCULATION_INTERVAL_MIN:g} and "
            f"{FDT_RECALCULATION_INTERVAL_MAX:g} seconds; using "
            f"{settings['fdt_recalculation_interval']:g}."
        )

    settings["rigctld_port"] = _coerce_int(
        third_party_settings.get("rigctld_port"),
        settings["rigctld_port"],
        "program_settings.third_party.rigctld_port",
    )
    rigctl_path = third_party_settings.get("rigctl_path")
    if rigctl_path is not None and str(rigctl_path).strip():
        settings["rigctl_path"] = str(rigctl_path).strip()

    rigctld_path = third_party_settings.get("rigctld_path")
    if rigctld_path is not None and str(rigctld_path).strip():
        settings["rigctld_path"] = str(rigctld_path).strip()

    return settings


GENERAL_SETTINGS = load_general_settings()
PYTHON_PATH = GENERAL_SETTINGS["python_path"]
RIGCTLD_PORT = GENERAL_SETTINGS["rigctld_port"]
RIGCTL_PATH = GENERAL_SETTINGS["rigctl_path"]
RIGCTLD_PATH = GENERAL_SETTINGS["rigctld_path"]
WEB_CONSOLE_PORT = get_web_console_port(GENERAL_SETTINGS_FILE)
SCHEDULER_SLEEP_INTERVAL_SECONDS = GENERAL_SETTINGS["scheduler_sleep_interval_seconds"]
HORIZON_THRESHOLD_ELEVATION = GENERAL_SETTINGS["horizon_threshold_elevation"]
WEB_COMMAND_TIMEOUT_SECONDS = GENERAL_SETTINGS["web_command_timeout_seconds"]
TEST_PASS_AZIMUTH = GENERAL_SETTINGS["test_pass_azimuth"]
TEST_PASS_ELEVATION = GENERAL_SETTINGS["test_pass_elevation"]
TEST_PASS_DOPPLER = GENERAL_SETTINGS["test_pass_doppler"]
TRACKING_SLEEP_INTERVAL_SECONDS = GENERAL_SETTINGS["tracking_sleep_interval_seconds"]
TRACKING_SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS = GENERAL_SETTINGS["tracking_sleep_interval_high_elevation_seconds"]
TRACKING_HIGH_PASS_ELEVATION = GENERAL_SETTINGS["tracking_high_pass_elevation"]
# The FDT slider starts at the YAML normal tracking interval, but remains in
# automatic elevation-based mode until the operator actually moves it.
FDT_RECALCULATION_INTERVAL = min(
    FDT_RECALCULATION_INTERVAL_MAX,
    max(FDT_RECALCULATION_INTERVAL_MIN, TRACKING_SLEEP_INTERVAL_SECONDS),
)

SCHEDULE_FILE = os.path.join(SCRIPT_DIR, "schedule.json")
SATELLITE_CONFIG_FILE = os.path.join(SCRIPT_DIR, "bqe_config", "satellites.yaml")
SCHEDULE_PASSES_SCRIPT = os.path.join(SCRIPT_DIR, "bqe_schedule_passes.py")
PRESETS_DIR = os.path.join(SCRIPT_DIR, "presets")
QTH_CONFIG_FILE = os.path.join(SCRIPT_DIR, "bqe_config", "my_qth.yaml")
TRACKING_SCRIPT = os.path.join(SCRIPT_DIR, "bqe_track_continuously.py")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_FILE = os.path.join(LOG_DIR, "bqe_wisp_tracking_status.json")
RIGCTLD_PID_FILE = os.path.join(LOG_DIR, "rigctld.pid")
PASS_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "pass_program.pid")
LEGACY_PASS_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "pass_program_pid")
TRACKING_STOP_FILE = os.path.join(LOG_DIR, "tracking_stop.request")
IDLE_WAIT_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "idle_wait_program.pid")
ANTENNA_TRACKING_OVERRIDE_FILE = os.path.join(LOG_DIR, "antenna_tracking_override.json")
FDT_CONTROL_FILE = os.path.join(LOG_DIR, "fdt_control.json")
FDT_WHEEL_STEP_HZ = 200.0
IDLE_TASK_STATUS_FILE = os.path.join(SCRIPT_DIR, "logs", "idle_task.yaml")
UPDATE_KEPS_SCRIPT = os.path.join(SCRIPT_DIR, "bqe_update_keps.py")
PRESET_WEB_PORT = getattr(idle_task_ctl, "WEB_PORT", 8015)


def _find_configured_executable(configured_path):
    """Resolve a configured executable name or path on Windows and Linux."""
    if configured_path is None or not str(configured_path).strip():
        return None

    expanded_path = os.path.expandvars(os.path.expanduser(str(configured_path).strip()))

    # shutil.which() handles PATH lookup on both platforms and also honors
    # PATHEXT on Windows (so a configured value such as "rigctl" can find
    # rigctl.exe).  When a relative path contains a directory component, also
    # try it relative to the BQE WISP installation directory.
    candidates = [expanded_path]
    if not os.path.isabs(expanded_path) and os.path.dirname(expanded_path):
        candidates.append(os.path.join(SCRIPT_DIR, expanded_path))

    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return os.path.abspath(resolved)

        # An explicitly configured file can still be usable on Windows even
        # when its executable bit is not represented like it is on Linux.
        if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return os.path.abspath(candidate)

    return None


def _diagnostic_read_yaml(path):
    if yaml is None:
        raise RuntimeError("PyYAML is not available")
    with open(path, "r", encoding="utf-8-sig") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _diagnostic_settings_values(data):
    """Compare named sections independently of list ordering or YAML layout."""
    data = dict(data)
    sections = data.get("program_settings")
    if isinstance(sections, list):
        merged = {}
        for item in sections:
            if not isinstance(item, dict):
                raise ValueError("program_settings entries must be mappings")
            for key, value in item.items():
                if key in merged:
                    raise ValueError(f"Duplicate program_settings section: {key}")
                merged[key] = value
        data["program_settings"] = merged
    values = {}

    def visit(value, path):
        if isinstance(value, dict) and value:
            for key, child in value.items():
                visit(child, path + (str(key),))
        else:
            # Keep ordinary lists intact; their order can be meaningful.
            values[path] = value

    visit(data, ())
    return values


def _diagnostic_value(value):
    return json.dumps(value, ensure_ascii=True, default=str)


def _configuration_diagnostics(report):
    report(f"[INFO] --------------- Checking BQE Wisp environment settings---------------")
    template_path = os.path.join(SCRIPT_DIR, "templates", "general_settings_template.yaml")
    report(f"[INFO] Configuration comparison: {GENERAL_SETTINGS_FILE} against {template_path}")
    try:
        current = _diagnostic_settings_values(_diagnostic_read_yaml(GENERAL_SETTINGS_FILE))
        template = _diagnostic_settings_values(_diagnostic_read_yaml(template_path))
        differences = 0
        
        for path in sorted(set(current) | set(template)):
            key = ".".join(path) or "<root>"
            if path not in current:
                report(f"[WARNING] Missing setting: {key}; template={_diagnostic_value(template[path])}")
            elif path not in template:
                report(f"[INFO] Added setting: {key}; current={_diagnostic_value(current[path])}; template=<not present>")
            elif _diagnostic_value(current[path]) != _diagnostic_value(template[path]):
                report(f"[WARNING] Changed setting: {key}; template={_diagnostic_value(template[path])}; current={_diagnostic_value(current[path])}")
            else:
                continue
            differences += 1
        report(f"[INFO] Configuration comparison complete: {differences} difference(s).")
    except Exception as exc:
        report(f"[WARNING] Could not compare configuration files: {exc}")

    for name in ("my_rig.yaml", "my_qth.yaml"):
        path = os.path.join(SCRIPT_DIR, "bqe_config", name)
        report(f"[INFO] Configuration key/value information: {path}")
        try:
            data = _diagnostic_read_yaml(path)
            if not data:
                report(f"[WARNING] {name} contains no key/value entries.")
            for key, value in data.items():
                report(f"[INFO] {name}: {key} = {_diagnostic_value(value)}")
        except Exception as exc:
            report(f"[WARNING] Could not read {path}: {exc}")


def run_environment_diagnostics(emit=True, include_plugin_tests=False):
    """Return a report; optionally print it without redirecting global stdout."""
    lines = []

    def print(message):
        lines.append(message.strip())

    print("[INFO] --------------- BQE Wisp Integrity checks ---------------")
    print(f"[INFO] Generated: {datetime.now(timezone.utc).isoformat()}")
    print("[INFO] --------------- Checking external dependencies---------------")

    for executable_name, configured_path in (
            ("rigctl", RIGCTL_PATH),
            ("rigctld", RIGCTLD_PATH)):
        resolved_path = _find_configured_executable(configured_path)
        if resolved_path:
            print(
                f"[INFO] {executable_name} found: {resolved_path} "
                f"(configured as {configured_path!r})"
            )
        else:
            print(
                f"    [WARNING] {executable_name} was not found using the configured "
                f"      path {configured_path!r}. Check "
                "program_settings.third_party in bqe_config/general_settings.yaml."
            )

    unexpected_satellite_config = os.path.join(SCRIPT_DIR, "satellites.yaml")
    if os.path.isfile(unexpected_satellite_config):
        print(
            "    [WARNING] satellites.yaml was found in the top-level program folder: "
            f"     {unexpected_satellite_config}. Move or remove this copy so it cannot "
            "be mistaken for the active configuration."
        )
    else:
        print(
            "    [INFO] satellites.yaml is not present in the top-level program folder "
            "    (This is a good thing...)."
        )

    if os.path.isfile(SATELLITE_CONFIG_FILE):
        print(f"    [INFO] Satellite configuration found: {SATELLITE_CONFIG_FILE}")
    else:
        print(
            "    [WARNING] Satellite configuration was not found at the required "
            f"     location: {SATELLITE_CONFIG_FILE}"
        )

    # Re-read settings on each request and resolve paths exactly as the gallery
    # does (relative paths are relative to the program directory).
    try:
        location = load_web_console_settings(GENERAL_SETTINGS_FILE).sstv_gallery_location
        directory = resolve_sstv_images_directory(location)
        if directory is None:
            print("[WARNING] SSTV gallery directory: sstv_gallery_location is not set in general_settings.yaml.")
        elif directory.is_dir():
            print(f"[INFO] SSTV gallery directory found: {directory} (sstv_gallery_location={location!r})")
        else:
            print(f"[WARNING] SSTV gallery directory not found or not a directory: {directory} (sstv_gallery_location={location!r})")
    except Exception as exc:
        print(f"[WARNING] Could not check SSTV gallery directory (sstv_gallery_location): {exc}")

    print("[INFO] UI web ports:")
    print(
        f"    [INFO]   bqe_wisp.py / bqe_wisp_web.py: {WEB_CONSOLE_PORT} "
        "       (main console and its map, FDT, audio, logs, and recordings views)"
    )
    print(
        f"    [INFO]   bqe_set_radio_from_yaml.py: {PRESET_WEB_PORT} "
        "       (standalone preset-control UI)"
    )
    print("[INFO] PC Audio Recorder / bqe_sound_recorder.py: 8765 "
          "(default UI port; --port can override this; not a check that the recorder is running)")
    print(f"    [INFO] Hamlib rigctld TCP control port (not a UI web port): {RIGCTLD_PORT}")
    _configuration_diagnostics(print)
    if include_plugin_tests:
        from plugins.self_tests import run_plugin_self_tests
        print(run_plugin_self_tests(lambda: SHUTDOWN_EVENT.is_set() or satellite_pass_is_active()))
    print("[INFO] Environment Diagnostics complete.")
    report = "\n".join(lines) + "\n"
    if emit:
        sys.stdout.write(report)
    return report

STATE_LOCK = threading.RLock()
FDT_TUNING_LOCK = threading.RLock()
AUDIO_RECORDING_LOCK = threading.RLock()
CURRENT_AUDIO_RECORDER = None
APP_STATE = {
    "passes": [],
    "current_pass_key": None,
    "current_satellite": None,
    "current_log": None,
    "tracking_running": False,
    "last_message": "Starting up...",
    "web_started_at": None,
    "completed": [],
    "tracking_status_file": STATUS_FILE,
    "command_running": False,
    "last_command": None,
    "last_command_result": None,
    "shutdown_requested": False,
    "restart_requested": False,
    "fdt_recalculation_interval": FDT_RECALCULATION_INTERVAL,
    "fdt_recalculation_interval_override": None,
    "presets": [],
    "preset_command_running": False,
    "idle_task_active": False,
    # None = use each satellite's YAML value; bool = temporary Tracking-menu override.
    "antenna_tracking_override": None,
}

SHUTDOWN_EVENT = threading.Event()
CURRENT_PASS_END_EVENT = threading.Event()
CURRENT_TRACKING_PROCESS = None
CURRENT_IDLE_PROCESS = None
CURRENT_WEB_COMMAND_PROCESS = None
WEB_SERVER = None


def restore_default_keyboard_interrupt_handler():
    """Keep Ctrl-C on Python's normal KeyboardInterrupt path.

    This avoids custom SIGINT handlers that can make terminal Ctrl-C appear to
    be ignored while the scheduler is waiting between passes.  Browser File >
    Exit still uses SHUTDOWN_EVENT.
    """
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except Exception as e:
        print(f"Warning: could not restore default Ctrl-C handler: {e}")


def sleep_until_shutdown_or_timeout(total_seconds, step_seconds=0.25):
    """Return False if File > Exit requests shutdown before timeout expires.

    Ctrl-C is intentionally not caught here.  A terminal Ctrl-C should raise
    KeyboardInterrupt and bubble up to main(), where the existing cleanup path
    stops helper programs and exits cleanly.
    """
    try:
        total_seconds = float(total_seconds)
    except (TypeError, ValueError):
        total_seconds = 0.0

    if total_seconds <= 0:
        return not SHUTDOWN_EVENT.is_set()

    deadline = time.monotonic() + total_seconds
    while not SHUTDOWN_EVENT.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(step_seconds, remaining))

    return False


def utc_now():
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def parse_time(value):
    """Parse a JSON ISO time string as timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def display_sat_name(entry):
    """Prefer nickname if available; otherwise use sat_name/name/satellite."""
    return entry.get("nickname") or entry.get("sat_name") or entry.get("name") or entry.get("satellite") or "UNKNOWN"


def pass_key(entry):
    """Stable key for a pass row."""
    return f"{display_sat_name(entry)}|{entry.get('start', '')}|{entry.get('end', '')}"


def ensure_schedule_file_exists(filename=SCHEDULE_FILE):
    """Create an empty schedule file if it does not already exist."""
    if os.path.exists(filename):
        return False

    schedule_dir = os.path.dirname(os.path.abspath(filename))
    if schedule_dir:
        os.makedirs(schedule_dir, exist_ok=True)

    with open(filename, "w", encoding="utf-8"):
        pass

    print(f"[INFO] Created empty schedule file: {filename}")
    return True


def load_schedule(filename=SCHEDULE_FILE):
    """Load the JSON schedule file."""
    if not os.path.exists(filename):
        raise FileNotFoundError(f"{filename} not found")

    with open(filename, "r", encoding="utf-8") as f:
        raw_schedule = f.read().strip()

    if not raw_schedule:
        return {"passes": []}

    return json.loads(raw_schedule)


def normalize_passes(schedule):
    """Return sorted schedule passes with parsed times and web-console fields."""
    raw_passes = schedule.get("passes", []) if isinstance(schedule, dict) else []
    normalized = []

    for idx, entry in enumerate(raw_passes, start=1):
        start_str = entry.get("start")
        end_str = entry.get("end")
        if not start_str or not end_str:
            continue
        try:
            start_time = parse_time(start_str)
            end_time = parse_time(end_str)
        except Exception:
            continue

        max_el = (
            entry.get("max_elevation")
            or entry.get("max_altitude")
            or entry.get("max_el")
            or entry.get("el")
            or ""
        )
        try:
            max_el = int(round(float(max_el)))
        except Exception:
            max_el = ""

        normalized.append({
            **entry,
            "_index": idx,
            "_key": pass_key(entry),
            "_satellite": display_sat_name(entry),
            "_start_dt": start_time,
            "_end_dt": end_time,
            "_max_el": max_el,
        })

    normalized.sort(key=lambda p: p["_start_dt"])
    return normalized


def refresh_schedule_state(filename=SCHEDULE_FILE):
    """Read schedule.json and make it visible to the web console."""
    schedule = load_schedule(filename)
    passes = normalize_passes(schedule)
    with STATE_LOCK:
        APP_STATE["passes"] = passes
        APP_STATE["last_message"] = f"Loaded {len(passes)} passes from {filename}"
    return passes



def _write_schedule_atomic(schedule, filename=SCHEDULE_FILE):
    """Write schedule JSON atomically so browser/status readers never see a partial file."""
    directory = os.path.dirname(os.path.abspath(filename))
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary_file = filename + ".tmp"
    with open(temporary_file, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)
        f.write("\n")
    os.replace(temporary_file, filename)


def load_test_pass_satellites(filename=SATELLITE_CONFIG_FILE):
    """Return satellite choices from bqe_config/satellites.yaml for the test-pass dialog."""
    if yaml is None:
        raise RuntimeError("PyYAML is required to read satellites.yaml.")
    with open(filename, "r", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    if not isinstance(root, dict):
        raise ValueError(f"{filename} must contain a YAML mapping.")
    satellites = root.get("satellites")
    if not isinstance(satellites, list):
        raise ValueError(f"{filename} must contain a top-level 'satellites' list.")

    choices = []
    seen = set()
    for item in satellites:
        if not isinstance(item, dict):
            continue
        nickname = str(item.get("nickname") or "").strip()
        if not nickname or nickname.casefold() in seen:
            continue
        seen.add(nickname.casefold())
        choices.append({
            "nickname": nickname,
            "satellite_name": str(item.get("satellite_name") or nickname).strip(),
            "satellite_type": str(item.get("satellite_type") or "").strip(),
            "catalog_number": item.get("catalog_number"),
        })
    return choices


def _normalize_catalog_number_for_custom_schedule(value):
    """Normalize catalog numbers so values such as 07530 and 7530 compare equal."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text.casefold()


def schedule_custom_passes_from_web(nicknames, filename=SATELLITE_CONFIG_FILE):
    """Validate selected nicknames, then schedule them with bqe_schedule_passes.py."""
    if not isinstance(nicknames, (list, tuple)):
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": "Custom Schedule Passes requires a list of selected satellite nicknames.",
        }

    requested = []
    seen_requested = set()
    for value in nicknames:
        nickname = str(value or "").strip()
        key = nickname.casefold()
        if nickname and key not in seen_requested:
            seen_requested.add(key)
            requested.append(nickname)

    if not requested:
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": "Select at least one satellite before creating a custom schedule.",
        }

    if yaml is None:
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": "PyYAML is required to read satellites.yaml.",
        }

    try:
        with open(filename, "r", encoding="utf-8") as f:
            root = yaml.safe_load(f) or {}
    except Exception as exc:
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": f"Could not read {filename}: {exc}",
        }

    satellites = root.get("satellites", []) if isinstance(root, dict) else []
    if not isinstance(satellites, list):
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": f"{filename} must contain a top-level 'satellites' list.",
        }

    entries = [item for item in satellites if isinstance(item, dict)]
    by_nickname = {}
    by_catalog = {}
    for item in entries:
        nickname = str(item.get("nickname") or "").strip()
        if nickname:
            by_nickname.setdefault(nickname.casefold(), item)

        catalog_key = _normalize_catalog_number_for_custom_schedule(item.get("catalog_number"))
        if catalog_key and nickname:
            by_catalog.setdefault(catalog_key, []).append(nickname)

    resolved_requested = []
    missing = []
    for nickname in requested:
        item = by_nickname.get(nickname.casefold())
        if item is None:
            missing.append(nickname)
        else:
            resolved_requested.append((nickname, item))

    if missing:
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "message": "Custom schedule cancelled. Nickname(s) not found in satellites.yaml: "
                       + ", ".join(missing),
        }

    # Validate only the nicknames selected in THIS request.  Multiple aliases
    # may legitimately exist in satellites.yaml, but a custom schedule may not
    # contain two selected nicknames that resolve to the same catalog_number.
    # This makes the validation retryable: the user can uncheck one conflicting
    # alias and press Schedule again without closing the dialog.
    selected_by_catalog = {}
    missing_catalog_numbers = []

    for requested_nickname, item in resolved_requested:
        catalog_value = item.get("catalog_number")
        catalog_key = _normalize_catalog_number_for_custom_schedule(catalog_value)
        if not catalog_key:
            missing_catalog_numbers.append(requested_nickname)
            continue

        group = selected_by_catalog.setdefault(
            catalog_key,
            {
                "catalog_number": catalog_value,
                "nicknames": [],
            },
        )
        group["nicknames"].append(requested_nickname)

    if missing_catalog_numbers:
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "retryable": True,
            "validation_error": "missing_catalog_number",
            "message": (
                "Custom schedule not started. The following selected nickname(s) have no "
                "catalog_number: " + ", ".join(repr(name) for name in missing_catalog_numbers)
                + ". Change the selections and press Schedule again."
            ),
        }

    conflicts = [
        group
        for group in selected_by_catalog.values()
        if len(group["nicknames"]) > 1
    ]

    if conflicts:
        conflict_text = "; ".join(
            "catalog {catalog}: {names}".format(
                catalog=group["catalog_number"],
                names=", ".join(repr(name) for name in group["nicknames"]),
            )
            for group in conflicts
        )
        return {
            "ok": False,
            "action": "custom_schedule_passes",
            "retryable": True,
            "validation_error": "duplicate_catalog_number",
            "message": (
                "Custom schedule not started because two or more selected nicknames "
                "refer to the same catalog_number: " + conflict_text
                + ". Change the selections and press Schedule again."
            ),
        }

    script_args = []
    for requested_nickname, item in resolved_requested:
        canonical_nickname = str(item.get("nickname") or requested_nickname).strip()
        script_args.extend(["--nickname", canonical_nickname])

    # Explicitly pass the same satellites.yaml file that was validated above.
    script_args.extend(["--satellites_yaml", filename])

    return _start_script_command(
        "custom_schedule_passes",
        "Custom Schedule Passes",
        SCHEDULE_PASSES_SCRIPT,
        script_args=script_args,
        refresh_schedule_after=True,
    )


def _find_test_pass_satellite(nickname, filename=SATELLITE_CONFIG_FILE):
    """Return one full satellites.yaml entry by nickname."""
    wanted = str(nickname or "").strip().casefold()
    if not wanted:
        return None
    if yaml is None:
        raise RuntimeError("PyYAML is required to read satellites.yaml.")
    with open(filename, "r", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    satellites = root.get("satellites", []) if isinstance(root, dict) else []
    if not isinstance(satellites, list):
        return None
    for item in satellites:
        if not isinstance(item, dict):
            continue
        if str(item.get("nickname") or "").strip().casefold() == wanted:
            return dict(item)
    return None


def _parse_frequency_range_mhz(value):
    """Return an ordered two-frequency tuple from a YAML passband range."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        values = value
    else:
        values = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(value or ""))
        if len(values) != 2:
            return None
    try:
        first, second = (float(values[0]), float(values[1]))
    except (TypeError, ValueError):
        return None
    low_mhz, high_mhz = sorted((first, second))
    if low_mhz <= 0 or high_mhz <= low_mhz:
        return None
    return low_mhz, high_mhz


def load_fdt_passband(nickname, filename=SATELLITE_CONFIG_FILE):
    """Load the active nickname's FDT passband limits and center frequencies."""
    try:
        satellite = _find_test_pass_satellite(nickname, filename)
    except Exception as exc:
        return {"error": f"Could not read FDT passband settings: {exc}"}
    if satellite is None:
        return {}

    def band_payload(direction):
        range_key = f"{direction}_frequency_range_mhz"
        center_key = f"{direction}_frequency_mhz"
        frequency_range = _parse_frequency_range_mhz(satellite.get(range_key))
        try:
            center_mhz = float(satellite.get(center_key))
        except (TypeError, ValueError):
            center_mhz = None
        if frequency_range is None or center_mhz is None or center_mhz <= 0:
            return None
        low_mhz, high_mhz = frequency_range
        return {
            "low_mhz": low_mhz,
            "high_mhz": high_mhz,
            "center_mhz": center_mhz,
            "range_text": str(satellite.get(range_key) or "").strip(),
        }

    return {
        "nickname": str(satellite.get("nickname") or nickname).strip(),
        "satellite_name": str(satellite.get("satellite_name") or nickname).strip(),
        "satellite_type": str(satellite.get("satellite_type") or "").strip(),
        "transponder_type": str(satellite.get("transponder_type") or "").strip(),
        "downlink": band_payload("downlink"),
        "uplink": band_payload("uplink"),
    }


def schedule_test_pass_from_web(nickname):
    """Insert a ten-minute test pass beginning ten seconds from now."""
    satellite = _find_test_pass_satellite(nickname)
    if satellite is None:
        return {
            "ok": False,
            "action": "run_test_pass",
            "message": f"Satellite {nickname!r} was not found in {SATELLITE_CONFIG_FILE}.",
        }

    start = utc_now() + timedelta(seconds=10)
    end = start + timedelta(minutes=10)
    start_text = start.isoformat().replace("+00:00", "Z")
    end_text = end.isoformat().replace("+00:00", "Z")

    entry = {
        "nickname": str(satellite.get("nickname") or nickname).strip(),
        "satellite_name": str(satellite.get("satellite_name") or nickname).strip(),
        "satellite_type": str(satellite.get("satellite_type") or "").strip(),
        "start": start_text,
        "end": end_text,
        "max_elevation": TEST_PASS_ELEVATION,
        "test_pass": True,
    }

    try:
        try:
            schedule = load_schedule(SCHEDULE_FILE)
        except FileNotFoundError:
            schedule = {"passes": []}
        if not isinstance(schedule, dict):
            schedule = {"passes": []}
        passes = schedule.get("passes")
        if not isinstance(passes, list):
            passes = []
            schedule["passes"] = passes
        passes.append(entry)
        passes.sort(key=lambda value: str(value.get("start") or ""))
        _write_schedule_atomic(schedule, SCHEDULE_FILE)
        refresh_schedule_state(SCHEDULE_FILE)
    except Exception as exc:
        return {
            "ok": False,
            "action": "run_test_pass",
            "message": f"Could not insert test pass into {SCHEDULE_FILE}: {exc}",
        }

    message = (
        f"Test pass for {entry['nickname']} scheduled for {start.strftime('%H:%M:%S')} UTC "
        f"through {end.strftime('%H:%M:%S')} UTC."
    )
    with STATE_LOCK:
        APP_STATE["last_message"] = message
        APP_STATE["last_command"] = "run_test_pass"
        APP_STATE["last_command_result"] = {
            "ok": True,
            "action": "run_test_pass",
            "nickname": entry["nickname"],
            "start": start_text,
            "end": end_text,
            "message": message,
        }
    print(f"[TEST PASS] {message}")
    return dict(APP_STATE["last_command_result"])


def _shorten_schedule_pass_end(current_key, new_end):
    """Set the current schedule entry's end time to new_end and return its new key."""
    if not current_key:
        return None
    try:
        schedule = load_schedule(SCHEDULE_FILE)
    except Exception:
        return None
    if not isinstance(schedule, dict):
        return None
    passes = schedule.get("passes", [])
    if not isinstance(passes, list):
        return None

    new_end_text = new_end.isoformat().replace("+00:00", "Z")
    updated_entry = None
    for entry in passes:
        if isinstance(entry, dict) and pass_key(entry) == current_key:
            entry["end"] = new_end_text
            entry["ended_early"] = True
            updated_entry = entry
            break

    if updated_entry is None:
        return None

    _write_schedule_atomic(schedule, SCHEDULE_FILE)
    refresh_schedule_state(SCHEDULE_FILE)
    return pass_key(updated_entry)


def end_current_pass_from_web():
    """Request early termination of whichever pass is currently running."""
    with STATE_LOCK:
        tracking_running = bool(APP_STATE.get("tracking_running"))
        current_key = APP_STATE.get("current_pass_key")
        current_satellite = APP_STATE.get("current_satellite")

    if not tracking_running or not current_key:
        return {
            "ok": False,
            "action": "end_current_pass",
            "message": "There is no current pass to end.",
        }

    now = utc_now()
    new_key = _shorten_schedule_pass_end(current_key, now)
    if new_key:
        with STATE_LOCK:
            APP_STATE["current_pass_key"] = new_key

    # Mark the pass as ending before clearing FDT so a queued wheel request
    # cannot recreate tuning data while the tracking process is stopping.
    CURRENT_PASS_END_EVENT.set()
    clear_fdt_control_state("End Current Pass requested")
    message = f"Ending current pass for {current_satellite or 'satellite'} early."
    with STATE_LOCK:
        APP_STATE["last_message"] = message
        APP_STATE["last_command"] = "end_current_pass"
        APP_STATE["last_command_result"] = {
            "ok": True,
            "action": "end_current_pass",
            "message": message,
        }
    print(f"[TRACKING] {message}")
    return dict(APP_STATE["last_command_result"])


def sleep_until_pass_end_or_timeout(total_seconds, step_seconds=0.25):
    """Wait for normal LOS, application shutdown, or Tracking > End Current Pass."""
    try:
        total_seconds = float(total_seconds)
    except (TypeError, ValueError):
        total_seconds = 0.0

    deadline = time.monotonic() + max(0.0, total_seconds)
    while True:
        if SHUTDOWN_EVENT.is_set():
            return "shutdown"
        if CURRENT_PASS_END_EVENT.is_set():
            return "ended_early"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        time.sleep(min(step_seconds, remaining))


def wait_until(target_time):
    """Sleep until target UTC time, while allowing Ctrl+C or File > Exit to stop cleanly."""
    if SHUTDOWN_EVENT.is_set():
        return False

    now = utc_now()
    delta = (target_time - now).total_seconds()
    if delta > 0:
        msg = f"Waiting {delta:.1f} seconds until {target_time.isoformat()}"
        print(f"[WAITING] {msg}")
        with STATE_LOCK:
            APP_STATE["last_message"] = msg
        if not sleep_until_shutdown_or_timeout(delta):
            print("[INFO] Exit requested while waiting for the next pass.")
            return False

    return not SHUTDOWN_EVENT.is_set()


def safe_subprocess_cmd(
    sat_name,
    status_file=STATUS_FILE,
    rigctld_pid_file=RIGCTLD_PID_FILE,
    pass_program_pid_file=PASS_PROGRAM_PID_FILE,
    antenna_tracking_override_file=ANTENNA_TRACKING_OVERRIDE_FILE,
    fdt_control_file=FDT_CONTROL_FILE,
    stop_file=TRACKING_STOP_FILE,
    test_pass=False,
):
    """Use the current Python executable when launching the tracking script."""
    command = [
        PYTHON_PATH,
        TRACKING_SCRIPT,  # MARKER - Code this better relies on fallback to find satellites.yaml in bqe_config, rather than TLD.
        sat_name,
        "--status_file", status_file,
        "--rigctld_pid_file", rigctld_pid_file,
        "--pass_program_pid_file", pass_program_pid_file,
        "--antenna_tracking_override_file", antenna_tracking_override_file,
        "--fdt_control_file", fdt_control_file,
        "--stop_file", stop_file,
    ]
    if test_pass:
        command.append("--test_pass")
    return command


def terminate_rigctld_from_pid_file(pid_file=RIGCTLD_PID_FILE):
    """Terminate the specific rigctld process whose PID was written by the tracker.

    This is safer than killing every process named rigctld/rigctld.exe, and it
    works on both Windows and Linux.
    """
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            pid_text = f.read().strip()
        if not pid_text:
            return
        pid = int(pid_text)
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"Warning: could not read rigctld PID file {pid_file}: {e}")
        return

    print(f"[INFO] Ensuring rigctld PID {pid} is stopped...")
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)
                # os.kill(pid, 0) raises if the process no longer exists.
                os.kill(pid, 0)
                print(f"rigctld PID {pid} did not exit after SIGTERM; sending SIGKILL...")
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as e:
        print(f"Warning: unable to terminate rigctld PID {pid}: {e}")
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError:
            pass


def terminate_process_from_pid_file(pid_file, process_name="process"):
    """Terminate the process whose PID is stored in pid_file.

    This is used as a safety-net cleanup for helper programs launched during a
    pass. It works on both Windows and Linux and removes the PID file afterward.
    """
    try:
        with open(pid_file, "r", encoding="utf-8") as f:
            pid_text = f.read().strip()
        if not pid_text:
            return
        pid = int(pid_text)
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"Warning: could not read {process_name} PID file {pid_file}: {e}")
        return

    print(f"[INFO] Ensuring {process_name} PID {pid} is stopped...")
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)
                # os.kill(pid, 0) raises if the process no longer exists.
                os.kill(pid, 0)
                print(f"{process_name} PID {pid} did not exit after SIGTERM; sending SIGKILL...")
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as e:
        print(f"Warning: unable to terminate {process_name} PID {pid}: {e}")
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError:
            pass


def request_tracking_process_stop(process, stop_file=TRACKING_STOP_FILE, timeout=15):
    """Ask the tracker to clean up its pass helper, then use force as fallback.

    The request-file handshake is intentionally cross-platform.  It lets the
    tracker tell bqe_sound_recorder.py to flush and close its MP3 before either
    process exits, including on Windows where Popen.terminate() is uncatchable.
    """
    if process is None:
        return

    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    try:
        os.makedirs(os.path.dirname(os.path.abspath(stop_file)), exist_ok=True)
        temporary_file = "{}.{}.{}.tmp".format(
            stop_file, os.getpid(), threading.get_ident()
        )
        with open(temporary_file, "w", encoding="utf-8") as f:
            f.write("stop\n")
        os.replace(temporary_file, stop_file)
        print("[INFO] Requested orderly tracking/pass-program shutdown...")
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[WARNING] Tracker did not exit after the clean-stop timeout; terminating it...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("[WARNING] Tracker still did not exit; force killing it...")
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    except Exception as e:
        print(f"Warning: unable to request orderly tracking shutdown: {e}")
        terminate_popen_process(process, "tracking process")
    finally:
        try:
            os.remove(stop_file)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"Warning: could not remove tracking stop request {stop_file}: {e}")


def run_pass(pass_entry, start, end):
    """Run tracking subprocess for a satellite and terminate it at end time."""
    global CURRENT_TRACKING_PROCESS

    if SHUTDOWN_EVENT.is_set():
        return

    # Serialize the handoff with manual starts.  Mark tracking active before
    # releasing the lock so a browser request cannot restart idle recording.
    with AUDIO_RECORDING_LOCK:
        stop_audio_recording()
        with STATE_LOCK:
            APP_STATE["tracking_running"] = True

    CURRENT_PASS_END_EVENT.clear()
    sat_name = pass_entry["_satellite"]
    os.makedirs(LOG_DIR, exist_ok=True)

    # Always begin from the configured transponder midpoint.  Clear any stale
    # control file before the new pass is exposed as active to the web UI.
    clear_fdt_control_state("starting a new pass")
    with STATE_LOCK:
        APP_STATE["fdt_recalculation_interval"] = FDT_RECALCULATION_INTERVAL
        APP_STATE["fdt_recalculation_interval_override"] = None

    timestamp = start.strftime("%Y%m%dT%H%M%SZ")
    safe_name = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in sat_name)
    log_filename = os.path.join(LOG_DIR, f"{safe_name}_{timestamp}.log")

    print(f"\n######### Starting tracking for {sat_name} at {start.isoformat()} ##########")
    print(f"Logging output to: {log_filename}")

    with STATE_LOCK:
        APP_STATE["current_pass_key"] = pass_entry["_key"]
        APP_STATE["current_satellite"] = sat_name
        APP_STATE["current_log"] = log_filename
        APP_STATE["tracking_running"] = True
        APP_STATE["idle_task_active"] = False
        APP_STATE["last_message"] = f"Tracking {sat_name} until {end.isoformat()}"
        APP_STATE["tracking_status_file"] = STATUS_FILE

    # A pass has now started, so the idle-task status is no longer valid.
    remove_idle_task_status_file()

    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except OSError:
        pass
    try:
        if os.path.exists(TRACKING_STOP_FILE):
            os.remove(TRACKING_STOP_FILE)
    except OSError as e:
        print(f"Warning: could not clear stale tracking stop request: {e}")
    with open(log_filename, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            safe_subprocess_cmd(
                sat_name,
                STATUS_FILE,
                RIGCTLD_PID_FILE,
                PASS_PROGRAM_PID_FILE,
                test_pass=bool(pass_entry.get("test_pass")),
            ),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        CURRENT_TRACKING_PROCESS = process

        duration = (end - utc_now()).total_seconds()
        if duration <= 0:
            print(f"⚠️ End time {end.isoformat()} already passed; skipping.")
            CURRENT_PASS_END_EVENT.set()
            clear_fdt_control_state("pass end time already elapsed")
            if process.poll() is None:
                request_tracking_process_stop(process)
            CURRENT_TRACKING_PROCESS = None
            terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
            terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
            terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")
            try:
                if os.path.exists(STATUS_FILE):
                    os.remove(STATUS_FILE)
            except OSError:
                pass
            with STATE_LOCK:
                APP_STATE["tracking_running"] = False
                APP_STATE["fdt_recalculation_interval"] = FDT_RECALCULATION_INTERVAL
                APP_STATE["fdt_recalculation_interval_override"] = None
                APP_STATE["completed"].append(pass_entry["_key"])
                APP_STATE["last_message"] = f"Skipped expired pass for {sat_name}."
                APP_STATE["current_pass_key"] = None
                APP_STATE["current_satellite"] = None
            return

        try:
            wait_result = sleep_until_pass_end_or_timeout(duration)
            if wait_result == "shutdown":
                print("[INFO] Exit requested. Stopping active tracking pass early.")
            elif wait_result == "ended_early":
                print(f"[INFO] End Current Pass requested. Stopping {sat_name} early.")
        finally:
            stop_time = utc_now()
            print(f"[INFO] Stopping {sat_name} at {stop_time.isoformat()} ...")
            CURRENT_PASS_END_EVENT.set()
            clear_fdt_control_state("satellite pass ended")
            if process.poll() is None:
                request_tracking_process_stop(process)

            CURRENT_TRACKING_PROCESS = None
            terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
            terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
            terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")

    with STATE_LOCK:
        APP_STATE["tracking_running"] = False
        APP_STATE["fdt_recalculation_interval"] = FDT_RECALCULATION_INTERVAL
        APP_STATE["fdt_recalculation_interval_override"] = None
        APP_STATE["completed"].append(pass_entry["_key"])
        APP_STATE["last_message"] = f"Completed {sat_name}; log saved to {log_filename}"
        APP_STATE["current_pass_key"] = None
        APP_STATE["current_satellite"] = None
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except OSError:
            pass

    print(f"[OK] Log saved to {log_filename}")



def discover_preset_nicknames(preset_root=PRESETS_DIR):
    """Return unique preset nicknames found at startup under presets/."""
    if yaml is None:
        print("Warning: PyYAML not available; preset buttons will not be populated.")
        return []

    patterns = (
        os.path.join(preset_root, "**", "*.yaml"),
        os.path.join(preset_root, "**", "*.yml"),
    )
    filenames = sorted(
        {filename for pattern in patterns for filename in glob.glob(pattern, recursive=True)},
        key=lambda filename: filename.casefold(),
    )

    presets = []
    seen_nicknames = set()
    for filename in filenames:
        try:
            with open(filename, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Warning: could not read preset {filename}: {e}")
            continue

        if not isinstance(cfg, dict):
            print(f"Warning: preset {filename} does not contain a YAML mapping; skipping it.")
            continue

        nickname = cfg.get("nickname") or cfg.get("NICKNAME")
        nickname = "" if nickname is None else str(nickname).strip()
        if not nickname:
            print(f"Warning: preset {filename} has no nickname; skipping it.")
            continue

        channel_name = cfg.get("channel_name") or cfg.get("CHANNEL_NAME")
        channel_name = "" if channel_name is None else str(channel_name).strip()

        nickname_key = nickname.casefold()
        if nickname_key in seen_nicknames:
            print(f"Warning: duplicate preset nickname {nickname!r} in {filename}; using the first occurrence.")
            continue

        seen_nicknames.add(nickname_key)
        presets.append({
            "nickname": nickname,
            "channel_name": channel_name,
            "source_file": os.path.relpath(filename, SCRIPT_DIR),
        })

    presets.sort(key=lambda preset: preset["nickname"].casefold())
    print(f"[OK] Loaded {len(presets)} radio preset button(s) from {preset_root}")
    return presets


def satellite_pass_is_active(now=None):
    """Return True while tracking is running or the schedule says a pass is active."""
    now = now or utc_now()
    with STATE_LOCK:
        if APP_STATE.get("tracking_running"):
            return True
        passes = list(APP_STATE.get("passes", []))
    return any(p["_start_dt"] <= now <= p["_end_dt"] for p in passes)


def audio_recording_status():
    """Expose the shared pass recorder's actual state, including device errors."""
    with AUDIO_RECORDING_LOCK:
        if CURRENT_AUDIO_RECORDER is None:
            return {"status": "stopped", "active": False}
        status = CURRENT_AUDIO_RECORDER.snapshot()
        status["active"] = bool(
            CURRENT_AUDIO_RECORDER.thread and CURRENT_AUDIO_RECORDER.thread.is_alive()
        )
        return status


def stop_audio_recording():
    """Close capture and flush the MP3 before handing audio over to a pass."""
    with AUDIO_RECORDING_LOCK:
        if CURRENT_AUDIO_RECORDER is not None:
            CURRENT_AUDIO_RECORDER.stop()
            CURRENT_AUDIO_RECORDER.join()
        return audio_recording_status()


def make_audio_recorder():
    """Use the same capture settings and encoder as the configured pass recorder."""
    from plugins.bqe_sound_recorder.bqe_sound_recorder import LoopbackRecorder, parse_args

    if yaml is None:
        raise RuntimeError("PyYAML is required to read the pass recording settings.")
    with open(SATELLITE_CONFIG_FILE, "r", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    recorder_args = []
    for satellite in root.get("satellites", []):
        if not isinstance(satellite, dict):
            continue
        command = satellite.get("program_to_run_during_pass")
        if not command:
            continue
        command = command_from_yaml_value(command)
        recorder_index = next((
            i for i, part in enumerate(command)
            if os.path.basename(part).lower() == "bqe_sound_recorder.py"
        ), None)
        if recorder_index is not None:
            recorder_args = command[recorder_index + 1:]
            break
    try:
        options = parse_args(recorder_args)
    except SystemExit as exc:
        raise ValueError("The configured pass recorder arguments are invalid.") from exc
    rig_path = os.path.join(SCRIPT_DIR, "bqe_config", "my_rig.yaml")
    with open(rig_path, "r", encoding="utf-8") as f:
        radio_config = yaml.safe_load(f) or {}
    if radio_config.get("radio_is_sdr") is True:
        from plugins.bqe_sdr.integration import audio_input
        # Subscribe to the idle receiver rather than opening a soundcard or
        # competing for the USB dongle. The bridge supplies 48 kHz mono PCM.
        options.input_device = audio_input(radio_config)
        options.device = None
        options.sample_rate = 48000
    directory = load_web_console_settings(GENERAL_SETTINGS_FILE).recordings_location
    directory = os.path.expanduser(directory or "plugins/bqe_sound_recorder/recordings")
    if not os.path.isabs(directory):
        directory = os.path.join(SCRIPT_DIR, directory)
    return LoopbackRecorder(
        output_prefix=os.path.join(directory, "Audio Recording"),
        device_name=options.device,
        input_device_name=options.input_device,
        samplerate=options.sample_rate,
        bitrate=options.bitrate,
        timestamp_separator=" ",
    )


def control_audio_recording(action):
    """Start between passes, or stop and save the menu's current recording."""
    global CURRENT_AUDIO_RECORDER
    with AUDIO_RECORDING_LOCK:
        try:
            if action == "stop_audio_recording":
                status = stop_audio_recording()
                message = "Audio recording stopped."
                if status.get("output_file"):
                    message += f" Saved to {status['output_file']}"
            elif SHUTDOWN_EVENT.is_set() or satellite_pass_is_active():
                return {"ok": False, "action": action,
                        "message": "Record Audio is available when no pass is active."}
            elif audio_recording_status()["active"]:
                return {"ok": True, "action": action, "message": "Audio recording is already in progress."}
            else:
                CURRENT_AUDIO_RECORDER = make_audio_recorder()
                CURRENT_AUDIO_RECORDER.start()
                message = "Audio recording starting. It will stop automatically when a pass starts."
            result = {"ok": True, "action": action, "message": message}
        except Exception as exc:
            result = {"ok": False, "action": action, "message": f"Audio recording failed: {exc}"}
        _set_status_message(result["message"], last_command=action, last_command_result=result)
        return result


def program_preset_from_web(nickname):
    """Program a startup-discovered preset when no satellite pass is active."""
    nickname = str(nickname or "").strip()
    if not nickname:
        message = "Preset nickname was not supplied."
        return {"ok": False, "action": "program_preset", "message": message}

    with STATE_LOCK:
        startup_presets = {
            str(preset.get("nickname", "")).strip()
            for preset in APP_STATE.get("presets", [])
            if isinstance(preset, dict)
        }
        command_already_running = APP_STATE.get("preset_command_running", False)

    if nickname not in startup_presets:
        message = f"Preset {nickname!r} was not present in the presets folder at program startup."
        return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}

    if satellite_pass_is_active():
        message = f"Cannot program preset {nickname!r} while a satellite pass is active."
        return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}

    if command_already_running:
        message = "Another preset command is already running."
        return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}

    if idle_task_ctl is None:
        message = "Preset control is unavailable because bqe_set_radio_from_yaml could not be imported."
        _set_status_message(
            message,
            last_command="program_preset",
            last_command_result={"ok": False, "nickname": nickname, "message": message},
        )
        return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}

    with STATE_LOCK:
        # Atomically re-check both the live tracker flag and the scheduled pass
        # window before marking the preset command in progress.
        now = utc_now()
        pass_active = bool(APP_STATE.get("tracking_running")) or any(
            p["_start_dt"] <= now <= p["_end_dt"] for p in APP_STATE.get("passes", [])
        )
        if pass_active:
            message = f"Cannot program preset {nickname!r} while a satellite pass is active."
            return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}
        if APP_STATE.get("preset_command_running"):
            message = "Another preset command is already running."
            return {"ok": False, "action": "program_preset", "nickname": nickname, "message": message}
        APP_STATE["preset_command_running"] = True
        APP_STATE["last_command"] = "program_preset"
        APP_STATE["last_command_result"] = None
        APP_STATE["last_message"] = f"Programming radio preset {nickname}..."

    message = f"Programming radio preset {nickname}..."
    result = {
        "ok": False,
        "action": "program_preset",
        "nickname": nickname,
        "message": message,
    }
    helper_process = None
    try:
        # A wait-time helper belongs to the preset that launched it.  Stop it
        # before changing frequency/mode so a program such as WSJT-X or MMSSTV
        # from the previous preset cannot continue running against the newly
        # selected radio configuration.
        stop_idle_wait_program(
            reason=f"before applying radio preset {nickname!r}"
        )
        idle_task_ctl.program_preset_by_nickname(nickname)
        helper_process = start_idle_wait_program(preset_nickname=nickname)

        status_updated = update_idle_task_status_for_preset(nickname)
        message = f"Radio programmed with preset {nickname}. Waiting for the next pass."
        if status_updated:
            message += " Updated idle_task_status.yaml."
        if helper_process is not None:
            message += f" Started its wait-time program (PID {helper_process.pid})."

        result = {
            "ok": True,
            "action": "program_preset",
            "nickname": nickname,
            "message": message,
        }
        print(f"[WEB PRESET] {message}")
        return result
    except Exception as e:
        stop_idle_wait_program(
            helper_process,
            reason=f"after radio preset {nickname!r} failed",
        )
        message = f"Could not program preset {nickname!r}: {e}"
        result = {
            "ok": False,
            "action": "program_preset",
            "nickname": nickname,
            "message": message,
        }
        print(f"[WEB PRESET] {message}")
        return result
    finally:
        with STATE_LOCK:
            APP_STATE["preset_command_running"] = False
            APP_STATE["last_message"] = message
            APP_STATE["last_command_result"] = result

def load_preset_config_by_nickname(nickname):
    """Find and load a preset YAML file by its nickname field.

    The idle configuration is intentionally read here because bqe_wisp needs to
    know which idle task to run before calling bqe_set_radio_from_yaml.py.
    """
    preset_root = os.path.join(SCRIPT_DIR, "presets")
    patterns = [
        os.path.join(preset_root, "**", "*.yaml"),
        os.path.join(preset_root, "**", "*.yml"),
    ]

    wanted = str(nickname).strip()
    for pattern in patterns:
        for filename in glob.glob(pattern, recursive=True):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Warning: could not read preset {filename}: {e}")
                continue

            if not isinstance(cfg, dict):
                continue

            preset_nickname = cfg.get("nickname") or cfg.get("NICKNAME")
            if preset_nickname is not None and str(preset_nickname).strip() == wanted:
                cfg["_source_file"] = filename
                return cfg

    return None


def remove_idle_task_status_file():
    """Remove the idle-task status file, if present."""
    try:
        os.remove(IDLE_TASK_STATUS_FILE)
        print(f"[INFO] Removed idle task status file: {IDLE_TASK_STATUS_FILE}")
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        print(f"Warning: could not remove idle task status file {IDLE_TASK_STATUS_FILE}: {e}")
        return False


def _copy_preset_to_idle_task_status(preset_cfg):
    """Atomically copy a selected preset YAML file into the program folder."""
    source_file = preset_cfg.get("_source_file") if isinstance(preset_cfg, dict) else None
    if not source_file:
        raise ValueError("Selected preset does not identify its source YAML file.")

    source_file = os.path.abspath(str(source_file))
    if not os.path.isfile(source_file):
        raise FileNotFoundError(f"Preset YAML file not found: {source_file}")

    temporary_file = IDLE_TASK_STATUS_FILE + ".tmp"
    try:
        with open(source_file, "rb") as source, open(temporary_file, "wb") as destination:
            shutil.copyfileobj(source, destination)
        os.replace(temporary_file, IDLE_TASK_STATUS_FILE)
    finally:
        try:
            if os.path.exists(temporary_file):
                os.remove(temporary_file)
        except OSError:
            pass

    print(
        f"[INFO] Copied idle preset {source_file} to "
        f"{IDLE_TASK_STATUS_FILE}"
    )
    return IDLE_TASK_STATUS_FILE


def begin_idle_task_status(nickname):
    """Mark an idle task active and create its status YAML from the selected preset."""
    preset_cfg = load_preset_config_by_nickname(nickname)
    if not preset_cfg:
        raise KeyError(f"Idle preset {nickname!r} was not found.")

    with STATE_LOCK:
        if APP_STATE.get("tracking_running"):
            return False
        _copy_preset_to_idle_task_status(preset_cfg)
        APP_STATE["idle_task_active"] = True
    return True


def update_idle_task_status_for_preset(nickname):
    """Update the status YAML after a user changes presets during an idle task."""
    preset_cfg = load_preset_config_by_nickname(nickname)
    if not preset_cfg:
        raise KeyError(f"Preset {nickname!r} was not found.")

    with STATE_LOCK:
        if not APP_STATE.get("idle_task_active") or APP_STATE.get("tracking_running"):
            return False
        _copy_preset_to_idle_task_status(preset_cfg)
    return True


def command_from_yaml_value(value):
    """Convert a YAML command value into a subprocess argument list."""
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if str(part).strip()]
    return shlex.split(str(value), posix=(os.name != "nt"))


def start_idle_wait_program(preset_nickname=None):
    """Start the external wait-time program for a preset.

    When ``preset_nickname`` is omitted, use ``radio_idle_preset_nickname``
    from bqe_config/my_rig.yaml.  A caller such as the web preset handler can
    supply a nickname so the helper belongs to the preset the operator actually
    selected instead of the configured default idle preset.

    Expected flow:
      1. Use the supplied preset nickname, or read radio_idle_preset_nickname
         from bqe_config/my_rig.yaml for an automatic idle transition.

      2. Find a YAML preset under presets/ whose nickname matches that value.

      3. Read that preset's tuning and  program_to_run_while_waiting values.

      4. The caller must finish tuning before calling this helper.
         If a program is specified, wait idle_tuning_delay_seconds from
         general_settings.yaml, then launch program_to_run_while_waiting.

    Note: radio_idle_preset_nickname is a preset nickname.  The nickname metadata contains
      information on where to tune the radio before releasing Hamlib control,  and can optionally
      specify the name of a specific companion program to run during the wait period as well.  
      This companion program is defined by the key 'program_to_run_while_waiting' within the 
      yaml preset metadata, and should be a CLI command such as 'wsjtx', or 'mmsstv' etc.  
      Other syntaxes may launch, but may not terminate properly.  
    """
    global CURRENT_IDLE_PROCESS

    if SHUTDOWN_EVENT.is_set():
        return None

    if yaml is None:
        print("Warning: PyYAML not available; skipping idle wait program.")
        return None

    idle_preset_nickname = preset_nickname
    if idle_preset_nickname is None:
        radio_config_path = os.path.join(SCRIPT_DIR, "bqe_config", "my_rig.yaml")
        try:
            with open(radio_config_path, "r", encoding="utf-8") as f:
                radio_cfg = yaml.safe_load(f) or {}
            idle_preset_nickname = radio_cfg.get("radio_idle_preset_nickname")
        except FileNotFoundError:
            print(f"Warning: rig config file {radio_config_path} not found. Skipping idle wait program.")
            return None
        except Exception as e:
            print(f"Warning: Could not parse rig config file {radio_config_path}: {e}. Skipping idle wait program.")
            return None

    if not idle_preset_nickname or idle_preset_nickname == 'None' or idle_preset_nickname == '':
        print("Warning: no idle preset nickname is set. Skipping idle wait program.")
        return None

    idle_preset_nickname = str(idle_preset_nickname).strip()
    program_to_run = None
    try:
        idle_preset_cfg = load_preset_config_by_nickname(idle_preset_nickname)
        if not idle_preset_cfg:
            print(
                f"Warning: idle preset {idle_preset_nickname!r} was not found under "
                f"{os.path.join(SCRIPT_DIR, 'presets')}."
            )
            return None

        program_to_run = idle_preset_cfg.get("program_to_run_while_waiting")
        # SSTV reception has its own lifecycle and may run alongside an
        # optional recorder/helper, including presets with no helper command.
        from plugins.bqe_sstv_decoder.integration import SSTV_RECEIVER
        from plugins.bqe_ssdv_decoder.integration import SSDV_RECEIVER
        from plugins.bqe_sdr.integration import SDR_RECEIVER
        from plugins.bqe_lrpt_decoder.integration import LRPT_RECEIVER, enabled as lrpt_enabled
        if lrpt_enabled(idle_preset_cfg):
            LRPT_RECEIVER.start(idle_preset_cfg, idle_preset_nickname)
        else:
            SDR_RECEIVER.start(idle_preset_cfg, idle_preset_nickname)
        for receiver in (SSTV_RECEIVER, SSDV_RECEIVER):
            try:
                receiver.start(idle_preset_cfg, idle_preset_nickname)
            except Exception as exc:
                print(f'[WARNING] Could not start {receiver.label} reception: {exc}')
        if not program_to_run:
            print(
                f"[INFO] Idle preset {idle_preset_nickname!r} was found in "
                f"{idle_preset_cfg.get('_source_file', 'unknown file')}, but it has no "
                "program_to_run_while_waiting entry; no idle program will be run."
            )
            return None

        command = command_from_yaml_value(program_to_run)
        if not command:
            print(f"[INFO]: program_to_run_while_waiting for idle preset {idle_preset_nickname!r} is empty.")
            return None

        tuning_delay = GENERAL_SETTINGS["idle_tuning_delay_seconds"]
        print(f"[INFO] Waiting {tuning_delay:g} seconds after tuning before launching the idle program.")
        if not sleep_until_shutdown_or_timeout(tuning_delay):
            return None

        print("[INFO] Starting idle wait program while waiting for next satellite")
        print(f"[INFO] Idle preset nickname: {idle_preset_nickname}")
        print(f"[INFO] Idle preset file: {idle_preset_cfg.get('_source_file', 'unknown file')}")
        print(f"Command: {' '.join(command)}")

        os.makedirs(os.path.dirname(IDLE_WAIT_PROGRAM_PID_FILE), exist_ok=True)
        popen_kwargs = {"cwd": SCRIPT_DIR}
        if os.name == "nt":
            # Give Windows GUI/console programs their own process group.
            # taskkill /T below will still be used for reliable cleanup.
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            # Put the idle program and any children in their own process group so
            # Linux cleanup can stop the whole group before a satellite pass starts.
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        CURRENT_IDLE_PROCESS = process
        with open(IDLE_WAIT_PROGRAM_PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(process.pid))

        with STATE_LOCK:
            APP_STATE["last_message"] = f"Started idle wait program: {' '.join(command)}"
        return process

    except FileNotFoundError:
        print(
            f"Warning: idle wait program {program_to_run!r} was not found on the system PATH."
        )
    except Exception as e:
        print(f"Warning: idle wait program failed to start: {e}")

    return None


def stop_idle_wait_program(
        process=None,
        pid_file=IDLE_WAIT_PROGRAM_PID_FILE,
        reason="before satellite pass starts"):
    """Stop an idle wait program before a pass or another preset replaces it.

    It is cross-platform: Windows uses taskkill /T /F to include child
    processes, and Linux/macOS use the process group created by
    start_new_session=True.
    """
    global CURRENT_IDLE_PROCESS

    from plugins.bqe_sstv_decoder.integration import SSTV_RECEIVER
    from plugins.bqe_ssdv_decoder.integration import SSDV_RECEIVER
    from plugins.bqe_sdr.integration import SDR_RECEIVER
    from plugins.bqe_lrpt_decoder.integration import LRPT_RECEIVER
    SSTV_RECEIVER.stop()
    SSDV_RECEIVER.stop()
    SDR_RECEIVER.stop()
    LRPT_RECEIVER.stop()

    if process is None:
        process = CURRENT_IDLE_PROCESS
    if process is None:
        return

    pid = process.pid
    if process.poll() is not None:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError:
            pass
        if CURRENT_IDLE_PROCESS is process:
            CURRENT_IDLE_PROCESS = None
        return

    reason_text = str(reason or "").strip() or "during idle-program cleanup"
    print(f"Stopping idle wait program PID {pid} {reason_text}...")
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("Idle wait program did not exit cleanly; force killing process group...")
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
    except Exception as e:
        print(f"Warning: unable to stop idle wait program PID {pid}: {e}")
    finally:
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError:
            pass
        if CURRENT_IDLE_PROCESS is process:
            CURRENT_IDLE_PROCESS = None
        with STATE_LOCK:
            APP_STATE["last_message"] = f"Stopped idle wait program {reason_text}."


def terminate_popen_process(process, process_name="process", timeout=10):
    """Terminate a live subprocess object without raising cleanup errors."""
    if process is None:
        return

    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    pid = getattr(process, "pid", None)
    print(f"[INFO] Stopping {process_name} PID {pid}...")
    try:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[WARNING] {process_name} did not exit cleanly; force killing...")
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    except Exception as e:
        print(f"Warning: unable to terminate {process_name} PID {pid}: {e}")


def cleanup_helper_programs():
    """Stop helper programs launched by the scheduler or active pass."""
    global CURRENT_TRACKING_PROCESS, CURRENT_IDLE_PROCESS, CURRENT_WEB_COMMAND_PROCESS

    stop_audio_recording()

    terminate_popen_process(CURRENT_WEB_COMMAND_PROCESS, "web command process")
    CURRENT_WEB_COMMAND_PROCESS = None

    request_tracking_process_stop(CURRENT_TRACKING_PROCESS)
    CURRENT_TRACKING_PROCESS = None

    stop_idle_wait_program(CURRENT_IDLE_PROCESS)
    CURRENT_IDLE_PROCESS = None

    terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
    terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
    terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")
    terminate_process_from_pid_file(IDLE_WAIT_PROGRAM_PID_FILE, "idle wait program")
    clear_fdt_control_state("application/helper cleanup")

    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except OSError:
        pass

    with STATE_LOCK:
        APP_STATE["idle_task_active"] = False
    remove_idle_task_status_file()


def request_shutdown(reason="Exit requested."):
    """Request an orderly application shutdown from the web UI or console."""
    SHUTDOWN_EVENT.set()
    with STATE_LOCK:
        APP_STATE["shutdown_requested"] = True
        APP_STATE["last_message"] = reason
        APP_STATE["command_running"] = False
        APP_STATE["last_command"] = "exit_app"
        APP_STATE["last_command_result"] = {"ok": True, "action": "exit_app", "message": reason}

    print(f"[INFO] {reason}")

    # Stop long-running helpers immediately. The scheduler loop also notices the
    # shutdown event and performs the same cleanup in its final block, so this is
    # safe if it runs twice.
    cleanup_helper_programs()

    return {"ok": True, "action": "exit_app", "message": reason}


def request_restart():
    """Request an orderly shutdown followed by a restart of this script."""
    with STATE_LOCK:
        APP_STATE["restart_requested"] = True

    result = request_shutdown(
        "Restart requested from File > Restart Server. Cleaning up helper programs..."
    )
    result["action"] = "restart_server"
    with STATE_LOCK:
        APP_STATE["last_command"] = "restart_server"
        APP_STATE["last_command_result"] = result
    return result


def wait_for_shutdown(interval_seconds=None):
    """Keep the web console alive until File > Exit or Ctrl+C requests shutdown."""
    interval = interval_seconds or SCHEDULER_SLEEP_INTERVAL_SECONDS
    while not SHUTDOWN_EVENT.is_set():
        if not sleep_until_shutdown_or_timeout(interval):
            break


# In between passes, we can tune the radio to some other frequency/mode via selecting a nickname from presets
#   yamls.

# We can also launch a helper program to run alongside this program to analyze data, decode sstv, monitor wspr etc.

def do_while_waiting():  # The specific waiting task is defined as radio_idle_preset_nickname in my_rig.yaml
    """Keep the radio busy while waiting. Currently tunes to an idle USB frequency."""
    if rig is None:
        print("Warning: bqe_hamlib_interface not available; skipping idle radio task.")
        return
    if yaml is None:
        print("Warning: PyYAML not available; skipping idle radio task.")
        return
    if idle_task_ctl is None:
        print("Warning - Idle task control module is not present")
        return

    radio_config_path = "bqe_config/my_rig.yaml"  #There is an assumption here that we are using a shared config file.
    try:
        with open(radio_config_path, "r", encoding="utf-8") as f:
            radio_cfg = yaml.safe_load(f) or {}
            nickname = radio_cfg.get("radio_idle_preset_nickname")
    
    except FileNotFoundError:
        print(f"Warning: rig config file {radio_config_path} not found. Skipping idle radio task.")
        return
    
    except Exception as e:
        print(f"Warning: Could not parse rig config file {radio_config_path}: {e}. Skipping idle radio task.")
        return
    
    print("Switching to idle radio task while waiting for next satellite\n")
    print("Idle task nickname is ", nickname )
    if nickname is not None and nickname != '':
        # Preserve the automatic idle transition pause, then release any old
        # companion's radio access before tuning and launching the new one.
        if not sleep_until_shutdown_or_timeout(5):
            return None
        stop_idle_wait_program(reason=f"before applying idle preset {nickname!r}")
        idle_task_ctl.program_preset_by_nickname(nickname)
        helper_process = start_idle_wait_program(preset_nickname=nickname)
        try:
            begin_idle_task_status(nickname)
        except Exception as e:
            print(f"Warning: could not create {IDLE_TASK_STATUS_FILE}: {e}")
    else:
        print("[INFO] Idle_preset_nickname not defined.  Skipping idle task start")
        helper_process = None

    return helper_process


def row_status(pass_entry, now=None):
    """Return pending/active/done for a pass based on current UTC time."""
    now = now or utc_now()
    if pass_entry["_start_dt"] <= now <= pass_entry["_end_dt"]:
        return "active"
    if pass_entry["_end_dt"] < now:
        return "done"
    return "pending"


def format_dt(dt):
    """Format like old WISP screen: 06-19-26 10:43:57."""
    return dt.strftime("%m-%d-%y %H:%M:%S")


def countdown_text(now, passes):
    """Return countdown to next AOS or LOS."""
    active = [p for p in passes if p["_start_dt"] <= now <= p["_end_dt"]]
    if active:
        seconds = max(0, int((active[0]["_end_dt"] - now).total_seconds()))
        label = "LOS"
    else:
        future = [p for p in passes if p["_start_dt"] > now]
        if not future:
            return "--:--:--", "DONE"
        seconds = max(0, int((future[0]["_start_dt"] - now).total_seconds()))
        label = "AOS"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}", label





def _walk_mappings(value):
    """Yield nested dictionaries from a parsed YAML value."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _coerce_coordinate(value):
    """Convert a numeric or simple text coordinate to float.

    Accepts plain decimals, strings with units, and common hemisphere suffixes
    such as "42.7833 N" or "71.5167 W".  The map code needs signed decimal
    degrees, so south/west suffixes are converted to negative values.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    try:
        coordinate = float(match.group(0))
    except ValueError:
        return None

    hemisphere_match = re.search(r"(?:^|[^A-Za-z])([NSEW])(?:[^A-Za-z]|$)", text, re.IGNORECASE)
    if hemisphere_match:
        hemisphere = hemisphere_match.group(1).upper()
        if hemisphere in {"S", "W"}:
            coordinate = -abs(coordinate)
        elif hemisphere in {"N", "E"}:
            coordinate = abs(coordinate)

    return coordinate


def _compact_key(value):
    """Normalize a mapping key so variants like qth-lat and qth_lat match."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _first_nested_coordinate(root, names):
    """Find the first coordinate value matching one of the supplied key names."""
    wanted = {str(name).lower() for name in names}
    wanted_compact = {_compact_key(name) for name in names}
    for mapping in _walk_mappings(root):
        for key, value in mapping.items():
            key_text = str(key).lower()
            if key_text in wanted or _compact_key(key) in wanted_compact:
                coordinate = _coerce_coordinate(value)
                if coordinate is not None:
                    return coordinate
    return None


def _first_direct_coordinate(mapping, names):
    """Find a coordinate in only one mapping, without walking nested objects."""
    if not isinstance(mapping, dict):
        return None
    wanted = {str(name).lower() for name in names}
    wanted_compact = {_compact_key(name) for name in names}
    for key, value in mapping.items():
        key_text = str(key).lower()
        if key_text in wanted or _compact_key(key) in wanted_compact:
            coordinate = _coerce_coordinate(value)
            if coordinate is not None:
                return coordinate
    return None


def _first_nested_text(root, names):
    """Find the first text value matching one of the supplied key names."""
    wanted = {name.lower() for name in names}
    for mapping in _walk_mappings(root):
        for key, value in mapping.items():
            if str(key).lower() in wanted and value not in (None, ""):
                return str(value).strip()
    return None


def load_observer_location(filename=QTH_CONFIG_FILE):
    """Read observer/QTH position from bqe_config/my_qth.yaml."""
    location = {
        "label": "Observer",
        "latitude": None,
        "longitude": None,
        "elevation_m": 0.0,
    }

    if yaml is None or not os.path.exists(filename):
        return location

    try:
        with open(filename, "r", encoding="utf-8") as f:
            root = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Warning: could not read observer location from {filename}: {e}")
        return location

    if not isinstance(root, (dict, list)):
        return location

    latitude = _first_nested_coordinate(
        root,
        (
            "latitude",
            "lat",
            "latitude_deg",
            "latitude_degrees",
            "observer_latitude",
            "observer_lat",
            "observer_latitude_deg",
            "observer_lat_deg",
            "qth_latitude",
            "qth_lat",
            "qth_latitude_deg",
            "qth_lat_deg",
            "home_latitude",
            "home_lat",
            "station_latitude",
            "station_lat",
            "my_latitude",
            "my_lat",
        ),
    )
    longitude = _first_nested_coordinate(
        root,
        (
            "longitude",
            "long",
            "lon",
            "lng",
            "longitude_deg",
            "longitude_degrees",
            "observer_longitude",
            "observer_lon",
            "observer_lng",
            "observer_longitude_deg",
            "observer_lon_deg",
            "observer_lng_deg",
            "qth_longitude",
            "qth_lon",
            "qth_lng",
            "qth_longitude_deg",
            "qth_lon_deg",
            "home_longitude",
            "home_lon",
            "station_longitude",
            "station_lon",
            "my_longitude",
            "my_lon",
        ),
    )
    elevation_m = _first_nested_coordinate(
        root,
        (
            "elevation",
            "elevation_m",
            "altitude",
            "altitude_m",
            "observer_elevation",
            "observer_elevation_m",
            "observer_altitude",
            "observer_altitude_m",
            "qth_elevation",
            "qth_elevation_m",
            "my_elevation",
            "my_altitude",
        ),
    )
    label = _first_nested_text(root, ("label", "name", "qth_name", "station_name", "station", "callsign"))

    if latitude is not None and -90.0 <= latitude <= 90.0:
        location["latitude"] = latitude
    if longitude is not None:
        # Normalize longitudes such as 190 degrees into the browser-friendly -180..180 range.
        location["longitude"] = ((longitude + 180.0) % 360.0) - 180.0
    if elevation_m is not None:
        location["elevation_m"] = elevation_m
    if label:
        location["label"] = label

    return location


def _normalize_degrees(value):
    """Normalize an angle to the range 0 <= angle < 360 degrees."""
    return float(value) % 360.0


def _signed_longitude(value):
    """Normalize a longitude to the range -180 <= longitude < 180."""
    return ((float(value) + 180.0) % 360.0) - 180.0


def _julian_date(when):
    """Convert an aware UTC datetime to a Julian Date."""
    when = when.astimezone(timezone.utc)
    year = when.year
    month = when.month
    day_fraction = (
        when.day
        + (when.hour + (when.minute + (when.second + when.microsecond / 1e6) / 60.0) / 60.0) / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    century = year // 100
    correction = 2 - century + century // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day_fraction
        + correction
        - 1524.5
    )


def _ecliptic_to_equatorial(x, y, z, obliquity_degrees):
    """Rotate rectangular ecliptic coordinates into equatorial coordinates."""
    obliquity = math.radians(obliquity_degrees)
    equatorial_x = x
    equatorial_y = y * math.cos(obliquity) - z * math.sin(obliquity)
    equatorial_z = y * math.sin(obliquity) + z * math.cos(obliquity)
    distance = math.sqrt(
        equatorial_x * equatorial_x
        + equatorial_y * equatorial_y
        + equatorial_z * equatorial_z
    )
    right_ascension = _normalize_degrees(
        math.degrees(math.atan2(equatorial_y, equatorial_x))
    )
    declination = math.degrees(math.asin(equatorial_z / distance))
    return right_ascension, declination, distance


def _eccentric_anomaly(mean_anomaly_degrees, eccentricity):
    """Solve Kepler's equation for the low-eccentricity Sun/Moon orbits."""
    mean_anomaly = math.radians(_normalize_degrees(mean_anomaly_degrees))
    eccentric_anomaly = mean_anomaly
    for _ in range(8):
        correction = (
            eccentric_anomaly
            - eccentricity * math.sin(eccentric_anomaly)
            - mean_anomaly
        ) / (1.0 - eccentricity * math.cos(eccentric_anomaly))
        eccentric_anomaly -= correction
        if abs(correction) < 1e-12:
            break
    return eccentric_anomaly


def _horizontal_coordinates(
        right_ascension_degrees,
        declination_degrees,
        distance_earth_radii,
        observer_latitude_degrees,
        observer_longitude_degrees,
        observer_elevation_m,
        sidereal_degrees):
    """Return topocentric azimuth/elevation, including lunar parallax."""
    right_ascension = math.radians(right_ascension_degrees)
    declination = math.radians(declination_degrees)
    observer_latitude = math.radians(observer_latitude_degrees)
    local_sidereal = math.radians(
        _normalize_degrees(sidereal_degrees + observer_longitude_degrees)
    )

    object_x = distance_earth_radii * math.cos(declination) * math.cos(right_ascension)
    object_y = distance_earth_radii * math.cos(declination) * math.sin(right_ascension)
    object_z = distance_earth_radii * math.sin(declination)

    observer_radius = 1.0 + float(observer_elevation_m or 0.0) / 6378137.0
    observer_x = observer_radius * math.cos(observer_latitude) * math.cos(local_sidereal)
    observer_y = observer_radius * math.cos(observer_latitude) * math.sin(local_sidereal)
    observer_z = observer_radius * math.sin(observer_latitude)

    relative_x = object_x - observer_x
    relative_y = object_y - observer_y
    relative_z = object_z - observer_z

    east = (
        -math.sin(local_sidereal) * relative_x
        + math.cos(local_sidereal) * relative_y
    )
    north = (
        -math.sin(observer_latitude) * math.cos(local_sidereal) * relative_x
        - math.sin(observer_latitude) * math.sin(local_sidereal) * relative_y
        + math.cos(observer_latitude) * relative_z
    )
    up = (
        math.cos(observer_latitude) * math.cos(local_sidereal) * relative_x
        + math.cos(observer_latitude) * math.sin(local_sidereal) * relative_y
        + math.sin(observer_latitude) * relative_z
    )

    azimuth = _normalize_degrees(math.degrees(math.atan2(east, north)))
    elevation = math.degrees(math.atan2(up, math.hypot(east, north)))
    return azimuth, elevation


def calculate_sun_moon_positions(observer_location, when=None):
    """Calculate Sun/Moon subpoints and topocentric Az/El for the web map.

    The compact orbital model is self-contained, so displaying the map does not
    require downloading a planetary ephemeris.  The main lunar perturbations and
    topocentric parallax are included for useful antenna-pointing readouts.
    """
    observer_location = observer_location or {}
    observer_latitude = _coerce_coordinate(observer_location.get("latitude"))
    observer_longitude = _coerce_coordinate(observer_location.get("longitude"))
    observer_elevation_m = _coerce_coordinate(observer_location.get("elevation_m"))
    if not _valid_lat_lon(observer_latitude, observer_longitude):
        return {}

    when = (when or utc_now()).astimezone(timezone.utc)
    julian_date = _julian_date(when)
    days_since_epoch = julian_date - 2451543.5
    centuries_since_j2000 = (julian_date - 2451545.0) / 36525.0
    sidereal_degrees = _normalize_degrees(
        280.46061837
        + 360.98564736629 * (julian_date - 2451545.0)
        + 0.000387933 * centuries_since_j2000 * centuries_since_j2000
        - centuries_since_j2000 * centuries_since_j2000 * centuries_since_j2000 / 38710000.0
    )
    obliquity = 23.4393 - 3.563e-7 * days_since_epoch

    # Sun: geocentric ecliptic position, with distance expressed in AU.
    sun_perihelion = _normalize_degrees(282.9404 + 4.70935e-5 * days_since_epoch)
    sun_eccentricity = 0.016709 - 1.151e-9 * days_since_epoch
    sun_mean_anomaly = _normalize_degrees(356.0470 + 0.9856002585 * days_since_epoch)
    sun_eccentric_anomaly = _eccentric_anomaly(sun_mean_anomaly, sun_eccentricity)
    sun_x_orbit = math.cos(sun_eccentric_anomaly) - sun_eccentricity
    sun_y_orbit = (
        math.sqrt(1.0 - sun_eccentricity * sun_eccentricity)
        * math.sin(sun_eccentric_anomaly)
    )
    sun_distance_au = math.hypot(sun_x_orbit, sun_y_orbit)
    sun_true_anomaly = math.degrees(math.atan2(sun_y_orbit, sun_x_orbit))
    sun_longitude = _normalize_degrees(sun_true_anomaly + sun_perihelion)
    sun_x = sun_distance_au * math.cos(math.radians(sun_longitude))
    sun_y = sun_distance_au * math.sin(math.radians(sun_longitude))
    sun_ra, sun_declination, _ = _ecliptic_to_equatorial(
        sun_x, sun_y, 0.0, obliquity
    )
    sun_distance_earth_radii = sun_distance_au * 149597870.7 / 6378.137

    # Moon: geocentric orbit in Earth radii plus the principal perturbations.
    moon_node = _normalize_degrees(125.1228 - 0.0529538083 * days_since_epoch)
    moon_inclination = 5.1454
    moon_perigee = _normalize_degrees(318.0634 + 0.1643573223 * days_since_epoch)
    moon_eccentricity = 0.054900
    moon_mean_anomaly = _normalize_degrees(115.3654 + 13.0649929509 * days_since_epoch)
    moon_eccentric_anomaly = _eccentric_anomaly(moon_mean_anomaly, moon_eccentricity)
    moon_x_orbit = 60.2666 * (math.cos(moon_eccentric_anomaly) - moon_eccentricity)
    moon_y_orbit = (
        60.2666
        * math.sqrt(1.0 - moon_eccentricity * moon_eccentricity)
        * math.sin(moon_eccentric_anomaly)
    )
    moon_distance_earth_radii = math.hypot(moon_x_orbit, moon_y_orbit)
    moon_true_anomaly = math.degrees(math.atan2(moon_y_orbit, moon_x_orbit))
    moon_argument = math.radians(_normalize_degrees(moon_true_anomaly + moon_perigee))
    moon_node_radians = math.radians(moon_node)
    moon_inclination_radians = math.radians(moon_inclination)
    moon_x = moon_distance_earth_radii * (
        math.cos(moon_node_radians) * math.cos(moon_argument)
        - math.sin(moon_node_radians)
        * math.sin(moon_argument)
        * math.cos(moon_inclination_radians)
    )
    moon_y = moon_distance_earth_radii * (
        math.sin(moon_node_radians) * math.cos(moon_argument)
        + math.cos(moon_node_radians)
        * math.sin(moon_argument)
        * math.cos(moon_inclination_radians)
    )
    moon_z = (
        moon_distance_earth_radii
        * math.sin(moon_argument)
        * math.sin(moon_inclination_radians)
    )
    moon_longitude = math.degrees(math.atan2(moon_y, moon_x))
    moon_latitude = math.degrees(
        math.atan2(moon_z, math.hypot(moon_x, moon_y))
    )
    moon_mean_longitude = _normalize_degrees(
        moon_mean_anomaly + moon_perigee + moon_node
    )
    sun_mean_longitude = _normalize_degrees(sun_mean_anomaly + sun_perihelion)
    elongation = _normalize_degrees(moon_mean_longitude - sun_mean_longitude)
    argument_of_latitude = _normalize_degrees(moon_mean_longitude - moon_node)

    def sin_degrees(value):
        return math.sin(math.radians(value))

    def cos_degrees(value):
        return math.cos(math.radians(value))

    moon_longitude += (
        -1.274 * sin_degrees(moon_mean_anomaly - 2.0 * elongation)
        + 0.658 * sin_degrees(2.0 * elongation)
        - 0.186 * sin_degrees(sun_mean_anomaly)
        - 0.059 * sin_degrees(2.0 * moon_mean_anomaly - 2.0 * elongation)
        - 0.057 * sin_degrees(moon_mean_anomaly - 2.0 * elongation + sun_mean_anomaly)
        + 0.053 * sin_degrees(moon_mean_anomaly + 2.0 * elongation)
        + 0.046 * sin_degrees(2.0 * elongation - sun_mean_anomaly)
        + 0.041 * sin_degrees(moon_mean_anomaly - sun_mean_anomaly)
        - 0.035 * sin_degrees(elongation)
        - 0.031 * sin_degrees(moon_mean_anomaly + sun_mean_anomaly)
        - 0.015 * sin_degrees(2.0 * argument_of_latitude - 2.0 * elongation)
        + 0.011 * sin_degrees(moon_mean_anomaly - 4.0 * elongation)
    )
    moon_latitude += (
        -0.173 * sin_degrees(argument_of_latitude - 2.0 * elongation)
        - 0.055 * sin_degrees(
            moon_mean_anomaly - argument_of_latitude - 2.0 * elongation
        )
        - 0.046 * sin_degrees(
            moon_mean_anomaly + argument_of_latitude - 2.0 * elongation
        )
        + 0.033 * sin_degrees(argument_of_latitude + 2.0 * elongation)
        + 0.017 * sin_degrees(2.0 * moon_mean_anomaly + argument_of_latitude)
    )
    moon_distance_earth_radii += (
        -0.58 * cos_degrees(moon_mean_anomaly - 2.0 * elongation)
        - 0.46 * cos_degrees(2.0 * elongation)
    )
    moon_longitude_radians = math.radians(moon_longitude)
    moon_latitude_radians = math.radians(moon_latitude)
    moon_x = (
        moon_distance_earth_radii
        * math.cos(moon_longitude_radians)
        * math.cos(moon_latitude_radians)
    )
    moon_y = (
        moon_distance_earth_radii
        * math.sin(moon_longitude_radians)
        * math.cos(moon_latitude_radians)
    )
    moon_z = moon_distance_earth_radii * math.sin(moon_latitude_radians)
    moon_ra, moon_declination, _ = _ecliptic_to_equatorial(
        moon_x, moon_y, moon_z, obliquity
    )

    sun_azimuth, sun_elevation = _horizontal_coordinates(
        sun_ra,
        sun_declination,
        sun_distance_earth_radii,
        observer_latitude,
        observer_longitude,
        observer_elevation_m or 0.0,
        sidereal_degrees,
    )
    moon_azimuth, moon_elevation = _horizontal_coordinates(
        moon_ra,
        moon_declination,
        moon_distance_earth_radii,
        observer_latitude,
        observer_longitude,
        observer_elevation_m or 0.0,
        sidereal_degrees,
    )

    return {
        "timestamp_utc": when.isoformat(),
        "sun": {
            "latitude": sun_declination,
            "longitude": _signed_longitude(sun_ra - sidereal_degrees),
            "azimuth": sun_azimuth,
            "elevation": sun_elevation,
        },
        "moon": {
            "latitude": moon_declination,
            "longitude": _signed_longitude(moon_ra - sidereal_degrees),
            "azimuth": moon_azimuth,
            "elevation": moon_elevation,
        },
    }

def read_tracking_status(status_file=STATUS_FILE, max_age_seconds=120):
    """Read the latest Az/El status written by bqe_track_continuously.py."""
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        timestamp = data.get("timestamp_utc")
        if timestamp:
            dt = parse_time(timestamp)
            age = (utc_now() - dt).total_seconds()
            data["age_seconds"] = age
            if age > max_age_seconds:
                data["stale"] = True
        return data
    except FileNotFoundError:
        return {}
    except Exception as e:
        return {"error": str(e)}


MAP_OBSERVER_LAT_FIELDS = (
    "observer_latitude", "observer_lat", "observer_latitude_deg", "observer_lat_deg",
    "qth_latitude", "qth_lat", "qth_latitude_deg", "qth_lat_deg",
    "station_latitude", "station_lat", "home_latitude", "home_lat",
)
MAP_OBSERVER_LON_FIELDS = (
    "observer_longitude", "observer_lon", "observer_lng", "observer_longitude_deg", "observer_lon_deg", "observer_lng_deg",
    "qth_longitude", "qth_lon", "qth_lng", "qth_longitude_deg", "qth_lon_deg", "qth_lng_deg",
    "station_longitude", "station_lon", "home_longitude", "home_lon",
)
MAP_GENERIC_LAT_FIELDS = ("latitude", "lat", "latitude_deg", "latitude_degrees")
MAP_GENERIC_LON_FIELDS = ("longitude", "lon", "lng", "longitude_deg", "longitude_degrees")
MAP_SATELLITE_LAT_FIELDS = (
    "satellite_latitude", "satellite_lat", "satellite_latitude_deg", "satellite_lat_deg",
    "sat_latitude", "sat_lat", "sat_latitude_deg", "sat_lat_deg",
    "subsatellite_latitude", "subsatellite_lat", "subsatellite_latitude_deg", "subsatellite_lat_deg",
    "subpoint_latitude", "subpoint_lat", "subpoint_latitude_deg", "subpoint_lat_deg",
    "ground_track_latitude", "ground_track_lat", "ground_track_latitude_deg", "ground_track_lat_deg",
)
MAP_SATELLITE_LON_FIELDS = (
    "satellite_longitude", "satellite_lon", "satellite_lng", "satellite_longitude_deg", "satellite_lon_deg", "satellite_lng_deg",
    "sat_longitude", "sat_lon", "sat_lng", "sat_longitude_deg", "sat_lon_deg", "sat_lng_deg",
    "subsatellite_longitude", "subsatellite_lon", "subsatellite_lng", "subsatellite_longitude_deg", "subsatellite_lon_deg", "subsatellite_lng_deg",
    "subpoint_longitude", "subpoint_lon", "subpoint_lng", "subpoint_longitude_deg", "subpoint_lon_deg", "subpoint_lng_deg",
    "ground_track_longitude", "ground_track_lon", "ground_track_lng", "ground_track_longitude_deg", "ground_track_lon_deg", "ground_track_lng_deg",
)
MAP_RANGE_KM_FIELDS = (
    "range_km", "slant_range_km", "satellite_range_km", "distance_km",
    "range", "slant_range", "satellite_range", "distance",
)
MAP_RANGE_M_FIELDS = (
    "range_m", "slant_range_m", "satellite_range_m", "distance_m",
)


def _normalize_longitude(value):
    if value is None:
        return None
    return ((float(value) + 180.0) % 360.0) - 180.0


def _valid_lat_lon(latitude, longitude):
    return (
        latitude is not None
        and longitude is not None
        and -90.0 <= float(latitude) <= 90.0
    )


def _set_if_missing(mapping, key, value):
    if value is not None and mapping.get(key) in (None, ""):
        mapping[key] = value


def augment_tracking_status_for_map(tracking_status, observer_location=None):
    """Add canonical map fields to tracker JSON without changing existing fields.

    The web gauges only need azimuth/elevation, but the Leaflet map needs signed
    decimal latitude/longitude.  This function makes the API tolerant of several
    common tracker/status field names and also copies the QTH from my_qth.yaml
    into the status payload so the browser can still show the observer marker
    when the tracker status file contains only Az/El.
    """
    if not isinstance(tracking_status, dict):
        tracking_status = {}
    status = dict(tracking_status)
    observer_location = observer_location or {}

    observer_latitude = _first_nested_coordinate(status, MAP_OBSERVER_LAT_FIELDS)
    observer_longitude = _first_nested_coordinate(status, MAP_OBSERVER_LON_FIELDS)
    if observer_latitude is None:
        observer_latitude = _coerce_coordinate(observer_location.get("latitude"))
    if observer_longitude is None:
        observer_longitude = _coerce_coordinate(observer_location.get("longitude"))
    if _valid_lat_lon(observer_latitude, observer_longitude):
        observer_longitude = _normalize_longitude(observer_longitude)
        _set_if_missing(status, "observer_latitude", observer_latitude)
        _set_if_missing(status, "observer_lat", observer_latitude)
        _set_if_missing(status, "qth_latitude", observer_latitude)
        _set_if_missing(status, "observer_longitude", observer_longitude)
        _set_if_missing(status, "observer_lon", observer_longitude)
        _set_if_missing(status, "qth_longitude", observer_longitude)
        _set_if_missing(status, "qth_lon", observer_longitude)
        status["map_observer_available"] = True
    else:
        status["map_observer_available"] = False

    satellite_latitude = _first_nested_coordinate(status, MAP_SATELLITE_LAT_FIELDS)
    satellite_longitude = _first_nested_coordinate(status, MAP_SATELLITE_LON_FIELDS)
    if satellite_latitude is None:
        satellite_latitude = _first_direct_coordinate(status, MAP_GENERIC_LAT_FIELDS)
    if satellite_longitude is None:
        satellite_longitude = _first_direct_coordinate(status, MAP_GENERIC_LON_FIELDS)
    if _valid_lat_lon(satellite_latitude, satellite_longitude):
        satellite_longitude = _normalize_longitude(satellite_longitude)
        _set_if_missing(status, "satellite_latitude", satellite_latitude)
        _set_if_missing(status, "satellite_lat", satellite_latitude)
        _set_if_missing(status, "satellite_longitude", satellite_longitude)
        _set_if_missing(status, "satellite_lon", satellite_longitude)
        status["map_satellite_available"] = True
    else:
        status["map_satellite_available"] = False

    range_km = _first_nested_coordinate(status, MAP_RANGE_KM_FIELDS)
    if range_km is None:
        range_m = _first_nested_coordinate(status, MAP_RANGE_M_FIELDS)
        if range_m is not None:
            range_km = float(range_m) / 1000.0
    if range_km is not None and range_km > 0:
        _set_if_missing(status, "range_km", range_km)
        _set_if_missing(status, "slant_range_km", range_km)
        status["map_range_available"] = True
    else:
        status["map_range_available"] = False

    status["map_debug"] = {
        "observer_available": status["map_observer_available"],
        "satellite_available": status["map_satellite_available"],
        "range_available": status["map_range_available"],
        "qth_config_file": QTH_CONFIG_FILE,
        "status_file": STATUS_FILE,
    }
    return status


def write_antenna_tracking_override(enabled):
    """Atomically publish a temporary antenna-tracking override for the active tracker."""
    os.makedirs(LOG_DIR, exist_ok=True)
    payload = {
        "enable_antenna_tracking": bool(enabled),
        "timestamp_utc": utc_now().isoformat(),
    }
    tmp_name = ANTENNA_TRACKING_OVERRIDE_FILE + ".tmp"
    with open(tmp_name, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp_name, ANTENNA_TRACKING_OVERRIDE_FILE)


def clear_antenna_tracking_override():
    """Remove the temporary override so satellite YAML controls tracking again."""
    try:
        if os.path.exists(ANTENNA_TRACKING_OVERRIDE_FILE):
            os.remove(ANTENNA_TRACKING_OVERRIDE_FILE)
    except OSError as e:
        print(f"Warning: could not remove antenna tracking override file: {e}")


def set_antenna_tracking_override(enabled):
    """Set a temporary runtime override and make it visible to the web console."""
    enabled = bool(enabled)
    write_antenna_tracking_override(enabled)
    with STATE_LOCK:
        APP_STATE["antenna_tracking_override"] = enabled
        message = (
            "Antenna tracking temporarily enabled from Tracking menu."
            if enabled
            else "Antenna tracking temporarily disabled from Tracking menu."
        )
        APP_STATE["last_message"] = message
    return {
        "ok": True,
        "antenna_tracking_override": enabled,
        "antenna_tracking_disabled": not enabled,
        "message": message,
    }


def _set_status_message(message, command_running=None, last_command=None, last_command_result=None):
    """Update the web-console status message and optional command state."""
    with STATE_LOCK:
        APP_STATE["last_message"] = message
        if command_running is not None:
            APP_STATE["command_running"] = bool(command_running)
        if last_command is not None:
            APP_STATE["last_command"] = last_command
        if last_command_result is not None:
            APP_STATE["last_command_result"] = last_command_result


def _summarize_command_output(output, max_chars=500):
    """Return a short single-line summary of subprocess output for the web UI."""
    text = (output or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    summary = " | ".join(lines[-3:])
    if len(summary) > max_chars:
        summary = summary[-max_chars:]
    return summary


def _start_script_command(action, display_name, script_path, script_args=None, refresh_schedule_after=False):
    """Start a project helper script without blocking the web UI."""
    script_args = [str(part) for part in (script_args or [])]
    if not script_path or not os.path.exists(script_path):
        message = f"{display_name} is not configured; script not found."
        _set_status_message(
            message,
            command_running=False,
            last_command=action,
            last_command_result={"ok": False, "message": message},
        )
        return {"ok": False, "action": action, "message": message}

    with STATE_LOCK:
        if APP_STATE.get("command_running"):
            message = f"Cannot start {display_name}; another Tracking command is already running."
            return {"ok": False, "action": action, "message": message}
        APP_STATE["command_running"] = True
        APP_STATE["last_command"] = action
        APP_STATE["last_command_result"] = None
        APP_STATE["last_message"] = f"{display_name} started..."

    def worker():
        global CURRENT_WEB_COMMAND_PROCESS

        process = None
        started = utc_now()
        message = f"{display_name} did not complete."
        ok = False
        output = ""
        return_code = None
        print(f"WEB COMMAND - Starting {started.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        try:
            process = subprocess.Popen(
                [PYTHON_PATH, script_path, *script_args],
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            CURRENT_WEB_COMMAND_PROCESS = process
            try:
                output, _ = process.communicate(timeout=WEB_COMMAND_TIMEOUT_SECONDS)
                return_code = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                return_code = process.returncode
                message = f"{display_name} timed out after {WEB_COMMAND_TIMEOUT_SECONDS} seconds."
                ok = False
            else:
                if return_code == 0:
                    ok = True
                    message = f"{display_name} completed successfully."
                    if refresh_schedule_after:
                        try:
                            passes = refresh_schedule_state(SCHEDULE_FILE)
                            message += f" Reloaded {len(passes)} scheduled passes."
                        except Exception as e:
                            message += f" Schedule reload failed: {e}"
                else:
                    ok = False
                    message = f"{display_name} failed with exit code {return_code}."

            summary = _summarize_command_output(output)
            if summary:
                message = f"{message} {summary}"

        except Exception as e:
            ok = False
            message = f"{display_name} failed: {e}"
        finally:
            if process is not None and CURRENT_WEB_COMMAND_PROCESS is process:
                CURRENT_WEB_COMMAND_PROCESS = None

        finished = utc_now()
        result = {
            "ok": ok,
            "action": action,
            "display_name": display_name,
            "script": script_path,
            "args": script_args,
            "return_code": return_code,
            "started_utc": started.isoformat(),
            "finished_utc": finished.isoformat(),
            "message": message,
        }
        _set_status_message(
            message,
            command_running=False,
            last_command=action,
            last_command_result=result,
        )
        print(f"[WEB COMMAND] {message} {finished.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    thread = threading.Thread(target=worker, name=f"BQEWebCommand-{action}", daemon=True)
    thread.start()
    return {"ok": True, "action": action, "message": f"{display_name} started."}


def handle_web_command(action, request_payload=None):
    """Run a command requested from the web-console menus or preset pane."""
    action = str(action or "").strip().lower()
    request_payload = request_payload if isinstance(request_payload, dict) else {}

    if action in ("start_audio_recording", "stop_audio_recording"):
        return control_audio_recording(action)

    if action == "environment_diagnostics":
        try:
            report = run_environment_diagnostics(emit=False,
                include_plugin_tests=request_payload.get('include_plugin_tests') is True)
            return {"ok": True, "action": action, "report": report,
                    "warnings": report.count("[WARNING]"), "failures": report.count("[FAIL]")}
        except Exception as exc:
            return {"ok": False, "action": action,
                    "message": f"Environment Diagnostics failed: {exc}"}

    if action == "program_preset":
        return program_preset_from_web(request_payload.get("nickname"))

    if action == "get_test_pass_satellites":
        try:
            satellites = load_test_pass_satellites()
            return {
                "ok": True,
                "action": action,
                "satellites": satellites,
                "message": f"Loaded {len(satellites)} satellites from {SATELLITE_CONFIG_FILE}.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "action": action,
                "satellites": [],
                "message": f"Could not load test-pass satellites: {exc}",
            }

    if action == "run_test_pass":
        return schedule_test_pass_from_web(request_payload.get("nickname"))

    if action == "custom_schedule_passes":
        return schedule_custom_passes_from_web(request_payload.get("nicknames"))

    if action == "end_current_pass":
        return end_current_pass_from_web()

    if action == "enable_fdt":
        return enable_fdt_from_web(
            request_payload.get("receive_frequency_mhz"),
            request_payload.get("uplink_frequency_mhz"),
            request_payload.get("fdt_recalculation_interval"),
        )

    if action == "disable_fdt":
        return disable_fdt_from_web()

    if action == "tune_fdt_step":
        return tune_fdt_step_from_web(request_payload.get("downlink_direction"))

    if action == "set_fdt_recalculation_interval":
        return set_fdt_recalculation_interval_from_web(
            request_payload.get("fdt_recalculation_interval")
        )

    if action == "reset_fdt_recalculation_interval":
        return reset_fdt_recalculation_interval_from_web()

    if action in {"exit", "exit_app", "quit"}:
        return request_shutdown("Exit requested from File > Exit. Cleaning up helper programs...")

    if action in {"restart", "restart_server"}:
        return request_restart()

    if action == "update_keps":
        script_path = UPDATE_KEPS_SCRIPT
        return _start_script_command("update_keps", "Update Keps", script_path)

    if action == "schedule_passes":
        return _start_script_command(
            "schedule_passes",
            "Schedule Passes",
            "bqe_schedule_passes.py",
            script_args=["--auto_schedule"],
            refresh_schedule_after=True,
        )

    if action == "disable_antenna_tracking":
        return set_antenna_tracking_override(False)

    if action == "enable_antenna_tracking":
        return set_antenna_tracking_override(True)

    message = f"Unknown command: {action or '(blank)'}"
    return {"ok": False, "action": action, "message": message}


def _write_fdt_control_payload(payload, filename=FDT_CONTROL_FILE):
    """Atomically publish FDT satellite/live frequencies for the tracking process."""
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    temporary_file = f"{filename}.{threading.get_ident()}.tmp"
    with FDT_TUNING_LOCK:
        if CURRENT_PASS_END_EVENT.is_set() or SHUTDOWN_EVENT.is_set():
            raise RuntimeError("the active pass is ending; FDT data was not saved")
        try:
            with open(temporary_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(temporary_file, filename)
        finally:
            try:
                if os.path.exists(temporary_file):
                    os.remove(temporary_file)
            except OSError:
                pass


def clear_fdt_control_state(reason=None, filename=FDT_CONTROL_FILE):
    """Remove current and temporary FDT data without racing a wheel update."""
    removed_any = False
    with FDT_TUNING_LOCK:
        candidates = [filename, filename + ".tmp"]
        candidates.extend(glob.glob(filename + ".*.tmp"))
        for candidate in dict.fromkeys(candidates):
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
                    removed_any = True
            except OSError as exc:
                print(f"[WARNING] Could not remove FDT data file {candidate}: {exc}")

    with STATE_LOCK:
        if APP_STATE.get("last_command") in {"enable_fdt", "disable_fdt", "tune_fdt_step"}:
            APP_STATE["last_command"] = None
            APP_STATE["last_command_result"] = None

    if removed_any:
        suffix = f" ({reason})" if reason else ""
        print(f"[INFO] Cleared FDT control data{suffix}.")
    return removed_any


def _raise_for_rigctld_error(response, description):
    """Raise when a rigctld setter reports a non-zero RPRT result."""
    if response is None:
        # Older bqe_hamlib_interface versions did not return the response.
        return
    response_text = str(response).strip()
    result_codes = re.findall(r"RPRT\s+(-?\d+)", response_text, flags=re.IGNORECASE)
    failing_codes = [code for code in result_codes if int(code) != 0]
    if failing_codes:
        raise RuntimeError(
            f"rigctld rejected the {description} command "
            f"(RPRT {failing_codes[-1]})"
        )


def _fdt_status_enabled(fdt_status):
    """Return whether a valid FDT control payload enables live tracking."""
    if not isinstance(fdt_status, dict) or not fdt_status:
        return False
    # Payloads written by older versions did not contain an explicit flag;
    # their presence meant that FDT was enabled.
    return fdt_status.get("enabled", True) is True


def get_fdt_recalculation_interval():
    """Return the slider value; it is not necessarily an active override."""
    with STATE_LOCK:
        return float(
            APP_STATE.get("fdt_recalculation_interval", FDT_RECALCULATION_INTERVAL)
        )


def get_fdt_recalculation_interval_override():
    """Return the active fixed FDT cadence override, or None for YAML auto mode."""
    with STATE_LOCK:
        value = APP_STATE.get("fdt_recalculation_interval_override")
    return None if value is None else float(value)


def set_fdt_recalculation_interval_from_web(requested_interval):
    """Apply the FDT Console interval slider value for the current session."""
    action = "set_fdt_recalculation_interval"
    try:
        interval = float(requested_interval)
    except (TypeError, ValueError):
        interval = float("nan")

    if (
            not math.isfinite(interval)
            or interval < FDT_RECALCULATION_INTERVAL_MIN
            or interval > FDT_RECALCULATION_INTERVAL_MAX):
        return {
            "ok": False,
            "action": action,
            "message": (
                "FDT recalculation interval must be between "
                f"{FDT_RECALCULATION_INTERVAL_MIN:g} and "
                f"{FDT_RECALCULATION_INTERVAL_MAX:g} seconds."
            ),
        }

    status = read_tracking_status()
    with STATE_LOCK:
        tracking_running = bool(APP_STATE.get("tracking_running"))
        current_key = APP_STATE.get("current_pass_key")
        active_pass = next(
            (p for p in APP_STATE.get("passes", []) if p.get("_key") == current_key),
            {},
        )
    satellite_type = status.get("satellite_type") or active_pass.get("satellite_type") or ""
    if not tracking_running or str(satellite_type).strip().upper() != "LINEAR":
        return {
            "ok": False,
            "action": action,
            "message": "The FDT interval can be adjusted only during an active LINEAR satellite pass.",
        }

    with STATE_LOCK:
        APP_STATE["fdt_recalculation_interval"] = interval
        APP_STATE["fdt_recalculation_interval_override"] = interval

    # If FDT is already running, republish its payload so the tracker adopts
    # the fixed override immediately. When FDT is paused, the selected value
    # is retained and included by the next Sync FDT command.
    with FDT_TUNING_LOCK:
        current_fdt = read_fdt_console_status()
        if _fdt_status_enabled(current_fdt):
            current_fdt["timestamp_utc"] = utc_now().isoformat()
            current_fdt["cadence_mode"] = "override"
            current_fdt["fdt_recalculation_interval_override"] = interval
            current_fdt["fdt_recalculation_interval"] = interval
            _write_fdt_control_payload(current_fdt)

    message = f"FDT cadence override set to {interval:g} seconds."
    _set_status_message(message, last_command=action)
    return {
        "ok": True,
        "action": action,
        "message": message,
        "fdt_recalculation_interval": interval,
        "fdt_recalculation_interval_override": interval,
        "fdt_cadence_mode": "override",
    }


def reset_fdt_recalculation_interval_from_web():
    """Return FDT to the same automatic YAML cadence used by all satellites."""
    action = "reset_fdt_recalculation_interval"
    with STATE_LOCK:
        APP_STATE["fdt_recalculation_interval"] = FDT_RECALCULATION_INTERVAL
        APP_STATE["fdt_recalculation_interval_override"] = None

    with FDT_TUNING_LOCK:
        current_fdt = read_fdt_console_status()
        if _fdt_status_enabled(current_fdt):
            current_fdt["timestamp_utc"] = utc_now().isoformat()
            current_fdt["cadence_mode"] = "auto"
            current_fdt["fdt_recalculation_interval_override"] = None
            current_fdt.pop("fdt_recalculation_interval", None)
            _write_fdt_control_payload(current_fdt)

    message = (
        "FDT cadence returned to YAML automatic mode: "
        f"{TRACKING_SLEEP_INTERVAL_SECONDS:g} seconds normally and "
        f"{TRACKING_SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS:g} seconds above "
        f"{TRACKING_HIGH_PASS_ELEVATION:g} degrees elevation."
    )
    _set_status_message(message, last_command=action)
    return {
        "ok": True,
        "action": action,
        "message": message,
        "fdt_recalculation_interval": FDT_RECALCULATION_INTERVAL,
        "fdt_recalculation_interval_override": None,
        "fdt_cadence_mode": "auto",
    }


def tune_fdt_step_from_web(downlink_direction):
    """Move a LINEAR transponder pair by one 200 Hz mouse-wheel step."""
    action = "tune_fdt_step"
    try:
        direction_value = float(downlink_direction)
    except (TypeError, ValueError):
        direction_value = 0.0
    if not math.isfinite(direction_value) or direction_value == 0:
        return {
            "ok": False,
            "action": action,
            "message": "FDT wheel tuning requires an up or down direction.",
        }
    downlink_delta_hz = FDT_WHEEL_STEP_HZ if direction_value > 0 else -FDT_WHEEL_STEP_HZ

    with STATE_LOCK:
        tracking_running = bool(APP_STATE.get("tracking_running"))
        current_nickname = str(APP_STATE.get("current_satellite") or "").strip()
        current_key = APP_STATE.get("current_pass_key")
        active_pass = next(
            (p for p in APP_STATE.get("passes", []) if p.get("_key") == current_key),
            {},
        )

    status = read_tracking_status()
    satellite_type = str(
        status.get("satellite_type") or active_pass.get("satellite_type") or ""
    ).strip().upper()
    if (
            not tracking_running
            or satellite_type != "LINEAR"
            or not current_nickname
            or CURRENT_PASS_END_EVENT.is_set()
            or SHUTDOWN_EVENT.is_set()):
        return {
            "ok": False,
            "action": action,
            "message": "FDT wheel tuning is available only during an active LINEAR satellite pass.",
        }

    passband = load_fdt_passband(current_nickname)
    if passband.get("error"):
        return {"ok": False, "action": action, "message": passband["error"]}
    downlink_band = passband.get("downlink")
    uplink_band = passband.get("uplink")
    if not isinstance(downlink_band, dict) or not isinstance(uplink_band, dict):
        return {
            "ok": False,
            "action": action,
            "message": (
                "FDT wheel tuning requires valid uplink_frequency_range_mhz and "
                "downlink_frequency_range_mhz values in satellites.yaml."
            ),
        }

    transponder_type = str(passband.get("transponder_type") or "").strip()
    normalized_transponder_type = re.sub(r"[^A-Z]", "", transponder_type.upper())
    if normalized_transponder_type.startswith("NON"):
        uplink_delta_hz = downlink_delta_hz
    elif normalized_transponder_type == "INVERTING":
        uplink_delta_hz = -downlink_delta_hz
    else:
        return {
            "ok": False,
            "action": action,
            "message": (
                "FDT wheel tuning requires transponder_type to be INVERTING or "
                "NON-INVERTING in satellites.yaml."
            ),
        }

    try:
        with FDT_TUNING_LOCK:
            current_fdt = read_fdt_console_status()
            if not _fdt_status_enabled(current_fdt):
                raise RuntimeError("FDT is disabled; press Enable FDT before wheel tuning")

            def finite_frequency_hz(value):
                try:
                    result = float(value)
                except (TypeError, ValueError):
                    return None
                return result if math.isfinite(result) and result > 0 else None

            current_downlink_at_sat_hz = finite_frequency_hz(
                current_fdt.get("downlink_frequency_hz")
            )
            if current_downlink_at_sat_hz is None:
                status_downlink_mhz = finite_frequency_hz(status.get("downlink_frequency_mhz"))
                current_downlink_at_sat_hz = (
                    status_downlink_mhz * 1e6
                    if status_downlink_mhz is not None
                    else float(downlink_band["center_mhz"]) * 1e6
                )

            current_uplink_at_sat_hz = finite_frequency_hz(
                current_fdt.get("uplink_frequency_hz")
            )
            if current_uplink_at_sat_hz is None:
                status_uplink_mhz = finite_frequency_hz(status.get("uplink_frequency_mhz"))
                current_uplink_at_sat_hz = (
                    status_uplink_mhz * 1e6
                    if status_uplink_mhz is not None
                    else float(uplink_band["center_mhz"]) * 1e6
                )

            tracker_downlink_at_sat_hz = finite_frequency_hz(
                status.get("downlink_frequency_mhz")
            )
            try:
                tracker_downlink_doppler_hz = float(status.get("downlink_doppler_hz"))
            except (TypeError, ValueError):
                tracker_downlink_doppler_hz = float("nan")
            if tracker_downlink_at_sat_hz is None or not math.isfinite(tracker_downlink_doppler_hz):
                raise RuntimeError("waiting for the tracker to report current Doppler data")
            tracker_downlink_at_sat_hz *= 1e6
            doppler_ratio = tracker_downlink_doppler_hz / tracker_downlink_at_sat_hz

            new_downlink_at_sat_hz = current_downlink_at_sat_hz + downlink_delta_hz
            new_uplink_at_sat_hz = current_uplink_at_sat_hz + uplink_delta_hz
            downlink_low_hz = float(downlink_band["low_mhz"]) * 1e6
            downlink_high_hz = float(downlink_band["high_mhz"]) * 1e6
            uplink_low_hz = float(uplink_band["low_mhz"]) * 1e6
            uplink_high_hz = float(uplink_band["high_mhz"]) * 1e6

            if not downlink_low_hz <= new_downlink_at_sat_hz <= downlink_high_hz:
                raise ValueError(
                    "downlink passband edge reached; the radio was not retuned"
                )
            if not uplink_low_hz <= new_uplink_at_sat_hz <= uplink_high_hz:
                raise ValueError(
                    "uplink passband edge reached; the radio was not retuned"
                )

            downlink_doppler_at_sat_hz = new_downlink_at_sat_hz * doppler_ratio
            # Uplink Doppler correction must always have the opposite sign from
            # the downlink correction.  Its magnitude is scaled for the uplink
            # frequency, but its sign is explicitly inverted here.
            uplink_doppler_at_sat_hz = -new_uplink_at_sat_hz * doppler_ratio
            radio_receive_hz = int(round(new_downlink_at_sat_hz + downlink_doppler_at_sat_hz))
            radio_uplink_hz = int(round(new_uplink_at_sat_hz + uplink_doppler_at_sat_hz))
            previous_radio_receive_hz = int(round(
                current_downlink_at_sat_hz * (1.0 + doppler_ratio)
            ))

            if rig is None:
                raise RuntimeError("Hamlib radio control is unavailable")

            downlink_programmed = False
            try:
                response = rig.rigctld_set_downlink_frequency(
                    radio_receive_hz, RIGCTLD_PORT
                )
                _raise_for_rigctld_error(response, "downlink frequency")
                downlink_programmed = True
                response = rig.rigctld_set_uplink_frequency(
                    radio_uplink_hz, RIGCTLD_PORT
                )
                _raise_for_rigctld_error(response, "uplink frequency")
            except Exception:
                if downlink_programmed:
                    try:
                        rig.rigctld_set_downlink_frequency(
                            previous_radio_receive_hz, RIGCTLD_PORT
                        )
                    except Exception as rollback_exc:
                        print(f"[WARNING] Could not restore downlink after FDT failure: {rollback_exc}")
                raise

            payload = {
                "timestamp_utc": utc_now().isoformat(),
                "enabled": True,
                "cadence_mode": (
                    "override" if get_fdt_recalculation_interval_override() is not None else "auto"
                ),
                "fdt_recalculation_interval_override": get_fdt_recalculation_interval_override(),
                "downlink_frequency_hz": new_downlink_at_sat_hz,
                "radio_receive_frequency_hz": radio_receive_hz,
                "doppler_at_receive_frequency_hz": downlink_doppler_at_sat_hz,
                "frequency_source": "fdt_mouse_wheel",
                "uplink_frequency_hz": new_uplink_at_sat_hz,
                "radio_uplink_frequency_hz": radio_uplink_hz,
                "doppler_at_radio_uplink_frequency_hz": uplink_doppler_at_sat_hz,
                "uplink_frequency_source": "fdt_mouse_wheel",
                "transponder_type": transponder_type,
                "downlink_step_hz": downlink_delta_hz,
                "uplink_step_hz": uplink_delta_hz,
            }
            _write_fdt_control_payload(payload)

        down_word = "up" if downlink_delta_hz > 0 else "down"
        up_word = "up" if uplink_delta_hz > 0 else "down"
        message = (
            f"FDT tuned downlink {down_word} 200 Hz to {radio_receive_hz / 1e6:.6f} MHz "
            f"and uplink {up_word} 200 Hz to {radio_uplink_hz / 1e6:.6f} MHz "
            f"({transponder_type})."
        )
        _set_status_message(message, last_command=action)
        return {"ok": True, "action": action, "message": message, **payload}
    except Exception as exc:
        message = f"Could not tune FDT: {exc}"
        _set_status_message(message, last_command=action)
        return {"ok": False, "action": action, "message": message}

def enable_fdt_from_web(
        manual_receive_frequency_mhz=None,
        manual_uplink_frequency_mhz=None,
        requested_recalculation_interval=None):
    """Anchor FDT to the radio's tuning and begin continuous LINEAR correction."""
    status = read_tracking_status()
    with STATE_LOCK:
        tracking_running = bool(APP_STATE.get("tracking_running"))
        current_key = APP_STATE.get("current_pass_key")
        active_pass = next(
            (p for p in APP_STATE.get("passes", []) if p.get("_key") == current_key),
            {},
        )
    satellite_type = status.get("satellite_type") or active_pass.get("satellite_type") or ""
    if (
            not tracking_running
            or str(satellite_type).strip().upper() != "LINEAR"
            or CURRENT_PASS_END_EVENT.is_set()
            or SHUTDOWN_EVENT.is_set()):
        return {
            "ok": False,
            "action": "enable_fdt",
            "message": "Enable FDT is available only during an active LINEAR satellite pass.",
        }
    if requested_recalculation_interval is not None:
        interval_result = set_fdt_recalculation_interval_from_web(
            requested_recalculation_interval
        )
        if not interval_result.get("ok"):
            return {
                "ok": False,
                "action": "enable_fdt",
                "message": interval_result.get(
                    "message", "Could not set the FDT recalculation interval."
                ),
            }
    try:
        receive_hz = None
        radio_error = None
        if rig is not None:
            try:
                receive_hz = float(rig.rigctld_get_downlink_frequency(RIGCTLD_PORT))
            except Exception as exc:
                radio_error = exc
        else:
            radio_error = RuntimeError("Hamlib radio control is unavailable")

        if receive_hz is None:
            if not bool(status.get("test_pass")):
                raise RuntimeError(f"could not query the radio: {radio_error}")
            if manual_receive_frequency_mhz is None or str(manual_receive_frequency_mhz).strip() == "":
                raise ValueError(
                    "Radio frequency could not be queried during this test pass. "
                    "Enter the receive frequency in the FDT Console."
                )
            receive_hz = float(manual_receive_frequency_mhz) * 1e6
            if receive_hz <= 0:
                raise ValueError("the entered receive frequency must be greater than zero")
        tracker_downlink_at_sat_hz = float(status.get("downlink_frequency_mhz")) * 1e6
        tracker_downlink_doppler_hz = float(status.get("downlink_doppler_hz"))
        if tracker_downlink_at_sat_hz <= 0:
            raise ValueError("tracker reported an invalid downlink frequency at the satellite")

        # The tracker reports Doppler at its downlink frequency at the
        # satellite.  Recover the dimensionless Doppler ratio, then solve for
        # the operator-selected frequency at the satellite.  Do not calculate
        # Doppler from the frequency displayed on the radio.
        doppler_ratio = tracker_downlink_doppler_hz / tracker_downlink_at_sat_hz
        downlink_radio_multiplier = 1.0 + doppler_ratio
        if downlink_radio_multiplier <= 0:
            raise ValueError("tracker reported an invalid downlink Doppler ratio")
        downlink_at_sat_hz = receive_hz / downlink_radio_multiplier
        downlink_doppler_at_sat_hz = downlink_at_sat_hz * doppler_ratio

        configured_uplink_mhz = float(status.get("uplink_frequency_mhz") or 0)
        uplink_hz = None
        uplink_at_sat_hz = None
        uplink_doppler_at_sat_hz = None
        uplink_source = None
        if configured_uplink_mhz > 0:
            uplink_radio_error = None
            if rig is not None:
                try:
                    uplink_hz = float(rig.rigctld_get_uplink_frequency(RIGCTLD_PORT))
                except Exception as exc:
                    uplink_radio_error = exc
            else:
                uplink_radio_error = RuntimeError("Hamlib radio control is unavailable")

            if uplink_hz is None:
                if not bool(status.get("test_pass")):
                    raise RuntimeError(f"could not query the radio uplink frequency: {uplink_radio_error}")
                if manual_uplink_frequency_mhz is None or str(manual_uplink_frequency_mhz).strip() == "":
                    raise ValueError(
                        "Radio uplink frequency could not be queried during this test pass. "
                        "Enter the uplink frequency in the FDT Console."
                    )
                uplink_hz = float(manual_uplink_frequency_mhz) * 1e6
                if uplink_hz <= 0:
                    raise ValueError("the entered uplink frequency must be greater than zero")

            # Uplink Doppler has the opposite sign.  The radio transmits at
            # uplink_at_sat_hz * (1 - doppler_ratio), so solve that equation
            # before calculating the signed Doppler shift at the satellite.
            uplink_radio_multiplier = 1.0 - doppler_ratio
            if uplink_radio_multiplier <= 0:
                raise ValueError("tracker reported an invalid uplink Doppler ratio")
            uplink_at_sat_hz = uplink_hz / uplink_radio_multiplier
            uplink_doppler_at_sat_hz = -uplink_at_sat_hz * doppler_ratio
            uplink_source = "radio" if uplink_radio_error is None else "manual_test_pass_entry"

        # Program the newly calculated Doppler-corrected radio frequencies now.
        # The tracking process will repeat this calculation with fresh Doppler
        # data on every loop while the control payload remains enabled.
        radio_receive_target_hz = int(round(
            downlink_at_sat_hz + downlink_doppler_at_sat_hz
        ))
        radio_uplink_target_hz = None
        if uplink_at_sat_hz is not None:
            radio_uplink_target_hz = int(round(
                uplink_at_sat_hz + uplink_doppler_at_sat_hz
            ))

        if rig is None:
            raise RuntimeError("Hamlib radio control is unavailable")

        payload = {
            "timestamp_utc": utc_now().isoformat(),
            "enabled": True,
            "cadence_mode": (
                "override" if get_fdt_recalculation_interval_override() is not None else "auto"
            ),
            "fdt_recalculation_interval_override": get_fdt_recalculation_interval_override(),
            "downlink_frequency_hz": downlink_at_sat_hz,
            "radio_receive_frequency_hz": radio_receive_target_hz,
            "doppler_at_receive_frequency_hz": downlink_doppler_at_sat_hz,
            "frequency_source": "radio" if radio_error is None else "manual_test_pass_entry",
            "uplink_frequency_hz": uplink_at_sat_hz,
            "radio_uplink_frequency_hz": radio_uplink_target_hz,
            "doppler_at_radio_uplink_frequency_hz": uplink_doppler_at_sat_hz,
            "uplink_frequency_source": uplink_source,
        }
        downlink_programmed = False
        try:
            response = rig.rigctld_set_downlink_frequency(
                radio_receive_target_hz, RIGCTLD_PORT
            )
            _raise_for_rigctld_error(response, "downlink frequency")
            downlink_programmed = True
            if radio_uplink_target_hz is not None:
                response = rig.rigctld_set_uplink_frequency(
                    radio_uplink_target_hz, RIGCTLD_PORT
                )
                _raise_for_rigctld_error(response, "uplink frequency")
            _write_fdt_control_payload(payload)
        except Exception:
            if downlink_programmed:
                try:
                    rig.rigctld_set_downlink_frequency(
                        int(round(receive_hz)), RIGCTLD_PORT
                    )
                except Exception as rollback_exc:
                    print(f"[WARNING] Could not restore downlink after FDT enable failure: {rollback_exc}")
            if uplink_hz is not None:
                try:
                    rig.rigctld_set_uplink_frequency(
                        int(round(uplink_hz)), RIGCTLD_PORT
                    )
                except Exception as rollback_exc:
                    print(f"[WARNING] Could not restore uplink after FDT enable failure: {rollback_exc}")
            raise
        message = (
            f"FDT enabled: radio receive {radio_receive_target_hz / 1e6:.6f} MHz; "
            f"downlink at satellite {downlink_at_sat_hz / 1e6:.6f} MHz."
        )
        if radio_uplink_target_hz is not None and uplink_at_sat_hz is not None:
            message += (
                f" Radio uplink {radio_uplink_target_hz / 1e6:.6f} MHz; "
                f"uplink at satellite {uplink_at_sat_hz / 1e6:.6f} MHz."
            )
        _set_status_message(message, last_command="enable_fdt")
        return {"ok": True, "action": "enable_fdt", "message": message, **payload}
    except Exception as exc:
        message = f"Could not enable FDT: {exc}"
        _set_status_message(message, last_command="enable_fdt")
        return {"ok": False, "action": "enable_fdt", "message": message}


def disable_fdt_from_web():
    """Stop continuous FDT for the current LINEAR pass."""
    action = "disable_fdt"
    status = read_tracking_status()
    with STATE_LOCK:
        tracking_running = bool(APP_STATE.get("tracking_running"))
        current_key = APP_STATE.get("current_pass_key")
        active_pass = next(
            (p for p in APP_STATE.get("passes", []) if p.get("_key") == current_key),
            {},
        )
    satellite_type = status.get("satellite_type") or active_pass.get("satellite_type") or ""
    if not tracking_running or str(satellite_type).strip().upper() != "LINEAR":
        return {
            "ok": False,
            "action": action,
            "message": "Pause FDT is available only during an active LINEAR satellite pass.",
        }

    clear_fdt_control_state("Pause FDT requested")
    message = "FDT paused. Continuous Doppler tuning has stopped; the radio remains at its last tuned frequencies."
    _set_status_message(message, last_command=action)
    return {
        "ok": True,
        "action": action,
        "message": message,
        "fdt_enabled": False,
        "fdt_recalculation_interval": get_fdt_recalculation_interval(),
        "fdt_recalculation_interval_override": get_fdt_recalculation_interval_override(),
        "fdt_cadence_mode": (
            "override" if get_fdt_recalculation_interval_override() is not None else "auto"
        ),
    }

def read_fdt_console_status(filename=FDT_CONTROL_FILE):
    """Return the current pass's FDT calculation, or an empty mapping."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def update_test_pass_fdt_console_status(fdt_status, tracking_status):
    """Add current tracker Doppler and overlay live test-pass radio values."""
    live_status = dict(fdt_status) if isinstance(fdt_status, dict) else {}
    if not live_status:
        # A Doppler-only console status must not make the legacy
        # payload-presence check report that FDT itself is enabled.
        live_status["enabled"] = False
    if not isinstance(tracking_status, dict):
        return live_status

    def copy_finite_value(target_key, source_key, multiplier=1.0):
        try:
            value = float(tracking_status.get(source_key)) * multiplier
        except (TypeError, ValueError):
            return
        if math.isfinite(value):
            live_status[target_key] = value

    # This is the authoritative current downlink Doppler from
    # bqe_track_continuously.  It is a live orbital calculation for a real pass
    # and the automatically declining configured value for a test pass.
    copy_finite_value("current_downlink_doppler_hz", "downlink_doppler_hz")

    if (
            not _fdt_status_enabled(fdt_status)
            or not bool(tracking_status.get("test_pass"))):
        return live_status

    copy_finite_value("radio_receive_frequency_hz", "downlink_frequency_hz")
    copy_finite_value("radio_uplink_frequency_hz", "uplink_frequency_hz")
    copy_finite_value("doppler_at_receive_frequency_hz", "downlink_doppler_hz")
    copy_finite_value(
        "doppler_at_radio_uplink_frequency_hz",
        "uplink_doppler_hz",
        multiplier=-1.0,
    )
    return live_status


def runtime_control_status(
        tracking_running,
        satellite_type,
        antenna_tracking_effective,
        tracking_status,
        fdt_console_status):
    """Return the effective antenna-tracking and radio-tuning states.

    The tracker reports antenna movement directly.  For LINEAR passes, the FDT
    control payload is the authoritative runtime tuning switch.  Other passes
    use the tracker's tuning/Doppler boolean when it is available.
    """
    process_running = bool(tracking_running)
    tracking_enabled = bool(
        process_running and antenna_tracking_effective is True
    )

    reported_tuning = None
    for field_name in (
            "tuning_enabled",
            "radio_tuning_enabled",
            "frequency_tracking_enabled",
            "doppler_tracking_enabled",
            "enable_frequency_tracking",
            "enable_doppler_tracking"):
        field_value = tracking_status.get(field_name)
        if isinstance(field_value, bool):
            reported_tuning = field_value
            break

    if not process_running:
        tuning_enabled = False
    elif str(satellite_type or "").strip().upper() == "LINEAR":
        tuning_enabled = _fdt_status_enabled(fdt_console_status)
    elif isinstance(reported_tuning, bool):
        tuning_enabled = reported_tuning
    else:
        tuning_enabled = True

    return tracking_enabled, tuning_enabled

def status_payload():
    """Build JSON-safe status object for /api/status."""
    now = utc_now()
    try:
        # Re-read schedule.json so the browser reflects external schedule changes.
        refresh_schedule_state(SCHEDULE_FILE)
    except Exception as e:
        with STATE_LOCK:
            APP_STATE["last_message"] = f"Schedule refresh failed: {e}"

    observer_location = load_observer_location()
    recording_status = audio_recording_status()
    from plugins.bqe_sound_recorder.bqe_sound_recorder import external_recording_status
    pass_recording_status = external_recording_status()
    tracking_status = augment_tracking_status_for_map(read_tracking_status(), observer_location)
    fdt_console_status = read_fdt_console_status()
    fdt_console_status = update_test_pass_fdt_console_status(
        fdt_console_status,
        tracking_status,
    )
    celestial_positions = calculate_sun_moon_positions(observer_location, now)
    with STATE_LOCK:
        current_satellite_for_fdt = APP_STATE.get("current_satellite")
    fdt_passband = (
        load_fdt_passband(current_satellite_for_fdt)
        if current_satellite_for_fdt
        else {}
    )

    with STATE_LOCK:
        passes = list(APP_STATE["passes"])
        current_key = APP_STATE["current_pass_key"]
        current_pass = next((p for p in passes if p.get("_key") == current_key), {})
        current_satellite_type = (
            tracking_status.get("satellite_type")
            or current_pass.get("satellite_type")
            or ""
        )
        pass_active = bool(APP_STATE["tracking_running"]) or any(
            p["_start_dt"] <= now <= p["_end_dt"] for p in passes
        )
        preset_command_running = bool(APP_STATE.get("preset_command_running", False))
        antenna_tracking_override = APP_STATE.get("antenna_tracking_override")
        reported_antenna_tracking = tracking_status.get("enable_antenna_tracking")
        # A menu override is authoritative immediately; otherwise use the active
        # tracker's reported YAML/CLI-derived state when a pass is running.
        if isinstance(antenna_tracking_override, bool):
            antenna_tracking_effective = antenna_tracking_override
        elif pass_active and isinstance(reported_antenna_tracking, bool):
            antenna_tracking_effective = reported_antenna_tracking
        else:
            antenna_tracking_effective = None
        antenna_tracking_disabled = antenna_tracking_effective is False
        tracking_enabled, tuning_enabled = runtime_control_status(
            APP_STATE["tracking_running"],
            current_satellite_type,
            antenna_tracking_effective,
            tracking_status,
            fdt_console_status,
        )
        fdt_enabled = _fdt_status_enabled(fdt_console_status)
        payload = {
            "utc_time": now.strftime("%H:%M:%S UTC"),
            "utc_date": now.strftime("%d %b %Y"),
            "message": APP_STATE["last_message"],
            "tracking_running": APP_STATE["tracking_running"],
            "pass_active": pass_active,
            "audio_recording": recording_status,
            "external_audio_recording": pass_recording_status,
            "current_satellite": APP_STATE["current_satellite"],
            "current_log": APP_STATE["current_log"],
            "tracking_status": tracking_status,
            "observer_location": observer_location,
            "celestial_positions": celestial_positions,
            "command_running": APP_STATE.get("command_running", False),
            "last_command": APP_STATE.get("last_command"),
            "last_command_result": APP_STATE.get("last_command_result"),
            "shutdown_requested": APP_STATE.get("shutdown_requested", False),
            "restart_requested": APP_STATE.get("restart_requested", False),
            "presets": list(APP_STATE.get("presets", [])),
            "preset_command_running": preset_command_running,
            "preset_buttons_enabled": not pass_active and not preset_command_running,
            "antenna_tracking_override": antenna_tracking_override,
            "antenna_tracking_effective": antenna_tracking_effective,
            "antenna_tracking_disabled": antenna_tracking_disabled,
            "tracking_enabled": tracking_enabled,
            "tuning_enabled": tuning_enabled,
            "fdt_enabled": fdt_enabled,
            "fdt_recalculation_interval": get_fdt_recalculation_interval(),
            "fdt_recalculation_interval_override": get_fdt_recalculation_interval_override(),
            "fdt_cadence_mode": (
                "override" if get_fdt_recalculation_interval_override() is not None else "auto"
            ),
            "tracking_sleep_interval_seconds": TRACKING_SLEEP_INTERVAL_SECONDS,
            "tracking_sleep_interval_high_elevation_seconds": TRACKING_SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS,
            "tracking_high_pass_elevation": TRACKING_HIGH_PASS_ELEVATION,
            "fdt_available": bool(
                APP_STATE.get("tracking_running")
                and str(current_satellite_type).strip().upper() == "LINEAR"
                and not CURRENT_PASS_END_EVENT.is_set()
                and not SHUTDOWN_EVENT.is_set()
            ),
            "fdt_console_status": fdt_console_status,
            "fdt_passband": fdt_passband,
            "countdown": countdown_text(now, passes),
            "passes": [],
        }

    for p in passes:
        status = row_status(p, now)
        if current_key and p["_key"] == current_key:
            status = "active"
        payload["passes"].append({
            "key": p["_key"],
            "satellite": p["_satellite"],
            "satellite_type": p.get("satellite_type") or "",
            "pass_number": p.get("pass") or p.get("pass_number") or p.get("orbit") or p["_index"],
            "el": p["_max_el"],
            "start": format_dt(p["_start_dt"]),
            "finish": format_dt(p["_end_dt"]),
            "status": status,
            "test_pass": bool(p.get("test_pass")),
        })
    return payload



def make_web_console_index_html():
    """Return web-console HTML generated by bqe_wisp_web using YAML settings."""
    web_settings = load_web_console_settings(GENERAL_SETTINGS_FILE)
    return build_index_html(web_settings)

def start_web_console(port=None):
    """Start the web console in a background daemon thread."""
    global WEB_SERVER

    web_settings = load_web_console_settings(GENERAL_SETTINGS_FILE)
    if port is None:
        port = web_settings.ui_port

    handler_factory = partial(
        WebConsoleHandler,
        status_payload_func=status_payload,
        command_payload_func=handle_web_command,
        index_html=build_index_html(web_settings),
        sstv_gallery_location=web_settings.sstv_gallery_location,
        recordings_location=web_settings.recordings_location,
        logs_location=LOG_DIR,
        ui_theme=web_settings.ui_theme,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_factory)
    WEB_SERVER = server

    web_module = sys.modules.get(WebConsoleHandler.__module__)
    web_module_file = getattr(web_module, "__file__", "unknown")
    print(f"[WEB] Using web UI module: {web_module_file}")

    thread = threading.Thread(target=server.serve_forever, name="BQEWebConsole", daemon=True)
    thread.start()
    with STATE_LOCK:
        APP_STATE["web_started_at"] = utc_now().isoformat()
        APP_STATE["last_message"] = f"Web console available at http://localhost:{port}/"
    print(f"[WEB] Web console available at http://localhost:{port}/")
    return server


def main():
    restore_default_keyboard_interrupt_handler()
    run_environment_diagnostics()
    # Clear temporary/stale runtime files left by a previous scheduler process.
    remove_idle_task_status_file()
    clear_antenna_tracking_override()
    with STATE_LOCK:
        APP_STATE["antenna_tracking_override"] = None
        # The preset pane intentionally reflects only files present at startup.
        APP_STATE["presets"] = discover_preset_nicknames(PRESETS_DIR)
    web_server = start_web_console()

    try:
        try:
            ensure_schedule_file_exists(SCHEDULE_FILE)
        except Exception as e:
            print(f"Error: could not create {SCHEDULE_FILE}: {e}")
            with STATE_LOCK:
                APP_STATE["last_message"] = f"Error creating {SCHEDULE_FILE}: {e}"
            wait_for_shutdown()
            return

        # Re-read schedule.json while waiting instead of iterating one startup snapshot.
        # This is important for Tracking > Run test pass now: the new entry is inserted
        # only ten seconds in the future and must be noticed even when the original
        # schedule was empty or the scheduler was waiting for a much later pass.
        idle_for_key = None
        no_pending_message_printed = False

        while not SHUTDOWN_EVENT.is_set():
            try:
                passes = refresh_schedule_state(SCHEDULE_FILE)
            except Exception as e:
                print(f"Error loading {SCHEDULE_FILE}: {e}")
                with STATE_LOCK:
                    APP_STATE["last_message"] = f"Error loading {SCHEDULE_FILE}: {e}"
                sleep_until_shutdown_or_timeout(1.0)
                continue

            now = utc_now()
            with STATE_LOCK:
                completed = set(APP_STATE.get("completed", []))

            # Mark expired entries once so they are not reconsidered on every reload.
            for entry in passes:
                if entry["_end_dt"] <= now and entry["_key"] not in completed:
                    completed.add(entry["_key"])
                    with STATE_LOCK:
                        APP_STATE["completed"].append(entry["_key"])

            pending = [
                entry for entry in passes
                if entry["_end_dt"] > now and entry["_key"] not in completed
            ]

            if not pending:
                if not no_pending_message_printed:
                    print(f"[INFO] No pending passes in {SCHEDULE_FILE}. Waiting for schedule changes.")
                    with STATE_LOCK:
                        APP_STATE["last_message"] = "No pending passes. Waiting for schedule changes."
                    no_pending_message_printed = True
                sleep_until_shutdown_or_timeout(0.5)
                continue

            no_pending_message_printed = False
            entry = pending[0]
            sat_name = entry["_satellite"]
            start_time = entry["_start_dt"]
            end_time = entry["_end_dt"]

            now = utc_now()
            if start_time > now:
                # Start the idle activity once for the pass currently at the head of
                # the schedule. Re-evaluate the schedule every second so a newly
                # inserted test pass can become the next pass immediately.
                if idle_for_key != entry["_key"]:
                    stop_idle_wait_program()
                    do_while_waiting()
                    idle_for_key = entry["_key"]

                remaining = (start_time - now).total_seconds()
                wait_seconds = min(1.0, max(0.05, remaining))
                sleep_until_shutdown_or_timeout(wait_seconds)
                continue

            # CURRENT_IDLE_PROCESS is authoritative because a helper may have
            # been replaced asynchronously by a web preset selection.
            stop_idle_wait_program()
            idle_for_key = None

            if SHUTDOWN_EVENT.is_set():
                break

            run_pass(entry, start_time, end_time)

            if SHUTDOWN_EVENT.is_set():
                break

            print("[OK] Pass complete. Re-reading schedule...\n")
            sleep_until_shutdown_or_timeout(0.25)

        stop_idle_wait_program()

        if SHUTDOWN_EVENT.is_set():
            print("\n[INFO] Shutdown requested. Exiting BQE WISP.")
            with STATE_LOCK:
                APP_STATE["last_message"] = "Shutdown requested. Exiting BQE WISP."

    except KeyboardInterrupt:
        print("\n[INFO] Keyboard interrupt received. Exiting BQE WISP.")
        request_shutdown("Keyboard interrupt received. Cleaning up helper programs...")
    finally:
        with STATE_LOCK:
            restart_requested = APP_STATE["restart_requested"]
        cleanup_helper_programs()
        clear_antenna_tracking_override()
        with STATE_LOCK:
            APP_STATE["tracking_running"] = False
            APP_STATE["fdt_recalculation_interval"] = FDT_RECALCULATION_INTERVAL
            APP_STATE["fdt_recalculation_interval_override"] = None
            APP_STATE["current_pass_key"] = None
            APP_STATE["current_satellite"] = None
            APP_STATE["last_message"] = "BQE WISP has exited."
        if web_server is not None and not restart_requested:
            time.sleep(load_web_console_settings(GENERAL_SETTINGS_FILE).ui_refresh_interval_seconds + 0.25)
        try:
            if web_server is not None:
                from plugins.bqe_audio_stream import AUDIO_STREAM
                AUDIO_STREAM.close()
                web_server.shutdown()
                web_server.server_close()
        except Exception as e:
            print(f"Warning: unable to stop web console cleanly: {e}")
        if restart_requested:
            print("[OK] BQE WISP shut down cleanly. Restarting server...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print("[OK] BQE WISP exited cleanly.  Please close any associated browser sessions.")


if __name__ == "__main__":
    main()
