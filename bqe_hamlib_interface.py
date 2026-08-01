#!/bin/python
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

"""
Can either be run as a single script, or the various subroutines may be included elsewhere
and called independently.

If run as a single script, the various values are either specified in a yaml file supplied via
the CLI, or the entire parameter set can be specified on the CLI.

Example: python bqe_hamlib_interface.py  --remote_channel_config "bqe_config/6m_wachusett_repeater.yaml"

"""


# Takes arguments from an external caller, and passes them along to the radio via hamlib
import argparse
import os
import socket # Used by rigctld
import subprocess
import time
import yaml  # pip install PyYaml on windows 10. 

def report_status(result):
    print(result)
    if result.returncode == 0:
         print("[OK] Command succeeded...")
    else:
        print("[ERROR] Command failed...")
        print("Error output:", result.stderr.strip())

############# RIGCTL Routines ###############
def do_hamlib_cmd(cmd):
    try:
        print(f"Hamlib Communication...")
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("[ERROR] 'rigctl' not found. Please install Hamlib and ensure it's in your PATH.")
        result = None
    except Exception as e:
        print(f"Unexpected error: {e}")
        result = None
    return result


def hamlib_set_ctcss_tone(radio_type, radio_port, desired_ctcss_tone):
    ctcss_tone = desired_ctcss_tone.replace(".", "")  # Hamlib wants 71.9 as 719
    print(f"[INFO] Setting CTCSS Tone frequency: {desired_ctcss_tone} → {ctcss_tone}")
    try:
        platform, platform_rigctl = which_rigctl()
        cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "C", str(ctcss_tone)]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})")
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"Unexpected error: {e}")
    return result

# Turn split/satellite mode on
def hamlib_enable_satellite_mode(radio_type, radio_port):
    print("[INFO] Enabling Satellite/Split mode...")
    try:
        platform, platform_rigctl = which_rigctl()
        cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "-s", radio_baud, "S", "1", "VFOB"]
        # LINUX SYNTAX cmd = ["./rigctl", "-m", str(radio_type), "-r", str(radio_port), "F", str(desired_frequency_hz)]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})" )
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"Unexpected error: {e}")
    return result

# Turn split/satellite mode off
def hamlib_disable_satellite_mode(radio_type, radio_port):
    print("[INFO] Disbling Satellite/Split mode...")
    try:
        platform, platform_rigctl = which_rigctl()
        cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "-s", radio_baud, "S", "0", "VFOA"]
        # LINUX SYNTAX cmd = ["./rigctl", "-m", str(radio_type), "-r", str(radio_port), "F", str(desired_frequency_hz)]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})" )
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"Unexpected error: {e}")
    return result


def hamlib_set_frequency(radio_type, radio_port, desired_frequency_hz):
    print("[INFO] Communicating with radio...")
    try:
        platform, platform_rigctl = which_rigctl()
        cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "-s", radio_baud, "F", str(desired_frequency_hz)]
        # LINUX SYNTAX cmd = ["./rigctl", "-m", str(radio_type), "-r", str(radio_port), "F", str(desired_frequency_hz)]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})" )
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
    return result


def hamlib_set_mode(radio_type, radio_port, desired_mode, desired_bandwidth):
    try:
        platform, platform_rigctl = which_rigctl()
        if platform == "Windows":
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port),"-s", radio_baud, "M", desired_mode, desired_bandwidth]
        else:
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "M", desired_mode, desired_bandwidth]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})")
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")

def hamlib_set_repeater_offset(radio_type, radio_port, desired_repeater_offset_khz):
    desired_repeater_offset_hz = str(float(desired_repeater_offset_khz) * 1000)
    try:
        platform, platform_rigctl = which_rigctl()
        if platform == "Windows":
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port),"-s", radio_baud, "O", desired_repeater_offset_hz]
        else:
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "O", desired_repeater_offset_hz]
        
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})")
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")


def hamlib_set_repeater_shift(radio_type, radio_port, desired_repeater_shift):
    try:
        platform, platform_rigctl = which_rigctl()
        platform_rigctl = which_rigctl()
        if platform == "Windows":
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "-s", radio_baud, "R", desired_repeater_shift]
        else:
            cmd = [platform_rigctl, "-m", str(radio_type), "-r", str(radio_port), "R", desired_repeater_shift]
        print(f"[INFO] Initializing communications to radio (model {radio_type} port {radio_port})")
        result = do_hamlib_cmd(cmd)
        report_status(result)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")

def which_rigctl():
    # Rigctl location differs by platform (Linux or windows)
    if os.name == 'nt':
        platform = "Windows"
        platform_rigctl = "rigctl"  # If installed, it appears in windows default path.
    elif os.name == 'posix':
        platform = "Linux"
        platform_rigctl = "./rigctl" #Rigctl must be in path. (or modify this)
    else:
        print("Unknown operating system - cannot determine proper rigctl syntax.")
        exit(1)
    #print(f" Returning {platform} and {platform_rigctl}")
    return(platform, platform_rigctl)

