#!/bin/py
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

# Author: AL Lawler, WB1BQE

# Continuously track a satellite and call tuning with doppler values when above horizon. 
# Can be run standalone, or called by bqe_wisp.
#
# How to run:  
# 
# Simple format:
#       Update defaults in bqe_config (Currently for WB1BQE) and then python bqe-track-continuously.py "ISS"  --freq "145.8"
#
# Longer format with overrides for defaults:
#       python bqe-track-continuously.py keps-250920.txt "AO-07" --lat 42.7833 --lon -71.5167 --alt 0 --freq 450


from skyfield.api import EarthSatellite, load, wgs84
from datetime import datetime, timezone
import numpy as np
import argparse
import time
import json
import os
import re


from urllib.parse import urlencode
import requests   # Antenna tracking

import subprocess # Used by Hamlib interface routines
import shutil
import yaml       # For loading configs from YAML

import bqe_hamlib_interface as rig


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERAL_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "bqe_config", "general_settings.yaml")

DEFAULT_GENERAL_SETTINGS = {
    "tle_file": None,
    "satellite_config": None,
    "qth_config": None,
    "radio_config": None,
    "status_file": None,
    "rigctld_pid_file": None,
    "pass_program_pid_file": None,
    "rigctld_port": None,
    "rigctld_path": None,
    "sleep_interval_seconds": None,
    "sleep_interval_high_elevation_seconds": None,
    "high_pass_elevation": None,
    "horizon_threshold_elevation": None,
    "default_observer_latitude": None,
    "default_observer_longitude": None,
    "default_observer_altitude_m": None,
    "default_uplink_frequency_mhz": None,
    "default_downlink_mode": None,
    "default_uplink_mode": None,
    "default_ctcss_tone": None,
    "default_satellite_mode_required": None,
    "default_enable_antenna_tracking": None,
    "default_enable_tuning": None,
    "initial_antenna": None,
    "antenna_switch_url": None,
    "antenna_request_timeout_seconds": None,
    "antenna_selection_rules": [],
    "rigctld_startup_delay_seconds": 2.0,
    "rigctld_startup_output_timeout_seconds": 2.0,
    "process_stop_timeout_seconds": 5.0,
}

def _settings_section(program_settings, name):
    """Return one named section from general_settings.yaml."""
    if isinstance(program_settings, dict):
        value = program_settings.get(name)
        return value if isinstance(value, dict) else {}

    if isinstance(program_settings, list):
        for item in program_settings:
            if not isinstance(item, dict) or name not in item:
                continue
            value = item.get(name)
            if isinstance(value, dict):
                return value
            section = dict(item)
            section.pop(name, None)
            return section

    return {}


def _setting_value(settings, key, default):
    value = settings.get(key, default)
    return default if value is None else value


def _first_config_value(*candidates):
    """Return the first non-None value from (mapping, key) candidates."""
    for mapping, key in candidates:
        if isinstance(mapping, dict) and key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _parse_float(value, default, setting_name):
    value = _setting_value({"value": value}, "value", default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"general_settings.yaml setting {setting_name!r} must be numeric, not {value!r}")


def _parse_int(value, default, setting_name):
    value = _setting_value({"value": value}, "value", default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"general_settings.yaml setting {setting_name!r} must be an integer, not {value!r}")


