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
from datetime import datetime, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from bqe_wisp_web import (
    WebConsoleHandler,
    build_index_html,
    get_web_console_port,
    load_web_console_settings,
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

# TODO -Review sleep intervals and see if we need them,as they are likely in bqe_track_continuously.
DEFAULT_GENERAL_SETTINGS = {
    "python_path": sys.executable or "python",
    "sleep_interval_seconds": 60,
    "sleep_interval_high_elevation_seconds": 10,
    "horizon_threshold_elevation": 0.1,
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
    third_party_settings = _settings_section(program_settings, "third_party")

    python_path = common_settings.get("python_path")
    if python_path is not None and str(python_path).strip():
        settings["python_path"] = str(python_path).strip()

    settings["sleep_interval_seconds"] = _coerce_seconds(
        wisp_settings.get("sleep_interval"),
        settings["sleep_interval_seconds"],
        "program_settings.bqe_wisp.sleep_interval",
    )
    settings["sleep_interval_high_elevation_seconds"] = _coerce_seconds(
        wisp_settings.get("sleep_interval_high_elevation"),
        settings["sleep_interval_high_elevation_seconds"],
        "program_settings.bqe_wisp.sleep_interval_high_elevation",
    )

    settings["horizon_threshold_elevation"] = _coerce_float(
        wisp_settings.get("horizon_threshold_elevation"),
        settings["horizon_threshold_elevation"],
        "program_settings.bqe_wisp.horizon_threshold_elevation",
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
SCHEDULER_SLEEP_INTERVAL_SECONDS = GENERAL_SETTINGS["sleep_interval_seconds"]
SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS = GENERAL_SETTINGS["sleep_interval_high_elevation_seconds"]
HORIZON_THRESHOLD_ELEVATION = GENERAL_SETTINGS["horizon_threshold_elevation"]

SCHEDULE_FILE = os.path.join(SCRIPT_DIR, "schedule.json")
PRESETS_DIR = os.path.join(SCRIPT_DIR, "presets")
QTH_CONFIG_FILE = os.path.join(SCRIPT_DIR, "bqe_config", "my_qth.yaml")
TRACKING_SCRIPT = os.path.join(SCRIPT_DIR, "bqe_track_continuously.py")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_FILE = os.path.join(LOG_DIR, "bqe_wisp_tracking_status.json")
RIGCTLD_PID_FILE = os.path.join(LOG_DIR, "rigctld.pid")
PASS_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "pass_program.pid")
LEGACY_PASS_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "pass_program_pid")
IDLE_WAIT_PROGRAM_PID_FILE = os.path.join(LOG_DIR, "idle_wait_program.pid")
IDLE_TASK_STATUS_FILE = os.path.join(SCRIPT_DIR, "logs", "idle_task.yaml")
UPDATE_KEPS_SCRIPT = os.path.join(SCRIPT_DIR, "bqe_update_keps.py")
WEB_COMMAND_TIMEOUT_SECONDS = 300

STATE_LOCK = threading.RLock()
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
    "presets": [],
    "preset_command_running": False,
    "idle_task_active": False,
}

SHUTDOWN_EVENT = threading.Event()
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


def safe_subprocess_cmd(sat_name, status_file=STATUS_FILE, rigctld_pid_file=RIGCTLD_PID_FILE, pass_program_pid_file=PASS_PROGRAM_PID_FILE):
    """Use the current Python executable when launching the tracking script."""
    return [
        PYTHON_PATH,
        TRACKING_SCRIPT,
        sat_name,
        "--status_file", status_file,
        "--rigctld_pid_file", rigctld_pid_file,
        "--pass_program_pid_file", pass_program_pid_file,
    ]


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


