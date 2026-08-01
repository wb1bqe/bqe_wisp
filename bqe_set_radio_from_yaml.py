#!/usr/bin/env python3
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

# To run via the CLI using the default radio configuration:
#   python bqe_set_radio_from_yaml.py <nickname>
#
# To override the radio configuration file:
#   python bqe_set_radio_from_yaml.py <nickname> --my_rig path/to/my_rig.yaml
#
# To run as a web app (the --my_rig override also works in web mode):
#   python bqe_set_radio_from_yaml.py --web [--my_rig path/to/my_rig.yaml]

# To access the web console:  http://localhost:8015

  

# This locally reads my_config.yaml to find out radio type and port etc.   It
#  pre-supposes a common set of configuration files and reads the config each
# time we issue a rigctl command, which allows it to function as a standalone
# script in other contexts as well.

# There is also a simple web interface that listens on port 8015 for standalone use. 

import argparse
import glob
import html
import os
import subprocess
import sys
import yaml
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs


preset_GLOB = "presets/*_preset.yaml"
DEFAULT_RADIO_CONFIG = "bqe_config/my_rig.yaml"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8015


def load_yaml(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_preset_dictionary():
    presets = {}

    for filename in glob.glob(preset_GLOB):
        cfg = load_yaml(filename)

        if not isinstance(cfg, dict):
            continue

        nickname = cfg.get("nickname")

        if not nickname:
            base = os.path.basename(filename)
            nickname = base.replace("_preset.yaml", "")

        nickname = str(nickname).lower()
        cfg["_source_file"] = filename
        presets[nickname] = cfg

    return presets


def run_rigctl(radio_type, radio_port, args, radio_baud):
    cmd = ["rigctl", "-m", str(radio_type), "-r", str(radio_port)]

    if radio_baud:
        cmd += ["-s", str(radio_baud)]

    cmd += args

    #print("Running:", " ".join(cmd))

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"rigctl failed:\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDERR: {result.stderr}"
        )

    return result.stdout.strip()


def get_radio_settings(radio_cfg):
    radio_type = radio_cfg.get("radio_type")

    radio_port = radio_cfg.get("radio_port")

    radio_baud = radio_cfg.get("radio_baud")

    if radio_type is None:
        raise KeyError("Missing radio_type in my_rig.yaml")

    if radio_port is None:
        raise KeyError("Missing radio_port in my_rig.yaml")
    
    if radio_baud is None:
        raise KeyError("Missing radio_baud in my_rig.yaml")

    return radio_type, radio_port,radio_baud


def offset_hz_from_config(cfg):
    offset_khz = float(cfg.get("repeater_offset_khz", 0))
    shift = str(cfg.get("repeater_shift", "")).strip()

    offset_hz = int(offset_khz * 1000)

    if shift == "+":
        return offset_hz
    elif shift == "-":
        return -offset_hz
    else:
        return 0


def program_preset_by_nickname(nickname, radio_config=DEFAULT_RADIO_CONFIG):
    print("debug program_preset_by_nickname got nickname: ", nickname)
    presets = load_preset_dictionary()
    nickname = nickname.lower()

    if nickname not in presets:
        raise KeyError(f"Nickname '{nickname}' not found")

    radio_cfg = load_yaml(radio_config)
    preset_cfg = presets[nickname]

    return program_preset(preset_cfg, radio_cfg)


def program_preset(preset_cfg, radio_cfg):
    radio_type, radio_port, radio_baud = get_radio_settings(radio_cfg)

    frequency_hz = int(preset_cfg["frequency_hz"])
    mode = str(preset_cfg.get("mode", "FM")).upper()
    bandwidth = int(preset_cfg.get("bandwidth", 0))

    offset_hz = offset_hz_from_config(preset_cfg)
    shift = str(preset_cfg.get("repeater_shift", "")).strip()

    ctcss_tone = preset_cfg.get("ctcss_tone")
    power = preset_cfg.get("power")


    log = []

    def add(msg):
        print(msg)
        log.append(msg)

    add(f"Programming preset: {preset_cfg.get('channel_name', 'Unnamed')}")
    add(f"Source file  : {preset_cfg.get('_source_file')}")
    add(f"Nickname     : {preset_cfg.get('nickname')}")
    add(f"Radio type   : {radio_type}")
    add(f"Radio port   : {radio_port}")
    add(f"Radio baud   : {radio_baud}")
    add(f"RX frequency : {frequency_hz}")
    add(f"Mode         : {mode}")
    add(f"Bandwidth    : {bandwidth}")
    add(f"Shift        : {shift}")
    add(f"Offset Hz    : {offset_hz}")
    add(f"CTCSS tone   : {ctcss_tone}")
    add(f"Power        : {power}")  # Disabled below for now.
    add("")

    # Disable satellite/split mode in case bqe_track_continuously.py was terminated before it could fully clean up.
    run_rigctl(radio_type, radio_port, ["S", "0", "VFOA"],radio_baud)
    add(f"Disable Satellite/Split mode")

    # Mode needs to be programmed before frequency to avoid any offset errors that happen with mode changes.
    run_rigctl(radio_type, radio_port, ["M", mode, str(bandwidth)],radio_baud)
    add(f"Set mode to {mode}")

    run_rigctl(radio_type, radio_port, ["F", str(frequency_hz)],radio_baud)
    add(f"Set frequency to {frequency_hz}")

    run_rigctl(radio_type, radio_port, ["O", str(offset_hz)],radio_baud)
    add(f"Set preset offset to {offset_hz}")

    if shift in ["+", "-"]:
        run_rigctl(radio_type, radio_port, ["R", shift],radio_baud)
        add(f"Set preset shift to {shift}")
    else:
        run_rigctl(radio_type, radio_port, ["R", "none"],radio_baud)
        add("Set preset shift to none")

    if ctcss_tone:
        run_rigctl(radio_type, radio_port, ["C", str(ctcss_tone)],radio_baud)
        add(f"Set CTCSS tone to {ctcss_tone}")

        run_rigctl(radio_type, radio_port, ["U", "TONE", "1"],radio_baud)
        add("Set CTCSS Mode to ENC")
    else:
        run_rigctl(radio_type, radio_port, ["U", "TONE", "0"],radio_baud)
        add("Set CTCSS Mode to OFF since no tone specified.")


    add("")
    add("preset programming complete.")

    return "\n".join(log)


