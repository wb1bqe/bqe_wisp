#!/bin/py
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

# Author: AL Lawler, WB1BQE


"""
  Mimics the scheduling behavior of Wisp and generates a schedule of leo 
  Satellite passes.  Unlike wisp, if there are conflicts, this script takes
  the satellite with the highest pass elevation, rather than by (TODO:) specified priority

  This script takes one or more satellite nicknames, names or NORAD IDs. 
 
  A nickname is a key to entries in bqe_wisp/satellites.yaml. If a nickname is given, the yaml is consulted
  for the corresponding name and catalog number, which is then used to calculate pass information.
 
  Once a list of satellite names (Either supplied or resolved from a nickname has been created,
  it looks up the appropriate entry in a keps file, then iterates a fast-forward sequence of 
  tracking in time to find when
  each satellite is visible, notes its highest elevation, and writes a combined JSON schedule which
  is typically consumed by bqe-track-continuously.py

# Example:  python bqe_schedule_passes.py --nickname umka-1 --nickname arcticsat1 

"""

from skyfield.api import EarthSatellite, load, wgs84
from datetime import datetime, timezone, timedelta
import numpy as np
import argparse
import sys
#import time
import json
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import yaml  # NEW: for QTH config


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _program_settings_section(root, section_name):
    """Return one named program_settings section from general_settings.yaml."""
    program_settings = root.get("program_settings") if isinstance(root, dict) else None

    if isinstance(program_settings, dict):
        value = program_settings.get(section_name)
        return value if isinstance(value, dict) else {}

    if isinstance(program_settings, list):
        for item in program_settings:
            if not isinstance(item, dict) or section_name not in item:
                continue
            value = item.get(section_name)
            return value if isinstance(value, dict) else {}

    return {}


def _parse_bool_setting(value, default=False):
    """Accept YAML booleans plus common string forms."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Expected a boolean setting, not {value!r}")


def resolve_general_settings_path():
    """Find the project's bqe_config/general_settings.yaml file."""
    candidates = [
        os.path.join(SCRIPT_DIR, "bqe_config", "general_settings.yaml"),
        os.path.join(os.path.dirname(SCRIPT_DIR), "bqe_config", "general_settings.yaml"),
        os.path.join(os.getcwd(), "bqe_config", "general_settings.yaml"),
        os.path.join(SCRIPT_DIR, "general_settings.yaml"),
        os.path.join(os.getcwd(), "general_settings.yaml"),
    ]

    seen = set()
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return candidate

    # Return the normal project location so the warning names the expected file.
    return os.path.join(SCRIPT_DIR, "bqe_config", "general_settings.yaml")


def load_scheduler_parallel_settings():
    """Load multiprocessing controls for bqe_schedule_passes.

    Missing settings deliberately fall back to sequential operation so older
    general_settings.yaml files retain their previous behavior.
    """
    settings = {
        "parallel_pass_generation": False,
        "parallel_pass_workers": 4,
    }
    path = resolve_general_settings_path()

    try:
        with open(path, "r", encoding="utf-8") as f:
            root = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Warning: general settings file {path} not found; pass generation will remain single-process.")
        return settings, path
    except Exception as e:
        print(f"Warning: could not read {path}: {e}; pass generation will remain single-process.")
        return settings, path

    section = _program_settings_section(root, "bqe_schedule_passes")
    settings["parallel_pass_generation"] = _parse_bool_setting(
        section.get("parallel_pass_generation"), settings["parallel_pass_generation"]
    )

    raw_workers = section.get("parallel_pass_workers", settings["parallel_pass_workers"])
    try:
        workers = int(raw_workers)
    except (TypeError, ValueError):
        raise ValueError(
            "general_settings.yaml setting "
            "program_settings.bqe_schedule_passes.parallel_pass_workers "
            f"must be an integer, not {raw_workers!r}"
        )
    if workers < 1:
        raise ValueError(
            "general_settings.yaml setting "
            "program_settings.bqe_schedule_passes.parallel_pass_workers must be at least 1"
        )
    settings["parallel_pass_workers"] = workers
    return settings, path