############# RIGCTLD Routines ###############
def rigctld_enable_satellite_mode(rigctld_port):
    cmd = "S 1 Sub"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_disable_satellite_mode(rigctld_port):
    cmd = "S 0 Sub"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_set_downlink_frequency(downlink_frequency_hz, rigctld_port):
    cmd = f"F {downlink_frequency_hz}"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_set_uplink_frequency(uplink_frequency_hz, rigctld_port):
    cmd = f"I {uplink_frequency_hz}"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_set_downlink_mode(downlink_mode, rigctld_port):
    cmd = f"M {downlink_mode} 0"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_set_uplink_mode(uplink_mode, rigctld_port):  # Mode and bandwidth
    cmd = f"X {uplink_mode} 0"
    print(send_rig_command(cmd, rigctld_port))

def rigctld_set_ctcss_tone(ctcss_tone, rigctld_port):
    ctcss_tone = ctcss_tone.replace(".", "")  # Hamlib wants 1/10ths of hz - I.e.  71.9 as 719
    cmd = f"C {ctcss_tone} 0"
    print(send_rig_command (cmd, rigctld_port))

def rigctld_set_ctcss_mode(ctcss_mode, rigctld_port):
    print("DEBUG - setting ctcss tone - non-ft736r")
    match ctcss_mode:
        case "OFF":
            cmd = f"U TONE 0"
        case "ENC":
            cmd = f"U TONE 1"
        case "DEC":
            cmd = f"U TONE 2"
    print(send_rig_command (cmd, rigctld_port))


def rigctld_hex_escape(byte_values):
    return "".join(f"\\0x{value:02X}" for value in byte_values)

# Only turn ctcss on if tone has been set just prior for ft-736r, since it is otherwise unknown. 
def rigctld_set_ctcss_mode_ft736(ctcss_mode, rigctld_port): #ENC, DEC or OFF  

    print("[INFO] rigctld hamlib interface Setting ctcss_mode to be ", ctcss_mode)
   
    print("[INFO] Setting ctcss mode via FT736r syntax: ", ctcss_mode)
    match ctcss_mode:
        case "OFF":
            byte_cmd =  [0x8A, 0x8A, 0x8A, 0x8A, 0x8A]  # Only last byte matters - others are padding.
        case "ENC":
            byte_cmd =  [0x4A, 0x4A, 0x4A, 0x4A, 0x4A]
        case "DEC":
            byte_cmd =  [0x0A, 0x0A, 0x0A, 0x0A, 0x0A]  #  Enables both Encode and Decode
        
    cmd = rigctld_hex_escape(byte_cmd) # Properly format the native cat byte command for transmission through hamlib.
    cmd = f"w {cmd} 0"
    print(send_rig_command (cmd, rigctld_port))
    

def send_rig_command(cmd, rigctld_port):
    """
    Send a command to rigctld and return response.

    """

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", int(rigctld_port)))

    s.sendall(f"{cmd}\n".encode())

    response = b""

    # Read until socket quiets down
    s.settimeout(0.5)

    try:
        while True:
            data = s.recv(4096)

            if not data:
                break

            response += data

    except socket.timeout:
        pass

    s.close()

    return response.decode(errors="ignore")


def start_rigctld(radio_type, radio_port, radio_baud, rigctld_port):
    """
    Start rigctld as background process
    """
    cmd = [
        "rigctld",
        "-m", str(radio_type),
        "-r", str(radio_port),
        "-s", str(radio_baud),
        "-T", "127.0.0.1",
        "-t", str(rigctld_port),
        "-v"
    ]

    print("[INFO] Starting rigctld...")
    proc = subprocess.Popen(cmd)

    # Give daemon time to start
    time.sleep(2)

    return proc





