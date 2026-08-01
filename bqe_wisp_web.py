#!/usr/bin/env python3
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

"""HTTP request handler for the BQE WISP web console."""

import json
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union
from urllib.parse import urlparse

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
GENERAL_SETTINGS_PATH = SCRIPT_DIR / "bqe_config" / "general_settings.yaml"
QTH_CONFIG_PATH = SCRIPT_DIR / "bqe_config" / "my_qth.yaml"
QTH_DEFAULT_FIELDS = ("latitude", "longitude", "elevation", "my_callsign", "my_country")
QTH_ALWAYS_EDITABLE_FIELDS = ("my_callsign", "my_country")
RIG_CONFIG_PATH = SCRIPT_DIR / "bqe_config" / "my_rig.yaml"
RIG_DEFAULT_FIELDS = ("radio_type", "radio_port", "radio_speed", "radio_idle_task_nickname")
IDLE_TASK_TEMPLATE_PATH = SCRIPT_DIR / "templates" / "idle_task_template.yaml"
PRESETS_DIR = SCRIPT_DIR / "presets"
LICENSE_PATH = SCRIPT_DIR / "license.txt"
PRESET_DEFAULT_FIELDS = (
    "nickname",
    "program_to_run_while_waiting",
)
PRESET_OPTIONAL_FIELDS = frozenset((
    "bandwidth",
    "repeater_offset",
    "repeater_shift",
    "ctcss_tone",
))
DEFAULT_UI_REFRESH_INTERVAL_SECONDS = 5.0
DEFAULT_UI_PORT = 8013
MIN_UI_REFRESH_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class WebConsoleSettings:
    """Runtime configuration for the BQE WISP web console."""

    ui_refresh_interval_seconds: float
    ui_port: int

    @property
    def ui_refresh_interval_ms(self) -> int:
        """Return the browser polling interval in milliseconds."""
        return int(round(self.ui_refresh_interval_seconds * 1000))


def _program_settings_sections(raw_settings: Any) -> dict[str, Mapping[str, Any]]:
    """Normalize general_settings.yaml into named program settings sections."""
    program_settings = raw_settings.get("program_settings", raw_settings)
    sections: dict[str, Mapping[str, Any]] = {}

    if isinstance(program_settings, Mapping):
        for section_name, section_values in program_settings.items():
            sections[str(section_name)] = section_values or {}
        return sections

    for item in program_settings:
        if not isinstance(item, Mapping):
            continue
        for section_name, section_values in item.items():
            sections[str(section_name)] = section_values or {}

    return sections


def _parse_seconds(value: Any) -> float:
    """Parse YAML time values such as 5, "5", "5 seconds", or "5000 ms"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        match = re.fullmatch(
            r"([0-9]+(?:\.[0-9]+)?)\s*"
            r"(ms|millisecond|milliseconds|s|sec|secs|second|seconds)?",
            str(value).strip().lower(),
        )
        if not match:
            raise ValueError(f"Invalid time value in general_settings.yaml: {value!r}")

        seconds = float(match.group(1))
        unit = match.group(2) or "seconds"
        if unit in {"ms", "millisecond", "milliseconds"}:
            seconds /= 1000.0

    if seconds < MIN_UI_REFRESH_INTERVAL_SECONDS:
        raise ValueError(
            "ui_refresh_interval must be at least "
            f"{MIN_UI_REFRESH_INTERVAL_SECONDS} seconds"
        )

    return seconds


def _parse_port(value: Any) -> int:
    """Parse and validate the configured web-console TCP port."""
    port = int(str(value).strip())
    if not 1 <= port <= 65535:
        raise ValueError(f"ui_port must be between 1 and 65535, not {port!r}")
    return port


def load_web_console_settings(
    settings_path: Union[str, Path] = GENERAL_SETTINGS_PATH,
) -> WebConsoleSettings:
    """Load web-console settings from bqe_config/general_settings.yaml."""
    raw_settings = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8"))
    sections = _program_settings_sections(raw_settings)

    # The bqe_wisp section holds shared scheduler/web-console settings.
    # Values in bqe_wisp_web can override them when web-only settings are added.
    merged_settings: dict[str, Any] = {
        "ui_refresh_interval": DEFAULT_UI_REFRESH_INTERVAL_SECONDS,
        "ui_port": DEFAULT_UI_PORT,
    }
    merged_settings.update(sections.get("bqe_wisp", {}))
    merged_settings.update(sections.get("bqe_wisp_web", {}))

    return WebConsoleSettings(
        ui_refresh_interval_seconds=_parse_seconds(merged_settings["ui_refresh_interval"]),
        ui_port=_parse_port(merged_settings["ui_port"]),
    )


def get_web_console_port(settings_path: Union[str, Path] = GENERAL_SETTINGS_PATH) -> int:
    """Return the configured port for the code that starts the HTTP server."""
    return load_web_console_settings(settings_path).ui_port


def _qth_value_to_text(value: Any) -> str:
    """Convert a YAML value into text suitable for an editable form field."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _infer_qth_value_type(text_value: Any, previous_value: Any = None) -> Any:
    """Convert edited form text back into a YAML-friendly scalar value.

    Existing numeric fields remain numeric.  New numeric-looking values are also
    written as numbers so latitude/longitude/elevation continue to work with the
    tracker and map code.
    """
    text_value = "" if text_value is None else str(text_value).strip()

    if isinstance(previous_value, bool):
        lowered = text_value.lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
        return text_value

    if isinstance(previous_value, int) and not isinstance(previous_value, bool):
        try:
            return int(text_value)
        except ValueError:
            try:
                return float(text_value)
            except ValueError:
                return text_value

    if isinstance(previous_value, float):
        try:
            return float(text_value)
        except ValueError:
            return text_value

    if isinstance(previous_value, (dict, list)):
        try:
            return yaml.safe_load(text_value)
        except Exception:
            return text_value

    lowered = text_value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"[-+]?\d+", text_value):
        try:
            return int(text_value)
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", text_value) or re.fullmatch(r"[-+]?\d+[eE][-+]?\d+", text_value):
        try:
            return float(text_value)
        except ValueError:
            pass
    return text_value