def _parse_bool(value):
    """Accept bools or common string forms from YAML/CLI."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _parse_seconds(value, default, setting_name):
    value = _setting_value({"value": value}, "value", default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        match = re.fullmatch(
            r"([0-9]+(?:\.[0-9]+)?)\s*"
            r"(ms|millisecond|milliseconds|s|sec|secs|second|seconds)?",
            str(value).strip().lower(),
        )
        if not match:
            raise ValueError(f"general_settings.yaml setting {setting_name!r} is not a valid duration: {value!r}")
        seconds = float(match.group(1))
        unit = match.group(2) or "seconds"
        if unit in {"ms", "millisecond", "milliseconds"}:
            seconds /= 1000.0
    if seconds <= 0:
        raise ValueError(f"general_settings.yaml setting {setting_name!r} must be positive")
    return seconds


def _resolve_config_path(value):
    """Resolve relative config/file paths from the directory containing this script."""
    if value is None or str(value).strip() == "":
        return None
    path = os.path.expanduser(str(value).strip())
    if os.path.isabs(path):
        return path
    return os.path.join(SCRIPT_DIR, path)


def _normalize_url(value):
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "http://" + value
    return value.rstrip("/")


#def _load_antenna_rules(track_settings, defaults):
#   rules = track_settings.get("antenna_selection_rules", defaults["antenna_selection_rules"])
#    if not isinstance(rules, list):
#        raise ValueError("general_settings.yaml setting 'antenna_selection_rules' must be a list")

#    normalized_rules = []
#    for index, rule in enumerate(rules, start=1):
#        if not isinstance(rule, dict):
#            raise ValueError(f"antenna_selection_rules item {index} must be a mapping")
#        normalized_rules.append({
#            "min_azimuth": _parse_float(rule.get("min_azimuth"), 0, f"antenna_selection_rules[{index}].min_azimuth"),
#            "max_azimuth": _parse_float(rule.get("max_azimuth"), 360, f"antenna_selection_rules[{index}].max_azimuth"),
#            "antenna": _parse_int(rule.get("antenna"), 1, f"antenna_selection_rules[{index}].antenna"),
#            "description": str(rule.get("description") or f"Antenna {rule.get('antenna')}").strip(),
#        })
#    return normalized_rules


def load_general_settings(filename=GENERAL_SETTINGS_FILE):
    """Load practical runtime settings for this tracker from general_settings.yaml."""
    settings = dict(DEFAULT_GENERAL_SETTINGS)

    with open(filename, "r", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    if not isinstance(root, dict):
        raise ValueError(f"{filename} must contain a YAML mapping")

    program_settings = root.get("program_settings") or {}
    track_settings = _settings_section(program_settings, "bqe_track_continuously")
    third_party_settings = _settings_section(program_settings, "third_party")


    path_key_aliases = {
        "tle_file": ("tle_file", "default_tle_file"),
        "satellite_config": ("satellite_config", "default_satellite_config_file"),
        "qth_config": ("qth_config", "default_qth_config_file"),
        "radio_config": ("radio_config", "default_radio_config_file"),
        "status_file": ("status_file", "default_status_file"),
        "rigctld_pid_file": ("rigctld_pid_file", "default_rigctld_pid_file"),
        "pass_program_pid_file": ("pass_program_pid_file", "default_pass_program_pid_file"),
    }

    # This syntax handles duplicate keys in the yaml file.  (There should never be any)
    for key, aliases in path_key_aliases.items():
        value = _first_config_value(*((track_settings, alias) for alias in aliases))
        if value is not None:
            settings[key] = value

    # Common/third-party Hamlib settings shared by multiple BQE programs.
    settings["rigctld_port"] = _parse_int(
        third_party_settings.get("rigctld_port"),
        settings["rigctld_port"],
        "program_settings.third_party.rigctld_port",
    )
    if third_party_settings.get("rigctld_path"):
        settings["rigctld_path"] = str(third_party_settings["rigctld_path"]).strip()

    # Tracker-specific runtime behavior.
    settings["sleep_interval_seconds"] = _parse_seconds(
        track_settings.get("sleep_interval"),
        settings["sleep_interval_seconds"],
        "program_settings.bqe_track_continuously.sleep_interval",
    )
    settings["sleep_interval_high_elevation_seconds"] = _parse_seconds(
        track_settings.get("sleep_interval_high_elevation"),
        settings["sleep_interval_high_elevation_seconds"],
        "program_settings.bqe_track_continuously.sleep_interval_high_elevation",
    )
    settings["high_pass_elevation"] = _parse_float(
        track_settings.get("high_pass_elevation"),
        settings["high_pass_elevation"],
        "program_settings.bqe_track_continuously.high_pass_elevation",
    )
    settings["horizon_threshold_elevation"] = _parse_float(
        track_settings.get("horizon_threshold_elevation"),
        settings["horizon_threshold_elevation"],
        "program_settings.bqe_track_continuously.horizon_threshold_elevation",
    )
    settings["initial_antenna"] = _parse_int(
        track_settings.get("initial_antenna"),
        settings["initial_antenna"],
        "program_settings.bqe_track_continuously.initial_antenna",
    )
    settings["antenna_switch_url"] = _normalize_url(
        track_settings.get("antenna_switch_url", settings["antenna_switch_url"])
    )

    settings["antenna_request_timeout_seconds"] = _parse_seconds(
        track_settings.get("antenna_request_timeout"),
        settings["antenna_request_timeout_seconds"],
        "program_settings.bqe_track_continuously.antenna_request_timeout",
    )
    
    # Data Validations
    for key in ("default_downlink_mode", "default_uplink_mode", "default_ctcss_tone"):
        if key in track_settings:
            settings[key] = str(track_settings[key])
    for key in ("default_satellite_mode_required", "default_enable_antenna_tracking", "default_enable_tuning"):
        if key in track_settings:
            settings[key] = _parse_bool(track_settings[key])

    settings["rigctld_startup_delay_seconds"] = _parse_seconds(
        third_party_settings.get("rigctld_startup_delay_seconds"),
        settings["rigctld_startup_delay_seconds"],
        "program_settings.third_party.rigctld_startup_delay_seconds",
    )

    settings["rigctld_startup_output_timeout_seconds"] = _parse_seconds(
        third_party_settings.get("rigctl-startup_output_timeout_seconds"),
        settings["rigctld_startup_output_timeout_seconds"],
        "program_settings.third_party.rigctld_startup_output_timeout_seconds",
    )

    settings["process_stop_timeout_seconds"] = _parse_seconds(
        track_settings.get("process_stop_timeout_seconds"),
        settings["process_stop_timeout_seconds"],
        "program_settings.bqe_track_continuously.process_stop_timeout_seconds",
    )

    # Normalize file paths after loading values from YAML.
    for key in (
        "tle_file",
        "satellite_config",
        "qth_config",
        "radio_config",
        "status_file",
        "rigctld_pid_file",
        "pass_program_pid_file",
    ):
        settings[key] = _resolve_config_path(settings[key])

    return settings


GENERAL_SETTINGS = load_general_settings()
print(GENERAL_SETTINGS)
DEFAULT_TLE_FILE = GENERAL_SETTINGS["tle_file"]
DEFAULT_SATELLITE_CONFIG_FILE = GENERAL_SETTINGS["satellite_config"]
DEFAULT_QTH_CONFIG_FILE = GENERAL_SETTINGS["qth_config"]
DEFAULT_RADIO_CONFIG_FILE = GENERAL_SETTINGS["radio_config"]
DEFAULT_STATUS_FILE = GENERAL_SETTINGS["status_file"]
DEFAULT_RIGCTLD_PID_FILE = GENERAL_SETTINGS["rigctld_pid_file"]
DEFAULT_PASS_PROGRAM_PID_FILE = GENERAL_SETTINGS["pass_program_pid_file"]
RIGCTLD_PORT = GENERAL_SETTINGS["rigctld_port"]
RIGCTLD_PATH = GENERAL_SETTINGS["rigctld_path"]
SLEEP_INTERVAL_SECONDS = GENERAL_SETTINGS["sleep_interval_seconds"]
SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS = GENERAL_SETTINGS["sleep_interval_high_elevation_seconds"]
HIGH_PASS_ELEVATION = GENERAL_SETTINGS["high_pass_elevation"]
HORIZON_THRESHOLD_ELEVATION = GENERAL_SETTINGS["horizon_threshold_elevation"]
INITIAL_ANTENNA = GENERAL_SETTINGS["initial_antenna"]
ANTENNA_SWITCH_URL = GENERAL_SETTINGS["antenna_switch_url"]
ANTENNA_REQUEST_TIMEOUT_SECONDS = GENERAL_SETTINGS["antenna_request_timeout_seconds"]
ANTENNA_SELECTION_RULES = GENERAL_SETTINGS["antenna_selection_rules"]
RIGCTLD_STARTUP_DELAY_SECONDS = GENERAL_SETTINGS["rigctld_startup_delay_seconds"]
RIGCTLD_STARTUP_OUTPUT_TIMEOUT_SECONDS = GENERAL_SETTINGS["rigctld_startup_output_timeout_seconds"]
PROCESS_STOP_TIMEOUT_SECONDS = GENERAL_SETTINGS["process_stop_timeout_seconds"]


def write_status_file(status_file, **values):
    """Write one atomic JSON status update for bqe_wisp.py to read."""
    if not status_file:
        return
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **values,
    }
    try:
        directory = os.path.dirname(os.path.abspath(status_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_name = status_file + ".tmp"
        with open(tmp_name, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_name, status_file)
    except Exception as e:
        print(f"Warning: could not write WISP status file {status_file}: {e}")


def select_antenna(desired_az, desired_el):
    """Choose the antenna number for the requested azimuth/elevation."""

    # Determine what antenna we want to select based on az & el
    # (duplicate of RCS-10 rules).
    #
    # Current Antenna Assignments:
    # 1 SE Yagi
    # 2 UHF on pole
    # 3 Southwest Yagi
    # 4 Vertical in tree
    # 7 NE Yagi
    # 8 NW Yagi

    # Modified for UHF antennas.  2m is NOT on switch.
    if 0 <= float(desired_az) < 90:
        print('Selecting Antenna 7 (NE Yagi)\n')
        return 7
    elif 90 <= float(desired_az) < 220:
        print('Selecting Antenna 1 (SE UHF yagi)\n')
        return 1
    elif 220 <= float(desired_az) < 260:
        print('Selecting antenna 2 (SW UHF Yagi\n')
        return 2
    elif 260 <= float(desired_az) < 360:
        print('Selecting Antenna 8 NW UHF Yagi\n')
        return 8
    else:
        print('Default choice: Selecting Antenna 1 (UHF on pole)\n')
        return 1


def do_tracking(desired_antenna, my_callsign):
    """Send an antenna selection unless tracking is disabled for this callsign."""
    
    print("Selecting antenna", desired_antenna)
    curl_payload = "Antenna+Choice="+ str(desired_antenna)

    # Define the antenna switch URL and message to post
    url = "http://192.168.1.110:8000"
    data = {"message": curl_payload}

    # Send the POST request
    response = requests.post(url, data=data) 

    # Check the response status
    if response.status_code == 200:
        print("Message posted successfully!")
    else:
        print(f"Failed to post message. Status code: {response.status_code}")


# This is required to get around an FT-763r CAT limitation, and implements the "Rev" button.
# It's essentially a "shell game" to get around the fact that the TX & RX VFO's can't be on the
# same band..
def do_ft736r_band_swap(radio_ft736r_intermediate_frequency_mhz, rigctld_port):
    radio_ft736r_intermediate_frequency_hz = float(radio_ft736r_intermediate_frequency_mhz) * 1e06 #Convert to hz
    rig.rigctld_set_uplink_frequency(radio_ft736r_intermediate_frequency_hz, rigctld_port)
    return()

def do_satellite_setup(uplink_frequency_mhz, uplink_mode, downlink_frequency_mhz, downlink_mode, ctcss_tone,  radio_type, rigctld_port, radio_ft736r_intermediate_frequency_mhz):

    rig.rigctld_enable_satellite_mode(rigctld_port)

    # Marker -review this and see if we should convert to hz earlier for nicer looking code
    uplink_frequency_hz = float(uplink_frequency_mhz) * 1e06
    downlink_frequency_hz = float(downlink_frequency_mhz) * 1e06

    # Workaround for ft-736r mode B / Mode J swap issue.
    if radio_type == 1010 or radio_type == 1:
        print("Doing FT-736r specific band swap procedure using intermediate freq", radio_ft736r_intermediate_frequency_mhz)
        do_ft736r_band_swap(radio_ft736r_intermediate_frequency_mhz, rigctld_port)
        
    rig.rigctld_set_downlink_frequency(downlink_frequency_hz, rigctld_port)
    rig.rigctld_set_downlink_mode(downlink_mode, rigctld_port)

    rig.rigctld_set_uplink_frequency(uplink_frequency_hz, rigctld_port)
    rig.rigctld_set_uplink_mode(uplink_mode, rigctld_port)


    if ctcss_tone != '0': # TODO  make sure datatypes match between text and number here.
        print("Setting ctcss_tone to be ", ctcss_tone)
        rig.rigctld_set_ctcss_tone(ctcss_tone, rigctld_port )
        rig.rigctld_set_ctcss_mode("ENC", rigctld_port)
    else:
        print("CTCSS tone will not be enabled.")
        rig.rigctld_set_ctcss_mode("OFF", rigctld_port)
    return()


def normalize_catalog_number(catalog_number):
    """Return a comparable NORAD catalog number string, ignoring leading zeroes."""
    if catalog_number is None:
        return None

    catalog_number = str(catalog_number).strip()
    if not catalog_number:
        return None

    # TLE line 1 stores the catalog number in columns 3-7.  Some YAML files
    # may store AO-07 as 7530 while the TLE stores it as 07530, so compare
    # without leading zeroes.
    return catalog_number.lstrip("0") or "0"


def load_tle_by_catalog_number(tle_path, catalog_number):
    """
    Returns (tle_satellite_name, line1, line2) for the satellite whose
    NORAD catalog number matches catalog_number.

    The TLE file is expected as repeating triplets: name, line1, line2.
    Satellite names are intentionally not used for matching.
    """
    target_catalog_number = normalize_catalog_number(catalog_number)
    if target_catalog_number is None:
        raise ValueError("A satellite catalog_number is required to look up TLE data.")

    with open(tle_path, 'r', encoding='utf-8') as f:
        lines = [ln.rstrip('\n') for ln in f if ln.strip()]

    for i in range(len(lines) - 2):
        name, l1, l2 = lines[i], lines[i+1], lines[i+2]
        if not (l1.startswith('1 ') and l2.startswith('2 ')):
            continue

        tle_catalog_number = normalize_catalog_number(l1[2:7])
        if tle_catalog_number == target_catalog_number:
            return name, l1.strip(), l2.strip()

    raise ValueError(f"Satellite catalog number '{catalog_number}' not found in {tle_path}")


def track(t, sat, observer, downlink_frequency_mhz):

    ts = load.timescale()

    # Topocentric (observer->sat) object using Skyfield convenience: sat - observer
    topocentric = (sat - observer).at(t)

    # alt (Angle), az (Angle), distance (Distance)
    altitude, azimuth, distance = topocentric.altaz()
    alt_deg = altitude.degrees
    az_deg = azimuth.degrees
    range_km = distance.km

    # Geographic subpoint and height are used by the web map to place the
    # satellite and calculate its horizon-to-horizon coverage footprint.
    subpoint = wgs84.subpoint(sat.at(t))
    satellite_latitude = subpoint.latitude.degrees
    satellite_longitude = subpoint.longitude.degrees
    satellite_altitude_km = subpoint.elevation.km

    # --- Compute radial range-rate manually (kg/s units are km/s) ---
    # Satellite geocentric position & velocity
    geocentric = sat.at(t)
    sat_pos_km = np.asarray(geocentric.position.km)          # shape (3,)
    sat_vel_km_s = np.asarray(geocentric.velocity.km_per_s)  # shape (3,)

    # Observer geocentric position & velocity (includes Earth rotation)
    obs_geoc = observer.at(t)
    obs_pos_km = np.asarray(obs_geoc.position.km)
    obs_vel_km_s = np.asarray(obs_geoc.velocity.km_per_s)

    # Line-of-sight vector and unit vector (from observer to satellite)
    los_vec = sat_pos_km - obs_pos_km
    los_dist = np.linalg.norm(los_vec)
    if los_dist == 0:
        print("Observer and satellite positions coincide (!) — cannot compute LOS.")
        return
    los_unit = los_vec / los_dist

    # Relative velocity (satellite minus observer)
    rel_vel = sat_vel_km_s - obs_vel_km_s

    # Range rate (km/s) — positive means distance increasing (receding)
    range_rate_km_s = float(np.dot(rel_vel, los_unit))

    # Doppler shift at f = 150 MHz; sign chosen so approaching (range_rate < 0) -> +Δf
    
    c_km_s = 299792.458
    downlink_doppler_hz = downlink_frequency_mhz * 1e6 * (-range_rate_km_s / c_km_s)

    return (
        az_deg,
        alt_deg,
        downlink_doppler_hz,
        range_km,
        satellite_latitude,
        satellite_longitude,
        satellite_altitude_km,
    )


def predict_visible_ground_track(
        start_time, sat, observer, horizon_degrees=0.0,
        sample_interval_seconds=15, max_prediction_seconds=7200,
        before_pass_seconds=3600, after_pass_seconds=3600):
    """Return subpoints from one hour before AOS until one hour after LOS.

    If tracking starts during a pass, scan backward to AOS as well as forward
    to LOS.  If tracking starts before AOS, find the next complete pass.  The
    returned ground track is then extended on both sides of that visible pass.
    """
    sample_interval_seconds = int(sample_interval_seconds)
    max_prediction_seconds = int(max_prediction_seconds)
    before_pass_seconds = max(0, int(before_pass_seconds))
    after_pass_seconds = max(0, int(after_pass_seconds))
    if sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")
    if max_prediction_seconds <= 0:
        raise ValueError("max_prediction_seconds must be positive")

    def elevation_at(offset_seconds):
        sample_time = start_time + (offset_seconds / 86400.0)
        return sample_time, (sat - observer).at(sample_time).altaz()[0].degrees

    def subpoint_at(sample_time):
        subpoint = wgs84.subpoint(sat.at(sample_time))
        return [subpoint.latitude.degrees, subpoint.longitude.degrees]

    _, current_elevation = elevation_at(0)
    aos_offset_seconds = None
    los_offset_seconds = None
    aos_boundary_found = False
    los_boundary_found = False

    if current_elevation >= horizon_degrees:
        # Walk backward to the first above-horizon sample after AOS.
        for offset_seconds in range(
                0, -max_prediction_seconds - 1, -sample_interval_seconds):
            _, elevation = elevation_at(offset_seconds)
            if elevation < horizon_degrees:
                aos_offset_seconds = offset_seconds + sample_interval_seconds
                aos_boundary_found = True
                break

        # Walk forward to the last above-horizon sample before LOS.
        for offset_seconds in range(
                sample_interval_seconds,
                max_prediction_seconds + 1,
                sample_interval_seconds):
            _, elevation = elevation_at(offset_seconds)
            if elevation < horizon_degrees:
                los_offset_seconds = offset_seconds - sample_interval_seconds
                los_boundary_found = True
                break
    else:
        # The satellite is not up yet. Find the next AOS and LOS.
        satellite_has_risen = False
        for offset_seconds in range(
                0, max_prediction_seconds + 1, sample_interval_seconds):
            _, elevation = elevation_at(offset_seconds)
            if not satellite_has_risen and elevation >= horizon_degrees:
                satellite_has_risen = True
                aos_offset_seconds = offset_seconds
                aos_boundary_found = True
            elif satellite_has_risen and elevation < horizon_degrees:
                los_offset_seconds = offset_seconds - sample_interval_seconds
                los_boundary_found = True
                break

    if not aos_boundary_found or not los_boundary_found:
        return []

    ground_track_start = aos_offset_seconds - before_pass_seconds
    ground_track_end = los_offset_seconds + after_pass_seconds
    return [
        subpoint_at(start_time + (offset_seconds / 86400.0))
        for offset_seconds in range(
            ground_track_start,
            ground_track_end + 1,
            sample_interval_seconds,
        )
    ]

def find_satellite_by_nickname(config, nickname):
    """Search all top-level lists for an entry with the given nickname."""
    for _, value in config.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("nickname") == nickname:
                    return item
    return None

def load_satellite_config(path=DEFAULT_SATELLITE_CONFIG_FILE):
    """Load the satellites YAML file and return its contents."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Warning: satellite config file {path} not found.")
    except Exception as e:
        print(f"Warning: could not read satellite config {path}: {e}")
    return {}