def load_tle_by_name_or_id(tle_path, target):
    """
    Returns (satellite_name, line1, line2) for the first satellite whose
    name contains 'target' (case-insensitive) OR whose NORAD ID matches target.
    The TLE file is expected as repeating triplets: name, line1, line2.
    """
    with open(tle_path, 'r', encoding='utf-8') as f:
        lines = [ln.rstrip('\n') for ln in f if ln.strip()]

    for i in range(len(lines) - 2):
        name, l1, l2 = lines[i], lines[i+1], lines[i+2]
        if not (l1.startswith('1 ') and l2.startswith('2 ')):
            continue
        satnum = l1[2:7].strip()
        if target.isdigit() and target == satnum:
            return name, l1, l2
        if target.lower() in name.lower():
            return name, l1.strip(), l2.strip()

    raise ValueError(f"Satellite '{target}' not found in {tle_path}")


########################## Tracking math ###################################

def track(t, sat, observer, freq_mhz):
    ts = load.timescale()

    # Topocentric (observer->sat) object
    topocentric = (sat - observer).at(t)
    altitude, azimuth, distance = topocentric.altaz()
    alt_deg = altitude.degrees
    az_deg = azimuth.degrees
    range_km = distance.km

    # Satellite geocentric position & velocity
    geocentric = sat.at(t)
    sat_pos_km = np.asarray(geocentric.position.km)
    sat_vel_km_s = np.asarray(geocentric.velocity.km_per_s)

    obs_geoc = observer.at(t)
    obs_pos_km = np.asarray(obs_geoc.position.km)
    obs_vel_km_s = np.asarray(obs_geoc.velocity.km_per_s)

    los_vec = sat_pos_km - obs_pos_km
    los_dist = np.linalg.norm(los_vec)
    if los_dist == 0:
        print("Observer and satellite positions coincide (!) — cannot compute LOS.")
        return
    los_unit = los_vec / los_dist

    rel_vel = sat_vel_km_s - obs_vel_km_s

    c_km_s = 299792.458
    range_rate_km_s = float(np.dot(rel_vel, los_unit))

    # Doppler shift
    doppler_hz = freq_mhz * 1e6 * (-range_rate_km_s / c_km_s)

    return az_deg, alt_deg, doppler_hz, alt_deg


def get_upcoming_passes(
        sat_name, nickname, sat, observer, minimum_elevation, duration_hours=48,
        satellite_type="", start_datetime_utc=None, verbose=True):
    ts = load.timescale()
    now_utc = start_datetime_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    start_time = ts.from_datetime(now_utc)
    end_time = ts.from_datetime(now_utc + timedelta(hours=duration_hours))

    stime = now_utc
    ftime = now_utc + timedelta(hours=duration_hours)
    
    passes = []
    current_pass = None
    t = start_time
    
    if verbose:
        print("start/end times ", start_time, end_time)
    while stime <= ftime:
        sat_pos = (sat - observer).at(t)
        altitude = sat_pos.altaz()[0].degrees
        azimuth = sat_pos.altaz()[1].degrees
        if altitude > 0:
            if current_pass is None:
                current_pass = {
                    'start': t,
                    'max_altitude': altitude,
                    'max_alt_at_azimuth': azimuth,
                    'sat_name': sat_name,
                    'nickname': nickname,
                    'satellite_type': satellite_type or "",
                }
            else:
                if altitude > current_pass['max_altitude']:
                    current_pass['max_altitude'] = round(altitude)
                    current_pass['max_alt_at_azimuth'] = round(azimuth)
                    current_pass['sat_name'] = sat_name
                    current_pass['nickname'] = nickname
                    current_pass['satellite_type'] = satellite_type or ""
        elif current_pass is not None:
            current_pass['end'] = t

            #Limiting rules
            if current_pass['max_altitude'] >= minimum_elevation: # Minimum pass height to be worth trying.
                passes.append(current_pass)
            current_pass = None

        t += timedelta(seconds=15)
        stime += timedelta(seconds=15)

    return passes