def main():

    # Supported modes and corresponding bandwidths.
    supported_modes = {
        'AM': '9000', 'LSB': '3000', 'USB': '3000', 'FMN': '9000',
        'FM-D': '16000', 'FM': '16000', 'CW': '500', 'CWR': '500'
    }

    supported_ctcss_tones = [
        "67.0","69.4","71.9","74.4","77.0","79.7","82.5","85.4","88.5","91.5",
        "94.8","97.4","100.0","103.5","107.2","110.9","114.8","118.8","123.0",
        "127.3","131.8","136.5","141.3","146.2","150.0","151.4","156.7","159.8",
        "162.2","165.5","167.9","171.3","173.8","177.3","179.9","183.5","186.2",
        "189.9","192.8","196.6","199.5","203.5","206.5","210.7","218.1","225.7",
        "229.1","233.6","241.8","250.3","254.1"
    ]

    supported_repeater_shifts = ["+", "-", "None"]

    ap = argparse.ArgumentParser(description="Hamlib command dispatcher (YAML defaults, CLI overrides).")

    # YAML config paths
    ap.add_argument("--my_station_config", type=str, default="bqe_config/my_rig.yaml")
    ap.add_argument("--remote_channel_config", type=str, default="bqe_config/remote_channel.yaml")

    # Allow CLI overrides (empty default means “use YAML unless specified”)
    ap.add_argument("--radio_type", type=str, default="")
    ap.add_argument("--radio_port", type=str, default="")
    ap.add_argument("--radio_baud", type=str, default="")
    ap.add_argument("--frequency_hz", type=str, default="")
    ap.add_argument("--mode", type=str, default="")
    ap.add_argument("--repeater_shift", type=str, default="")
    ap.add_argument("--repeater_offset_khz", type=str, default="")
    ap.add_argument("--ctcss_tone", type=str, default="")

    args = ap.parse_args()

  

    # -------------------------------------------------------------
    # Load RADIO HARDWARE CONFIG (radio_type, radio_port)
    # -------------------------------------------------------------
    try:
        with open(args.my_station_config, "r") as f:
            rig_cfg = yaml.safe_load(f)

        radio_type = str(args.radio_type or rig_cfg.get("radio_type"))
        radio_port = str(args.radio_port or rig_cfg.get("radio_port"))
        radio_baud = str(args.radio_baud or rig_cfg.get("radio_baud"))

        print(f"[Rig Config] radio_type={radio_type}, radio_port={radio_port}, radio_baud={radio_baud}")

    except Exception as e:
        print(f"[WARNING] Could not read {args.my_station_config}: {e}")
        radio_type = args.radio_type
        radio_port = args.radio_port
        radio_baud = args.radio_baud

    # -------------------------------------------------------------
    # Load OPERATING PARAMETERS (freq, mode, offset, shift, tone)
    # -------------------------------------------------------------
    try:
        with open(args.remote_channel_config, "r") as f:
            st_cfg = yaml.safe_load(f)

        # CLI overrides YAML
        desired_frequency_hz = args.frequency_hz or str(st_cfg.get("frequency_hz", ""))
        desired_mode = (args.mode or st_cfg.get("mode", "")).upper()
        desired_repeater_shift = args.repeater_shift or st_cfg.get("repeater_shift", "")
        desired_repeater_offset_khz = args.repeater_offset_khz or st_cfg.get("repeater_offset_khz", "")
        desired_ctcss_tone = args.ctcss_tone or st_cfg.get("ctcss_tone", "")

        print("[INFO] [Remote Channel Config]")
        print(f"  frequency_hz={desired_frequency_hz}")
        print(f"  mode={desired_mode}")
        print(f"  repeater_shift={desired_repeater_shift}")
        print(f"  repeater_offset_khz={desired_repeater_offset_khz}")
        print(f"  ctcss_tone={desired_ctcss_tone}")

    except Exception as e:
        print(f"[WARNING] Could not read {args.remote_channel_config}: {e}")
        desired_frequency_hz = args.frequency_hz
        desired_mode = args.mode.upper()
        desired_repeater_shift = args.repeater_shift
        desired_repeater_offset_khz = args.repeater_offset_khz
        desired_ctcss_tone = args.ctcss_tone

    # -------------------------------------------------------------
    # Execute operations
    # -------------------------------------------------------------
    if desired_frequency_hz:
        hamlib_set_frequency(radio_type, radio_port, desired_frequency_hz)

    if desired_mode:
        if desired_mode in supported_modes:
            bw = supported_modes[desired_mode] # Gets the bandwidth from mode dictonary
            print(f"Setting mode {desired_mode} BW={bw}")
            hamlib_set_mode(radio_type, radio_port, desired_mode, bw)
        else:
            print(f"Unsupported mode: {desired_mode}")

    if desired_mode == "FM":

        if desired_repeater_shift:
            if desired_repeater_shift in supported_repeater_shifts:
                hamlib_set_repeater_shift(radio_type, radio_port, desired_repeater_shift)
            else:
                print(f"Invalid repeater shift: {desired_repeater_shift}")

        if desired_repeater_offset_khz:
            hamlib_set_repeater_offset(radio_type, radio_port, desired_repeater_offset_khz)

        if desired_ctcss_tone:
            if desired_ctcss_tone in supported_ctcss_tones:
                hamlib_set_ctcss_tone(radio_type, radio_port, desired_ctcss_tone)
            else:
                print(f"[WARNING] Ignoring nvalid CTCSS tone: {desired_ctcss_tone}")

if __name__ == "__main__":
    main()