def write_rigctld_pid_file(pid_file, pid):
    """Write the rigctld PID so a parent scheduler can clean it up if needed."""
    if not pid_file:
        return
    try:
        directory = os.path.dirname(os.path.abspath(pid_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_name = pid_file + ".tmp"
        with open(tmp_name, "w", encoding="utf-8") as f:
            f.write(str(pid))
        os.replace(tmp_name, pid_file)
        print(f"Wrote rigctld PID {pid} to {pid_file}")
    except Exception as e:
        print(f"Warning: could not write rigctld PID file {pid_file}: {e}")


def remove_rigctld_pid_file(pid_file):
    """Remove the rigctld PID file during normal cleanup."""
    if not pid_file:
        return
    try:
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except Exception as e:
        print(f"Warning: could not remove rigctld PID file {pid_file}: {e}")


def start_rigctld_process(
    radio_type,
    radio_port,
    radio_baud,
    rigctld_port,
    rigctld_path=RIGCTLD_PATH,
    startup_delay_seconds=RIGCTLD_STARTUP_DELAY_SECONDS,
    startup_output_timeout_seconds=RIGCTLD_STARTUP_OUTPUT_TIMEOUT_SECONDS,
):
    """Start rigctld and verify that it remains running.

    This avoids a common argument-order problem in wrapper functions.  The
    Hamlib command line is:
        rigctld -m <radio_type> -r <radio_port> -s <radio_baud> -t <tcp_port>
    """
    if radio_type is None or str(radio_type).strip() == "":
        raise ValueError("radio_type is required to start rigctld")
    if radio_port is None or str(radio_port).strip() == "":
        raise ValueError("radio_port is required to start rigctld")
    if radio_baud is None or str(radio_baud).strip() == "":
        raise ValueError("radio_baud is required to start rigctld")

    rigctld_exe = shutil.which(str(rigctld_path)) or str(rigctld_path)
    cmd = [
        rigctld_exe,
        "-m", str(radio_type),
        "-r", str(radio_port).upper() if os.name == "nt" else str(radio_port),
        "-s", str(radio_baud),
        "-t", str(rigctld_port),
    ]

    print("Starting rigctld with command:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Give rigctld a moment to fail fast if the command line, COM port, or radio
    # model is wrong.  If it exits immediately, show stderr/stdout to make the
    # failure visible instead of continuing with a dead process.
    time.sleep(startup_delay_seconds)
    if proc.poll() is not None:
        stdout, stderr = proc.communicate(timeout=startup_output_timeout_seconds)
        raise RuntimeError(
            "rigctld failed to start or exited immediately.\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit code: {proc.returncode}\n"
            f"stdout: {stdout.strip()}\n"
            f"stderr: {stderr.strip()}"
        )

    print(f"rigctld started successfully; PID={proc.pid}, TCP port={rigctld_port}")
    return proc

def stop_rigctld_process(proc, timeout_seconds=PROCESS_STOP_TIMEOUT_SECONDS):
    """Stop the rigctld process started by this script."""
    if proc is None:
        return

    try:
        if proc.poll() is None:
            print("Stopping rigctld process...")
            proc.terminate()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                print("rigctld did not stop after terminate(); killing it...")
                proc.kill()
                proc.wait(timeout=timeout_seconds)
    except Exception as e:
        print(f"Warning: could not stop rigctld process cleanly: {e}")



def write_pid_file(pid_file, pid, process_name="process"):
    """Write a child process PID using the same atomic file pattern as rigctld."""
    if not pid_file:
        return
    try:
        directory = os.path.dirname(os.path.abspath(pid_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_name = pid_file + ".tmp"
        with open(tmp_name, "w", encoding="utf-8") as f:
            f.write(str(pid))
        os.replace(tmp_name, pid_file)
        print(f"Wrote {process_name} PID {pid} to {pid_file}")
    except Exception as e:
        print(f"Warning: could not write {process_name} PID file {pid_file}: {e}")


def remove_pid_file(pid_file, process_name="process"):
    """Remove a PID file during normal cleanup."""
    if not pid_file:
        return
    try:
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except Exception as e:
        print(f"Warning: could not remove {process_name} PID file {pid_file}: {e}")


def normalize_program_command(program_to_run):
    """Return a subprocess command list for a YAML string or list."""
    if program_to_run is None:
        return None
    if isinstance(program_to_run, (list, tuple)):
        command = [str(part) for part in program_to_run if str(part).strip()]
        return command or None
    program_to_run = str(program_to_run).strip()
    if not program_to_run:
        return None
    return [program_to_run]


def start_pass_program(program_to_run, pid_file):
    """Start the optional per-pass program from satellites.yaml, if configured."""
    cmd = normalize_program_command(program_to_run)
    if not cmd:
        print("No program_to_run_during_pass configured; skipping optional pass program.")
        return None

    exe = shutil.which(cmd[0]) or cmd[0]
    cmd[0] = exe
    print("Starting pass program with command:", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    write_pid_file(pid_file, proc.pid, "pass program")
    return proc


def stop_pass_program(proc, timeout_seconds=PROCESS_STOP_TIMEOUT_SECONDS):
    """Stop the optional per-pass program started by this script."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            print("Stopping pass program...")
            proc.terminate()
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                print("Pass program did not stop after terminate(); killing it...")
                proc.kill()
                proc.wait(timeout=timeout_seconds)
    except Exception as e:
        print(f"Warning: could not stop pass program cleanly: {e}")

def mhz_to_hz(mhz):
    hz = mhz * 1e06
    return hz

def cli_override(cli_value, config_value, fallback=None):
    """Return CLI value when supplied; otherwise YAML/dict value; otherwise fallback."""
    if cli_value is not None:
        return cli_value
    if config_value is not None:
        return config_value
    return fallback


def first_mapping_value(mapping, *keys):
    """Return the first non-None value from mapping for any of the supplied keys."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def required_float_setting(value, setting_name):
    """Convert a required runtime value to float and report the missing key clearly."""
    if value is None or str(value).strip() == "":
        raise ValueError(
            f"Required setting {setting_name} was not found. "
            "Set it on the command line, in the QTH YAML file, in the satellite YAML entry, "
            "or as a default_observer_* value in general_settings.yaml."
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Required setting {setting_name} must be numeric, not {value!r}")

def parse_bool(value):
    """Accept bools or common string forms from YAML/CLI."""
    return _parse_bool(value)

def main():
    #Note: Keps can be downloaded from:  https://www.amsat.org/tle/dailytle.txt
    rigctld_port = RIGCTLD_PORT
    ap = argparse.ArgumentParser(description="Compute Az/El and Doppler for the specified satellite ")
    current_antenna = INITIAL_ANTENNA

    ########################### Satellite Tracking Args ###########################
   
    ap.add_argument("--tle_file", default=DEFAULT_TLE_FILE, help="Path to TLE file (name + 2 lines format).")
    ap.add_argument("--satellite_config", default=DEFAULT_SATELLITE_CONFIG_FILE, help="Path to satellite YAML file.")
    ap.add_argument("satellite", help="Satellite nickname from the configured satellite YAML. TLE data is matched by that entry's catalog_number.")
    ap.add_argument("--lat", type=float, default=None, help="Observer latitude in degrees. Overrides YAML/default if supplied.")
    ap.add_argument("--lon", type=float, default=None, help="Observer longitude in degrees. Overrides YAML/default if supplied.")
    ap.add_argument("--alt", type=float, default=None, help="Observer altitude (AGL) in meters. Overrides YAML/default if supplied.")
    ap.add_argument("--downlink_frequency_mhz", type=float, default=None, help="Downlink frequency in MHz. Overrides YAML/default if supplied.")
    ap.add_argument("--downlink_mode", type=str, default=None, help="Downlink mode. Overrides YAML/default if supplied.")
    ap.add_argument("--uplink_frequency_mhz", type=float, default=None, help="Uplink frequency in MHz. Overrides YAML/default if supplied.")
    ap.add_argument("--uplink_mode", type=str, default=None, help="Uplink mode. Overrides YAML/default if supplied.")
    ap.add_argument("--ctcss_tone", type=str, default=None, help='CTCSS tone for FM sat, or 0 if not required.')
    ap.add_argument("--satellite_mode_required", type=str, default=None, help="Enable rig satellite_mode. Overrides YAML/default if supplied.")
    ap.add_argument("--enable_antenna_tracking", type=str, default=None, help="Enable antenna tracking. Overrides YAML/default if supplied.")
    ap.add_argument("--enable_tuning", type=str, default=None, help="Enable radio tuning. Overrides YAML/default if supplied.")
    ap.add_argument("--status_file", type=str, default=DEFAULT_STATUS_FILE, help="Optional JSON status file for bqe_wisp.py web console Az/El updates.")
    ap.add_argument("--rigctld_pid_file", type=str, default=DEFAULT_RIGCTLD_PID_FILE, help="Path where the started rigctld PID is written for parent-process cleanup.")
    ap.add_argument("--pass_program_pid_file", type=str, default=DEFAULT_PASS_PROGRAM_PID_FILE, help="Path where the optional program_to_run_during_pass PID is written for cleanup.")
    
    ###########################       Hamlib Args       ###########################
    # Default set to None so we use Yaml definitions unless overridden here.
    # NEW: QTH config file

    ap.add_argument("--qth_config", type=str, default=DEFAULT_QTH_CONFIG_FILE,
        help="Path to QTH YAML file.")
    
    ap.add_argument("--my_latitude", type=str, default=None, help="Latitude")
    ap.add_argument("--my_longitude", type=str, default=None, help="Longitude (west is negative)")
    ap.add_argument("--my_elevation", type=str, default=None, help="Elevation in Meters")


    ap.add_argument("--radio_config", type=str, default=DEFAULT_RADIO_CONFIG_FILE,
        help="Path to radio YAML file.")
 
    ap.add_argument("--radio_type", type=str, default=None, help="Type of radio being used. (1035 for FT-991a)")
    ap.add_argument("--radio_port", type=str, default=None, help="Serial Port connected to radio (com4, /dev/ttyusb0 etc.)")
    ap.add_argument("--radio_baud", type=str, default=None, help="Serial port baud rate for radio)")
    ap.add_argument("--radio_ft736r_intermediate_frequency_mhz", type=float, default=None, help="used for FT-736r satellite band switches")

    args = ap.parse_args()

    satellite_setup_required = True  # Always assume satellite/split mode for now.

    status_file = args.status_file
    rigctld_pid_file = args.rigctld_pid_file
    pass_program_pid_file = args.pass_program_pid_file
    if status_file:
        write_status_file(
            status_file,
            satellite=args.satellite,
            satellite_type=None,
            azimuth=None,
            elevation=None,
            uplink_freq_hz=None,
            downlink_freq_hz=None,
            uplink_mode=None,
            downlink_mode=None,
            message="tracking starting",
        )

    tle_file = args.tle_file
    satellite = args.satellite

    timestamp_utc = datetime.now(timezone.utc).isoformat()

    # ------------------- Satellite config: sat_config_all + CLI override -------------------
    # Load the satellite dictionary first, then override individual values from
    # the CLI only when the CLI value is not None.
    sat_config_all = load_satellite_config(args.satellite_config)
    satellite_config = find_satellite_by_nickname(sat_config_all, satellite)

    if satellite_config is None:
        raise ValueError(f"Satellite '{satellite}' was not found in {args.satellite_config}")

    satellite_nickname        = satellite_config.get("nickname", satellite)
    satellite_name            = satellite_config.get("satellite_name", satellite)
    satellite_type            = satellite_config.get("satellite_type") or ""
    satellite_catalog_number  = satellite_config.get("catalog_number")
    if normalize_catalog_number(satellite_catalog_number) is None:
        raise ValueError(
            f"Satellite '{satellite_nickname}' in {args.satellite_config} is missing catalog_number; "
            "catalog_number is required for TLE lookup."
        )
    satellite_bandwidth       = satellite_config.get("bandwidth")
    program_to_run_during_pass = satellite_config.get("program_to_run_during_pass")

    qth_config = {}
    if args.qth_config:
        try:
            with open(args.qth_config, "r", encoding="utf-8") as f:
                qth_config = yaml.safe_load(f) or {}
            if not isinstance(qth_config, dict):
                print(f"{timestamp_utc} Warning: QTH config file {args.qth_config} did not contain a YAML mapping. Using satellite/general defaults.")
                qth_config = {}
        except FileNotFoundError:
            print(f"{timestamp_utc} Warning: QTH config file {args.qth_config} not found. Using satellite/general defaults.")
        except Exception as e:
            print(f"{timestamp_utc} Warning: Could not parse QTH config file {args.qth_config}: {e}. Using satellite/general defaults.")
    else:
        print(f"{timestamp_utc} No QTH config file configured. Using CLI, satellite YAML, or general defaults for observer location.")

    # TODO - remove these.
    cli_latitude = args.lat if args.lat is not None else args.my_latitude
    cli_longitude = args.lon if args.lon is not None else args.my_longitude
    cli_elevation = args.alt if args.alt is not None else args.my_elevation

    my_callsign = str(qth_config.get("my_callsign", "")).strip()
    print("[INFO] my_callsign is: ", my_callsign)
    observer_lat_deg = float(cli_override(args.lat, qth_config.get("my_latitude")))
    observer_lon_deg = float(cli_override(args.lon, qth_config.get("my_longitude")))
    observer_altitude_m = float(cli_override(args.alt, qth_config.get("my_altitude")))
    downlink_frequency_mhz = float(cli_override(args.downlink_frequency_mhz, satellite_config.get("downlink_frequency_mhz")))
    uplink_frequency_mhz = float(cli_override(args.uplink_frequency_mhz, satellite_config.get("uplink_frequency_mhz")))
    downlink_mode = cli_override(args.downlink_mode, satellite_config.get("downlink_mode"), GENERAL_SETTINGS["default_downlink_mode"])
    uplink_mode = cli_override(args.uplink_mode, satellite_config.get("uplink_mode"), GENERAL_SETTINGS["default_uplink_mode"])
    downlink_freq_hz = downlink_frequency_mhz * 1e6
    uplink_freq_hz = uplink_frequency_mhz * 1e6 if uplink_frequency_mhz > 0 else 0
    ctcss_tone = cli_override(args.ctcss_tone, satellite_config.get("ctcss_tone"), GENERAL_SETTINGS["default_ctcss_tone"])

    satellite_mode_required = parse_bool(cli_override(args.satellite_mode_required, satellite_config.get("satellite_mode_required"), GENERAL_SETTINGS["default_satellite_mode_required"]))
    enable_antenna_tracking = parse_bool(cli_override(args.enable_antenna_tracking, satellite_config.get("enable_antenna_tracking"), GENERAL_SETTINGS["default_enable_antenna_tracking"]))
    enable_tuning = parse_bool(cli_override(args.enable_tuning, satellite_config.get("enable_tuning"), GENERAL_SETTINGS["default_enable_tuning"]))

    if status_file:
        write_status_file(
            status_file,
            satellite=satellite_nickname,
            satellite_name=satellite_name,
            satellite_type=satellite_type,
            azimuth=None,
            elevation=None,
            uplink_frequency_mhz=uplink_frequency_mhz,
            downlink_frequency_mhz=downlink_frequency_mhz,
            uplink_freq_hz=uplink_freq_hz,
            downlink_freq_hz=downlink_freq_hz,
            uplink_mode=uplink_mode,
            downlink_mode=downlink_mode,
            mode=downlink_mode,
            message="tracking configured",
        )

    # ------------------- Radio config: YAML + CLI override -------------------
    rig_config_path = args.radio_config
    yaml_radio_type = None
    yaml_radio_port = None
    yaml_radio_baud = None
    yaml_radio_ft736r_intermediate_frequency_mhz = None

    if rig_config_path:
        try:
            with open(rig_config_path, "r", encoding="utf-8") as f:
                radio_config = yaml.safe_load(f) or {}
            if not isinstance(radio_config, dict):
                print(f"{timestamp_utc} Warning: radio config file {rig_config_path} did not contain a YAML mapping. Using CLI/defaults.")
                radio_config = {}
            
            yaml_radio_type = radio_config.get("radio_type")
            yaml_radio_port = radio_config.get("radio_port")
            yaml_radio_baud = radio_config.get("radio_baud")
            print("line 993 yaml_radio_type is ", yaml_radio_type)
            yaml_radio_ft736r_intermediate_frequency_mhz = radio_config.get("radio_ft736r_intermediate_frequency_mhz")
            print(f"{timestamp_utc} Loaded radio config from {rig_config_path}: "
                  f"radio_type={yaml_radio_type}, radio_port={yaml_radio_port}, radio_baud={yaml_radio_baud}")
        except FileNotFoundError:
            print(f"{timestamp_utc} Warning: radio config file {rig_config_path} not found. Using CLI/defaults.")
        except Exception as e:
            print(f"{timestamp_utc} Warning: Could not parse radio config file {rig_config_path}: {e}. Using CLI/defaults.")
    else:
        print(f"{timestamp_utc} No radio config file configured. Using CLI values for radio_type, radio_port, and radio_baud.")

    # CLI overrides YAML
    radio_type = args.radio_type if args.radio_type is not None else yaml_radio_type
    radio_port = args.radio_port if args.radio_port is not None else yaml_radio_port
    radio_baud = args.radio_baud if args.radio_baud is not None else yaml_radio_baud
    radio_ft736r_intermediate_frequency_mhz = (
        args.radio_ft736r_intermediate_frequency_mhz
        if args.radio_ft736r_intermediate_frequency_mhz is not None
        else float(yaml_radio_ft736r_intermediate_frequency_mhz or 0)
    )

    print(f"{timestamp_utc} Using radio config: radio_type={radio_type}, radio_port={radio_port}, radio_baud={radio_baud}")

    

    # Initialize communication with the radio.  Start rigctld directly here so
    # the argument order is explicit and we can verify that the process stays up.
    proc = None
    pass_program_proc = None
    proc = start_rigctld_process(radio_type, radio_port, radio_baud, rigctld_port)
    write_rigctld_pid_file(rigctld_pid_file, proc.pid)

    try:
        pass_program_proc = start_pass_program(program_to_run_during_pass, pass_program_pid_file)

        # Enable split/satellite mode after dictionary values and CLI overrides are resolved.

        if satellite_mode_required:
            print("{timestamp_utc} Calling do_satellite_setup with",uplink_frequency_mhz, uplink_mode, downlink_frequency_mhz, downlink_mode, ctcss_tone, radio_type, rigctld_port, radio_ft736r_intermediate_frequency_mhz )
            do_satellite_setup(uplink_frequency_mhz, uplink_mode, downlink_frequency_mhz, downlink_mode, ctcss_tone, radio_type, rigctld_port, radio_ft736r_intermediate_frequency_mhz)
        else:
            print("{timestamp_utc} Satellite mode setup disabled by satellite config or CLI.")
        
        sleep_interval = SLEEP_INTERVAL_SECONDS
        high_elev_sleep_interval = SLEEP_INTERVAL_HIGH_ELEVATION_SECONDS
        
        # Observer location (WGS84)
        observer = wgs84.latlon(observer_lat_deg, observer_lon_deg,
                                observer_altitude_m)

        print("satellite_name", satellite_name)
        print("catalog number", satellite_catalog_number)
        print("downlink_frequency_mhz", downlink_frequency_mhz)
        print("uplink_frequency_mhz", uplink_frequency_mhz)
        print("ctcss_tone", ctcss_tone)
        print("enable_antenna_tracking", enable_antenna_tracking)
        print("enable_tuning", enable_tuning)

        
        print(satellite_nickname, satellite_name, satellite_catalog_number, downlink_mode, satellite_bandwidth, downlink_frequency_mhz, enable_antenna_tracking, enable_tuning)
    
        tle_satellite_name, tle_line1, tle_line2 = load_tle_by_catalog_number(tle_file, satellite_catalog_number)
        print("TLE satellite name", tle_satellite_name)
    
        # Calculate at least once to find the satellite position, and continue while above horizon.
        # We need to do this to prevent premature exit if the satellite is still below horizon at pass time. 
        satellite_has_risen=False # Initialization
        satellite_has_set=False # Cleanup and exit.
        predicted_ground_track = None

        el_deg=-1 # Initialization - assume below horizon until we calculate that it is visible.
    
        while satellite_has_set == False: # (I.e. Initially below horizon, and then when above horizon. )

            #Use timezone-aware UTC datetime
            ts = load.timescale()
            now_utc = datetime.now(timezone.utc)
            t = ts.from_datetime(now_utc)

            # Satellite object
            sat = EarthSatellite(tle_line1, tle_line2, tle_satellite_name, ts)
            if predicted_ground_track is None:
                predicted_ground_track = predict_visible_ground_track(
                    t,
                    sat,
                    observer,
                    horizon_degrees=HORIZON_THRESHOLD_ELEVATION,
                )
                print(
                    f"----------------> Extended predicted ground track contains "
                    f"{len(predicted_ground_track)} points "
                    f"(one hour before AOS through one hour after LOS)."
                )
       
            # Do the tracking calculation.
            downlink_frequency_mhz = float(downlink_frequency_mhz)
            (
                az_deg,
                el_deg,
                downlink_doppler_hz,
                range_km,
                satellite_latitude,
                satellite_longitude,
                satellite_altitude_km,
            ) = track(t, sat, observer, downlink_frequency_mhz)
            downlink_freq_with_doppler_hz  = (downlink_frequency_mhz * 1e6) + downlink_doppler_hz
        
            timestamp_utc = datetime.now(timezone.utc).isoformat()
            print(f" \n\n {timestamp_utc}")
            print(f"----------------> Azimuth: {az_deg:.6f} deg")
            print(f"----------------> Elevation: {el_deg:.6f} deg")
            print(f"----------------> Downlink Doppler @ {downlink_frequency_mhz} MHz: {downlink_doppler_hz:.2f} Hz. Downlink Frequency with doppler:  {downlink_freq_with_doppler_hz}")

            # Uplink information is calculated only if it is needed. (We don't bother for listen-only satellites.)
            uplink_frequency_mhz = float(uplink_frequency_mhz)
            uplink_doppler_hz = None
            uplink_freq_with_doppler_hz = None
            if uplink_frequency_mhz > 0: #I.e. If defined in the yaml file for a bi-directional (repeater) satellite

                # Scaling the ratio of downlink to uplink frequencies then inversely subtracting or adding it to the
                # calculated downlink doppler this way is less compute intensive than re-calculating again for uplink.
                uplink_doppler_hz = downlink_doppler_hz * (uplink_frequency_mhz / downlink_frequency_mhz)
                uplink_freq_with_doppler_hz = (uplink_frequency_mhz * 1e06) - (uplink_doppler_hz)

            # Makes current pointing data available to bqe_wisp.py's web console via shared text file.
            write_status_file(
                status_file,
                satellite=satellite_nickname,
                satellite_name=satellite_name,
                satellite_type=satellite_type,
                azimuth=az_deg,
                elevation=el_deg,
                downlink_doppler_hz=downlink_doppler_hz,
                uplink_doppler_hz=uplink_doppler_hz,
                uplink_frequency_mhz=uplink_frequency_mhz,
                downlink_frequency_mhz=downlink_frequency_mhz,
                uplink_freq_hz=uplink_freq_hz,
                downlink_freq_hz=downlink_freq_hz,
                uplink_frequency_hz=uplink_freq_with_doppler_hz,
                downlink_frequency_hz=downlink_freq_with_doppler_hz,
                uplink_mode=uplink_mode,
                downlink_mode=downlink_mode,
                ctcss_tone=ctcss_tone,
                range_km=range_km,
                satellite_latitude=satellite_latitude,
                satellite_longitude=satellite_longitude,
                satellite_altitude_km=satellite_altitude_km,
                predicted_ground_track=predicted_ground_track,
                predicted_ground_track_count=len(predicted_ground_track),
                message="tracking active",
            )

            if uplink_doppler_hz is not None:
                print(f"----------------> Uplink Doppler @ {uplink_frequency_mhz} MHz: {uplink_doppler_hz:.2f} Hz. Uplink Frequency with doppler:  {uplink_freq_with_doppler_hz}")
            else:
                print("----------------> Uplink frequency not configured for this satellite.")
     
            if el_deg > HORIZON_THRESHOLD_ELEVATION:
                print("Satellite is above the Horizon...  Performing antenna tracking and tuning if enabled in yaml for this satellite.")
                satellite_has_risen = True # Satellite has risen - begin tracking and tuning if enabled.

                if enable_tuning is True:
                    print("----------------> Setting downlink frequency", downlink_freq_with_doppler_hz)
                    rig.rigctld_set_downlink_frequency(downlink_freq_with_doppler_hz, rigctld_port)

                    # Set uplink only if it was specified in yaml. (SSTV sats do not require.)   
                    if uplink_frequency_mhz > 0:

                            print("----------------> Setting uplink frequency", uplink_freq_with_doppler_hz)
                            rig.rigctld_set_uplink_frequency(uplink_freq_with_doppler_hz, rigctld_port)

                else:
                    print("Frequency Tuning not enabled in yaml or via CLI.  Skipping...\n")
            
                print("DEBUG] My callsign is : ", my_callsign)
                if enable_antenna_tracking is True and my_callsign == "WB1BQE":
                    # Select proper antenna if not done already
                    desired_az = az_deg  # making these explicit since we may know about actual az & el at some point.
                    desired_el = el_deg
                    desired_antenna = select_antenna(desired_az, desired_el)

                    if desired_antenna != current_antenna:
                        do_tracking(desired_antenna, my_callsign)
                        current_antenna = desired_antenna #Assume successful for now.
                    else:
                        print("Antenna movement not required.  Skipping...\n")
                else:
                    print("Antenna Tracking is not enabled in the yaml or from the CLI for this satellite. Skipping...")
            if el_deg > HIGH_PASS_ELEVATION:
                    modified_sleep_interval = high_elev_sleep_interval  # At high altitudes track more frequently.
                    print("High Pass elevation detected.  Short sleep interval selected: ", modified_sleep_interval)
            else:
                    modified_sleep_interval = sleep_interval
                    print("Using normal sleep interval: ", modified_sleep_interval)

            time.sleep(modified_sleep_interval)


            # If the satellite has previously risen, but is now below the horizon, then cleanup and exit.
            if el_deg < HORIZON_THRESHOLD_ELEVATION and satellite_has_risen is True:
                satellite_has_set = True
                timestamp_utc = datetime.now(timezone.utc).isoformat()
                print(f"{timestamp_utc} Satellite is below horizon - skipping radio and antenna tuning")
        
            #Otherwise, sleep and repeat tracking tuning loop.
        
        print("Satellite has set below horizon ", el_deg)
        write_status_file(
            status_file,
            satellite=satellite_nickname,
            satellite_name=satellite_name,
            satellite_type=satellite_type,
            azimuth=None,
            elevation=None,
            uplink_frequency_mhz=uplink_frequency_mhz,
            downlink_frequency_mhz=downlink_frequency_mhz,
            uplink_freq_hz=uplink_freq_hz,
            downlink_freq_hz=downlink_freq_hz,
            uplink_mode=uplink_mode,
            downlink_mode=downlink_mode,
            mode=downlink_mode,
            message="tracking stopped",
        )

    finally:
        # Always try to leave the radio and rigctld in a sane state, even if
        # tracking, tuning, TLE parsing, or antenna switching raises an error.
        try:
            if satellite_mode_required:
                rig.rigctld_disable_satellite_mode(rigctld_port)
        except Exception as e:
            print(f"Warning: could not disable satellite mode cleanly: {e}")

        stop_pass_program(pass_program_proc)
        remove_pid_file(pass_program_pid_file, "pass program")

        stop_rigctld_process(proc)
        remove_rigctld_pid_file(rigctld_pid_file)


    exit(0)

if __name__ == "__main__":
    main()