def _pass_to_ipc(pass_info):
    """Convert Skyfield Time objects into process-safe ISO strings."""
    item = dict(pass_info)
    item["start"] = pass_info["start"].utc_datetime().isoformat()
    if "end" in pass_info:
        item["end"] = pass_info["end"].utc_datetime().isoformat()
    return item


def _pass_from_ipc(pass_info, timescale):
    """Restore process-safe pass data to the Skyfield Time objects used below."""
    item = dict(pass_info)
    start_datetime = datetime.fromisoformat(item["start"])
    if start_datetime.tzinfo is None:
        start_datetime = start_datetime.replace(tzinfo=timezone.utc)
    item["start"] = timescale.from_datetime(start_datetime)

    if item.get("end"):
        end_datetime = datetime.fromisoformat(item["end"])
        if end_datetime.tzinfo is None:
            end_datetime = end_datetime.replace(tzinfo=timezone.utc)
        item["end"] = timescale.from_datetime(end_datetime)
    else:
        item.pop("end", None)
    return item


def calculate_passes_worker(task):
    """Calculate one satellite's possible passes in a worker process.

    Only primitive/picklable values cross the process boundary.  Each worker
    creates its own Skyfield objects, then returns ISO timestamps so this works
    with the Windows 'spawn' multiprocessing model as well as Linux.
    """
    ts = load.timescale()
    observer = wgs84.latlon(
        float(task["observer_lat_deg"]),
        float(task["observer_lon_deg"]),
        float(task["observer_altitude_m"]),
    )
    sat = EarthSatellite(
        task["tle_line1"], task["tle_line2"], task["tle_satellite_name"], ts
    )
    start_datetime_utc = datetime.fromisoformat(task["start_datetime_utc"])
    if start_datetime_utc.tzinfo is None:
        start_datetime_utc = start_datetime_utc.replace(tzinfo=timezone.utc)

    passes = get_upcoming_passes(
        task["schedule_sat_name"],
        task["nickname"],
        sat,
        observer,
        float(task["minimum_elevation"]),
        duration_hours=float(task.get("duration_hours", 48)),
        satellite_type=task.get("satellite_type", ""),
        start_datetime_utc=start_datetime_utc,
        verbose=False,
    )

    return {
        "label": task["label"],
        "worker_pid": os.getpid(),
        "passes": [_pass_to_ipc(p) for p in passes],
    }


def run_pass_tasks(pass_tasks, parallel_enabled, configured_workers):
    """Run pass scans sequentially or across multiple CPU processes."""
    combined_passes = []
    restore_timescale = load.timescale()

    if not pass_tasks:
        return combined_passes

    available_cpus = os.cpu_count() or 1
    worker_count = min(int(configured_workers), available_cpus, len(pass_tasks))
    use_parallel = bool(parallel_enabled) and worker_count > 1 and len(pass_tasks) > 1

    if use_parallel:
        print(
            f"[INFO] Parallel pass generation enabled: {len(pass_tasks)} satellite(s), "
            f"{worker_count} worker processes ({available_cpus} logical CPUs available)."
        )
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_to_label = {
                executor.submit(calculate_passes_worker, task): task["label"]
                for task in pass_tasks
            }
            for future in as_completed(future_to_label):
                label = future_to_label[future]
                try:
                    result = future.result()
                except Exception as e:
                    print(f"ERROR: pass calculation failed for {label}: {e}")
                    raise
                restored = [_pass_from_ipc(p, restore_timescale) for p in result["passes"]]
                combined_passes.extend(restored)
                print(
                    f"[INFO] Completed {label} on worker PID {result['worker_pid']}: "
                    f"{len(restored)} qualifying pass(es)."
                )
    else:
        print(f"[INFO] Pass generation is single-process for {len(pass_tasks)} satellite(s).")
        for task in pass_tasks:
            result = calculate_passes_worker(task)
            restored = [_pass_from_ipc(p, restore_timescale) for p in result["passes"]]
            combined_passes.extend(restored)
            print(f"[INFO] Completed {task['label']}: {len(restored)} qualifying pass(es).")

    return combined_passes

########################## YAML helpers #################################