def _read_qth_yaml(path: Union[str, Path] = QTH_CONFIG_PATH) -> dict[str, Any]:
    """Read bqe_config/my_qth.yaml as a top-level YAML mapping."""
    qth_path = Path(path)
    if not qth_path.exists():
        return {}

    raw = yaml.safe_load(qth_path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{qth_path} must contain a YAML mapping at the top level.")
    return dict(raw)


def _qth_response(message: str = "") -> dict[str, Any]:
    """Build the JSON payload used by the QTH editor dialog."""
    data = _read_qth_yaml(QTH_CONFIG_PATH)
    editable = {str(key): _qth_value_to_text(value) for key, value in data.items()}

    if not editable:
        editable = {name: "" for name in QTH_DEFAULT_FIELDS}
    else:
        # Always expose station identity without adding duplicate coordinate keys
        # when an existing file uses names such as my_latitude/my_longitude.
        for name in QTH_ALWAYS_EDITABLE_FIELDS:
            editable.setdefault(name, "")
    return {
        "ok": True,
        "path": str(QTH_CONFIG_PATH),
        "exists": QTH_CONFIG_PATH.exists(),
        "data": editable,
        "message": message or f"Loaded QTH configuration from {QTH_CONFIG_PATH}.",
    }


def read_qth_config_payload() -> Mapping[str, Any]:
    """Return the current editable QTH configuration for the browser."""
    try:
        return _qth_response()
    except Exception as exc:
        return {"ok": False, "message": f"Could not read QTH configuration: {exc}"}


def write_qth_config_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Write edited QTH settings back to bqe_config/my_qth.yaml."""
    try:
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "QTH save request did not contain a data mapping."}

        existing_data = _read_qth_yaml(QTH_CONFIG_PATH)
        updated_data = dict(existing_data)

        for key, value in edited_data.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            updated_data[key_text] = _infer_qth_value_type(value, existing_data.get(key_text))

        QTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        QTH_CONFIG_PATH.write_text(
            yaml.safe_dump(updated_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return _qth_response(f"Saved QTH configuration to {QTH_CONFIG_PATH}.")
    except Exception as exc:
        return {"ok": False, "message": f"Could not save QTH configuration: {exc}"}
    

def _read_rig_config_yaml(path: Union[str, Path] = RIG_CONFIG_PATH) -> dict[str, Any]:
    """Read bqe_config/my_rig.yaml as a top-level YAML mapping, if present."""
    rig_path = Path(path)
    if not rig_path.exists():
        return {}

    raw = yaml.safe_load(rig_path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{rig_path} must contain a YAML mapping at the top level.")
    return dict(raw)


def _radio_editable_data() -> dict[str, Any]:
    """Return template fields with existing my_rig.yaml values overlaid when present."""
    #template_data = _read_rig_template_yaml(RIG_TEMPLATE_PATH)
    rig_yaml_data = _read_rig_config_yaml(RIG_CONFIG_PATH)

    #merged_data = dict(template_data)
    #for key, value in existing_data.items():
    #    merged_data[key] = value

    #if not merged_data:
    #    merged_data = {name: "" for name in RIG_DEFAULT_FIELDS}
    return rig_yaml_data

def _radio_response(message: str = "") -> dict[str, Any]:
    """Build the JSON payload used by the Radio editor dialog."""
    data = _radio_editable_data()
    editable = {str(key): _qth_value_to_text(value) for key, value in data.items()}
    return {
        "ok": True,
       # "template_path": str(RIG_CONFIG_PATH),
        "path": str(RIG_CONFIG_PATH),
       # "template_exists": RIG_TEMPLATE_PATH.exists(),
        "exists": RIG_CONFIG_PATH.exists(),
        "data": editable,
        "message": message or f"Loaded Radio configuration template from {RIG_CONFIG_PATH}.",
    }


def read_radio_config_payload() -> Mapping[str, Any]:
    """Return the editable Radio configuration built from the rig template."""
    try:
        return _radio_response()
    except Exception as exc:
        return {"ok": False, "message": f"Could not read Radio configuration template: {exc}"}


def write_radio_config_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Write edited Radio settings to bqe_config/my_rig.yaml."""
    try:
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "Radio save request did not contain a data mapping."}

        #template_data = _read_rig_template_yaml(RIG_TEMPLATE_PATH)
        existing_data = _read_rig_config_yaml(RIG_CONFIG_PATH)
        #previous_data = dict(template_data)
        #previous_data.update(existing_data)

        updated_data = dict(existing_data)
        for key, value in edited_data.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            updated_data[key_text] = _infer_qth_value_type(value, existing_data.get(key_text))

        RIG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RIG_CONFIG_PATH.write_text(
            yaml.safe_dump(updated_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return _radio_response(f"Saved Radio configuration to {RIG_CONFIG_PATH}.")
    except Exception as exc:
        return {"ok": False, "message": f"Could not save Radio configuration: {exc}"}


def _read_idle_task_template_yaml(path: Union[str, Path] = IDLE_TASK_TEMPLATE_PATH) -> dict[str, Any]:
    """Read templates/idle_task_template.yaml as a top-level YAML mapping."""
    template_path = Path(path)
    if not template_path.exists():
        raise FileNotFoundError(f"{template_path} not found")

    raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{template_path} must contain a YAML mapping at the top level.")
    return dict(raw)


def _suggest_preset_filename(data: Mapping[str, Any]) -> str:
    """Build a presets/*.yaml filename as <nickname>_preset.yaml."""
    for key in ("nickname", "NICKNAME"):
        value = data.get(key)
        if value is not None and str(value).strip():
            return _normalize_preset_filename(f"{value}_preset.yaml")
    return "idle_task_preset.yaml"


def _normalize_preset_filename(filename: Any) -> str:
    """Return a safe top-level YAML filename for the presets directory."""
    filename_text = "" if filename is None else str(filename).strip()
    if not filename_text:
        filename_text = "idle_task_preset"

    if "/" in filename_text or "\\" in filename_text:
        raise ValueError("Preset filename must be a filename only, not a path.")

    suffix = ".yaml"
    lowered = filename_text.lower()
    if lowered.endswith(".yaml"):
        stem = filename_text[:-5]
        suffix = ".yaml"
    elif lowered.endswith(".yml"):
        stem = filename_text[:-4]
        suffix = ".yml"
    else:
        stem = filename_text

    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    if not safe_stem:
        safe_stem = "idle_task_preset"
    return f"{safe_stem}{suffix}"


def _create_preset_response(message: str = "", filename: Optional[str] = None) -> dict[str, Any]:
    """Build the JSON payload used by the Create Preset dialog."""
    template_data = _read_idle_task_template_yaml(IDLE_TASK_TEMPLATE_PATH)

    # Always expose the standard preset fields, even when an older template does
    # not contain the newer optional radio settings.  Template values override
    # the blank defaults and any additional template-specific keys are retained.
    editable_data = {name: "" for name in PRESET_DEFAULT_FIELDS}
    editable_data.update(template_data)

    editable = {str(key): _qth_value_to_text(value) for key, value in editable_data.items()}
    suggested_filename = filename or _suggest_preset_filename(editable_data)
    return {
        "ok": True,
        "template_path": str(IDLE_TASK_TEMPLATE_PATH),
        "presets_path": str(PRESETS_DIR),
        "filename": suggested_filename,
        "data": editable,
        "message": message or f"Loaded idle-task preset template from {IDLE_TASK_TEMPLATE_PATH}.",
    }


def read_create_preset_payload() -> Mapping[str, Any]:
    """Return the editable idle-task preset template for the browser."""
    try:
        return _create_preset_response()
    except Exception as exc:
        return {"ok": False, "message": f"Could not read idle-task preset template: {exc}"}


def read_license_payload() -> Mapping[str, Any]:
    """Read license.txt from the program directory for the Help dialog."""
    try:
        content = LICENSE_PATH.read_text(encoding="utf-8-sig")
        return {
            "ok": True,
            "exists": True,
            "content": content,
            "message": f"Loaded license from {LICENSE_PATH}.",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "exists": False,
            "content": "",
            "message": f"License file not found: {LICENSE_PATH}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "exists": LICENSE_PATH.exists(),
            "content": "",
            "message": f"Could not read license file {LICENSE_PATH}: {exc}",
        }


def write_create_preset_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Create a new YAML preset under presets/ from the idle-task template."""
    try:
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "Preset save request did not contain a data mapping."}

        template_data = _read_idle_task_template_yaml(IDLE_TASK_TEMPLATE_PATH)
        updated_data = dict(template_data)
        for key, value in edited_data.items():
            key_text = str(key).strip()
            if not key_text:
                continue

            # These radio settings are optional.  A blank field means the key
            # must be absent from the newly created preset, even if an older
            # template contains that key with an empty/default value.
            if key_text in PRESET_OPTIONAL_FIELDS and not str(value or "").strip():
                updated_data.pop(key_text, None)
                continue

            updated_data[key_text] = _infer_qth_value_type(value, template_data.get(key_text))

        # Apply the same rule defensively in case a client omits an optional
        # field and the template itself contains a blank value for that key.
        for key_text in PRESET_OPTIONAL_FIELDS:
            value = updated_data.get(key_text)
            if value is None or (isinstance(value, str) and not value.strip()):
                updated_data.pop(key_text, None)

        nickname = str(
            updated_data.get("nickname", updated_data.get("NICKNAME", "")) or ""
        ).strip()
        if not nickname:
            return {
                "ok": False,
                "message": "Preset nickname is required because the filename is built as <nickname>_preset.yaml.",
            }

        safe_filename = _suggest_preset_filename(updated_data)
        preset_path = PRESETS_DIR / safe_filename

        if preset_path.exists():
            return {
                "ok": False,
                "message": f"Preset {preset_path} already exists. Change the preset nickname to create a different filename.",
            }

        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        preset_path.write_text(
            yaml.safe_dump(updated_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        return _create_preset_response(f"Saved preset to {preset_path}.", filename=safe_filename)
    except Exception as exc:
        return {"ok": False, "message": f"Could not save preset: {exc}"}


INDEX_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BQE WISP</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {
      color-scheme: light;
      --panel: #b9b9b9;
      --panel-dark: #9d9d9d;
      --panel-light: #d2d2d2;
      --border-dark: #6f6f6f;
      --border-light: #e8e8e8;
      --active-red: #d00000;
      --readout-green: #7cff5b;
      --readout-glow: 0 0 4px rgba(124,255,91,.95), 0 0 11px rgba(124,255,91,.55);
      --console-pad: 14px;
      --console-width-base: 1290px;
      --console-width-with-presets: 1540px;
      --console-width-with-map: 1690px;
      --console-width-with-map-and-presets: 1940px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #8f8f8f;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-size: 18px;
      font-weight: 700;
      min-height: 100vh;
      overflow-x: auto;
      display: block;
    }
    .console {
      display: grid;
      grid-template-columns: 320px minmax(520px, 1fr);
      grid-template-rows: minmax(0, 1fr) 132px;
      gap: 8px;
      width: 100%;
      max-width: var(--console-width-base);
      height: min(720px, calc(100vh - 34px));
      min-height: 650px;
      margin: 0;
      padding: var(--console-pad);
      background: linear-gradient(135deg, #cdcdcd, #a9a9a9);
    }
    .console.presets-visible {
      grid-template-columns: 320px minmax(520px, 1fr) 240px;
      max-width: var(--console-width-with-presets);
    }
    .console.map-visible {
      grid-template-columns: 320px minmax(520px, 1fr) minmax(360px, .85fr);
      max-width: var(--console-width-with-map);
      height: min(760px, calc(100vh - 34px));
    }
    .console.presets-visible.map-visible {
      grid-template-columns: 320px minmax(520px, 1fr) 240px minmax(360px, .85fr);
      max-width: var(--console-width-with-map-and-presets);
    }
    .panel {
      background: linear-gradient(135deg, #cfcfcf, var(--panel));
      border: 3px ridge var(--panel-light);
      box-shadow: inset 1px 1px 0 var(--border-light), inset -1px -1px 0 var(--border-dark);
    }
    .left {
      grid-row: 1 / span 2;
      padding: 8px;
    }
    .clock-block {
      text-align: center;
      font-size: 24px;
      line-height: 1.08;
      letter-spacing: 1px;
      margin-bottom: 8px;
    }
    .gauge-wrap {
      margin: 10px 0 10px;
      text-align: center;
      background: radial-gradient(circle at 50% 40%, #1d1d1d, #050505 78%);
      border: 2px inset #3a3a3a;
      padding: 8px 4px 6px;
      color: #eee;
    }
    .gauge-title {
      color: #f0f0f0;
      font-size: 16px;
      line-height: 1;
      margin-bottom: 3px;
      text-transform: uppercase;
      letter-spacing: .5px;
    }
    .svg-gauge {
      display: block;
      margin: 0 auto;
      overflow: visible;
    }
    .gauge-ring,
    .gauge-arc {
      fill: none;
      stroke: #e8e8e8;
      stroke-width: 3;
      filter: drop-shadow(0 0 2px rgba(255,255,255,.35));
    }
    .gauge-minor {
      stroke: #d8d8d8;
      stroke-width: 1.5;
    }
    .gauge-major {
      stroke: #f4f4f4;
      stroke-width: 2.5;
    }
    .gauge-label {
      fill: #f4f4f4;
      font-family: "Courier New", Consolas, monospace;
      font-size: 15px;
      font-weight: 900;
      text-anchor: middle;
      dominant-baseline: middle;
    }
    .gauge-needle {
      stroke: #d8ffd0;
      stroke-width: 2;
      stroke-linecap: round;
    }
    .gauge-boom {
      stroke: #d8ffd0;
      stroke-width: 2.5;
      stroke-linecap: round;
    }
    .gauge-hub {
      fill: #58d65a;
      stroke: #58ff4d;
      stroke-width: 2;
      filter: drop-shadow(0 0 4px rgba(88,255,77,.65));
    }
    .label { text-align: center; font-size: 22px; line-height: 1; }
    .radio-readouts {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(0, .9fr);
      gap: 8px;
      margin-top: 14px;
    }
    .radio-box {
      background: linear-gradient(135deg, #d1d1d1, #adadad);
      border: 3px ridge var(--panel-light);
      padding: 5px;
      min-height: 62px;
      min-width: 0;
    }
    .radio-box .box-title {
      display: block;
      color: #111;
      font-size: 13px;
      line-height: 1.1;
      text-align: center;
      margin-bottom: 4px;
    }
    .radio-box .box-value {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 3px 4px;
      border: 2px inset #232323;
      background: radial-gradient(circle at 50% 40%, #051805, #000 78%);
      color: var(--readout-green);
      font-size: 20px;
      font-weight: 900;
      line-height: 1;
      letter-spacing: .5px;
      text-shadow: var(--readout-glow);
      white-space: nowrap;
      overflow: hidden;
    }
    .radio-box.wide { grid-column: span 1; }
    .radio-box.mode .box-value { font-size: 25px; }
    .schedule {
      grid-column: 2;
      grid-row: 1;
      overflow: auto;
      min-height: 0;
      max-height: 100%;
      padding: 10px;
      scrollbar-color: #505050 #b5b5b5;
      scrollbar-width: auto;
      scroll-behavior: auto;
    }
    .schedule::-webkit-scrollbar { width: 18px; height: 18px; }
    .schedule::-webkit-scrollbar-track { background: #b5b5b5; border: 2px inset #d0d0d0; }
    .schedule::-webkit-scrollbar-thumb { background: #505050; border: 2px outset #7a7a7a; }
    .preset-panel {
      grid-column: 3;
      grid-row: 1;
      display: none;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
      padding: 10px;
      overflow: hidden;
    }
    .console.presets-visible .preset-panel {
      display: flex;
    }
    .preset-title {
      flex: 0 0 auto;
      padding: 2px 2px 10px;
      text-align: center;
      font-size: 22px;
      line-height: 1.1;
      text-transform: uppercase;
    }
    .preset-buttons {
      display: flex;
      flex: 1 1 auto;
      flex-direction: column;
      gap: 9px;
      min-height: 0;
      overflow-y: auto;
      padding: 2px 3px 4px;
      scrollbar-color: #505050 #b5b5b5;
    }
    .preset-button {
      flex: 0 0 auto;
      width: 100%;
      min-height: 44px;
      padding: 7px 8px;
      border: 3px outset #e4e4e4;
      background: linear-gradient(180deg, #e0e0e0, #bdbdbd);
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-size: 17px;
      font-weight: 900;
      line-height: 1.1;
      overflow-wrap: anywhere;
      cursor: pointer;
    }
    .preset-button:hover:not(:disabled),
    .preset-button:focus-visible:not(:disabled) {
      background: #1f4f91;
      color: white;
      outline: none;
    }
    .preset-button:active:not(:disabled) {
      border-style: inset;
    }
    .preset-button:disabled {
      border-style: inset;
      background: #aaaaaa;
      color: #696969;
      cursor: not-allowed;
      opacity: .82;
    }
    .preset-empty {
      padding: 12px 6px;
      color: #555;
      font-size: 15px;
      line-height: 1.3;
      text-align: center;
    }
    table {
      border-collapse: collapse;
      width: max-content;
      min-width: 100%;
      margin-left: 0;
    }
    th, td {
      padding: 9px clamp(8px, 1.6vw, 18px);
      white-space: nowrap;
      text-align: left;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #c9c9c9;
      border-bottom: 2px solid #777;
      font-size: 22px;
    }
    td { border-bottom: 1px solid rgba(0,0,0,.12); font-size: 22px; }
    tr.active {
      background: var(--active-red);
      color: white;
      text-shadow: 0 0 1px #fff;
    }
    tr.done { color: #777; }
    tr.pending { color: #111; }
    .map-panel {
      grid-column: 3;
      grid-row: 1;
      display: none;
      flex-direction: column;
      gap: 8px;
      min-width: 0;
      min-height: 0;
      padding: 10px;
    }
    .console.presets-visible .map-panel {
      grid-column: 4;
    }
    .console.map-visible .map-panel {
      display: flex;
    }

    /* A second copy of this page can run as a map-only detached window. */
    body.detached-map-mode {
      overflow: hidden;
    }
    body.detached-map-mode .taskbar,
    body.detached-map-mode .left,
    body.detached-map-mode .schedule,
    body.detached-map-mode .preset-panel,
    body.detached-map-mode .statusbar {
      display: none !important;
    }
    body.detached-map-mode .console,
    body.detached-map-mode .console.map-visible {
      display: block;
      width: 100vw;
      max-width: none;
      height: 100vh;
      min-height: 0;
      margin: 0;
      padding: 0;
      background: #8f8f8f;
    }
    body.detached-map-mode .map-panel,
    body.detached-map-mode .console.map-visible .map-panel {
      display: flex !important;
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 0;
      padding: 10px;
    }
    body.detached-map-mode #earthMap {
      flex: 1 1 auto;
      min-height: 0;
    }
    .map-title {
      text-align: center;
      font-size: 22px;
      line-height: 1.1;
      letter-spacing: .5px;
      text-transform: uppercase;
    }
    #earthMap {
      flex: 1 1 auto;
      min-height: 360px;
      width: 100%;
      border: 3px inset #3a3a3a;
      background: #1b1b1b;
    }
    .map-readouts {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr);
      gap: 4px 8px;
      padding: 7px 8px;
      background: rgba(255,255,255,.22);
      border: 2px inset #d0d0d0;
      font-size: 15px;
      line-height: 1.25;
    }
    .map-readouts .map-value {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #0b5f0b;
      text-shadow: var(--readout-glow);
    }
    .map-status {
      min-height: 18px;
      font-size: 13px;
      line-height: 1.2;
      color: #111;
    }
    .leaflet-container {
      font-family: Arial, Helvetica, sans-serif;
      font-weight: 400;
      font-size: 12px;
    }
    .statusbar {
      grid-column: 2 / -1;
      grid-row: 2;
      padding: 18px 20px;
      font-size: 20px;
      line-height: 1.5;
    }
    .status-grid {
      display: grid;
      grid-template-columns: 155px 90px 80px 140px 150px;
      gap: 8px;
      align-items: baseline;
    }
    .status-head { margin-bottom: 8px; }
    .message { margin-top: 10px; font-size: 20px; }
    @media (max-width: 1180px) {
      /* Preserve each selected pane and allow horizontal scrolling on narrow screens. */
      .console {
        min-width: 880px;
        height: auto;
        min-height: 650px;
      }
      .console.presets-visible {
        min-width: 1100px;
      }
      .console.map-visible {
        min-width: 1260px;
      }
      .console.presets-visible.map-visible {
        min-width: 1500px;
      }
    }
    .taskbar {
      display: flex;
      align-items: center;
      width: 100%;
      max-width: var(--console-width-base);
      min-height: 34px;
      margin: 0;
      padding: 0 var(--console-pad);
      background: linear-gradient(180deg, #eeeeee, #bdbdbd);
      border-bottom: 2px ridge var(--panel-light);
      box-shadow: inset 1px 1px 0 var(--border-light), inset -1px -1px 0 var(--border-dark);
      position: relative;
      z-index: 50;
    }
    body.presets-visible .taskbar {
      max-width: var(--console-width-with-presets);
    }
    body.map-visible .taskbar {
      max-width: var(--console-width-with-map);
    }
    body.presets-visible.map-visible .taskbar {
      max-width: var(--console-width-with-map-and-presets);
    }
    .menu {
      position: relative;
      display: inline-flex;
      align-items: stretch;
      min-height: 30px;
    }
    .help-menu {
      margin-left: auto;
    }
    .tracking-submenu {
      min-width: 230px;
    }
    .config-submenu {
      min-width: 190px;
    }
    .menu-button,
    .submenu button,
    .about-close {
      font-family: "Courier New", Consolas, monospace;
      font-weight: 900;
    }
    .menu-button {
      min-width: 82px;
      padding: 4px 14px;
      border: 2px outset #e4e4e4;
      background: #d2d2d2;
      color: #111;
      font-size: 18px;
      cursor: pointer;
    }
    .menu-button:hover,
    .menu-button:focus {
      background: #e2e2e2;
      outline: none;
    }
    .submenu {
      display: none;
      position: absolute;
      top: 100%;
      left: 0;
      min-width: 165px;
      background: #d5d5d5;
      border: 2px outset #e6e6e6;
      box-shadow: 3px 3px 0 rgba(0,0,0,.28);
      padding: 3px;
      z-index: 60;
    }
    .menu:hover .submenu,
    .menu:focus-within .submenu {
      display: block;
    }
    .submenu button {
      display: block;
      width: 100%;
      padding: 8px 12px;
      border: 0;
      background: transparent;
      color: #111;
      text-align: left;
      font-size: 17px;
      cursor: pointer;
    }
    .submenu button:hover,
    .submenu button:focus {
      background: #05058a;
      color: white;
      outline: none;
    }
    .menu-button.menu-click-flash,
    .submenu button.menu-click-flash,
    .preset-button.menu-click-flash {
      animation: bqe-menu-click-flash 1s ease-out forwards;
    }
    .submenu button.menu-command-running,
    .submenu button.menu-command-running:hover,
    .submenu button.menu-command-running:focus {
      background: #21b721 !important;
      color: #fff !important;
      box-shadow: inset 0 0 0 2px rgba(255,255,255,.45), 0 0 8px rgba(33,183,33,.7) !important;
      cursor: wait;
      outline: none;
    }
    @keyframes bqe-menu-click-flash {
      0%, 70% {
        background: #21b721;
        color: #fff;
        box-shadow: inset 0 0 0 2px rgba(255,255,255,.45), 0 0 8px rgba(33,183,33,.7);
      }
      100% {
        background: #d2d2d2;
        color: #111;
        box-shadow: none;
      }
    }
    .about-modal {
      display: none;
      position: fixed;
      inset: 0;
      align-items: center;
      justify-content: center;
      background: rgba(0,0,0,.38);
      z-index: 100;
    }
    .about-modal.open {
      display: flex;
    }
    .about-box {
      position: relative;
      width: min(390px, calc(100vw - 36px));
      padding: 22px 24px 20px;
      background: linear-gradient(135deg, #d7d7d7, #b8b8b8);
      color: #111;
      box-shadow: 6px 6px 0 rgba(0,0,0,.33);
    }
    .about-title {
      margin: 0 32px 18px 0;
      font-size: 23px;
      line-height: 1.2;
    }
    .about-line {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      margin: 10px 0;
      padding: 8px 10px;
      background: rgba(255,255,255,.32);
      border: 2px inset #d0d0d0;
      font-size: 20px;
    }
    .about-line strong {
      color: #0b5f0b;
      text-shadow: var(--readout-glow);
    }
    .about-close {
      position: absolute;
      top: 8px;
      right: 8px;
      width: 30px;
      height: 28px;
      border: 2px outset #e4e4e4;
      background: #d2d2d2;
      color: #111;
      font-size: 21px;
      line-height: 20px;
      cursor: pointer;
    }
    .config-modal {
      display: none;
      position: fixed;
      inset: 0;
      align-items: center;
      justify-content: center;
      background: rgba(0,0,0,.38);
      z-index: 100;
    }
    .config-modal.open {
      display: flex;
    }
    .config-box {
      position: relative;
      width: min(520px, calc(100vw - 36px));
      padding: 22px 24px 20px;
      background: linear-gradient(135deg, #d7d7d7, #b8b8b8);
      color: #111;
      box-shadow: 6px 6px 0 rgba(0,0,0,.33);
    }
    .config-title {
      margin: 0 32px 18px 0;
      font-size: 23px;
      line-height: 1.2;
    }
    .config-fields {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 10px 12px;
      align-items: center;
      margin: 10px 0 18px;
    }
    .config-fields label {
      font-size: 18px;
      text-align: right;
    }
    /* Give the Create Preset dialog extra room for long property names
       without changing the QTH or Radio configuration dialogs. */
    #presetFields {
      grid-template-columns: 310px minmax(0, 1fr);
    }
    #presetFields label {
      text-align: left;
    }
    .config-fields input {
      min-width: 0;
      width: 100%;
      padding: 7px 9px;
      border: 2px inset #d0d0d0;
      background: #f4f4f4;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-size: 18px;
      font-weight: 900;
    }
    .config-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 12px;
    }
    .config-actions button,
    .config-close {
      font-family: "Courier New", Consolas, monospace;
      font-weight: 900;
      border: 2px outset #e4e4e4;
      background: #d2d2d2;
      color: #111;
      cursor: pointer;
    }
    .config-actions button {
      min-width: 92px;
      padding: 7px 16px;
      font-size: 18px;
    }
    .config-actions button:hover,
    .config-actions button:focus,
    .config-close:hover,
    .config-close:focus {
      background: #e2e2e2;
      outline: none;
    }
    .config-close {
      position: absolute;
      top: 8px;
      right: 8px;
      width: 30px;
      height: 28px;
      font-size: 21px;
      line-height: 20px;
    }
    .config-message {
      min-height: 18px;
      margin-top: 6px;
      font-size: 14px;
      line-height: 1.25;
      color: #111;
    }
    .license-box {
      position: relative;
      display: flex;
      flex-direction: column;
      width: min(780px, calc(100vw - 36px));
      max-height: min(82vh, 760px);
      padding: 22px 24px 20px;
      background: linear-gradient(135deg, #d7d7d7, #b8b8b8);
      color: #111;
      box-shadow: 6px 6px 0 rgba(0,0,0,.33);
    }
    .license-content {
      flex: 1 1 auto;
      min-height: 220px;
      max-height: calc(82vh - 155px);
      margin: 0;
      padding: 12px 14px;
      overflow: auto;
      border: 2px inset #d0d0d0;
      background: #f4f4f4;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-size: 16px;
      font-weight: 700;
      line-height: 1.35;
      white-space: pre;
      tab-size: 4;
      scrollbar-color: #505050 #d0d0d0;
      scrollbar-width: auto;
    }
    .license-content::-webkit-scrollbar { width: 16px; height: 16px; }
    .license-content::-webkit-scrollbar-track { background: #d0d0d0; border: 2px inset #e4e4e4; }
    .license-content::-webkit-scrollbar-thumb { background: #505050; border: 2px outset #7a7a7a; }
  </style>
</head>
<body data-bqe-ui-build="auto-schedule-running-green-20260715">
  <nav class="taskbar" role="menubar" aria-label="Application menu">
    <div class="menu file-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="File options">File</button>
      <div class="submenu" role="menu" aria-label="File menu">
        <button type="button" role="menuitem" id="restartServerMenuItem">Restart Server</button>
        <button type="button" role="menuitem" id="exitMenuItem">Exit</button>
      </div>
    </div>
    <div class="menu view-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="View options">View</button>
      <div class="submenu" role="menu" aria-label="View menu">
        <button type="button" role="menuitemcheckbox" aria-checked="false" id="mapMenuItem">Map</button>
        <button type="button" role="menuitemcheckbox" aria-checked="false" id="presetMenuItem">Show Presets</button>
      </div>
    </div>
    <div class="menu config-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Configuration options">Config</button>
      <div class="submenu config-submenu" role="menu" aria-label="Config menu">
        <button type="button" role="menuitem" id="configQthMenuItem">QTH</button>
        <button type="button" role="menuitem" id="configRadioMenuItem">Radio</button>
        <button type="button" role="menuitem" id="configCreatePresetMenuItem">Create Preset</button>
      </div>
    </div>
    <div class="menu tracking-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Tracking tools">Tracking</button>
      <div class="submenu tracking-submenu" role="menu" aria-label="Tracking menu">
        <button type="button" role="menuitem" id="updateKepsMenuItem">Update Keps</button>
        <button type="button" role="menuitem" id="schedulePassesMenuItem" aria-busy="false" title="Run bqe_schedule_passes.py --auto_schedule">Auto-Schedule Passes</button>
      </div>
    </div>
    <div class="menu help-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Help">Help</button>
      <div class="submenu" role="menu" aria-label="Help menu">
        <button type="button" role="menuitem" id="licenseMenuItem">Show License</button>
        <button type="button" role="menuitem" id="aboutMenuItem">About</button>
      </div>
    </div>
  </nav>

  <div class="about-modal" id="aboutModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="aboutTitle">
    <div class="about-box panel">
      <button class="about-close" type="button" id="aboutClose" aria-label="Close about dialog">&times;</button>
      <div class="about-title" id="aboutTitle">About BQE WISP</div>
      <div class="about-line"><span>Author</span><strong>Al Lawler WB1BQE</strong></div>
      <div class="about-line"><span>Version</span><strong>0.6</strong></div>
    </div>
  </div>

  <div class="config-modal" id="licenseModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="licenseTitle">
    <div class="license-box panel">
      <div class="config-title" id="licenseTitle">BQE WISP License</div>
      <pre class="license-content" id="licenseContent" tabindex="0">Loading license...</pre>
      <div class="config-message" id="licenseMessage" aria-live="polite"></div>
      <div class="config-actions">
        <button type="button" id="licenseOkButton">OK</button>
      </div>
    </div>
  </div>

  <div class="config-modal" id="qthModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="qthTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="qthClose" aria-label="Close QTH dialog">&times;</button>
      <div class="config-title" id="qthTitle">QTH Configuration</div>
      <form id="qthForm">
        <div class="config-fields" id="qthFields">
          <label>Loading</label><input type="text" value="" disabled>
        </div>
        <div class="config-actions">
          <button type="submit" id="qthOkButton">OK</button>
          <button type="button" id="qthCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="qthMessage"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="radioModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="radioTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="radioClose" aria-label="Close Radio dialog">&times;</button>
      <div class="config-title" id="radioTitle">Radio Configuration</div>
      <form id="radioForm">
        <div class="config-fields" id="radioFields">
          <label>Loading</label><input type="text" value="" disabled>
        </div>
        <div class="config-actions">
          <button type="submit" id="radioOkButton">OK</button>
          <button type="button" id="radioCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="radioMessage"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="presetModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="presetTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="presetClose" aria-label="Close Create Preset dialog">&times;</button>
      <div class="config-title" id="presetTitle">Create Preset</div>
      <form id="presetForm">
        <div class="config-fields" id="presetFields">
          <label>Loading</label><input type="text" value="" disabled>
        </div>
        <div class="config-actions">
          <button type="submit" id="presetOkButton">OK</button>
          <button type="button" id="presetCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="presetMessage"></div>
      </form>
    </div>
  </div>

  <div class="console">
    <aside class="left panel">
      <div class="clock-block">
        <div id="utcTime">--:--:-- UTC</div>
        <div id="utcDate">-- --- ----</div>
        <div><span id="countdown">--:--:--</span> <span id="countdownLabel">AOS</span></div>
      </div>

      <div class="gauge-wrap">
        <div class="gauge-title">Azimuth</div>
        <svg class="svg-gauge az-gauge" viewBox="0 0 160 160" width="160" height="160" aria-label="Azimuth gauge">
          <circle class="gauge-ring" cx="80" cy="80" r="58"></circle>
          <g id="azTickMarks"></g>
          <text class="gauge-label" x="85" y="13">0°</text>
          <text class="gauge-label" x="155" y="82">90°</text>
          <text class="gauge-label" x="80" y="150">180°</text>
          <text class="gauge-label" x="3" y="82">270°</text>
          <g id="azNeedle" transform="rotate(0 80 80)">
            <!-- Boom -->
            <line class="gauge-boom" x1="80" y1="80" x2="80" y2="50"></line>
            <!-- Reflector (longest) -->
            <line class="gauge-needle" x1="72" y1="72" x2="88" y2="72"></line>
            <!-- Driven element -->
            <line class="gauge-needle" x1="74" y1="63" x2="86" y2="63"></line>
            <!-- Directors (tapered) -->
            <line class="gauge-needle" x1="75" y1="55" x2="85" y2="55"></line>
            <line class="gauge-needle" x1="76" y1="48" x2="84" y2="48"></line>
          </g>
          <circle class="gauge-hub" cx="80" cy="80" r="7"></circle>
        </svg>
        <div class="label">Az&nbsp;&nbsp;<span id="azValue">0</span></div>
      </div>

      <div class="gauge-wrap">
        <div class="gauge-title">Elevation</div>
<!-- BQE_WISP_ELEVATION_LABELS_30_60_BUILD_V2 -->
        <svg class="svg-gauge el-gauge" viewBox="0 0 160 110" width="160" height="110" aria-label="Elevation gauge">
          <path class="gauge-arc" d="M 138 88 A 58 58 0 0 0 80 30"></path>
          <g id="elTickMarks"></g>
          <!-- Elevation labels: intermediate markings are 30 and 60 degrees, outside the arc. -->
          <text class="gauge-label" x="152" y="88">0°</text>
          <text class="gauge-label" x="148" y="57">30°</text>
          <text class="gauge-label" x="117" y="25">60°</text>
          <text class="gauge-label" x="83" y="17">90°</text>
          <g id="elNeedle" transform="rotate(0 80 88)">
            <!-- Yagi boom: part of the pointer graphic, not a separate dynamic needle line. -->
            <line class="gauge-boom" x1="80" y1="88" x2="118" y2="88"></line>
            <!-- Reflector -->
            <line class="gauge-needle" x1="88" y1="80" x2="88" y2="96"></line>
            <!-- Driven -->
            <line class="gauge-needle" x1="98" y1="82" x2="98" y2="94"></line>
            <!-- Directors -->
            <line class="gauge-needle" x1="108" y1="83" x2="108" y2="93"></line>
            <line class="gauge-needle" x1="116" y1="84" x2="116" y2="92"></line>
          </g>
          <circle class="gauge-hub" cx="80" cy="88" r="7"></circle>
        </svg>
        <div class="label">El&nbsp;&nbsp;<span id="elValue">0</span></div>
      </div>

      <div class="radio-readouts">
        <div class="radio-box wide">
          <span class="box-title">UPLINK MHz</span>
          <span class="box-value" id="uplinkFrequency">--</span>
        </div>
        <div class="radio-box mode">
          <span class="box-title">UP MODE</span>
          <span class="box-value" id="uplinkMode">--</span>
        </div>
        <div class="radio-box wide">
          <span class="box-title">DOWNLINK MHz</span>
          <span class="box-value" id="downlinkFrequency">--</span>
        </div>
        <div class="radio-box mode">
          <span class="box-title">DN MODE</span>
          <span class="box-value" id="downlinkMode">--</span>
        </div>
      </div>
    </aside>

    <main class="schedule panel">
      <table>
        <thead>
          <tr><th>Satellite</th><th>Type</th><th>El</th><th>Start Time</th><th>Finish Time</th></tr>
        </thead>
        <tbody id="passRows"><tr><td colspan="5">Loading...</td></tr></tbody>
      </table>
    </main>

    <aside class="preset-panel panel" id="presetPanel" aria-label="Radio presets" aria-hidden="true">
      <div class="preset-title">Radio Presets</div>
      <div class="preset-buttons" id="presetButtons">
        <div class="preset-empty">Loading presets...</div>
      </div>
    </aside>

    <aside class="map-panel panel" id="mapPanel" aria-label="Observer, satellite, Sun, and Moon map" aria-hidden="true" style="display: none;">
      <div class="map-title">Earth Map</div>
      <div id="earthMap" role="img" aria-label="OpenStreetMap view of observer, satellite, Sun, and Moon positions"></div>
      <div class="map-readouts">
        <div>Observer</div><div class="map-value" id="observerMapPosition">--</div>
        <div>Satellite</div><div class="map-value" id="satelliteMapPosition">--</div>
        <div>Sun Az / El</div><div class="map-value" id="sunMapAzEl">--</div>
        <div>Moon Az / El</div><div class="map-value" id="moonMapAzEl">--</div>
      </div>
      <div class="map-status" id="mapStatus">Waiting for map data...</div>
    </aside>

    <section class="statusbar panel">
      <div class="status-grid status-head">
        <div>Satellite</div><div>Azm</div><div>El</div><div>Range</div><div>Doppler</div>
      </div>
      <div class="status-grid">
        <div id="detailSatellite">--</div><div id="detailAz">--</div><div id="detailEl">--</div><div id="detailRange">--</div><div id="detailDoppler">--</div>
      </div>
      <div class="message" id="message">Starting up...</div>
    </section>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    function text(value) { return value === null || value === undefined ? "" : String(value); }
    function readout(value, fallback='--') {
      return value === null || value === undefined || value === '' ? fallback : String(value);
    }
    function frequencyMHzReadout(valueHz, fallback='--') {
      const hz = Number(valueHz);
      if (Number.isFinite(hz) && hz > 0) { return (hz / 1e06).toFixed(6); }
      return fallback;
    }
    function set(id, value) { document.getElementById(id).textContent = value; }

    function clearMenuButtonRestoreStyle(button) {
      if (!button) { return; }
      button.classList.remove('menu-click-flash');
      button.style.background = '';
      button.style.color = '';
      button.style.boxShadow = '';
    }

    function menuButtonRestoreBackground(button) {
      return button.classList.contains('menu-button') ? '#d2d2d2' : '#d5d5d5';
    }

    function flashMenuButton(button) {
      if (!button || button.classList.contains('menu-command-running')) { return; }
      window.clearTimeout(button.bqeMenuFlashTimer);
      clearMenuButtonRestoreStyle(button);
      // Force the animation to restart when the same menu item is clicked repeatedly.
      void button.offsetWidth;
      button.classList.add('menu-click-flash');
      button.bqeMenuFlashTimer = window.setTimeout(() => {
        button.classList.remove('menu-click-flash');
        // Keep an inline grey restore color until the pointer leaves, so a
        // lingering hover/focus state does not turn the clicked item dark blue.
        button.style.background = menuButtonRestoreBackground(button);
        button.style.color = '#111';
        button.style.boxShadow = 'none';
        button.blur();
      }, 1000);
    }

    document.addEventListener('click', (event) => {
      const button = event.target.closest('.menu-button, .submenu button');
      const taskbar = document.querySelector('.taskbar');
      if (!button || !taskbar || !taskbar.contains(button)) { return; }
      flashMenuButton(button);
    });

    document.addEventListener('pointerleave', (event) => {
      const button = event.target.closest('.menu-button, .submenu button');
      const taskbar = document.querySelector('.taskbar');
      if (!button || !taskbar || !taskbar.contains(button)) { return; }
      clearMenuButtonRestoreStyle(button);
    }, true);

    function polarPoint(cx, cy, radius, degreesFromNorth) {
      const radians = (degreesFromNorth - 90) * Math.PI / 180.0;
      return {
        x: cx + radius * Math.cos(radians),
        y: cy + radius * Math.sin(radians)
      };
    }

    function makeTickMarks(groupId, cx, cy, outerRadius, innerMajor, innerMinor, startDeg, endDeg, stepDeg) {
      const group = document.getElementById(groupId);
      if (!group || group.childNodes.length) { return; }
      for (let deg = startDeg; deg <= endDeg; deg += stepDeg) {
        const outer = polarPoint(cx, cy, outerRadius, deg);
        const major = (deg % 30 === 0);
        const inner = polarPoint(cx, cy, major ? innerMajor : innerMinor, deg);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', inner.x.toFixed(2));
        line.setAttribute('y1', inner.y.toFixed(2));
        line.setAttribute('x2', outer.x.toFixed(2));
        line.setAttribute('y2', outer.y.toFixed(2));
        line.setAttribute('class', major ? 'gauge-major' : 'gauge-minor');
        group.appendChild(line);
      }
    }

    function setElevationNeedle(el) {
      const clamped = Math.max(0, Math.min(90, el));
      // Elevation is drawn as a right-side quarter circle:
      // 0 degrees = horizontal right, 90 degrees = straight up.
      // Rotate the complete Yagi pointer group.  Do not draw/update a separate
      // needle line, or the old pointer visually reappears on top of the Yagi.
      document.getElementById('elNeedle').setAttribute('transform', `rotate(${-clamped} 80 88)`);
    }

    function addCell(row, value) {
      const td = document.createElement('td');
      td.textContent = text(value);
      row.appendChild(td);
    }

    function resetPassListScroll() {
      const schedule = document.querySelector('.schedule');
      if (!schedule) { return; }
      requestAnimationFrame(() => {
        schedule.scrollLeft = 0;
      });
    }


    let earthMap = null;
    let observerMarker = null;
    let satelliteMarker = null;
    let sunMarker = null;
    let moonMarker = null;
    let nightSideOverlay = null;
    let dayNightTerminator = null;
    let satelliteCoverageCircle = null;
    let satelliteGroundTrack = null;
    let groundTrackSegments = [];
    let groundTrackSatellite = null;
    let groundTrackPassActive = false;
    let groundTrackPointCount = 0;
    let observerToSatelliteLine = null;
    let mapAutoFitDone = false;
    let mapVisible = false;
    let presetsVisible = false;
    let lastMapData = null;
    let lastMapStatus = null;
    const detachedMapMode = new URLSearchParams(window.location.search).get('detached_map') === '1';
    let detachedMapWindow = null;
    let detachedMapCloseMonitor = null;
    let detachedMapResizeTimer = null;
    let presetCommandInFlight = false;
    let lastPresetRenderSignature = null;

    function setPresetButtonsDisabled(disabled) {
      document.querySelectorAll('#presetButtons .preset-button').forEach((button) => {
        button.disabled = Boolean(disabled);
      });
    }

    function flashPresetButton(button) {
      if (!button) { return; }
      window.clearTimeout(button.bqePresetFlashTimer);
      button.classList.remove('menu-click-flash');
      // Force the animation to restart when the same preset is selected again.
      void button.offsetWidth;
      button.classList.add('menu-click-flash');
      button.bqePresetFlashTimer = window.setTimeout(() => {
        button.classList.remove('menu-click-flash');
        button.blur();
      }, 1000);
    }

    async function programPreset(nickname, button) {
      if (presetCommandInFlight) { return; }
      flashPresetButton(button);
      presetCommandInFlight = true;
      setPresetButtonsDisabled(true);
      set('message', `Programming radio preset ${nickname}...`);
      try {
        const response = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ action: 'program_preset', nickname })
        });
        const result = await response.json();
        set('message', result.message || `Preset ${nickname} command returned with no message.`);
      } catch (err) {
        set('message', `Preset ${nickname} command failed: ${err}`);
      } finally {
        presetCommandInFlight = false;
        refreshStatus();
      }
    }

    function renderPresetButtons(presets, enabled) {
      const container = document.getElementById('presetButtons');
      if (!container) { return; }

      const validPresets = Array.isArray(presets)
        ? presets.filter((preset) => preset && text(preset.nickname).trim())
        : [];
      const buttonsDisabled = !Boolean(enabled) || presetCommandInFlight;
      // Rebuild only when the startup preset list changes.  Enabled/disabled state
      // must still be applied on every status refresh; otherwise buttons that were
      // disabled for a command can remain grey until the browser is reloaded.
      const renderSignature = JSON.stringify(
        validPresets.map((preset) => [
          text(preset.nickname).trim(),
          text(preset.channel_name).trim()
        ])
      );
      if (renderSignature === lastPresetRenderSignature) {
        setPresetButtonsDisabled(buttonsDisabled);
        return;
      }
      lastPresetRenderSignature = renderSignature;
      container.innerHTML = '';
      if (!validPresets.length) {
        const empty = document.createElement('div');
        empty.className = 'preset-empty';
        empty.textContent = 'No preset YAML files with nicknames were found at startup.';
        container.appendChild(empty);
        return;
      }

      for (const preset of validPresets) {
        const nickname = text(preset.nickname).trim();
        const channelName = text(preset.channel_name).trim() || nickname;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'preset-button';
        button.textContent = channelName;
        button.title = `Program radio preset: ${nickname}`;
        button.disabled = buttonsDisabled;
        button.addEventListener('click', () => programPreset(nickname, button));
        container.appendChild(button);
      }
    }

    function numberFrom(value) {
      if (value === null || value === undefined || value === '') { return NaN; }
      const direct = Number(value);
      if (Number.isFinite(direct)) { return direct; }
      const textValue = String(value).replace(/,/g, '').trim();
      const match = textValue.match(/[-+]?\d+(?:\.\d+)?/);
      if (!match) { return NaN; }
      let number = Number(match[0]);
      const hemisphere = textValue.match(/(?:^|[^A-Za-z])([NSEW])(?:[^A-Za-z]|$)/i);
      if (hemisphere) {
        const h = hemisphere[1].toUpperCase();
        if (h === 'S' || h === 'W') { number = -Math.abs(number); }
        if (h === 'N' || h === 'E') { number = Math.abs(number); }
      }
      return number;
    }

    function firstFinite() {
      for (const value of arguments) {
        const number = numberFrom(value);
        if (Number.isFinite(number)) { return number; }
      }
      return NaN;
    }

    function objectFrom(value) {
      return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    }

    function normalizeLongitude(lon) {
      if (!Number.isFinite(lon)) { return lon; }
      return ((((lon + 180) % 360) + 360) % 360) - 180;
    }

    function validLatLon(lat, lon) {
      return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90;
    }

    function latLonText(lat, lon) {
      if (!validLatLon(lat, lon)) { return '--'; }
      return `${lat.toFixed(4)}, ${normalizeLongitude(lon).toFixed(4)}`;
    }

    function azElText(azimuth, elevation) {
      if (!Number.isFinite(azimuth) || !Number.isFinite(elevation)) { return '--'; }
      const normalizedAzimuth = ((azimuth % 360) + 360) % 360;
      return `Az ${normalizedAzimuth.toFixed(1)}° / El ${elevation.toFixed(1)}°`;
    }

    function dayNightTerminatorPoints(subsolarLatitude, subsolarLongitude) {
      if (!validLatLon(subsolarLatitude, subsolarLongitude)) { return []; }
      const degreesToRadians = Math.PI / 180.0;
      const radiansToDegrees = 180.0 / Math.PI;
      const declination = subsolarLatitude * degreesToRadians;
      const points = [];

      // At each longitude, solve for the latitude where the Sun is exactly
      // on the geometric horizon:
      //   sin(lat) sin(dec) + cos(lat) cos(dec) cos(hour-angle) = 0
      // atan2 remains stable near the equinox, when the curve approaches a
      // pair of pole-to-pole lines.
      for (let longitude = -180; longitude <= 180; longitude += 2) {
        const hourAngle = normalizeLongitude(
          longitude - subsolarLongitude
        ) * degreesToRadians;
        const latitude = Math.atan2(
          -Math.cos(declination) * Math.cos(hourAngle),
          Math.sin(declination)
        ) * radiansToDegrees;
        points.push([Math.max(-90, Math.min(90, latitude)), longitude]);
      }
      return points;
    }

    function updateNightSideOverlay(subsolarLatitude, subsolarLongitude) {
      if (!earthMap || !nightSideOverlay || !dayNightTerminator) { return; }
      const terminatorPoints = dayNightTerminatorPoints(
        subsolarLatitude,
        subsolarLongitude
      );

      if (terminatorPoints.length < 2) {
        if (earthMap.hasLayer(nightSideOverlay)) {
          earthMap.removeLayer(nightSideOverlay);
        }
        if (earthMap.hasLayer(dayNightTerminator)) {
          earthMap.removeLayer(dayNightTerminator);
        }
        return;
      }

      // When the Sun is north of the equator, the South Pole is always on
      // the night side; when it is south, the North Pole is on the night side.
      // Closing the polygon through that pole shades the hemisphere opposite
      // the Sun while keeping the daylight hemisphere clear.
      const nightPole = subsolarLatitude >= 0 ? -90 : 90;
      const nightPolygonPoints = terminatorPoints.concat([
        [nightPole, 180],
        [nightPole, -180]
      ]);

      nightSideOverlay.setLatLngs(nightPolygonPoints);
      dayNightTerminator.setLatLngs(terminatorPoints);
      if (!earthMap.hasLayer(nightSideOverlay)) {
        nightSideOverlay.addTo(earthMap);
      }
      if (!earthMap.hasLayer(dayNightTerminator)) {
        dayNightTerminator.addTo(earthMap);
      }
    }

    function initializeEarthMap() {
      const statusElement = document.getElementById('mapStatus');
      if (earthMap) { return true; }
      if (typeof L === 'undefined') {
        if (statusElement) {
          statusElement.textContent = 'Map library unavailable; check network access to Leaflet/OpenStreetMap.';
        }
        return false;
      }

      earthMap = L.map('earthMap', {
        worldCopyJump: true,
        zoomControl: true
      }).setView([20, 0], 2);

      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        minZoom: 2,
        maxZoom: 7,
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(earthMap);

      // Keep the satellite graphic above the predicted ground-track pane.
      earthMap.createPane('satelliteMarkerPane');
      earthMap.getPane('satelliteMarkerPane').style.zIndex = 650;
      earthMap.createPane('celestialMarkerPane');
      earthMap.getPane('celestialMarkerPane').style.zIndex = 640;
      earthMap.createPane('nightOverlayPane');
      earthMap.getPane('nightOverlayPane').style.zIndex = 350;
      earthMap.getPane('nightOverlayPane').style.pointerEvents = 'none';

      // Keep the night shading above the map tiles but below every existing
      // marker, coverage circle, pointer line, and ground-track graphic.
      nightSideOverlay = L.polygon([], {
        pane: 'nightOverlayPane',
        stroke: false,
        fillColor: '#071426',
        fillOpacity: 0.42,
        fillRule: 'evenodd',
        interactive: false
      });
      dayNightTerminator = L.polyline([], {
        pane: 'nightOverlayPane',
        weight: 1.25,
        color: '#8797b3',
        opacity: 0.7,
        interactive: false
      });

      observerMarker = L.circleMarker([0, 0], {
        radius: 8,
        weight: 2,
        color: '#008800',
        fillColor: '#7cff5b',
        fillOpacity: 0.9
      }).bindPopup('Observer');

      const satelliteIcon = L.divIcon({
        className: '',
        iconSize: [42, 30],
        iconAnchor: [21, 15],
        popupAnchor: [0, -17],
        html: `
          <svg width="42" height="30" viewBox="0 0 42 30"
               xmlns="http://www.w3.org/2000/svg"
               role="img" aria-label="Satellite">
            <g stroke="#222" stroke-width="1.5" stroke-linejoin="round">
              <rect x="1" y="9" width="13" height="12" rx="1.5" fill="#3478c7"/>
              <path d="M5.3 9v12M9.7 9v12M1 15h13" stroke="#b9dcff" stroke-width="1"/>
              <rect x="28" y="9" width="13" height="12" rx="1.5" fill="#3478c7"/>
              <path d="M32.3 9v12M36.7 9v12M28 15h13" stroke="#b9dcff" stroke-width="1"/>
              <path d="M14 15h4M24 15h4" fill="none"/>
              <rect x="18" y="7" width="6" height="16" rx="2" fill="#ffd84a"/>
              <circle cx="21" cy="12" r="2" fill="#fff4a3"/>
              <path d="M21 7V3M18 3h6" fill="none"/>
              <path d="M19 23l-3 5M23 23l3 5" fill="none"/>
            </g>
          </svg>`
      });
      satelliteMarker = L.marker([0, 0], {
        icon: satelliteIcon,
        pane: 'satelliteMarkerPane',
        keyboard: false
      }).bindPopup('Satellite');

      const sunIcon = L.divIcon({
        className: '',
        iconSize: [38, 38],
        iconAnchor: [19, 19],
        popupAnchor: [0, -20],
        html: `
          <svg width="38" height="38" viewBox="0 0 38 38"
               xmlns="http://www.w3.org/2000/svg"
               role="img" aria-label="Sun">
            <g stroke="#b56b00" stroke-width="2.2" stroke-linecap="round">
              <path d="M19 1v5M19 32v5M1 19h5M32 19h5"/>
              <path d="M6.3 6.3l3.5 3.5M28.2 28.2l3.5 3.5M31.7 6.3l-3.5 3.5M9.8 28.2l-3.5 3.5"/>
            </g>
            <circle cx="19" cy="19" r="10.5" fill="#ffd83d" stroke="#9b5b00" stroke-width="1.8"/>
            <circle cx="15.5" cy="15.5" r="3.2" fill="#fff5a8" opacity=".75"/>
          </svg>`
      });
      sunMarker = L.marker([0, 0], {
        icon: sunIcon,
        pane: 'celestialMarkerPane',
        keyboard: false
      }).bindPopup('Sun');

      const moonIcon = L.divIcon({
        className: '',
        iconSize: [34, 38],
        iconAnchor: [17, 19],
        popupAnchor: [0, -20],
        html: `
          <svg width="34" height="38" viewBox="0 0 34 38"
               xmlns="http://www.w3.org/2000/svg"
               role="img" aria-label="Moon">
            <path d="M25.5 3.4A15.8 15.8 0 1 0 25.5 34.6A13 13 0 0 1 25.5 3.4Z"
                  fill="#f2f0d8" stroke="#4b5362" stroke-width="1.8"/>
            <circle cx="11.5" cy="14" r="2.1" fill="#c7c5b3"/>
            <circle cx="15" cy="25" r="2.8" fill="#d4d2be"/>
            <circle cx="9" cy="22" r="1.3" fill="#bbb9aa"/>
          </svg>`
      });
      moonMarker = L.marker([0, 0], {
        icon: moonIcon,
        pane: 'celestialMarkerPane',
        keyboard: false
      }).bindPopup('Moon');

      satelliteCoverageCircle = L.circle([0, 0], {
        radius: 0,
        weight: 2,
        color: '#d4a900',
        opacity: 0.75,
        fillColor: '#ffe45c',
        fillOpacity: 0.18,
        interactive: false
      });

      earthMap.createPane('satelliteGroundTrackPane');
      earthMap.getPane('satelliteGroundTrackPane').style.zIndex = 550;
      earthMap.getPane('satelliteGroundTrackPane').style.pointerEvents = 'none';
      satelliteGroundTrack = L.layerGroup();

      observerToSatelliteLine = L.polyline([], {
        weight: 2,
        opacity: 0.7,
        dashArray: '6 5'
      });

      requestAnimationFrame(() => earthMap.invalidateSize());
      return true;
    }

    function estimateSatelliteSubpoint(obsLat, obsLon, azDeg, elDeg, rangeKm) {
      if (!validLatLon(obsLat, obsLon) ||
          !Number.isFinite(azDeg) ||
          !Number.isFinite(elDeg) ||
          !Number.isFinite(rangeKm) ||
          rangeKm <= 0) {
        return null;
      }

      // Spherical-Earth fallback.  It is good enough for a map marker when the
      // tracker reports Az/El/Range but not an explicit satellite subpoint.
      const earthRadiusKm = 6371.0;
      const degToRad = Math.PI / 180.0;
      const radToDeg = 180.0 / Math.PI;
      const lat = obsLat * degToRad;
      const lon = obsLon * degToRad;
      const az = azDeg * degToRad;
      const el = elDeg * degToRad;

      const eastComponent = rangeKm * Math.cos(el) * Math.sin(az);
      const northComponent = rangeKm * Math.cos(el) * Math.cos(az);
      const upComponent = rangeKm * Math.sin(el);

      const cosLat = Math.cos(lat);
      const sinLat = Math.sin(lat);
      const cosLon = Math.cos(lon);
      const sinLon = Math.sin(lon);

      const observerX = earthRadiusKm * cosLat * cosLon;
      const observerY = earthRadiusKm * cosLat * sinLon;
      const observerZ = earthRadiusKm * sinLat;

      const eastX = -sinLon;
      const eastY = cosLon;
      const eastZ = 0;

      const northX = -sinLat * cosLon;
      const northY = -sinLat * sinLon;
      const northZ = cosLat;

      const upX = cosLat * cosLon;
      const upY = cosLat * sinLon;
      const upZ = sinLat;

      const satX = observerX + eastComponent * eastX + northComponent * northX + upComponent * upX;
      const satY = observerY + eastComponent * eastY + northComponent * northY + upComponent * upY;
      const satZ = observerZ + eastComponent * eastZ + northComponent * northZ + upComponent * upZ;

      const projectedLat = Math.atan2(satZ, Math.sqrt(satX * satX + satY * satY)) * radToDeg;
      const projectedLon = normalizeLongitude(Math.atan2(satY, satX) * radToDeg);
      return { lat: projectedLat, lon: projectedLon };
    }

    function satelliteCoverageRadiusMeters(altitudeKm) {
      if (!Number.isFinite(altitudeKm) || altitudeKm <= 0) { return NaN; }
      const earthRadiusKm = 6371.0;
      // Great-circle distance from the subpoint to the geometric horizon.
      return earthRadiusKm * Math.acos(earthRadiusKm / (earthRadiusKm + altitudeKm)) * 1000.0;
    }

    function clearSatelliteGroundTrack() {
      groundTrackSegments = [];
      groundTrackSatellite = null;
      groundTrackPointCount = 0;
      if (satelliteGroundTrack) {
        satelliteGroundTrack.clearLayers();
        if (earthMap && earthMap.hasLayer(satelliteGroundTrack)) {
          earthMap.removeLayer(satelliteGroundTrack);
        }
      }
    }

    function renderSatelliteGroundTrack(segments) {
      satelliteGroundTrack.clearLayers();
      groundTrackPointCount = 0;

      for (const segment of segments) {
        if (!Array.isArray(segment) || segment.length < 2) { continue; }
        groundTrackPointCount += segment.length;

        // A dark halo keeps the path visible over both pale and dark map tiles.
        L.polyline(segment, {
          pane: 'satelliteGroundTrackPane',
          weight: 7,
          color: '#202020',
          opacity: 0.85,
          interactive: false
        }).addTo(satelliteGroundTrack);
        L.polyline(segment, {
          pane: 'satelliteGroundTrackPane',
          weight: 4,
          color: '#ff6a00',
          opacity: 1.0,
          interactive: false
        }).addTo(satelliteGroundTrack);
      }

      if (groundTrackPointCount >= 2 && !earthMap.hasLayer(satelliteGroundTrack)) {
        satelliteGroundTrack.addTo(earthMap);
      }
    }

    function groundTrackSegmentsFromPoints(points) {
      const segments = [];
      let segment = [];
      let previous = null;

      for (const rawPoint of points) {
        if (!Array.isArray(rawPoint) || rawPoint.length < 2) { continue; }
        const point = [numberFrom(rawPoint[0]), normalizeLongitude(numberFrom(rawPoint[1]))];
        if (!validLatLon(point[0], point[1])) { continue; }
        if (previous && Math.abs(point[1] - previous[1]) > 180) {
          if (segment.length) { segments.push(segment); }
          segment = [];
        }
        segment.push(point);
        previous = point;
      }
      if (segment.length) { segments.push(segment); }
      return segments;
    }

    function updateSatelliteGroundTrack(passActive, satelliteName, satLat, satLon, predictedPoints) {
      if (!passActive) {
        if (groundTrackPassActive || groundTrackSegments.length) {
          clearSatelliteGroundTrack();
        }
        groundTrackPassActive = false;
        return;
      }

      const trackName = text(satelliteName).trim();
      if (!groundTrackPassActive ||
          (trackName && groundTrackSatellite && trackName !== groundTrackSatellite)) {
        clearSatelliteGroundTrack();
      }
      groundTrackPassActive = true;
      if (trackName) { groundTrackSatellite = trackName; }

      const predictedSegments = Array.isArray(predictedPoints)
        ? groundTrackSegmentsFromPoints(predictedPoints)
        : [];
      if (predictedSegments.some((segment) => segment.length >= 2)) {
        groundTrackSegments = predictedSegments;
        renderSatelliteGroundTrack(groundTrackSegments);
        return;
      }

      if (!validLatLon(satLat, satLon)) { return; }
      const point = [satLat, normalizeLongitude(satLon)];
      let segment = groundTrackSegments[groundTrackSegments.length - 1];
      const previous = segment && segment[segment.length - 1];

      // Start a new segment at the Date Line so Leaflet does not draw a false
      // line across the full width of the world map.
      if (!segment || (previous && Math.abs(point[1] - previous[1]) > 180)) {
        segment = [];
        groundTrackSegments.push(segment);
      }

      if (!previous ||
          Math.abs(point[0] - previous[0]) > 0.0001 ||
          Math.abs(point[1] - previous[1]) > 0.0001) {
        segment.push(point);
      }

      renderSatelliteGroundTrack(groundTrackSegments);
    }

    function updateEarthMap(data, status) {
      const observer = objectFrom(data.observer_location || {});
      const celestialPositions = objectFrom(
        data.celestial_positions || status.celestial_positions || {}
      );
      const sunPosition = objectFrom(celestialPositions.sun);
      const moonPosition = objectFrom(celestialPositions.moon);
      const statusObserver = objectFrom(status.observer || status.qth || status.station || status.home || status.location);
      const statusSatellite = objectFrom(
        status.satellite_position ||
        status.sat_position ||
        status.satellite_subpoint ||
        status.subsatellite ||
        status.subpoint ||
        status.ground_track ||
        status.satellite_geo ||
        status.sat_geo
      );
      const obsLat = firstFinite(
        observer.latitude,
        observer.lat,
        observer.latitude_deg,
        observer.latitude_degrees,
        statusObserver.latitude,
        statusObserver.lat,
        statusObserver.latitude_deg,
        statusObserver.latitude_degrees,
        status.observer_latitude,
        status.observer_lat,
        status.observer_latitude_deg,
        status.observer_lat_deg,
        status.qth_latitude,
        status.qth_lat,
        status.qth_latitude_deg,
        status.qth_lat_deg,
        status.station_latitude,
        status.station_lat
      );
      const obsLon = normalizeLongitude(firstFinite(
        observer.longitude,
        observer.lon,
        observer.lng,
        observer.longitude_deg,
        observer.longitude_degrees,
        statusObserver.longitude,
        statusObserver.lon,
        statusObserver.lng,
        statusObserver.longitude_deg,
        statusObserver.longitude_degrees,
        status.observer_longitude,
        status.observer_lon,
        status.observer_lng,
        status.observer_longitude_deg,
        status.observer_lon_deg,
        status.observer_lng_deg,
        status.qth_longitude,
        status.qth_lon,
        status.qth_lng,
        status.qth_longitude_deg,
        status.qth_lon_deg,
        status.station_longitude,
        status.station_lon
      ));
      let satLat = firstFinite(
        statusSatellite.latitude,
        statusSatellite.lat,
        statusSatellite.latitude_deg,
        statusSatellite.latitude_degrees,
        status.satellite_latitude,
        status.sat_latitude,
        status.satellite_lat,
        status.sat_lat,
        status.satellite_latitude_deg,
        status.satellite_lat_deg,
        status.sat_latitude_deg,
        status.sat_lat_deg,
        status.subsatellite_latitude,
        status.subsatellite_lat,
        status.subsatellite_latitude_deg,
        status.subsatellite_lat_deg,
        status.subpoint_latitude,
        status.subpoint_lat,
        status.subpoint_latitude_deg,
        status.subpoint_lat_deg,
        status.ground_track_latitude,
        status.ground_track_lat,
        status.ground_track_latitude_deg,
        status.ground_track_lat_deg,
        status.latitude,
        status.lat,
        status.latitude_deg,
        status.latitude_degrees
      );
      let satLon = normalizeLongitude(firstFinite(
        statusSatellite.longitude,
        statusSatellite.lon,
        statusSatellite.lng,
        statusSatellite.longitude_deg,
        statusSatellite.longitude_degrees,
        status.satellite_longitude,
        status.sat_longitude,
        status.satellite_lon,
        status.sat_lon,
        status.satellite_lng,
        status.sat_lng,
        status.satellite_longitude_deg,
        status.satellite_lon_deg,
        status.satellite_lng_deg,
        status.sat_longitude_deg,
        status.sat_lon_deg,
        status.sat_lng_deg,
        status.subsatellite_longitude,
        status.subsatellite_lon,
        status.subsatellite_lng,
        status.subsatellite_longitude_deg,
        status.subsatellite_lon_deg,
        status.subpoint_longitude,
        status.subpoint_lon,
        status.subpoint_lng,
        status.subpoint_longitude_deg,
        status.subpoint_lon_deg,
        status.ground_track_longitude,
        status.ground_track_lon,
        status.ground_track_lng,
        status.ground_track_longitude_deg,
        status.ground_track_lon_deg,
        status.longitude,
        status.lon,
        status.lng,
        status.longitude_deg,
        status.longitude_degrees
      ));

      const hasObserver = validLatLon(obsLat, obsLon);
      let satelliteFromDerivedSubpoint = false;
      if (!validLatLon(satLat, satLon) && hasObserver) {
        const rangeKm = firstFinite(
          status.range_km,
          status.slant_range_km,
          status.satellite_range_km,
          status.distance_km,
          status.range,
          status.slant_range,
          status.satellite_range,
          status.distance
        );
        const rangeMeters = firstFinite(status.range_m, status.slant_range_m, status.satellite_range_m, status.distance_m);
        const derived = estimateSatelliteSubpoint(
          obsLat,
          obsLon,
          firstFinite(status.azimuth, status.az, status.azm, status.azimuth_deg, status.az_deg),
          firstFinite(status.elevation, status.el, status.elevation_deg, status.el_deg),
          Number.isFinite(rangeKm) ? rangeKm : (Number.isFinite(rangeMeters) ? rangeMeters / 1000.0 : NaN)
        );
        if (derived && validLatLon(derived.lat, derived.lon)) {
          satLat = derived.lat;
          satLon = derived.lon;
          satelliteFromDerivedSubpoint = true;
        }
      }

      const hasSatellite = validLatLon(satLat, satLon);
      const sunLat = firstFinite(sunPosition.latitude, sunPosition.lat);
      const sunLon = normalizeLongitude(firstFinite(
        sunPosition.longitude, sunPosition.lon, sunPosition.lng
      ));
      const sunAzimuth = firstFinite(sunPosition.azimuth, sunPosition.az);
      const sunElevation = firstFinite(sunPosition.elevation, sunPosition.el);
      const moonLat = firstFinite(moonPosition.latitude, moonPosition.lat);
      const moonLon = normalizeLongitude(firstFinite(
        moonPosition.longitude, moonPosition.lon, moonPosition.lng
      ));
      const moonAzimuth = firstFinite(moonPosition.azimuth, moonPosition.az);
      const moonElevation = firstFinite(moonPosition.elevation, moonPosition.el);
      const hasSun = validLatLon(sunLat, sunLon);
      const hasMoon = validLatLon(moonLat, moonLon);
      const satelliteAltitudeKm = firstFinite(
        status.satellite_altitude_km,
        status.satellite_height_km,
        status.altitude_km,
        status.height_km
      );
      const coverageRadiusMeters = satelliteCoverageRadiusMeters(satelliteAltitudeKm);
      const passActive = Boolean(data.pass_active || data.tracking_running);
      const satelliteLabel = readout(status.satellite || data.current_satellite, 'Satellite');
      set('observerMapPosition', latLonText(obsLat, obsLon));
      set('satelliteMapPosition', latLonText(satLat, satLon));
      set('sunMapAzEl', azElText(sunAzimuth, sunElevation));
      set('moonMapAzEl', azElText(moonAzimuth, moonElevation));

      if (!initializeEarthMap()) { return; }
      updateNightSideOverlay(sunLat, sunLon);

      if (hasObserver) {
        const observerLabel = readout(observer.label, 'Observer');
        observerMarker.setLatLng([obsLat, obsLon]);
        observerMarker.bindPopup(`${observerLabel}<br>${latLonText(obsLat, obsLon)}`);
        if (!earthMap.hasLayer(observerMarker)) { observerMarker.addTo(earthMap); }
      } else if (earthMap.hasLayer(observerMarker)) {
        earthMap.removeLayer(observerMarker);
      }

      if (hasSatellite) {
        satelliteMarker.setLatLng([satLat, satLon]);
        satelliteMarker.bindPopup(`${satelliteLabel}<br>${latLonText(satLat, satLon)}`);
        if (!earthMap.hasLayer(satelliteMarker)) { satelliteMarker.addTo(earthMap); }
      } else if (earthMap.hasLayer(satelliteMarker)) {
        earthMap.removeLayer(satelliteMarker);
      }

      if (hasSun) {
        sunMarker.setLatLng([sunLat, sunLon]);
        sunMarker.bindPopup(
          `Sun<br>${latLonText(sunLat, sunLon)}<br>${azElText(sunAzimuth, sunElevation)}`
        );
        if (!earthMap.hasLayer(sunMarker)) { sunMarker.addTo(earthMap); }
      } else if (earthMap.hasLayer(sunMarker)) {
        earthMap.removeLayer(sunMarker);
      }

      if (hasMoon) {
        moonMarker.setLatLng([moonLat, moonLon]);
        moonMarker.bindPopup(
          `Moon<br>${latLonText(moonLat, moonLon)}<br>${azElText(moonAzimuth, moonElevation)}`
        );
        if (!earthMap.hasLayer(moonMarker)) { moonMarker.addTo(earthMap); }
      } else if (earthMap.hasLayer(moonMarker)) {
        earthMap.removeLayer(moonMarker);
      }

      if (passActive && hasSatellite && Number.isFinite(coverageRadiusMeters)) {
        satelliteCoverageCircle.setLatLng([satLat, satLon]);
        satelliteCoverageCircle.setRadius(coverageRadiusMeters);
        if (!earthMap.hasLayer(satelliteCoverageCircle)) {
          satelliteCoverageCircle.addTo(earthMap);
          satelliteCoverageCircle.bringToBack();
        }
      } else if (earthMap.hasLayer(satelliteCoverageCircle)) {
        earthMap.removeLayer(satelliteCoverageCircle);
      }

      updateSatelliteGroundTrack(
        passActive,
        satelliteLabel,
        satLat,
        satLon,
        status.predicted_ground_track
      );

      if (hasObserver && hasSatellite) {
        observerToSatelliteLine.setLatLngs([[obsLat, obsLon], [satLat, satLon]]);
        if (!earthMap.hasLayer(observerToSatelliteLine)) { observerToSatelliteLine.addTo(earthMap); }
        if (!mapAutoFitDone) {
          earthMap.fitBounds([[obsLat, obsLon], [satLat, satLon]], { padding: [28, 28], maxZoom: 3 });
          mapAutoFitDone = true;
        }
        set('mapStatus', satelliteFromDerivedSubpoint ? 'Showing observer and estimated satellite subpoint from Az/El/Range.' : 'Showing observer and satellite subpoint.');
      } else {
        if (earthMap.hasLayer(observerToSatelliteLine)) {
          earthMap.removeLayer(observerToSatelliteLine);
        }
        if (hasObserver && !mapAutoFitDone) {
          earthMap.setView([obsLat, obsLon], 3);
          mapAutoFitDone = true;
        }
        if (hasObserver) {
          set('mapStatus', 'Showing observer. Satellite will appear when the tracker reports satellite lat/lon or Az/El/Range.');
        } else if (hasSatellite) {
          set('mapStatus', 'Showing satellite. Observer position is missing from bqe_config/my_qth.yaml or the status JSON.');
        } else {
          set('mapStatus', 'Waiting for observer and satellite latitude/longitude data. Check /api/status for observer_location and tracking_status map fields.');
        }
      }

      if (passActive) {
        const reportedTrackCount = firstFinite(status.predicted_ground_track_count);
        const receivedTrackCount = Number.isFinite(reportedTrackCount)
          ? Math.round(reportedTrackCount)
          : groundTrackPointCount;
        const mapStatus = document.getElementById('mapStatus');
        if (mapStatus) {
          mapStatus.textContent += ` Ground track: ${receivedTrackCount} predicted point${receivedTrackCount === 1 ? '' : 's'} received.`;
        }
      }

    }

    function setSchedulePassesRunning(running) {
      const button = document.getElementById('schedulePassesMenuItem');
      if (!button) { return; }
      const isRunning = Boolean(running);
      window.clearTimeout(button.bqeMenuFlashTimer);
      button.classList.remove('menu-click-flash');
      button.classList.toggle('menu-command-running', isRunning);
      button.setAttribute('aria-busy', isRunning ? 'true' : 'false');
      button.disabled = isRunning;
      button.style.background = '';
      button.style.color = '';
      button.style.boxShadow = '';
      if (!isRunning) { button.blur(); }
    }

    async function refreshStatus() {
      try {
        const response = await fetch('/api/status', { cache: 'no-store' });
        const data = await response.json();
        if (data.shutdown_requested && !data.restart_requested) {
          showServerExitedMessage();
          return;
        }
        setSchedulePassesRunning(
          Boolean(data.command_running) && text(data.last_command).toLowerCase() === 'schedule_passes'
        );
        set('utcTime', data.utc_time);
        set('utcDate', data.utc_date);
        set('countdown', data.countdown[0]);
        set('countdownLabel', data.countdown[1]);

        const status = data.tracking_status || {};
        lastMapData = data;
        lastMapStatus = status;
        if (mapVisible && detachedMapMode) {
          updateEarthMap(data, status);
        }
        const az = Number(status.azimuth);
        const el = Number(status.elevation);
        makeTickMarks('azTickMarks', 80, 80, 58, 47, 51, 0, 350, 10);
        makeTickMarks('elTickMarks', 80, 88, 58, 46, 51, 0, 90, 10);

        if (Number.isFinite(az)) {
          set('azValue', Math.round(az));
          document.getElementById('azNeedle').setAttribute('transform', `rotate(${az} 80 80)`);
          set('detailAz', az.toFixed(1));
        } else {
          set('azValue', '0');
          document.getElementById('azNeedle').setAttribute('transform', 'rotate(0 80 80)');
          set('detailAz', '--');
        }
        if (Number.isFinite(el)) {
          set('elValue', Math.round(el));
          setElevationNeedle(el);
          set('detailEl', el.toFixed(1));
        } else {
          set('elValue', '0');
          setElevationNeedle(0);
          set('detailEl', '--');
        }

        set('uplinkFrequency', frequencyMHzReadout(status.uplink_frequency_hz));
        set('downlinkFrequency', frequencyMHzReadout(status.downlink_frequency_hz));
        set('uplinkMode', readout(status.uplink_mode));
        set('downlinkMode', readout(status.downlink_mode));

        const doppler = Number(status.downlink_doppler_hz);
        set('detailSatellite', readout(status.satellite || data.current_satellite));
        set('detailRange', readout(status.range_km ? `${Number(status.range_km).toFixed(0)} km` : status.range));
        set('detailDoppler', Number.isFinite(doppler) ? `${doppler.toFixed(0)} Hz` : readout(status.doppler));
        set('message', status.satellite ? `Tracking ${status.satellite}` : (data.message || ''));
        renderPresetButtons(
          data.presets || [],
          Boolean(data.preset_buttons_enabled) && !presetCommandInFlight
        );

        const rows = document.getElementById('passRows');
        rows.innerHTML = '';
        if (!data.passes.length) {
          rows.innerHTML = '<tr><td colspan="5">No passes loaded from schedule.json</td></tr>';
          resetPassListScroll();
          return;
        }
        for (const pass of data.passes) {
          const tr = document.createElement('tr');
          tr.className = pass.status;
          addCell(tr, pass.satellite);
          addCell(tr, pass.satellite_type);
          addCell(tr, pass.el);
          addCell(tr, pass.start);
          addCell(tr, pass.finish);
          rows.appendChild(tr);
        }
        resetPassListScroll();
      } catch (err) {
        set('message', 'Web console update failed: ' + err);
      }
    }
    function updatePresetMenuState() {
      const presetMenuItem = document.getElementById('presetMenuItem');
      if (!presetMenuItem) { return; }
      presetMenuItem.setAttribute('aria-checked', presetsVisible ? 'true' : 'false');
      presetMenuItem.textContent = presetsVisible ? '✓ Show Presets' : 'Show Presets';
    }

    function setPresetsVisible(visible) {
      presetsVisible = !detachedMapMode && Boolean(visible);
      const consoleElement = document.querySelector('.console');
      const presetPanel = document.getElementById('presetPanel');

      if (consoleElement) {
        consoleElement.classList.toggle('presets-visible', presetsVisible);
      }
      document.body.classList.toggle('presets-visible', presetsVisible);
      if (presetPanel) {
        presetPanel.setAttribute('aria-hidden', presetsVisible ? 'false' : 'true');
        presetPanel.style.display = presetsVisible ? '' : 'none';
      }
      updatePresetMenuState();
    }

    function togglePresetVisibility() {
      setPresetsVisible(!presetsVisible);
    }

    function updateMapMenuState() {
      const mapMenuItem = document.getElementById('mapMenuItem');
      if (!mapMenuItem) { return; }
      mapMenuItem.setAttribute('aria-checked', mapVisible ? 'true' : 'false');
      mapMenuItem.textContent = mapVisible ? '✓ Map' : 'Map';
    }

    function stopDetachedMapCloseMonitor() {
      if (detachedMapCloseMonitor !== null) {
        window.clearInterval(detachedMapCloseMonitor);
        detachedMapCloseMonitor = null;
      }
    }

    function closeDetachedMapWindow() {
      stopDetachedMapCloseMonitor();
      if (detachedMapWindow && !detachedMapWindow.closed) {
        detachedMapWindow.close();
      }
      detachedMapWindow = null;
    }

    function openDetachedMapWindow() {
      if (detachedMapWindow && !detachedMapWindow.closed) {
        detachedMapWindow.focus();
        return true;
      }

      const mapUrl = new URL(window.location.href);
      mapUrl.searchParams.set('detached_map', '1');
      detachedMapWindow = window.open(
        mapUrl.toString(),
        'bqeWispDetachedMap',
        'popup=yes,width=820,height=680,resizable=yes,scrollbars=no'
      );

      if (!detachedMapWindow) {
        detachedMapWindow = null;
        return false;
      }

      stopDetachedMapCloseMonitor();
      detachedMapCloseMonitor = window.setInterval(() => {
        if (!detachedMapWindow || detachedMapWindow.closed) {
          stopDetachedMapCloseMonitor();
          detachedMapWindow = null;
          if (mapVisible) {
            mapVisible = false;
            updateMapMenuState();
          }
        }
      }, 500);
      return true;
    }

    function setMapVisible(visible) {
      mapVisible = Boolean(visible);
      const consoleElement = document.querySelector('.console');
      const mapPanel = document.getElementById('mapPanel');

      if (!detachedMapMode) {
        // Keep the embedded map hidden in the main console. The menu item now
        // controls a separate browser window instead.
        if (consoleElement) {
          consoleElement.classList.remove('map-visible');
        }
        document.body.classList.remove('map-visible');
        if (mapPanel) {
          mapPanel.setAttribute('aria-hidden', 'true');
          mapPanel.style.display = 'none';
        }

        if (mapVisible) {
          if (!openDetachedMapWindow()) {
            mapVisible = false;
            set('message', 'The detached map window was blocked by the browser. Allow popups for this site and try again.');
          }
        } else {
          closeDetachedMapWindow();
        }

        updateMapMenuState();
        return;
      }

      // In the detached page, display and update the existing map panel.
      if (consoleElement) {
        consoleElement.classList.toggle('map-visible', mapVisible);
      }
      document.body.classList.toggle('map-visible', mapVisible);
      if (mapPanel) {
        mapPanel.setAttribute('aria-hidden', mapVisible ? 'false' : 'true');
        mapPanel.style.display = mapVisible ? '' : 'none';
      }
      updateMapMenuState();

      if (mapVisible) {
        if (lastMapData && lastMapStatus) {
          updateEarthMap(lastMapData, lastMapStatus);
        } else {
          refreshStatus();
        }
        requestAnimationFrame(() => {
          if (earthMap) { earthMap.invalidateSize(); }
        });
      }
    }

    function toggleMapVisibility() {
      if (detachedMapMode) {
        window.close();
        return;
      }
      setMapVisible(!mapVisible);
    }

    async function runMenuCommand(action, label, refreshAfter = true) {
      set('message', `${label} requested...`);
      try {
        const response = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ action })
        });
        const result = await response.json();
        if (action === 'exit_app' && result.ok) {
          showServerExitedMessage();
          return result;
        }
        set('message', result.message || `${label} command returned with no message.`);
        if (refreshAfter) {
          await refreshStatus();
        }
        return result;
      } catch (err) {
        set('message', `${label} command failed: ${err}`);
        return null;
      }
    }

    function showServerExitedMessage() {
      document.body.innerHTML = `
        <main style="min-height:100vh;display:flex;align-items:center;justify-content:center;
                     padding:2rem;background:#0b1220;color:#f3f6fb;text-align:center;
                     font-family:Arial,sans-serif;box-sizing:border-box;">
          <div>
            <h1 style="margin:0 0 1rem;font-size:2rem;">The server has exited.</h1>
            <p style="margin:0;font-size:1.2rem;">This browser window should now be closed.</p>
          </div>
        </main>`;
    }

    async function runTrackingCommand(action, label) {
      const isSchedulePasses = action === 'schedule_passes';
      if (isSchedulePasses) {
        setSchedulePassesRunning(true);
      }
      const result = await runMenuCommand(action, label, true);
      if (isSchedulePasses && result === null) {
        setSchedulePassesRunning(false);
      }
      return result;
    }

    function runExitCommand() {
      return runMenuCommand('exit_app', 'Exit', false);
    }

    function runRestartServerCommand() {
      return runMenuCommand('restart_server', 'Restart Server', false);
    }

    function openAboutDialog() {
      const modal = document.getElementById('aboutModal');
      if (!modal) { return; }
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      const closeButton = document.getElementById('aboutClose');
      if (closeButton) { closeButton.focus(); }
    }

    function closeAboutDialog() {
      const modal = document.getElementById('aboutModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      const aboutMenuItem = document.getElementById('aboutMenuItem');
      if (aboutMenuItem) { aboutMenuItem.focus(); }
    }

    function closeLicenseDialog() {
      const modal = document.getElementById('licenseModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      const licenseMenuItem = document.getElementById('licenseMenuItem');
      if (licenseMenuItem) { licenseMenuItem.focus(); }
    }

    async function openLicenseDialog() {
      const modal = document.getElementById('licenseModal');
      const contentElement = document.getElementById('licenseContent');
      const messageElement = document.getElementById('licenseMessage');
      if (!modal || !contentElement) { return; }

      contentElement.textContent = 'Loading license...';
      if (messageElement) { messageElement.textContent = ''; }
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');

      try {
        const response = await fetch('/api/license', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || `License request failed with HTTP ${response.status}.`);
        }
        contentElement.textContent = text(result.content);
        contentElement.scrollTop = 0;
        contentElement.scrollLeft = 0;
        if (messageElement) { messageElement.textContent = ''; }
      } catch (err) {
        contentElement.textContent = '';
        if (messageElement) { messageElement.textContent = `Could not load license: ${err}`; }
      } finally {
        const okButton = document.getElementById('licenseOkButton');
        if (okButton) { okButton.focus(); }
      }
    }

    function setQthMessage(message) {
      const messageElement = document.getElementById('qthMessage');
      if (messageElement) { messageElement.textContent = message || ''; }
    }

    function renderQthFields(data) {
      const fieldsElement = document.getElementById('qthFields');
      if (!fieldsElement) { return; }
      fieldsElement.innerHTML = '';
      const fieldData = data && typeof data === 'object' ? data : {};
      let keys = Object.keys(fieldData);
      if (!keys.length) { keys = ['latitude', 'longitude', 'elevation', 'my_callsign', 'my_country']; }
      for (const key of keys) {
        const label = document.createElement('label');
        const safeId = 'qthField_' + key.replace(/[^A-Za-z0-9_-]/g, '_');
        label.setAttribute('for', safeId);
        label.textContent = key;

        const input = document.createElement('input');
        input.type = 'text';
        input.id = safeId;
        input.dataset.qthKey = key;
        input.value = fieldData[key] ?? '';

        fieldsElement.appendChild(label);
        fieldsElement.appendChild(input);
      }
    }

    function collectQthFields() {
      const data = {};
      document.querySelectorAll('#qthFields input[data-qth-key]').forEach((input) => {
        data[input.dataset.qthKey] = input.value;
      });
      return data;
    }

    async function openQthDialog() {
      const modal = document.getElementById('qthModal');
      if (!modal) { return; }
      setQthMessage('Loading QTH configuration...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      try {
        const response = await fetch('/api/config/qth', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not load QTH configuration.');
        }
        renderQthFields(result.data || {});
        setQthMessage(result.message || 'QTH configuration loaded.');
        const firstInput = document.querySelector('#qthFields input[data-qth-key]');
        if (firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderQthFields({ latitude: '', longitude: '', elevation: '', my_callsign: '', my_country: '' });
        setQthMessage('QTH configuration load failed: ' + err.message);
      }
    }

    function closeQthDialog() {
      const modal = document.getElementById('qthModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setQthMessage('');
      const qthMenuItem = document.getElementById('configQthMenuItem');
      if (qthMenuItem) { qthMenuItem.focus(); }
    }

    async function saveQthDialog(event) {
      if (event) { event.preventDefault(); }
      setQthMessage('Saving QTH configuration...');
      try {
        const response = await fetch('/api/config/qth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ data: collectQthFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not save QTH configuration.');
        }
        set('message', result.message || 'QTH configuration saved.');
        closeQthDialog();
        refreshStatus();
      } catch (err) {
        setQthMessage('QTH configuration save failed: ' + err.message);
      }
    }

    function setRadioMessage(message) {
      const messageElement = document.getElementById('radioMessage');
      if (messageElement) { messageElement.textContent = message || ''; }
    }

    function renderRadioFields(data) {
      const fieldsElement = document.getElementById('radioFields');
      if (!fieldsElement) { return; }
      fieldsElement.innerHTML = '';
      const fieldData = data && typeof data === 'object' ? data : {};
      let keys = Object.keys(fieldData);
      if (!keys.length) { keys = ['radio_type', 'radio_port', 'radio_speed']; }
      for (const key of keys) {
        const label = document.createElement('label');
        const safeId = 'radioField_' + key.replace(/[^A-Za-z0-9_-]/g, '_');
        label.setAttribute('for', safeId);
        label.textContent = key;

        const input = document.createElement('input');
        input.type = 'text';
        input.id = safeId;
        input.dataset.radioKey = key;
        input.value = fieldData[key] ?? '';

        fieldsElement.appendChild(label);
        fieldsElement.appendChild(input);
      }
    }

    function collectRadioFields() {
      const data = {};
      document.querySelectorAll('#radioFields input[data-radio-key]').forEach((input) => {
        data[input.dataset.radioKey] = input.value;
      });
      return data;
    }

    async function openRadioDialog() {
      const modal = document.getElementById('radioModal');
      if (!modal) { return; }
      setRadioMessage('Loading Radio configuration template...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      try {
        const response = await fetch('/api/config/radio', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not load Radio configuration template.');
        }
        renderRadioFields(result.data || {});
        setRadioMessage(result.message || 'Radio configuration loaded.');
        const firstInput = document.querySelector('#radioFields input[data-radio-key]');
        if (firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderRadioFields({ radio_type: '', radio_port: '', radio_speed: '' });
        setRadioMessage('Radio configuration load failed: ' + err.message);
      }
    }

    function closeRadioDialog() {
      const modal = document.getElementById('radioModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setRadioMessage('');
      const radioMenuItem = document.getElementById('configRadioMenuItem');
      if (radioMenuItem) { radioMenuItem.focus(); }
    }

    async function saveRadioDialog(event) {
      if (event) { event.preventDefault(); }
      setRadioMessage('Saving Radio configuration...');
      try {
        const response = await fetch('/api/config/radio', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ data: collectRadioFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not save Radio configuration.');
        }
        set('message', result.message || 'Radio configuration saved.');
        closeRadioDialog();
        refreshStatus();
      } catch (err) {
        setRadioMessage('Radio configuration save failed: ' + err.message);
      }
    }

    function setPresetMessage(message) {
      const messageElement = document.getElementById('presetMessage');
      if (messageElement) { messageElement.textContent = message || ''; }
    }

    function renderPresetFields(data) {
      const fieldsElement = document.getElementById('presetFields');
      if (!fieldsElement) { return; }
      fieldsElement.innerHTML = '';
      const fieldData = data && typeof data === 'object' ? data : {};
      let keys = Object.keys(fieldData);
      if (!keys.length) { keys = ['nickname', 'program_to_run_while_waiting', 'bandwidth', 'repeater_offset', 'repeater_shift', 'ctcss_tone']; }
      for (const key of keys) {
        const label = document.createElement('label');
        const safeId = 'presetField_' + key.replace(/[^A-Za-z0-9_-]/g, '_');
        label.setAttribute('for', safeId);
        label.textContent = key;

        const input = document.createElement('input');
        input.type = 'text';
        input.id = safeId;
        input.dataset.presetKey = key;
        input.value = fieldData[key] ?? '';

        fieldsElement.appendChild(label);
        fieldsElement.appendChild(input);
      }
    }

    function collectPresetFields() {
      const data = {};
      document.querySelectorAll('#presetFields input[data-preset-key]').forEach((input) => {
        data[input.dataset.presetKey] = input.value;
      });
      return data;
    }

    async function openPresetDialog() {
      const modal = document.getElementById('presetModal');
      if (!modal) { return; }
      setPresetMessage('Loading idle-task preset template...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      try {
        const response = await fetch('/api/config/create_preset', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not load idle-task preset template.');
        }
        renderPresetFields(result.data || {});
        setPresetMessage(result.message || 'Idle-task preset template loaded. Preset will be saved as <nickname>_preset.yaml.');
        const nicknameInput = document.querySelector('#presetFields input[data-preset-key="nickname"]');
        if (nicknameInput) { nicknameInput.focus(); nicknameInput.select(); }
      } catch (err) {
        renderPresetFields({ nickname: '', program_to_run_while_waiting: '', bandwidth: '', repeater_offset: '', repeater_shift: '', ctcss_tone: '' });
        setPresetMessage('Idle-task preset template load failed: ' + err.message);
      }
    }

    function closePresetDialog() {
      const modal = document.getElementById('presetModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setPresetMessage('');
      const presetMenuItem = document.getElementById('configCreatePresetMenuItem');
      if (presetMenuItem) { presetMenuItem.focus(); }
    }

    async function savePresetDialog(event) {
      if (event) { event.preventDefault(); }
      setPresetMessage('Saving preset...');
      try {
        const response = await fetch('/api/config/create_preset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({
            data: collectPresetFields()
          })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not save preset.');
        }
        set('message', result.message || 'Preset saved.');
        closePresetDialog();
        refreshStatus();
      } catch (err) {
        setPresetMessage('Preset save failed: ' + err.message);
      }
    }

    const mapMenuItem = document.getElementById('mapMenuItem');
    if (mapMenuItem) {
      mapMenuItem.addEventListener('click', toggleMapVisibility);
    }

    const presetMenuItem = document.getElementById('presetMenuItem');
    if (presetMenuItem) {
      presetMenuItem.addEventListener('click', togglePresetVisibility);
    }
    setPresetsVisible(false);

    if (detachedMapMode) {
      document.body.classList.add('detached-map-mode');
      document.title = 'BQE WISP Map';
      window.addEventListener('beforeunload', () => {
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage({ type: 'bqe-wisp-detached-map-closed' }, window.location.origin);
        }
      });
      window.addEventListener('resize', () => {
        window.clearTimeout(detachedMapResizeTimer);
        detachedMapResizeTimer = window.setTimeout(() => {
          if (earthMap) {
            earthMap.invalidateSize();
          }
        }, 120);
      });
      setMapVisible(true);
    } else {
      window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) { return; }
        if (!event.data || event.data.type !== 'bqe-wisp-detached-map-closed') { return; }
        stopDetachedMapCloseMonitor();
        detachedMapWindow = null;
        mapVisible = false;
        updateMapMenuState();
      });
      window.addEventListener('beforeunload', closeDetachedMapWindow);
      setMapVisible(false);
    }

    const exitMenuItem = document.getElementById('exitMenuItem');
    if (exitMenuItem) {
      exitMenuItem.addEventListener('click', runExitCommand);
    }

    const restartServerMenuItem = document.getElementById('restartServerMenuItem');
    if (restartServerMenuItem) {
      restartServerMenuItem.addEventListener('click', runRestartServerCommand);
    }

    const updateKepsMenuItem = document.getElementById('updateKepsMenuItem');
    if (updateKepsMenuItem) {
      updateKepsMenuItem.addEventListener('click', () => runTrackingCommand('update_keps', 'Update Keps'));
    }
    const schedulePassesMenuItem = document.getElementById('schedulePassesMenuItem');
    if (schedulePassesMenuItem) {
      schedulePassesMenuItem.addEventListener('click', () => runTrackingCommand('schedule_passes', 'Schedule Passes'));
    }

    const configQthMenuItem = document.getElementById('configQthMenuItem');
    if (configQthMenuItem) {
      configQthMenuItem.addEventListener('click', openQthDialog);
    }
    const qthForm = document.getElementById('qthForm');
    if (qthForm) {
      qthForm.addEventListener('submit', saveQthDialog);
    }
    const qthClose = document.getElementById('qthClose');
    if (qthClose) {
      qthClose.addEventListener('click', closeQthDialog);
    }
    const qthCancelButton = document.getElementById('qthCancelButton');
    if (qthCancelButton) {
      qthCancelButton.addEventListener('click', closeQthDialog);
    }
    const qthModal = document.getElementById('qthModal');
    if (qthModal) {
      qthModal.addEventListener('click', (event) => {
        if (event.target === qthModal) { closeQthDialog(); }
      });
    }

    const configRadioMenuItem = document.getElementById('configRadioMenuItem');
    if (configRadioMenuItem) {
      configRadioMenuItem.addEventListener('click', openRadioDialog);
    }
    const radioForm = document.getElementById('radioForm');
    if (radioForm) {
      radioForm.addEventListener('submit', saveRadioDialog);
    }
    const radioClose = document.getElementById('radioClose');
    if (radioClose) {
      radioClose.addEventListener('click', closeRadioDialog);
    }
    const radioCancelButton = document.getElementById('radioCancelButton');
    if (radioCancelButton) {
      radioCancelButton.addEventListener('click', closeRadioDialog);
    }
    const radioModal = document.getElementById('radioModal');
    if (radioModal) {
      radioModal.addEventListener('click', (event) => {
        if (event.target === radioModal) { closeRadioDialog(); }
      });
    }

    const configCreatePresetMenuItem = document.getElementById('configCreatePresetMenuItem');
    if (configCreatePresetMenuItem) {
      configCreatePresetMenuItem.addEventListener('click', openPresetDialog);
    }
    const presetForm = document.getElementById('presetForm');
    if (presetForm) {
      presetForm.addEventListener('submit', savePresetDialog);
    }
    const presetClose = document.getElementById('presetClose');
    if (presetClose) {
      presetClose.addEventListener('click', closePresetDialog);
    }
    const presetCancelButton = document.getElementById('presetCancelButton');
    if (presetCancelButton) {
      presetCancelButton.addEventListener('click', closePresetDialog);
    }
    const presetModal = document.getElementById('presetModal');
    if (presetModal) {
      presetModal.addEventListener('click', (event) => {
        if (event.target === presetModal) { closePresetDialog(); }
      });
    }

    const licenseMenuItem = document.getElementById('licenseMenuItem');
    if (licenseMenuItem) {
      licenseMenuItem.addEventListener('click', openLicenseDialog);
    }
    const licenseOkButton = document.getElementById('licenseOkButton');
    if (licenseOkButton) {
      licenseOkButton.addEventListener('click', closeLicenseDialog);
    }
    const licenseModal = document.getElementById('licenseModal');
    if (licenseModal) {
      licenseModal.addEventListener('click', (event) => {
        if (event.target === licenseModal) { closeLicenseDialog(); }
      });
    }

    const aboutMenuItem = document.getElementById('aboutMenuItem');
    if (aboutMenuItem) {
      aboutMenuItem.addEventListener('click', openAboutDialog);
    }
    const aboutClose = document.getElementById('aboutClose');
    if (aboutClose) {
      aboutClose.addEventListener('click', closeAboutDialog);
    }
    const aboutModal = document.getElementById('aboutModal');
    if (aboutModal) {
      aboutModal.addEventListener('click', (event) => {
        if (event.target === aboutModal) { closeAboutDialog(); }
      });
    }
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeLicenseDialog();
        closeAboutDialog();
        closeQthDialog();
        closeRadioDialog();
        closePresetDialog();
      }
    });

    const refreshIntervalMs = __BQE_UI_REFRESH_INTERVAL_MS__;
    refreshStatus();
    setInterval(refreshStatus, refreshIntervalMs);
  </script>
</body>
</html>
"""


def build_index_html(settings: Optional[WebConsoleSettings] = None) -> str:
    """Build the web-console page using the configured browser refresh interval."""
    settings = settings or load_web_console_settings()
    return INDEX_HTML_TEMPLATE.replace(
        "__BQE_UI_REFRESH_INTERVAL_MS__",
        str(settings.ui_refresh_interval_ms),
    )


INDEX_HTML = build_index_html()


StatusPayloadFunc = Callable[[], Mapping[str, Any]]
CommandPayloadFunc = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class WebConsoleHandler(BaseHTTPRequestHandler):
    """Small built-in web console for the schedule."""

    def __init__(
        self,
        *args: Any,
        status_payload_func: StatusPayloadFunc,
        command_payload_func: Optional[CommandPayloadFunc] = None,
        index_html: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self._status_payload_func = status_payload_func
        self._command_payload_func = command_payload_func
        self._index_html = index_html if index_html is not None else INDEX_HTML
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep browser refreshes from cluttering the scheduler console.
        return

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_payload(self, max_length: int = 65536) -> Optional[Mapping[str, Any]]:
        """Read and decode a small JSON request body."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw_body = self.rfile.read(min(length, max_length)) if length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, Mapping) else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", self._index_html.encode("utf-8"))
            return

        if path == "/api/status":
            body = json.dumps(self._status_payload_func(), default=str).encode("utf-8")
            self.send_bytes(200, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/qth":
            result = dict(read_qth_config_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/radio":
            result = dict(read_radio_config_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/create_preset":
            result = dict(read_create_preset_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/license":
            result = dict(read_license_payload())
            status = 200 if result.get("ok", False) else (404 if not result.get("exists", False) else 500)
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        self.send_bytes(404, "text/plain; charset=utf-8", b"Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        request_payload = self.read_json_payload()
        if request_payload is None:
            body = json.dumps({"ok": False, "message": "Invalid JSON."}).encode("utf-8")
            self.send_bytes(400, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/qth":
            result = dict(write_qth_config_payload(request_payload))
            status = 200 if result.get("ok", False) else 400
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/radio":
            result = dict(write_radio_config_payload(request_payload))
            status = 200 if result.get("ok", False) else 400
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/create_preset":
            result = dict(write_create_preset_payload(request_payload))
            status = 200 if result.get("ok", False) else 400
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path != "/api/command":
            self.send_bytes(404, "text/plain; charset=utf-8", b"Not found")
            return

        if self._command_payload_func is None:
            body = json.dumps({"ok": False, "message": "Command API is not configured."}).encode("utf-8")
            self.send_bytes(501, "application/json; charset=utf-8", body)
            return

        action = request_payload.get("action", "")
        result = dict(self._command_payload_func(str(action), request_payload))
        status = 200 if result.get("ok", False) else 400
        body = json.dumps(result, default=str).encode("utf-8")
        self.send_bytes(status, "application/json; charset=utf-8", body)