def run_pass(pass_entry, start, end):
    """Run tracking subprocess for a satellite and terminate it at end time."""
    global CURRENT_TRACKING_PROCESS

    if SHUTDOWN_EVENT.is_set():
        return

    sat_name = pass_entry["_satellite"]
    os.makedirs(LOG_DIR, exist_ok=True)

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

    with open(log_filename, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            safe_subprocess_cmd(sat_name, STATUS_FILE, RIGCTLD_PID_FILE, PASS_PROGRAM_PID_FILE),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        CURRENT_TRACKING_PROCESS = process

        duration = (end - utc_now()).total_seconds()
        if duration <= 0:
            print(f"⚠️ End time {end.isoformat()} already passed; skipping.")
            if process.poll() is None:
                process.terminate()
            CURRENT_TRACKING_PROCESS = None
            terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
            terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
            terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")
            return

        try:
            if not sleep_until_shutdown_or_timeout(duration):
                print("[INFO] Exit requested. Stopping active tracking pass early.")
        finally:
            print(f"[INFO] Stopping {sat_name} at {end.isoformat()} ...")
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    print("[WARNING] Process did not exit cleanly — force killing...")
                    process.kill()

            CURRENT_TRACKING_PROCESS = None
            terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
            terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
            terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")

    with STATE_LOCK:
        APP_STATE["tracking_running"] = False
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
    try:
        idle_task_ctl.program_preset_by_nickname(nickname)

        status_updated = update_idle_task_status_for_preset(nickname)
        message = f"Radio programmed with preset {nickname}. Waiting for the next pass."
        if status_updated:
            message += " Updated idle_task_status.yaml."

        result = {
            "ok": True,
            "action": "program_preset",
            "nickname": nickname,
            "message": message,
        }
        print(f"[WEB PRESET] {message}")
        return result
    except Exception as e:
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


def start_idle_wait_program():
    """Start the external wait-time program selected by the configured idle preset.

    Expected flow:
      1. Read radio_idle_preset_nickname from bqe_config/my_rig.yaml. This is global
         for all satellite types,  and needs to be within the capabilities of the radio
         being used. 

      2. Find a YAML preset under presets/ whose nickname matches that value.

      3. Read that preset's tuning and  program_to_run_while_waiting values.

      4. Tune the radio to the frequency and mode specified in the radio_idle_preset_nickname
         metadata.

      5. If specified in the preset yaml, launch the 'program_to_run_while_waiting'.  

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
        print("Warning: radio_idle_preset_nickname is not set in my_rig.yaml. Skipping idle wait program.")
        return None

    idle_preset_nickname = str(idle_preset_nickname).strip()
    if not sleep_until_shutdown_or_timeout(5):  # Wait a bit before switching to idle tasks for enhanced user experience.
        return None

    try:
        idle_preset_cfg = load_preset_config_by_nickname(idle_preset_nickname)
        if not idle_preset_cfg:
            print(
                f"Warning: idle preset {idle_preset_nickname!r} was not found under "
                f"{os.path.join(SCRIPT_DIR, 'presets')}."
            )
            return None

        program_to_run = idle_preset_cfg.get("program_to_run_while_waiting") # We do not currently allow arguments
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


def stop_idle_wait_program(process=None, pid_file=IDLE_WAIT_PROGRAM_PID_FILE):
    """Stop an idle wait program that was started before a scheduled pass.

    This intentionally stops the idle program just before tracking begins.  It is
    cross-platform: Windows uses taskkill /T /F to include child processes, and
    Linux/macOS use the process group created by start_new_session=True.
    """
    global CURRENT_IDLE_PROCESS

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

    print(f"Stopping idle wait program PID {pid} before satellite pass starts...")
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
            APP_STATE["last_message"] = "Stopped idle wait program before satellite pass."


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

    terminate_popen_process(CURRENT_WEB_COMMAND_PROCESS, "web command process")
    CURRENT_WEB_COMMAND_PROCESS = None

    terminate_popen_process(CURRENT_TRACKING_PROCESS, "tracking process")
    CURRENT_TRACKING_PROCESS = None

    stop_idle_wait_program(CURRENT_IDLE_PROCESS)
    CURRENT_IDLE_PROCESS = None

    terminate_rigctld_from_pid_file(RIGCTLD_PID_FILE)
    terminate_process_from_pid_file(PASS_PROGRAM_PID_FILE, "pass program")
    terminate_process_from_pid_file(LEGACY_PASS_PROGRAM_PID_FILE, "pass program")
    terminate_process_from_pid_file(IDLE_WAIT_PROGRAM_PID_FILE, "idle wait program")

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
        idle_task_ctl.program_preset_by_nickname(nickname)
        try:
            begin_idle_task_status(nickname)
        except Exception as e:
            print(f"Warning: could not create {IDLE_TASK_STATUS_FILE}: {e}")
    else:
        print("[INFO] Idle_preset_nickname not defined.  Skipping idle task start")

    # Optional command helper program (such as wsjtx) that can run while the radio is waiting.
    # useful for monitoring wspr, sstv etc. between passes.
    return start_idle_wait_program()


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
        print("WEB COMMAND - Starting")
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
        print(f"[WEB COMMAND] {message}")

    thread = threading.Thread(target=worker, name=f"BQEWebCommand-{action}", daemon=True)
    thread.start()
    return {"ok": True, "action": action, "message": f"{display_name} started."}


def handle_web_command(action, request_payload=None):
    """Run a command requested from the web-console menus or preset pane."""
    action = str(action or "").strip().lower()
    request_payload = request_payload if isinstance(request_payload, dict) else {}

    if action == "program_preset":
        return program_preset_from_web(request_payload.get("nickname"))

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

    message = f"Unknown command: {action or '(blank)'}"
    return {"ok": False, "action": action, "message": message}

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
    tracking_status = augment_tracking_status_for_map(read_tracking_status(), observer_location)
    celestial_positions = calculate_sun_moon_positions(observer_location, now)

    with STATE_LOCK:
        passes = list(APP_STATE["passes"])
        current_key = APP_STATE["current_pass_key"]
        pass_active = bool(APP_STATE["tracking_running"]) or any(
            p["_start_dt"] <= now <= p["_end_dt"] for p in passes
        )
        preset_command_running = bool(APP_STATE.get("preset_command_running", False))
        payload = {
            "utc_time": now.strftime("%H:%M:%S UTC"),
            "utc_date": now.strftime("%d %b %Y"),
            "message": APP_STATE["last_message"],
            "tracking_running": APP_STATE["tracking_running"],
            "pass_active": pass_active,
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
        })
    return payload



def make_web_console_index_html():
    """Return web-console HTML generated by bqe_wisp_web using YAML settings."""
    web_settings = load_web_console_settings(GENERAL_SETTINGS_FILE)
    return build_index_html(web_settings)

def start_web_console(port=None):
    """Start the web console in a background daemon thread."""
    global WEB_SERVER

    if port is None:
        port = get_web_console_port(GENERAL_SETTINGS_FILE)

    handler_factory = partial(
        WebConsoleHandler,
        status_payload_func=status_payload,
        command_payload_func=handle_web_command,
        index_html=make_web_console_index_html(),
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
    # Clear a stale file left by an unclean prior shutdown before a new idle task starts.
    remove_idle_task_status_file()
    with STATE_LOCK:
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
            # Keep the web console up so the error is visible in a browser.
            wait_for_shutdown()
            return

        try:
            passes = refresh_schedule_state(SCHEDULE_FILE)
        except Exception as e:
            print(f"Error: {e}")
            with STATE_LOCK:
                APP_STATE["last_message"] = f"Error loading {SCHEDULE_FILE}: {e}"
            # Keep the web console up so the error is visible in a browser.
            wait_for_shutdown()
            return

        if not passes:
            print(f"No passes found in {SCHEDULE_FILE}.")
            with STATE_LOCK:
                APP_STATE["last_message"] = f"No passes found in {SCHEDULE_FILE}."
            wait_for_shutdown()
            return

        print(f"[OK] Loaded {len(passes)} passes from {SCHEDULE_FILE}")

        for entry in passes:
            if SHUTDOWN_EVENT.is_set():
                break

            sat_name = entry["_satellite"]
            start_time = entry["_start_dt"]
            end_time = entry["_end_dt"]

            now = utc_now()
            if end_time <= now:
                msg = f"Skipping {sat_name} — pass already ended at {end_time.isoformat()}"
                print(msg)
                with STATE_LOCK:
                    APP_STATE["last_message"] = msg
                    APP_STATE["completed"].append(entry["_key"])
                continue

            if start_time > now:
                idle_process = None
                try:
                    idle_process = do_while_waiting()
                    if not wait_until(start_time):
                        break
                finally:
                    stop_idle_wait_program(idle_process)

            if SHUTDOWN_EVENT.is_set():
                break

            run_pass(entry, start_time, end_time)

            if SHUTDOWN_EVENT.is_set():
                break

            print("[OK] Pass complete. Waiting for next scheduled interval...\n")
            sleep_until_shutdown_or_timeout(5)

        if SHUTDOWN_EVENT.is_set():
            print("\n[INFO] Shutdown requested. Exiting BQE WISP.")
            with STATE_LOCK:
                APP_STATE["last_message"] = "Shutdown requested. Exiting BQE WISP."
        else:
            print("\n[SUCCESS] All scheduled passes completed. Web console remains available.")
            with STATE_LOCK:
                APP_STATE["last_message"] = "All scheduled passes completed."
            wait_for_shutdown()

    except KeyboardInterrupt:
        print("\n[INFO] Keyboard interrupt received. Exiting BQE WISP.")
        request_shutdown("Keyboard interrupt received. Cleaning up helper programs...")
    finally:
        with STATE_LOCK:
            restart_requested = APP_STATE["restart_requested"]
        cleanup_helper_programs()
        with STATE_LOCK:
            APP_STATE["tracking_running"] = False
            APP_STATE["current_pass_key"] = None
            APP_STATE["current_satellite"] = None
            APP_STATE["last_message"] = "BQE WISP has exited."
        if web_server is not None and not restart_requested:
            time.sleep(load_web_console_settings(GENERAL_SETTINGS_FILE).ui_refresh_interval_seconds + 0.25)
        try:
            if web_server is not None:
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