def resolve_satellites_yaml_path(configured_path="bqe_wisp/satellites.yaml"):
    """Return the satellite YAML file path to use for nickname lookup.

    The requested default is bqe_wisp/satellites.yaml.  A couple of fallbacks
    are kept so the script still works when it is launched from inside the
    bqe_wisp directory or from older project layouts.
    """
    candidates = [
        str(configured_path),
        "satellites.yaml",
        "bqe_config/satellites.yaml",
    ]

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.exists(candidate):
            if candidate != str(configured_path):
                print(f"Warning: {configured_path} was not found; using {candidate} instead.")
            return candidate

    return str(configured_path)


def load_satellite_config(path="bqe_wisp/satellites.yaml"):
    """Load the satellites YAML file and return its contents."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Warning: satellite config file {path} not found.")
    except Exception as e:
        print(f"Warning: could not read satellite config {path}: {e}")
    return {}


def iter_satellite_entries(config):
    """Yield satellite entry dictionaries from a nested YAML structure."""
    if isinstance(config, dict):
        if "nickname" in config:
            yield config
        for value in config.values():
            yield from iter_satellite_entries(value)
    elif isinstance(config, list):
        for item in config:
            yield from iter_satellite_entries(item)


def find_satellite_by_nickname(config, nickname):
    """Search a satellite config for an entry with the given nickname."""
    wanted = str(nickname).strip()
    for item in iter_satellite_entries(config):
        item_nickname = item.get("nickname")
        if item_nickname is not None and str(item_nickname).strip() == wanted:
            return item
    return None


def is_true_auto_schedule(value):
    """Return True for boolean true (can be expanded for other varients if needed)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true"}


def load_auto_schedule_nicknames(path="bqe_wisp/satellites.yaml"):
    """Return nicknames from YAML entries where auto_schedule is true."""
    resolved_path = resolve_satellites_yaml_path(path)
    config = load_satellite_config(resolved_path)
    nicknames = []
    seen = set()

    for item in iter_satellite_entries(config):
        if not is_true_auto_schedule(item.get("auto_schedule")):
            continue

        nickname = item.get("nickname")
        if nickname is None or str(nickname).strip() == "":
            print(f"Warning: auto_schedule entry has no nickname and will be skipped: {item}")
            continue

        nickname = str(nickname).strip()
        if nickname not in seen:
            seen.add(nickname)
            nicknames.append(nickname)

    return nicknames, config, resolved_path

def intervals_overlap(start1, end1, start2, end2):
    """Returns True if two Skyfield Time intervals overlap."""
    s1, e1 = start1.utc_datetime(), end1.utc_datetime()
    s2, e2 = start2.utc_datetime(), end2.utc_datetime()
    return (s1 < e2) and (s2 < e1)

