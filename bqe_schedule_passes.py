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
import yaml  # NEW: for QTH config


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


def get_upcoming_passes(sat_name, nickname, sat, observer, minimum_elevation, duration_hours=48, satellite_type=""):
    ts = load.timescale()
    now_utc = datetime.now(timezone.utc)
    start_time = ts.from_datetime(now_utc)
    end_time = ts.from_datetime(now_utc + timedelta(hours=duration_hours))

    stime = datetime.now(timezone.utc)
    ftime = stime + timedelta(hours=48)
    
    passes = []
    current_pass = None
    t = start_time
    
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
    observer = wgs84.latlon(observer_lat_deg, observer_lon_deg, observer_altitude_m)


    # --------------bqe_config/satellites.yaml nickname lookup and pass calculation---------- 
    #   supports   --nickname  <nickname>  where nickname is defined in satellites.yaml

    satellite_config = None
    satellite_name_from_cfg = None
    satellite_catalog_number = None

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

            satellite_nickname        = satellite_config.get("nickname")
            satellite_type            = satellite_config.get("satellite_type") or ""
            satellite_name_from_cfg   = satellite_config.get("satellite_name")  # Subkey of the nickname in the yaml
            satellite_catalog_number  = satellite_config.get("catalog_number")
            
            #Purely cosmetic use to let us list satellite names in printed schedule
            # rather than the catalog numbers that are in the generated json. 
            if satellite_catalog_number not in dict_catalog_to_sat_name.keys():
                dict_catalog_to_sat_name[satellite_catalog_number] = satellite_name_from_cfg

            # Use catalog numbers instead of text strings, which can be unreliable and have whitespace etc.
            if satellite_catalog_number:
                satellite = str(satellite_catalog_number)

            print("--------- Processing nickname orbit calculations for:", satellite)
            sat_name, tle_line1, tle_line2 = load_tle_by_name_or_id(tle_file, satellite)
            sat = EarthSatellite(tle_line1, tle_line2, sat_name, load.timescale())

            print("Getting passes for ", satellite)
            passes = get_upcoming_passes(satellite, nickname, sat, observer, minimum_elevation, satellite_type=satellite_type)
            for p in passes:
                combined_passes.append(p)

    ########################## Sat names without nicknames ##################
    # Calculate pass info and set nickname to "None"
    if args.sat_name:
        for sat_name in sat_names:
            print("--------- Processing ", sat_name)
            sat_name, tle_line1, tle_line2 = load_tle_by_name_or_id(tle_file, sat_name)
            sat = EarthSatellite(tle_line1, tle_line2, sat_name, load.timescale())

            print("Getting passes for sat_name object: ", sat_name)
            passes = get_upcoming_passes(sat_name, "None", sat, observer, minimum_elevation, satellite_type="")
            for p in passes:
                combined_passes.append(p)

   

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
    main()