class presetWebHandler(BaseHTTPRequestHandler):
    radio_config = DEFAULT_RADIO_CONFIG

    def send_html(self, content):
        data = content.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def page(self, message=""):
        presets = load_preset_dictionary()

        options = "\n".join(
            f'<option value="{html.escape(nick)}">{html.escape(nick)}</option>'
            for nick in sorted(presets.keys())
        )

        safe_message = html.escape(message)

        return f"""<!DOCTYPE html>
<html>
<head>
    <title>BQE preset Control</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 40px;
            background: #f4f4f4;
        }}
        .box {{
            background: white;
            padding: 25px;
            max-width: 700px;
            border-radius: 10px;
            box-shadow: 0 2px 10px #ccc;
        }}
        select, button {{
            font-size: 18px;
            padding: 8px;
        }}
        pre {{
            background: #111;
            color: #eee;
            padding: 15px;
            overflow-x: auto;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
    <div class="box">
        <h1>BQE preset Control</h1>

        <form method="POST" action="/set">
            <label for="nickname">Choose preset nickname:</label>
            <br><br>

            <select name="nickname" id="nickname">
                {options}
            </select>

            <button type="submit">OK</button>
        </form>

        <h2>Status</h2>
        <pre>{safe_message}</pre>
    </div>
</body>
</html>
"""

    def do_GET(self):
        self.send_html(self.page())

    def do_POST(self):
        if self.path != "/set":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        form = parse_qs(body)

        nickname = form.get("nickname", [""])[0]

        try:
            output = program_preset_by_nickname(
                nickname,
                radio_config=self.radio_config
            )
        except Exception as e:
            output = f"ERROR: {e}"

        self.send_html(self.page(output))


def run_web_server(radio_config=DEFAULT_RADIO_CONFIG):
    presetWebHandler.radio_config = radio_config
    server = HTTPServer((WEB_HOST, WEB_PORT), presetWebHandler)

    print("Starting BQE preset web app...")
    print(f"Radio config: {radio_config}")
    print(f"Open: http://localhost:{WEB_PORT}")

    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(
        description="Program radio from bqe_config/*_preset.yaml by nickname."
    )

    parser.add_argument(
        "nickname",
        nargs="?",
        help="preset nickname to load from bqe_config/*_preset.yaml"
    )

    parser.add_argument(
        "--my_rig",
        "--radio-config",
        dest="radio_config",
        metavar="FILENAME",
        default=DEFAULT_RADIO_CONFIG,
        help=(
            "Path to the radio configuration YAML file "
            f"(default: {DEFAULT_RADIO_CONFIG})"
        )
    )

    parser.add_argument(
        "--web",
        action="store_true",
        help="Run built-in web app"
    )

    args = parser.parse_args()

    if args.web:
        run_web_server(radio_config=args.radio_config)
        return

    if not args.nickname:
        print("ERROR: nickname is required unless using --web")
        print()
        print("Example:")
        print("  python bqe_set_radio_from_yaml.py greylock")
        print("  python bqe_set_radio_from_yaml.py greylock --my_rig other_rig.yaml")
        print("  python bqe_set_radio_from_yaml.py --web")
        print("  python bqe_set_radio_from_yaml.py --web --my_rig other_rig.yaml")
        sys.exit(1)

    try:
        output = program_preset_by_nickname(
            args.nickname,
            radio_config=args.radio_config
        )
        print(output)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()