def main():
    combined_passes = []
    sat_names = []

    ap = argparse.ArgumentParser(description="Compute Az/El and Doppler for one or more satellites.")

    ########################### Satellite Tracking Args ########################### 
    ap.add_argument("--nickname", action="append", 
                    help="Nickname of satellite in satellites.yaml file. Used for frequency and other related info.")
    ap.add_argument("--tle_file", default="keps.txt", help="Path to TLE file (name + 2 lines format).")
    ap.add_argument("--auto_schedule", action="store_true",
                    help="Ignore manual satellite selections and schedule nicknames marked auto_schedule: true in satellites.yaml.")
    ap.add_argument("--satellites_yaml", default="bqe_wisp/satellites.yaml",
                    help="Path to satellites.yaml for nickname lookup and --auto_schedule (default: bqe_wisp/satellites.yaml).")
    ap.add_argument("--sat_name", default = None, action="append",
                    help="Satellite name (substring) OR NORAD ID. Repeat for multiple satellites.")

    ########################### QTH config file ###################################
    ap.add_argument("--qth_config", type=str, default="bqe_config/my_qth.yaml",
                    help="Path to QTH YAML file (default: bqe_config/my_qth.yaml)")

    # Set defaults to None so we can tell if CLI explicitly set them
    ap.add_argument("--lat", type=float, default=None, help="Observer latitude in degrees.")
    ap.add_argument("--lon", type=float, default=None, help="Observer longitude in degrees.")
    ap.add_argument("--alt", type=float, default=None, help="Observer altitude (AGL) in meters.")
    ap.add_argument("--minimum_elevation", type=float, default=15, help="Minimum pass elevation to schedule")

    # If --auto_schedule is present, ignore any command-line arguments that
    # come after it. This lets the flag be appended to an existing command
    # without accidentally mixing manual satellite selections with the
    # auto_schedule list.
    raw_argv = sys.argv[1:]
    if "--auto_schedule" in raw_argv:
        auto_schedule_index = raw_argv.index("--auto_schedule")
        effective_argv = raw_argv[:auto_schedule_index + 1]
        ignored_argv = raw_argv[auto_schedule_index + 1:]
        args = ap.parse_args(effective_argv)
        if ignored_argv:
            print(f"--auto_schedule set; ignoring following arguments: {' '.join(ignored_argv)}")
    else:
        args = ap.parse_args()

    nicknames = []
    auto_schedule_config = None
    auto_schedule_config_path = None
    satellites_yaml_path = resolve_satellites_yaml_path(args.satellites_yaml)

    # Need to have at least one sat_name or nickname, but can have any mixture of multiples of each.
    if args.auto_schedule:
        nicknames, auto_schedule_config, auto_schedule_config_path = load_auto_schedule_nicknames(args.satellites_yaml)
        args.nickname = nicknames
        args.sat_name = None
        sat_names = []
        print(f"--auto_schedule loaded {len(nicknames)} nickname(s) from {auto_schedule_config_path}: {', '.join(nicknames) if nicknames else '(none)'}")
        if not nicknames:
            print("ERROR: --auto_schedule was set, but no satellite entries with auto_schedule: true and a nickname were found.")
            exit(1)
    else:
        if args.nickname is not None:
            nicknames = args.nickname # Might have multiple nickname arguments

        if args.sat_name is not None:  # Might have multiple sat_name arguments
            sat_names = args.sat_name  #only do this if not null

    tle_file = args.tle_file
    observer_lat_deg = args.lat
    observer_lon_deg = args.lon
    observer_altitude_m = args.alt # Called altitude instead of elevation to disambiguate with antenna/pass elevation etc. 
    minimum_elevation = args.minimum_elevation
   
    dict_catalog_to_sat_name = {}


    # ------------------ Load QTH from YAML unless overridden by CLI. ------------------
    yaml_lat = yaml_lon = yaml_alt = None
    try:
        with open(args.qth_config, "r", encoding="utf-8") as f:
            qth_cfg = yaml.safe_load(f) or {}
            yaml_lat = qth_cfg.get("my_latitude")
            yaml_lon = qth_cfg.get("my_longitude")
            yaml_alt = qth_cfg.get("my_altitude")
            print(f"Loaded QTH from {args.qth_config}: lat={yaml_lat}, lon={yaml_lon}, alt={yaml_alt}")
    except FileNotFoundError:
            print(f"Warning: QTH file {args.qth_config} not found. Falling back to CLI/defaults.")
    except Exception as e:
            print(f"Warning: Could not parse bqe_config/my_qth.json file {args.qth_config}: {e}.")

    # ------------------ Merge CLI QTH overrides ------------------
    observer_lat_deg = args.lat if args.lat is not None else yaml_lat
    observer_lon_deg = args.lon if args.lon is not None else yaml_lon
    observer_altitude_m = args.alt if args.alt is not None else yaml_alt

    print(f"Using observer QTH: lat={observer_lat_deg}, lon={observer_lon_deg}, alt={observer_altitude_m} m")

    scheduler_settings, scheduler_settings_path = load_scheduler_parallel_settings()
    print(
        f"Loaded scheduler parallel settings from {scheduler_settings_path}: "
        f"parallel_pass_generation={scheduler_settings['parallel_pass_generation']}, "
        f"parallel_pass_workers={scheduler_settings['parallel_pass_workers']}"
    )

    # Build lightweight work items first.  TLE/YAML lookups remain in the parent
    # process; only the expensive 48-hour orbit scans are sent to workers.
    pass_tasks = []
    schedule_start_utc = datetime.now(timezone.utc)

    # -------------- bqe_config/satellites.yaml nickname lookup ----------------
    if args.nickname:
        sat_cfg_all = auto_schedule_config if args.auto_schedule else load_satellite_config(satellites_yaml_path)
        config_source = auto_schedule_config_path if args.auto_schedule else satellites_yaml_path

        for nickname in nicknames:
            print(f"--------------- processing the following entry in multiple nicknames {nickname}\n\n")
            satellite_config = find_satellite_by_nickname(sat_cfg_all, nickname)
            print(f"-------{satellite_config} found for nickname  {nickname}\n\n")

            if satellite_config is None:
                print(f"ERROR: nickname '{nickname}' not found in {config_source}.")
                print("Exiting...")
                exit(1)
            else:
                print(f"Loaded satellite config for nickname '{nickname}': {satellite_config}")

            satellite_type = satellite_config.get("satellite_type") or ""
            satellite_name_from_cfg = satellite_config.get("satellite_name")
            satellite_catalog_number = satellite_config.get("catalog_number")

            # Purely cosmetic use to let us list satellite names in the printed
            # schedule rather than catalog numbers from the generated JSON.
            if satellite_catalog_number is not None:
                dict_catalog_to_sat_name[satellite_catalog_number] = satellite_name_from_cfg
                dict_catalog_to_sat_name[str(satellite_catalog_number)] = satellite_name_from_cfg

            # Prefer catalog numbers for TLE matching.  If an older YAML entry
            # has no catalog number, retain the prior name/nickname fallback.
            satellite = str(
                satellite_catalog_number
                if satellite_catalog_number not in (None, "")
                else (satellite_name_from_cfg or nickname)
            )

            print("--------- Preparing nickname orbit calculations for:", satellite)
            tle_sat_name, tle_line1, tle_line2 = load_tle_by_name_or_id(tle_file, satellite)
            pass_tasks.append({
                "label": f"nickname {nickname}",
                "schedule_sat_name": satellite,
                "nickname": nickname,
                "satellite_type": satellite_type,
                "tle_satellite_name": tle_sat_name,
                "tle_line1": tle_line1,
                "tle_line2": tle_line2,
                "observer_lat_deg": observer_lat_deg,
                "observer_lon_deg": observer_lon_deg,
                "observer_altitude_m": observer_altitude_m,
                "minimum_elevation": minimum_elevation,
                "duration_hours": 48,
                "start_datetime_utc": schedule_start_utc.isoformat(),
            })

    ########################## Sat names without nicknames ##################
    if args.sat_name:
        for requested_sat_name in sat_names:
            print("--------- Preparing ", requested_sat_name)
            tle_sat_name, tle_line1, tle_line2 = load_tle_by_name_or_id(tle_file, requested_sat_name)
            pass_tasks.append({
                "label": f"satellite {requested_sat_name}",
                "schedule_sat_name": tle_sat_name,
                "nickname": "None",
                "satellite_type": "",
                "tle_satellite_name": tle_sat_name,
                "tle_line1": tle_line1,
                "tle_line2": tle_line2,
                "observer_lat_deg": observer_lat_deg,
                "observer_lon_deg": observer_lon_deg,
                "observer_altitude_m": observer_altitude_m,
                "minimum_elevation": minimum_elevation,
                "duration_hours": 48,
                "start_datetime_utc": schedule_start_utc.isoformat(),
            })

    # Per the requested policy, parallelization is considered only when
    # auto_schedule is active or more than three nicknames were supplied.
    parallel_triggered = bool(args.auto_schedule or len(nicknames) > 3)
    parallel_enabled = bool(
        scheduler_settings["parallel_pass_generation"] and parallel_triggered
    )

    if scheduler_settings["parallel_pass_generation"] and not parallel_triggered:
        print(
            "[INFO] Parallel pass generation is enabled in general_settings.yaml, "
            "but this run has three or fewer nicknames and is not --auto_schedule; "
            "using one process."
        )
    elif parallel_triggered and not scheduler_settings["parallel_pass_generation"]:
        print(
            "[INFO] This run qualifies for parallel pass generation, but it is "
            "disabled in general_settings.yaml; using one process."
        )

    # Report the effective scheduling mode before beginning the orbit scans.
    available_cpus = os.cpu_count() or 1
    effective_worker_count = min(
        int(scheduler_settings["parallel_pass_workers"]),
        available_cpus,
        max(1, len(pass_tasks)),
    )
    if parallel_enabled and effective_worker_count > 1 and len(pass_tasks) > 1:
        print(
            f"[INFO] Parallel pass scheduling is ENABLED; "
            f"using {effective_worker_count} worker processes."
        )
    else:
        print("[INFO] Parallel pass scheduling is DISABLED; using one process.")

    combined_passes = run_pass_tasks(
        pass_tasks,
        parallel_enabled=parallel_enabled,
        configured_workers=scheduler_settings["parallel_pass_workers"],
    )


    # Sort combined_passes by 'start' timestamp
    combined_passes.sort(key=lambda p: p['start'].utc_datetime())

    # Check for overlapping passes and remove the lower-altitude one
    print("\n------------------Checking for overlapping passes:")
    filtered_passes = []

    for p in combined_passes:
        if not filtered_passes:
            filtered_passes.append(p)
            continue

        prev = filtered_passes[-1]
        start1 = prev['start']
        end1 = prev.get('end', prev['start'])
        start2 = p['start']
        end2 = p.get('end', p['start'])

        if intervals_overlap(start1, end1, start2, end2):
            if p['max_altitude'] > prev['max_altitude']:
                print(f"[WARNING] Overlap detected: Keeping {p['sat_name']} (higher alt {p['max_altitude']}°), removing {prev['sat_name']} ({prev['max_altitude']}°)")
                filtered_passes[-1] = p
            else:
                print(f"[WARNING] Overlap detected: Keeping {prev['sat_name']} (higher alt {prev['max_altitude']}°), removing {p['sat_name']} ({p['max_altitude']}°)")
        else:
            filtered_passes.append(p)

    combined_passes = filtered_passes

    print("\n------------------ Schedule:")
    for p in combined_passes:
        start_time = p['start'].utc_iso()
        end_time = p['end'].utc_iso() if 'end' in p else 'Ongoing'
        satellite_name = p['sat_name']
        satellite_name_text = dict_catalog_to_sat_name.get(satellite_name, satellite_name) # Convert cat number to name for printing only.
        satellite_type_text = p.get("satellite_type", "")
        print(f"Start: {start_time}, End: {end_time}, Sat Name: {satellite_name_text}, Type: {satellite_type_text} "
              f"Max Altitude: {p['max_altitude']}°, Az: {p['max_alt_at_azimuth']}°")

    print(f"\nSummary: {len(combined_passes)} non-overlapping passes retained.")
    print(dict_catalog_to_sat_name)

    # ------------------ Export schedule to JSON which is consumed by bqe-wisp.py  ------------------
    print("\nWriting schedule to schedule.json ...")
    export_data = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "observer": {
            "latitude_deg": observer_lat_deg,
            "longitude_deg": observer_lon_deg,
            "altitude_m": observer_altitude_m
        },
        "tle_file": tle_file,
        "satellites_requested": nicknames if args.auto_schedule else (nicknames or sat_names),
        "passes": []
    }

    for p in combined_passes:
        export_data["passes"].append({
            "sat_name": p.get("sat_name", ""),
            "nickname": p.get("nickname", ""),
            "satellite_type": p.get("satellite_type", ""),
            "start": p["start"].utc_iso(),
            "end": p["end"].utc_iso() if "end" in p else None,
            "max_altitude": p.get("max_altitude", None),
            "max_alt_at_azimuth": p.get("max_alt_at_azimuth", None)
        })

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=4)
    print("[DONE] schedule.json successfully written/overwritten.")


if __name__ == "__main__":
    # Required for safe ProcessPoolExecutor startup on Windows; harmless on Linux.
    multiprocessing.freeze_support()
    main()


