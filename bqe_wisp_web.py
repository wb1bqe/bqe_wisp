#!/usr/bin/env python3
# Copyright (c) 2026 Al Lawler, WB1BQE. All rights reserved.

"""HTTP request handler for the BQE WISP web console."""

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union
from urllib.parse import parse_qs, quote, urlparse

import yaml

from plugins.bqe_audio_stream import AUDIO_STREAM


SCRIPT_DIR = Path(__file__).resolve().parent
GENERAL_SETTINGS_PATH = SCRIPT_DIR / "bqe_config" / "general_settings.yaml"
GENERAL_SETTINGS_TEMPLATE_PATH = SCRIPT_DIR / "templates" / "general_settings.yaml"
GENERAL_SETTINGS_TEMPLATE_FALLBACK_PATH = SCRIPT_DIR / "templates" / "general_settings_template.yaml"
SATELLITES_CONFIG_PATH = SCRIPT_DIR / "bqe_config" / "satellites.yaml"
SATELLITES_TEMPLATE_PATH = SCRIPT_DIR / "templates" / "satellites_template.yaml"
SATELLITES_TEMPLATE_TYPO_PATH = SCRIPT_DIR / "templates" / "satelllites_template.yaml"
NEW_SATELLITE_TEMPLATE_PATH = SCRIPT_DIR / "templates" / "new_satellite_template.yaml"
NEW_SATELLITE_TEMPLATE_FALLBACK_PATH = SCRIPT_DIR / "templates" / "add_satellite_template.yaml"
QTH_CONFIG_PATH = SCRIPT_DIR / "bqe_config" / "my_qth.yaml"
QTH_DEFAULT_FIELDS = ("latitude", "longitude", "elevation", "my_callsign", "my_country")
QTH_ALWAYS_EDITABLE_FIELDS = ("my_callsign", "my_country")
RIG_CONFIG_PATH = SCRIPT_DIR / "bqe_config" / "my_rig.yaml"
RIG_DEFAULT_FIELDS = ("radio_type", "radio_port", "radio_speed", "radio_idle_task_nickname")
IDLE_TASK_TEMPLATE_PATH = SCRIPT_DIR / "templates" / "idle_task_template.yaml"
PRESETS_DIR = SCRIPT_DIR / "presets"
LICENSE_PATH = SCRIPT_DIR / "license.txt"
LOGS_PATH = SCRIPT_DIR / "logs"
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
DEFAULT_UI_THEME = "classic"
MIN_UI_REFRESH_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True)
class WebConsoleSettings:
    """Runtime configuration for the BQE WISP web console."""

    ui_refresh_interval_seconds: float
    ui_port: int
    sstv_gallery_location: str = ""
    recordings_location: str = ""
    ui_theme: str = DEFAULT_UI_THEME

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


def _parse_ui_theme(value: Any) -> str:
    """Return a supported UI theme name from general_settings.yaml."""
    theme = str(value or DEFAULT_UI_THEME).strip().lower()
    if theme not in {"classic", "modern"}:
        raise ValueError(
            "ui_theme must be either 'classic' or 'modern', "
            f"not {value!r}"
        )
    return theme


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
        "sstv_gallery_location": "",
        "ui_theme": DEFAULT_UI_THEME,
    }
    merged_settings.update(sections.get("bqe_wisp", {}))
    merged_settings.update(sections.get("bqe_wisp_web", {}))

    plugins_settings = sections.get("plugins", {})
    sound_recorder_settings = (
        plugins_settings.get("bqe_sound_recorder", {})
        if isinstance(plugins_settings, Mapping)
        else {}
    )
    recordings_location = (
        sound_recorder_settings.get("recordings", "")
        if isinstance(sound_recorder_settings, Mapping)
        else ""
    )

    return WebConsoleSettings(
        ui_refresh_interval_seconds=_parse_seconds(merged_settings["ui_refresh_interval"]),
        ui_port=_parse_port(merged_settings["ui_port"]),
        sstv_gallery_location=str(merged_settings.get("sstv_gallery_location") or "").strip(),
        recordings_location=str(recordings_location or "").strip(),
        ui_theme=_parse_ui_theme(merged_settings.get("ui_theme")),
    )


def get_web_console_port(settings_path: Union[str, Path] = GENERAL_SETTINGS_PATH) -> int:
    """Return the configured port for the code that starts the HTTP server."""
    return load_web_console_settings(settings_path).ui_port


def load_test_pass_satellites_for_ui(
    path: Union[str, Path] = SATELLITES_CONFIG_PATH,
) -> list[dict[str, str]]:
    """Read satellite nicknames for Tracking > Run test pass now."""
    satellite_path = Path(path)
    if not satellite_path.exists():
        return []

    raw = yaml.safe_load(satellite_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        return []
    entries = raw.get("satellites", [])
    if not isinstance(entries, list):
        return []

    satellites: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        nickname = str(entry.get("nickname") or "").strip()
        if not nickname or nickname.casefold() in seen:
            continue
        seen.add(nickname.casefold())
        satellites.append({
            "nickname": nickname,
            "satellite_name": str(entry.get("satellite_name") or nickname).strip(),
            "satellite_type": str(entry.get("satellite_type") or "").strip(),
            "catalog_number": entry.get("catalog_number"),
        })
    return satellites


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



def _read_general_settings_yaml(path: Union[str, Path]) -> dict[str, Any]:
    """Read a general-settings YAML file as a top-level mapping."""
    settings_path = Path(path)
    if not settings_path.exists():
        return {}

    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{settings_path} must contain a YAML mapping at the top level.")
    return dict(raw)


def _resolve_general_settings_template_path() -> Path:
    """Prefer templates/general_settings.yaml, with the older template name as a fallback."""
    if GENERAL_SETTINGS_TEMPLATE_PATH.exists():
        return GENERAL_SETTINGS_TEMPLATE_PATH
    if GENERAL_SETTINGS_TEMPLATE_FALLBACK_PATH.exists():
        return GENERAL_SETTINGS_TEMPLATE_FALLBACK_PATH
    return GENERAL_SETTINGS_TEMPLATE_PATH


def _normalize_general_settings_tree(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the program_settings list-of-sections into a mapping for editing."""
    normalized = dict(raw)
    program_settings = normalized.get("program_settings")
    if isinstance(program_settings, list):
        sections: dict[str, Any] = {}
        for item in program_settings:
            if not isinstance(item, Mapping):
                continue
            for section_name, section_value in item.items():
                sections[str(section_name)] = section_value
        normalized["program_settings"] = sections
    return normalized


def _restore_general_settings_shape(normalized: Mapping[str, Any], original_raw: Mapping[str, Any]) -> dict[str, Any]:
    """Restore the current file's original program_settings list/mapping shape."""
    restored = dict(normalized)
    original_program_settings = original_raw.get("program_settings") if isinstance(original_raw, Mapping) else None
    program_settings = restored.get("program_settings")
    if isinstance(original_program_settings, list) and isinstance(program_settings, Mapping):
        restored["program_settings"] = [
            {str(section_name): section_value}
            for section_name, section_value in program_settings.items()
        ]
    return restored


def _flatten_general_settings_values(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Return dotted leaf keys while keeping list values editable as a single YAML value."""
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        key_text = str(key)
        dotted_key = f"{prefix}.{key_text}" if prefix else key_text
        if isinstance(value, Mapping):
            flattened.update(_flatten_general_settings_values(value, dotted_key))
        else:
            flattened[dotted_key] = value
    return flattened


def _general_setting_value_to_text(value: Any) -> str:
    """Convert a general-settings value into one-line editable text."""
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        dumped = yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip()
        return dumped
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _set_dotted_general_setting(root: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set one dotted key in a normalized general-settings mapping."""
    parts = [part for part in str(dotted_key).split(".") if part]
    if not parts:
        return
    node = root
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, Mapping):
            child = {}
            node[part] = child
        elif not isinstance(child, dict):
            child = dict(child)
            node[part] = child
        node = child
    node[parts[-1]] = value


def _general_settings_editor_response(message: str = "") -> dict[str, Any]:
    """Build the three-column Advanced general-settings editor payload."""
    template_path = _resolve_general_settings_template_path()
    template_raw = _read_general_settings_yaml(template_path)

    # The editable Current column comes directly from the live configuration
    # used by BQE WISP: bqe_config/general_settings.yaml.  If it does not yet
    # exist, use the template as the initial editable content; Save will create
    # general_settings.yaml.
    current_raw = _read_general_settings_yaml(GENERAL_SETTINGS_PATH)
    using_template_source = False
    if not current_raw and not GENERAL_SETTINGS_PATH.exists():
        current_raw = dict(template_raw)
        using_template_source = True

    current_normalized = _normalize_general_settings_tree(current_raw)
    template_normalized = _normalize_general_settings_tree(template_raw)
    current_values = _flatten_general_settings_values(current_normalized)
    original_values = _flatten_general_settings_values(template_normalized)

    ordered_keys = list(original_values.keys())
    ordered_keys.extend(key for key in current_values.keys() if key not in original_values)

    rows = []
    for key in ordered_keys:
        current_present = key in current_values
        original_present = key in original_values
        current_value = current_values.get(key, original_values.get(key, ""))
        original_value = original_values.get(key, "")
        current_text = _general_setting_value_to_text(current_value)
        original_text = _general_setting_value_to_text(original_value)
        rows.append({
            "key": key,
            "current": current_text,
            "original": original_text,
            "current_present": current_present,
            "original_present": original_present,
            # A presence mismatch is also a real configuration difference, even
            # when the editor seeds a missing current value from the template.
            "different": (
                current_present != original_present
                or (current_present and original_present and current_text != original_text)
            ),
        })

    if not rows:
        raise ValueError(
            f"No settings were found in {GENERAL_SETTINGS_PATH} or {template_path}."
        )

    default_message = (
        f"Loaded current settings from {GENERAL_SETTINGS_PATH} "
        f"and originals from {template_path}."
    )
    if using_template_source:
        default_message += f" Save will create {GENERAL_SETTINGS_PATH}."

    return {
        "ok": True,
        "path": str(GENERAL_SETTINGS_PATH),
        "current_source_path": str(GENERAL_SETTINGS_PATH),
        "template_path": str(template_path),
        "exists": GENERAL_SETTINGS_PATH.exists(),
        "rows": rows,
        "message": message or default_message,
    }

def read_general_settings_editor_payload() -> Mapping[str, Any]:
    """Return Current-vs-Original values for Config > Advanced > Edit general_settings.yaml."""
    try:
        return _general_settings_editor_response()
    except Exception as exc:
        return {"ok": False, "message": f"Could not read general settings: {exc}"}


def write_general_settings_editor_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Write the editable Current column directly to bqe_config/general_settings.yaml."""
    try:
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "General-settings save request did not contain a data mapping."}

        # Edit the same file the running BQE WISP installation uses.  If it is
        # absent, seed it from the shipped template before applying edits.
        if GENERAL_SETTINGS_PATH.exists():
            current_raw = _read_general_settings_yaml(GENERAL_SETTINGS_PATH)
        else:
            current_raw = _read_general_settings_yaml(_resolve_general_settings_template_path())

        current_normalized = _normalize_general_settings_tree(current_raw)
        existing_values = _flatten_general_settings_values(current_normalized)
        template_normalized = _normalize_general_settings_tree(
            _read_general_settings_yaml(_resolve_general_settings_template_path())
        )
        template_values = _flatten_general_settings_values(template_normalized)

        allowed_keys = set(existing_values) | set(template_values)
        for key, text_value in edited_data.items():
            key_text = str(key).strip()
            if not key_text or key_text not in allowed_keys:
                continue
            previous_value = existing_values.get(key_text, template_values.get(key_text))
            converted_value = _infer_qth_value_type(text_value, previous_value)
            _set_dotted_general_setting(current_normalized, key_text, converted_value)

        output_data = _restore_general_settings_shape(current_normalized, current_raw)
        GENERAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = GENERAL_SETTINGS_PATH.with_suffix(GENERAL_SETTINGS_PATH.suffix + ".tmp")
        temporary_path.write_text(
            yaml.safe_dump(output_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        temporary_path.replace(GENERAL_SETTINGS_PATH)

        return _general_settings_editor_response(
            f"Saved general settings to {GENERAL_SETTINGS_PATH}."
        )
    except Exception as exc:
        return {"ok": False, "message": f"Could not save general settings: {exc}"}



def _read_satellites_yaml(path: Union[str, Path] = SATELLITES_CONFIG_PATH) -> dict[str, Any]:
    """Read a satellites YAML file and validate its top-level structure."""
    satellite_path = Path(path)
    if not satellite_path.exists():
        raise FileNotFoundError(f"{satellite_path} not found")

    raw = yaml.safe_load(satellite_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{satellite_path} must contain a YAML mapping at the top level.")
    satellites = raw.get("satellites")
    if not isinstance(satellites, list):
        raise ValueError(f"{satellite_path} must contain a top-level 'satellites' list.")
    for index, entry in enumerate(satellites):
        if not isinstance(entry, Mapping):
            raise ValueError(f"Satellite entry {index + 1} in {satellite_path} must be a YAML mapping.")
    return dict(raw)


def _resolve_satellites_template_path() -> Path:
    """Return the installed satellites comparison template path."""
    for candidate in (SATELLITES_TEMPLATE_PATH, SATELLITES_TEMPLATE_TYPO_PATH):
        if candidate.exists():
            return candidate
    return SATELLITES_TEMPLATE_PATH


def _resolve_new_satellite_template_path() -> Path:
    """Prefer the documented new-satellite template name, with the uploaded legacy name as fallback."""
    for candidate in (NEW_SATELLITE_TEMPLATE_PATH, NEW_SATELLITE_TEMPLATE_FALLBACK_PATH):
        if candidate.exists():
            return candidate
    return NEW_SATELLITE_TEMPLATE_PATH


def _satellite_value_to_text(value: Any) -> str:
    """Convert one satellite field to compact editable text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip()
    return str(value)


def _infer_satellite_field_value(text_value: Any, previous_value: Any = None) -> Any:
    """Convert browser text back to the type already used by a satellite/template field."""
    text = "" if text_value is None else str(text_value).strip()

    if isinstance(previous_value, bool):
        lowered = text.lower()
        if lowered in {"true", "yes", "on", "1"}:
            return True
        if lowered in {"false", "no", "off", "0"}:
            return False
        raise ValueError(f"Expected true/false, not {text_value!r}")

    if isinstance(previous_value, int) and not isinstance(previous_value, bool):
        return int(text)
    if isinstance(previous_value, float):
        return float(text)
    if isinstance(previous_value, (dict, list)):
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, type(previous_value)):
            raise ValueError(f"Expected a {type(previous_value).__name__} YAML value, not {text_value!r}")
        return parsed
    if previous_value is None:
        if text == "":
            return None
        parsed = yaml.safe_load(text)
        return parsed

    # Most satellite fields are intentionally stored as strings, including
    # frequencies/catalog numbers where leading/trailing zeroes can matter.
    return text


def _satellite_entry_rows(current_entry: Mapping[str, Any], original_entry: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build Key/Current/Original rows for one satellite definition."""
    ordered_keys = list(current_entry.keys())
    ordered_keys.extend(key for key in original_entry.keys() if key not in current_entry)
    return [
        {
            "key": str(key),
            "current": _satellite_value_to_text(current_entry.get(key, original_entry.get(key, ""))),
            "original": _satellite_value_to_text(original_entry.get(key, "")),
        }
        for key in ordered_keys
    ]


def _satellites_editor_response(selected_index: int = 0, message: str = "") -> dict[str, Any]:
    """Build the Config > Advanced > Edit satellites.yaml payload."""
    current_raw = _read_satellites_yaml(SATELLITES_CONFIG_PATH)
    template_path = _resolve_satellites_template_path()
    try:
        template_raw = _read_satellites_yaml(template_path)
    except FileNotFoundError:
        template_raw = {"satellites": []}

    current_entries = current_raw.get("satellites", [])
    template_entries = template_raw.get("satellites", [])
    if not current_entries:
        raise ValueError(f"No satellite definitions were found in {SATELLITES_CONFIG_PATH}.")

    try:
        selected_index = int(selected_index)
    except (TypeError, ValueError):
        selected_index = 0
    selected_index = max(0, min(selected_index, len(current_entries) - 1))
    current_entry = dict(current_entries[selected_index])
    nickname = str(current_entry.get("nickname") or "").strip()

    original_entry: dict[str, Any] = {}
    if nickname:
        for entry in template_entries:
            if str(entry.get("nickname") or "").strip().casefold() == nickname.casefold():
                original_entry = dict(entry)
                break
    if not original_entry and selected_index < len(template_entries):
        original_entry = dict(template_entries[selected_index])

    choices = []
    for index, entry in enumerate(current_entries):
        entry_nickname = str(entry.get("nickname") or "").strip()
        sat_name = str(entry.get("satellite_name") or "").strip()
        label = entry_nickname or sat_name or f"Satellite {index + 1}"
        if sat_name and sat_name.casefold() != label.casefold():
            label += f" - {sat_name}"
        choices.append({"index": index, "label": label})

    return {
        "ok": True,
        "path": str(SATELLITES_CONFIG_PATH),
        "template_path": str(template_path),
        "selected_index": selected_index,
        "choices": choices,
        "rows": _satellite_entry_rows(current_entry, original_entry),
        "message": message or f"Loaded {nickname or 'satellite definition'} from {SATELLITES_CONFIG_PATH}.",
    }


def read_satellites_editor_payload(selected_index: Any = 0) -> Mapping[str, Any]:
    """Return one editable satellite definition with template comparison values."""
    try:
        return _satellites_editor_response(selected_index=selected_index)
    except Exception as exc:
        return {"ok": False, "message": f"Could not read satellites configuration: {exc}"}


def _validate_satellites_document(candidate: Any) -> dict[str, Any]:
    """Run a YAML round-trip/shape validation and return the parsed mapping."""
    dumped = yaml.safe_dump(candidate, sort_keys=False, default_flow_style=False)
    reparsed = yaml.safe_load(dumped)
    if not isinstance(reparsed, Mapping):
        raise ValueError("Generated satellites YAML did not parse back as a mapping.")
    satellites = reparsed.get("satellites")
    if not isinstance(satellites, list):
        raise ValueError("Generated satellites YAML did not contain a top-level 'satellites' list.")
    if any(not isinstance(entry, Mapping) for entry in satellites):
        raise ValueError("Every generated satellite definition must be a YAML mapping.")
    return dict(reparsed)


def _write_satellites_atomic(data: Mapping[str, Any]) -> None:
    """Validate and atomically replace bqe_config/satellites.yaml."""
    validated = _validate_satellites_document(data)
    SATELLITES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = SATELLITES_CONFIG_PATH.with_suffix(SATELLITES_CONFIG_PATH.suffix + ".tmp")
    temporary_path.write_text(
        yaml.safe_dump(validated, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    # Parse the exact bytes that will be installed before replacing the live file.
    _read_satellites_yaml(temporary_path)
    temporary_path.replace(SATELLITES_CONFIG_PATH)


def write_satellites_editor_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Update one definition in bqe_config/satellites.yaml after YAML validation."""
    try:
        selected_index = int(request_payload.get("index", 0))
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "Satellite save request did not contain a data mapping."}

        current_raw = _read_satellites_yaml(SATELLITES_CONFIG_PATH)
        entries = list(current_raw.get("satellites", []))
        if not 0 <= selected_index < len(entries):
            return {"ok": False, "message": "Selected satellite definition no longer exists."}

        previous_entry = dict(entries[selected_index])
        updated_entry = dict(previous_entry)
        for key, text_value in edited_data.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            updated_entry[key_text] = _infer_satellite_field_value(text_value, previous_entry.get(key_text))

        entries[selected_index] = updated_entry
        updated_raw = dict(current_raw)
        updated_raw["satellites"] = entries
        _write_satellites_atomic(updated_raw)
        nickname = str(updated_entry.get("nickname") or f"entry {selected_index + 1}")
        return _satellites_editor_response(
            selected_index=selected_index,
            message=f"Saved satellite definition {nickname!r} to {SATELLITES_CONFIG_PATH}.",
        )
    except Exception as exc:
        return {"ok": False, "message": f"Could not save satellites configuration: {exc}"}


def _load_new_satellite_template_entry() -> tuple[dict[str, Any], Path]:
    """Load one blank satellite definition from the new-satellite template."""
    template_path = _resolve_new_satellite_template_path()
    if not template_path.exists():
        raise FileNotFoundError(f"{template_path} not found")
    raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    entry: Any = None
    if isinstance(raw, list) and raw:
        entry = raw[0]
    elif isinstance(raw, Mapping):
        if isinstance(raw.get("satellites"), list) and raw["satellites"]:
            entry = raw["satellites"][0]
        else:
            entry = raw
    if not isinstance(entry, Mapping):
        raise ValueError(f"{template_path} must contain one satellite-definition mapping.")
    return dict(entry), template_path


def read_add_satellite_payload() -> Mapping[str, Any]:
    """Return fields for Config > Advanced > Add new Satellite Definition."""
    try:
        template_entry, template_path = _load_new_satellite_template_entry()
        return {
            "ok": True,
            "template_path": str(template_path),
            "data": {str(key): _satellite_value_to_text(value) for key, value in template_entry.items()},
            "message": f"Loaded new satellite template from {template_path}.",
        }
    except Exception as exc:
        return {"ok": False, "message": f"Could not load new satellite template: {exc}"}


def add_new_satellite_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a new definition and append it to bqe_config/satellites.yaml."""
    try:
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "Add-satellite request did not contain a data mapping."}

        template_entry, _template_path = _load_new_satellite_template_entry()
        new_entry: dict[str, Any] = {}
        for key, template_value in template_entry.items():
            key_text = str(key)
            new_entry[key_text] = _infer_satellite_field_value(edited_data.get(key_text, ""), template_value)

        # Preserve any extra template-independent fields the UI may legitimately send.
        for key, text_value in edited_data.items():
            key_text = str(key).strip()
            if key_text and key_text not in new_entry:
                new_entry[key_text] = _infer_satellite_field_value(text_value, None)

        nickname = str(new_entry.get("nickname") or "").strip()
        if not nickname:
            return {"ok": False, "message": "A new satellite definition must have a nickname."}

        # Basic YAML parsing test requested by the UI workflow.
        parsed_test = yaml.safe_load(yaml.safe_dump({"satellites": [new_entry]}, sort_keys=False))
        if not isinstance(parsed_test, Mapping) or not isinstance(parsed_test.get("satellites"), list):
            return {"ok": False, "message": "The new satellite definition failed YAML validation."}

        current_raw = _read_satellites_yaml(SATELLITES_CONFIG_PATH)
        entries = list(current_raw.get("satellites", []))
        for existing in entries:
            existing_nickname = str(existing.get("nickname") or "").strip()
            if existing_nickname.casefold() == nickname.casefold():
                return {"ok": False, "message": f"A satellite definition with nickname {nickname!r} already exists."}

        entries.append(new_entry)
        updated_raw = dict(current_raw)
        updated_raw["satellites"] = entries
        _write_satellites_atomic(updated_raw)
        return {
            "ok": True,
            "message": f"Added satellite definition {nickname!r} to {SATELLITES_CONFIG_PATH}.",
            "nickname": nickname,
            "index": len(entries) - 1,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Could not add satellite definition: {exc}"}


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


def _list_editable_preset_files() -> list[Path]:
    """Return top-level YAML preset files without following entries outside presets/."""
    if not PRESETS_DIR.is_dir():
        return []

    presets_root = PRESETS_DIR.resolve()
    preset_files = []
    for candidate in PRESETS_DIR.iterdir():
        if not candidate.is_file() or candidate.suffix.casefold() not in {".yaml", ".yml"}:
            continue
        try:
            resolved_candidate = candidate.resolve()
            if resolved_candidate.parent != presets_root:
                continue
        except OSError:
            continue
        preset_files.append(candidate)
    return sorted(preset_files, key=lambda path: path.name.casefold())


def _resolve_existing_preset_file(filename: Any) -> Path:
    """Resolve one existing top-level preset YAML file by its exact filename."""
    requested_name = "" if filename is None else str(filename).strip()
    if not requested_name:
        raise ValueError("Select a preset file.")
    if Path(requested_name).name != requested_name or "/" in requested_name or "\\" in requested_name:
        raise ValueError("Preset filename must be a filename only, not a path.")
    if Path(requested_name).suffix.casefold() not in {".yaml", ".yml"}:
        raise ValueError("Preset filename must end in .yaml or .yml.")

    available = {path.name: path for path in _list_editable_preset_files()}
    preset_path = available.get(requested_name)
    if preset_path is None:
        raise FileNotFoundError(f"Preset {requested_name!r} was not found in {PRESETS_DIR}.")
    return preset_path


def _read_preset_yaml(path: Path) -> dict[str, Any]:
    """Read one preset and require the same top-level mapping used by QTH editing."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} must contain a YAML mapping at the top level.")
    return dict(raw)


def _infer_preset_value_type(text_value: Any, previous_value: Any = None) -> Any:
    """Preserve quoted/string preset values such as frequency_hz and bandwidth."""
    if isinstance(previous_value, str):
        return "" if text_value is None else str(text_value).strip()
    return _infer_qth_value_type(text_value, previous_value)


def _edit_preset_response(filename: Any = "", message: str = "") -> dict[str, Any]:
    """Build the Config > Edit existing Preset selector and editor payload."""
    preset_files = _list_editable_preset_files()
    filenames = [path.name for path in preset_files]
    if not filenames:
        return {
            "ok": True,
            "presets_path": str(PRESETS_DIR),
            "files": [],
            "filename": "",
            "data": {},
            "message": message or f"No preset YAML files were found in {PRESETS_DIR}.",
        }

    selected_name = str(filename or "").strip() or filenames[0]
    preset_path = _resolve_existing_preset_file(selected_name)
    preset_data = _read_preset_yaml(preset_path)
    if not preset_data:
        preset_data = {name: "" for name in PRESET_DEFAULT_FIELDS}
    editable = {str(key): _qth_value_to_text(value) for key, value in preset_data.items()}
    return {
        "ok": True,
        "presets_path": str(PRESETS_DIR),
        "path": str(preset_path),
        "files": filenames,
        "filename": preset_path.name,
        "data": editable,
        "message": message or f"Loaded preset from {preset_path}.",
    }


def read_edit_preset_payload(filename: Any = "") -> Mapping[str, Any]:
    """Return a selected preset YAML file for the browser editor."""
    try:
        return _edit_preset_response(filename)
    except Exception as exc:
        return {"ok": False, "message": f"Could not read preset: {exc}"}


def write_edit_preset_payload(request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Update one existing YAML preset under presets/ after validation."""
    try:
        preset_path = _resolve_existing_preset_file(request_payload.get("filename", ""))
        edited_data = request_payload.get("data", {})
        if not isinstance(edited_data, Mapping):
            return {"ok": False, "message": "Preset save request did not contain a data mapping."}

        existing_data = _read_preset_yaml(preset_path)
        updated_data = dict(existing_data)
        for key, value in edited_data.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            updated_data[key_text] = _infer_preset_value_type(value, existing_data.get(key_text))

        temporary_path = preset_path.with_suffix(preset_path.suffix + ".tmp")
        temporary_path.write_text(
            yaml.safe_dump(updated_data, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        temporary_path.replace(preset_path)
        return _edit_preset_response(
            preset_path.name,
            f"Saved preset to {preset_path}.",
        )
    except Exception as exc:
        return {"ok": False, "message": f"Could not save preset: {exc}"}


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


def resolve_sstv_images_directory(configured_location: str) -> Optional[Path]:
    """Resolve the configured SSTV image folder relative to the program directory."""
    location = str(configured_location or "").strip()
    if not location:
        return None

    directory = Path(location).expanduser()
    if not directory.is_absolute():
        directory = SCRIPT_DIR / directory
    return directory.resolve()


def list_sstv_jpg_files(configured_location: str) -> tuple[Optional[Path], list[Path], str]:
    """Return safe, top-level .jpg files from the configured SSTV folder."""
    directory = resolve_sstv_images_directory(configured_location)
    if directory is None:
        return None, [], "sstv_gallery_location is not set in general_settings.yaml."
    if not directory.exists():
        return directory, [], f"The configured SSTV image folder does not exist: {directory}"
    if not directory.is_dir():
        return directory, [], f"The configured SSTV image location is not a folder: {directory}"

    try:
        jpg_files = []
        for entry in directory.iterdir():
            if entry.suffix.casefold() != ".jpg" or not entry.is_file():
                continue
            # Do not expose a symlink that resolves outside the configured folder.
            if entry.resolve().parent != directory:
                continue
            jpg_files.append(entry)
        # Default gallery order is by file modification timestamp, newest first.
        # Filename is used as a stable tie-breaker when timestamps are identical.
        jpg_files.sort(key=lambda path: (-path.stat().st_mtime, path.name.casefold()))
        message = "" if jpg_files else f"No JPG files were found in {directory}."
        return directory, jpg_files, message
    except OSError as exc:
        return directory, [], f"Could not read the configured SSTV image folder: {exc}"


def resolve_sstv_image_file(configured_location: str, requested_name: str) -> Optional[Path]:
    """Resolve one requested JPG without allowing access outside the configured folder."""
    directory = resolve_sstv_images_directory(configured_location)
    name = str(requested_name or "")
    if directory is None or not name or Path(name).name != name:
        return None
    if Path(name).suffix.casefold() != ".jpg":
        return None

    try:
        candidate = (directory / name).resolve()
        if candidate.parent != directory or not candidate.is_file():
            return None
        return candidate
    except OSError:
        return None


def build_sstv_gallery_html(configured_location: str, ui_theme: str = DEFAULT_UI_THEME) -> str:
    """Build the independent SSTV thumbnail-gallery page."""
    directory, jpg_files, message = list_sstv_jpg_files(configured_location)
    cards = []
    for image_path in jpg_files:
        safe_name = html.escape(image_path.name)
        image_url = "/sstv-image?name=" + quote(image_path.name, safe="")
        modified_timestamp = image_path.stat().st_mtime
        cards.append(
            '<a class="image-card" href="{url}" target="_blank" rel="noopener" '
            'title="Open {name} full size" data-filename="{sort_name}" data-mtime="{mtime:.6f}">'
            '<img src="{url}" alt="{name}" loading="lazy">'
            '<span>{name}</span></a>'.format(
                url=image_url,
                name=safe_name,
                sort_name=html.escape(image_path.name.casefold(), quote=True),
                mtime=modified_timestamp,
            )
        )

    source_text = html.escape(str(directory)) if directory is not None else "Not configured"
    if message:
        content = f'<div class="gallery-message">{html.escape(message)}</div>'
    else:
        content = '<div class="gallery">' + "".join(cards) + "</div>"

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BQE WISP SSTV Gallery</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: #b9b9b9;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-weight: 700;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 12px 16px;
      background: linear-gradient(180deg, #eeeeee, #bdbdbd);
      border-bottom: 2px ridge #d2d2d2;
      box-shadow: 0 2px 4px rgba(0,0,0,.25);
    }
    h1 { margin: 0; font-size: 24px; }
    .source { margin-top: 4px; font-size: 13px; overflow-wrap: anywhere; }
    .gallery-controls {
      display: flex;
      flex: 0 0 auto;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    button {
      flex: 0 0 auto;
      padding: 7px 14px;
      border: 2px outset #e4e4e4;
      background: #d2d2d2;
      color: #111;
      font: inherit;
      cursor: pointer;
    }
    button:active { border-style: inset; }
    button.sort-active {
      border-style: inset;
      background: #bcbcbc;
    }
    .gallery {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 14px;
      padding: 16px;
    }
    .image-card {
      display: flex;
      min-width: 0;
      flex-direction: column;
      padding: 8px;
      border: 2px outset #e4e4e4;
      background: #d2d2d2;
      color: #111;
      text-decoration: none;
    }
    .image-card:hover, .image-card:focus { background: #e2e2e2; outline: 3px solid #05058a; }
    .image-card img {
      width: 100%;
      height: 180px;
      object-fit: contain;
      background: #202020;
      border: 2px inset #d0d0d0;
    }
    .image-card span {
      padding: 8px 3px 2px;
      overflow-wrap: anywhere;
      text-align: center;
      font-size: 14px;
    }
    .gallery-message {
      margin: 24px;
      padding: 18px;
      border: 2px inset #d0d0d0;
      background: #d2d2d2;
      font-size: 18px;
    }
    body.modern-ui {
      background: #071321;
      color: #edf2f7;
      font-family: "Segoe UI", Ubuntu, "Noto Sans", Arial, sans-serif;
      font-weight: 500;
    }
    body.modern-ui header {
      padding: 14px 20px;
      background: #102943;
      border: 0;
      border-bottom: 1px solid #3f7199;
      box-shadow: 0 8px 24px rgba(0,0,0,.24);
    }
    body.modern-ui h1 { color: #f7fbff; font-size: 21px; font-weight: 700; }
    body.modern-ui .source { color: #b8ddf5; font-weight: 500; }
    body.modern-ui button {
      border: 1px solid #477aa3;
      border-radius: 7px;
      background: #1b4569;
      color: #f7fbff;
      font-weight: 600;
    }
    body.modern-ui button:hover,
    body.modern-ui button:focus-visible {
      background: #256391;
      border-color: #66c7ff;
      outline: 2px solid rgba(50,215,255,.32);
    }
    body.modern-ui button.sort-active {
      border-color: #32d7ff;
      background: #12618b;
      color: #e8faff;
    }
    body.modern-ui .gallery { gap: 16px; padding: 20px; }
    body.modern-ui .image-card {
      padding: 8px;
      border: 1px solid #3f7199;
      border-radius: 10px;
      background: #102943;
      color: #f7fbff;
      box-shadow: 0 8px 22px rgba(0,0,0,.18);
    }
    body.modern-ui .image-card:hover,
    body.modern-ui .image-card:focus {
      background: #173a5b;
      outline: 2px solid #32d7ff;
      transform: translateY(-1px);
    }
    body.modern-ui .image-card img {
      border: 0;
      border-radius: 6px;
      background: #090d12;
    }
    body.modern-ui .image-card span { color: #e1f2ff; font-weight: 600; }
    body.modern-ui .gallery-message {
      border: 1px solid #3f7199;
      border-radius: 9px;
      background: #102943;
      color: #f7fbff;
    }
  </style>
</head>
<body class="__UI_THEME__-ui">
  <header>
    <div><h1>SSTV Gallery</h1><div class="source">Folder: __SOURCE__</div></div>
    <div class="gallery-controls">
      <button type="button" id="sortFilenameButton" onclick="sortGallery('filename')">Sort by Filename</button>
      <button type="button" id="sortTimestampButton" class="sort-active" onclick="sortGallery('timestamp')">Sort by Timestamp</button>
      <button type="button" onclick="window.location.reload()">Refresh</button>
    </div>
  </header>
  __CONTENT__
  <script>
    function sortGallery(sortMode) {
      const gallery = document.querySelector('.gallery');
      if (!gallery) return;

      const cards = Array.from(gallery.querySelectorAll('.image-card'));
      cards.sort((left, right) => {
        if (sortMode === 'filename') {
          return left.dataset.filename.localeCompare(right.dataset.filename, undefined, {
            numeric: true,
            sensitivity: 'base'
          });
        }

        const leftTime = Number(left.dataset.mtime || 0);
        const rightTime = Number(right.dataset.mtime || 0);
        if (rightTime !== leftTime) return rightTime - leftTime;
        return left.dataset.filename.localeCompare(right.dataset.filename, undefined, {
          numeric: true,
          sensitivity: 'base'
        });
      });

      cards.forEach(card => gallery.appendChild(card));
      document.getElementById('sortFilenameButton')?.classList.toggle('sort-active', sortMode === 'filename');
      document.getElementById('sortTimestampButton')?.classList.toggle('sort-active', sortMode === 'timestamp');
    }
  </script>
</body>
</html>
""".replace("__SOURCE__", source_text).replace("__CONTENT__", content).replace(
        "__UI_THEME__", _parse_ui_theme(ui_theme)
    )


def resolve_recordings_directory(configured_location: str) -> Optional[Path]:
    """Resolve the sound recorder's MP3 folder relative to the program directory."""
    location = str(configured_location or "").strip()
    if not location:
        return None

    directory = Path(location).expanduser()
    if not directory.is_absolute():
        directory = SCRIPT_DIR / directory
    return directory.resolve()


def list_recording_mp3_files(
        configured_location: str) -> tuple[Optional[Path], list[Path], str]:
    """Return safe, top-level MP3 files from the configured recordings folder."""
    directory = resolve_recordings_directory(configured_location)
    if directory is None:
        return (
            None,
            [],
            "plugins.bqe_sound_recorder.recordings is not set in general_settings.yaml.",
        )
    if not directory.exists():
        return directory, [], f"The configured recordings folder does not exist: {directory}"
    if not directory.is_dir():
        return directory, [], f"The configured recordings location is not a folder: {directory}"

    try:
        recordings = []
        for entry in directory.iterdir():
            if entry.suffix.casefold() != ".mp3" or not entry.is_file():
                continue
            # Do not expose a symlink that resolves outside the configured folder.
            if entry.resolve().parent != directory:
                continue
            recordings.append(entry)
        recordings.sort(key=lambda path: (-path.stat().st_mtime, path.name.casefold()))
        message = "" if recordings else f"No MP3 files were found in {directory}."
        return directory, recordings, message
    except OSError as exc:
        return directory, [], f"Could not read the configured recordings folder: {exc}"


def resolve_recording_file(configured_location: str, requested_name: str) -> Optional[Path]:
    """Resolve one requested MP3 without allowing access outside the recordings folder."""
    directory = resolve_recordings_directory(configured_location)
    name = str(requested_name or "")
    if directory is None or not name or Path(name).name != name:
        return None
    if Path(name).suffix.casefold() != ".mp3":
        return None

    try:
        candidate = (directory / name).resolve()
        if candidate.parent != directory or not candidate.is_file():
            return None
        return candidate
    except OSError:
        return None


def build_recordings_html(
        configured_location: str, ui_theme: str = DEFAULT_UI_THEME) -> str:
    """Build an independent MP3 recordings browser and player page."""
    directory, recordings, message = list_recording_mp3_files(configured_location)
    rows = []
    for recording_path in recordings:
        try:
            stat = recording_path.stat()
        except OSError:
            continue

        safe_name = html.escape(recording_path.name)
        file_url = "/recording-file?name=" + quote(recording_path.name, safe="")
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
        rows.append(
            '<tr data-filename="{sort_name}" data-mtime="{mtime:.6f}">'
            '<td class="recording-actions">'
            '<input type="checkbox" class="morse-checkbox" data-url="{url}" '
            'data-name="{attribute_name}" aria-label="Select {attribute_name} for Morse decoding">'
            '<button type="button" class="play-button" data-url="{url}" '
            'data-name="{attribute_name}">Play</button></td>'
            '<td><a href="{url}" class="recording-link" data-url="{url}" '
            'data-name="{attribute_name}">{name}</a></td>'
            '<td>{modified}</td><td>{size}</td>'
            '<td class="morse-result" data-morse-result>Not analyzed</td></tr>'.format(
                sort_name=html.escape(recording_path.name.casefold(), quote=True),
                mtime=stat.st_mtime,
                url=html.escape(file_url, quote=True),
                attribute_name=html.escape(recording_path.name, quote=True),
                name=safe_name,
                modified=html.escape(modified.strftime("%Y-%m-%d %H:%M:%S %Z")),
                size=html.escape(_format_file_size(stat.st_size)),
            )
        )

    source_text = html.escape(str(directory)) if directory is not None else "Not configured"
    if message:
        content = f'<div class="recordings-message">{html.escape(message)}</div>'
    else:
        content = (
            '<table><thead><tr><th><label class="select-all-morse">'
            '<input type="checkbox" id="selectAllMorse"> Morse</label></th>'
            '<th>Recording</th><th>Last modified</th>'
            '<th>Size</th><th>Morse decode</th></tr></thead><tbody id="recordingsBody">'
            + "".join(rows) + "</tbody></table>"
        )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BQE WISP Recordings</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; background: #b9b9b9; color: #111;
      font-family: "Courier New", Consolas, monospace; font-weight: 700;
    }
    header {
      position: sticky; top: 0; z-index: 2; padding: 12px 16px;
      background: linear-gradient(180deg, #eeeeee, #bdbdbd);
      border-bottom: 2px ridge #d2d2d2; box-shadow: 0 2px 4px rgba(0,0,0,.25);
    }
    .header-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
    h1 { margin: 0; font-size: 24px; }
    .source { margin-top: 4px; font-size: 13px; overflow-wrap: anywhere; }
    .controls { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
    button {
      padding: 7px 14px; border: 2px outset #e4e4e4; background: #d2d2d2;
      color: #111; font: inherit; cursor: pointer;
    }
    button:active, button.sort-active { border-style: inset; background: #bcbcbc; }
    button:disabled { opacity: .52; cursor: default; }
    .player-panel {
      display: grid; grid-template-columns: minmax(160px, 1fr) minmax(300px, 2fr);
      align-items: center; gap: 14px; margin-top: 12px; padding: 10px;
      border: 2px inset #d0d0d0; background: #d2d2d2;
    }
    #nowPlaying { min-width: 0; overflow-wrap: anywhere; }
    audio { width: 100%; }
    .playback-options {
      grid-column: 1 / -1; display: flex; align-items: center; flex-wrap: wrap;
      gap: 9px 14px; padding-top: 2px;
    }
    .noise-reduction-toggle {
      display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
    }
    .noise-reduction-toggle input { width: 18px; height: 18px; accent-color: #e3c400; }
    #noiseReductionStatus { font-size: 12px; color: #333; }
    .audio-visuals {
      width: calc(100% - 32px); margin: 16px; padding: 14px;
      border: 2px inset #d0d0d0; background: #d2d2d2;
    }
    .visual-heading {
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 10px; margin-bottom: 7px;
    }
    .visual-title { font-size: 15px; }
    .analysis-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
    .analysis-controls label { white-space: nowrap; }
    #sensitivity { width: 150px; accent-color: #e3c400; }
    #sensitivityValue { min-width: 1.4em; text-align: center; }
    #analysisStatus { min-height: 1.25em; margin: 7px 0 12px; font-size: 13px; }
    .canvas-wrap {
      position: relative; width: 100%; overflow: hidden; background: #000;
      border: 2px inset #777;
    }
    #activityTimeline { display: block; width: 100%; height: 62px; cursor: pointer; }
    #oscilloscope { display: block; width: 100%; height: 112px; }
    .legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 7px 0 14px; font-size: 12px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 17px; height: 10px; border: 1px solid #555; background: #000; }
    .swatch.activity { background: #ffe600; }
    .swatch.playhead { width: 3px; background: #25dcff; border-color: #25dcff; }
    .morse-panel {
      display: flex; align-items: flex-start; gap: 10px; margin-top: 14px;
      padding: 10px; border: 2px inset #d0d0d0; background: #c8c8c8;
    }
    .morse-panel strong { white-space: nowrap; }
    #morseBatchStatus { font-size: 13px; font-weight: 500; }
    table { width: calc(100% - 32px); margin: 16px; border-collapse: collapse; background: #d2d2d2; }
    th, td { padding: 9px 12px; border: 2px inset #d0d0d0; text-align: left; }
    th { background: #c5c5c5; }
    th:first-child, td:first-child { width: 1%; white-space: nowrap; }
    th:last-child, td:last-child { width: 42%; }
    td:nth-child(3), td:nth-child(4) { white-space: nowrap; }
    .recording-actions { display: flex; align-items: center; gap: 8px; }
    .morse-checkbox, #selectAllMorse { width: 18px; height: 18px; accent-color: #e3c400; cursor: pointer; }
    .select-all-morse { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
    .morse-result { min-width: 260px; font-family: "Segoe UI", Ubuntu, Arial, sans-serif; font-weight: 500; }
    .morse-result details { min-width: 0; }
    .morse-result summary { cursor: pointer; font-weight: 700; overflow-wrap: anywhere; }
    .morse-result-meta { margin-top: 6px; font-size: 12px; color: #333; }
    .morse-candidate { margin-top: 8px; color: #4c4100; font-weight: 750; overflow-wrap: anywhere; }
    .morse-text {
      max-height: 190px; margin: 8px 0 0; padding: 8px; overflow: auto;
      border: 1px solid #777; background: #050505; color: #ffe600;
      font: 600 13px/1.45 Consolas, "Courier New", monospace; white-space: pre-wrap;
    }
    .morse-error { color: #9b1010; font-weight: 700; }
    a { color: #05058a; overflow-wrap: anywhere; }
    a:hover, a:focus { color: #b00000; outline: 2px solid #05058a; outline-offset: 2px; }
    tr.playing td { background: #fff3a8; }
    .recordings-message { margin: 24px; padding: 18px; border: 2px inset #d0d0d0; background: #d2d2d2; }
    body.modern-ui { background: #071321; color: #edf2f7; font-family: "Segoe UI", Ubuntu, Arial, sans-serif; font-weight: 500; }
    body.modern-ui header { background: #102943; border: 0; border-bottom: 1px solid #3f7199; box-shadow: 0 8px 24px rgba(0,0,0,.24); }
    body.modern-ui h1 { color: #f7fbff; font-size: 21px; }
    body.modern-ui .source { color: #b8ddf5; }
    body.modern-ui button { border: 1px solid #477aa3; border-radius: 7px; background: #1b4569; color: #f7fbff; font-weight: 600; }
    body.modern-ui button:hover, body.modern-ui button:focus-visible { background: #256391; border-color: #66c7ff; outline: 2px solid rgba(50,215,255,.32); }
    body.modern-ui button.sort-active { border-color: #32d7ff; background: #12618b; }
    body.modern-ui .player-panel, body.modern-ui table { border: 1px solid #3f7199; border-radius: 9px; background: #102943; }
    body.modern-ui .audio-visuals { border: 1px solid #3f7199; border-radius: 9px; background: #102943; }
    body.modern-ui .canvas-wrap { border: 1px solid #477aa3; border-radius: 6px; }
    body.modern-ui #analysisStatus, body.modern-ui .legend { color: #b8ddf5; }
    body.modern-ui #noiseReductionStatus { color: #b8ddf5; }
    body.modern-ui .morse-panel { border: 1px solid #3f7199; border-radius: 7px; background: #163b5c; }
    body.modern-ui #morseBatchStatus, body.modern-ui .morse-result-meta { color: #b8ddf5; }
    body.modern-ui .morse-candidate { color: #fff27a; }
    body.modern-ui .morse-error { color: #ff9cad; }
    body.modern-ui th, body.modern-ui td { border: 1px solid #315f80; }
    body.modern-ui th { background: #163b5c; color: #ccecff; }
    body.modern-ui tr:hover td { background: #173a5b; }
    body.modern-ui tr.playing td { background: #15566f; color: #fff; }
    body.modern-ui a { color: #66d9ff; }
    body.modern-ui a:hover, body.modern-ui a:focus { color: #fff27a; outline-color: #32d7ff; }
    body.modern-ui .recordings-message { border: 1px solid #3f7199; border-radius: 9px; background: #102943; }
    @media (max-width: 760px) {
      .header-row { align-items: flex-start; flex-direction: column; }
      .controls { justify-content: flex-start; }
      .player-panel { grid-template-columns: 1fr; }
      .audio-visuals { width: calc(100% - 16px); margin: 8px; padding: 10px; }
      .analysis-controls { align-items: flex-start; flex-direction: column; }
      #sensitivity { width: min(260px, 75vw); }
      .morse-panel { flex-direction: column; }
      table { width: calc(100% - 16px); margin: 8px; }
      th, td { padding: 7px 8px; }
      th:nth-child(3), td:nth-child(3), th:nth-child(4), td:nth-child(4) { display: none; }
      th:last-child, td:last-child { width: auto; }
      .morse-result { min-width: 190px; }
    }
  </style>
</head>
<body class="__UI_THEME__-ui">
  <header>
    <div class="header-row">
      <div><h1>Recordings</h1><div class="source">Folder: __SOURCE__</div></div>
      <div class="controls">
        <button type="button" id="morseDecodeButton" disabled>Decode Checked Morse</button>
        <button type="button" id="sortFilenameButton" onclick="sortRecordings('filename')">Sort by Filename</button>
        <button type="button" id="sortTimestampButton" class="sort-active" onclick="sortRecordings('timestamp')">Sort by Timestamp</button>
        <button type="button" onclick="window.location.reload()">Refresh</button>
      </div>
    </div>
    <div class="player-panel">
      <div id="nowPlaying">Choose a recording below.</div>
      <audio id="recordingPlayer" controls preload="metadata">Your browser does not support MP3 playback.</audio>
      <div class="playback-options">
        <label class="noise-reduction-toggle" for="noiseReductionCheckbox"
               title="Limits playback to the speech and CW audio range and adaptively suppresses noise-only intervals.">
          <input id="noiseReductionCheckbox" type="checkbox">
          Digital white-noise reduction
        </label>
        <span id="noiseReductionStatus">Off — original audio</span>
      </div>
    </div>
  </header>
  <section class="audio-visuals" aria-label="Audio analysis and oscilloscope">
    <div class="visual-heading">
      <div class="visual-title">Signal Activity Timeline</div>
      <div class="analysis-controls">
        <label for="sensitivity">Detection sensitivity</label>
        <input id="sensitivity" type="range" min="1" max="10" value="6" step="1">
        <output id="sensitivityValue" for="sensitivity">6</output>
      </div>
    </div>
    <div id="analysisStatus" role="status" aria-live="polite">Choose a recording to analyze.</div>
    <div class="canvas-wrap">
      <canvas id="activityTimeline" tabindex="0"
              aria-label="Signal activity timeline. Click or use the arrow keys to seek."></canvas>
    </div>
    <div class="legend" aria-hidden="true">
      <span class="legend-item"><span class="swatch"></span>Likely white noise</span>
      <span class="legend-item"><span class="swatch activity"></span>Likely signal activity</span>
      <span class="legend-item"><span class="swatch playhead"></span>Playback position</span>
    </div>
    <div class="visual-title">Live Oscilloscope</div>
    <div class="canvas-wrap" style="margin-top:7px">
      <canvas id="oscilloscope" aria-label="Live audio waveform"></canvas>
    </div>
    <div class="morse-panel">
      <strong>Morse Decoder</strong>
      <span id="morseBatchStatus" role="status" aria-live="polite">
        Check one or more recordings below, then select Decode Checked Morse.
        The decoder follows the strongest keyed tone as its pitch changes with Doppler.
      </span>
    </div>
  </section>
  __CONTENT__
  <script>
    const player = document.getElementById('recordingPlayer');
    const nowPlaying = document.getElementById('nowPlaying');
    const activityCanvas = document.getElementById('activityTimeline');
    const oscilloscopeCanvas = document.getElementById('oscilloscope');
    const sensitivitySlider = document.getElementById('sensitivity');
    const sensitivityValue = document.getElementById('sensitivityValue');
    const analysisStatus = document.getElementById('analysisStatus');
    const noiseReductionCheckbox = document.getElementById('noiseReductionCheckbox');
    const noiseReductionStatus = document.getElementById('noiseReductionStatus');
    const morseDecodeButton = document.getElementById('morseDecodeButton');
    const morseBatchStatus = document.getElementById('morseBatchStatus');
    const selectAllMorse = document.getElementById('selectAllMorse');

    let audioContext = null;
    let mediaSource = null;
    let analyser = null;
    let dryPlaybackGain = null;
    let reducedPlaybackGain = null;
    let noiseGateGain = null;
    let noiseReductionMeter = null;
    let noiseReductionMeterData = null;
    let estimatedNoiseFloorDb = null;
    let currentNoiseGateGain = 1;
    let lastNoiseReductionUpdate = 0;
    let oscilloscopeData = null;
    let activityScores = [];
    let activityDuration = 0;
    let analysisGeneration = 0;
    let lastTimelinePaint = 0;
    let morseDecodeRunning = false;

    const MORSE_CHARACTERS = Object.freeze({
      '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F',
      '--.': 'G', '....': 'H', '..': 'I', '.---': 'J', '-.-': 'K', '.-..': 'L',
      '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P', '--.-': 'Q', '.-.': 'R',
      '...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X',
      '-.--': 'Y', '--..': 'Z',
      '-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
      '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9',
      '.-.-.-': '.', '--..--': ',', '..--..': '?', '.----.': "'", '-.-.--': '!',
      '-..-.': '/', '-.--.': '(', '-.--.-': ')', '.-...': '&', '---...': ':',
      '-.-.-.': ';', '-...-': '=', '.-.-.': '+', '-....-': '-', '..--.-': '_',
      '.-..-.': '"', '...-..-': '$', '.--.-.': '@', '...---...': '<SOS>',
      '...-.-': '<SK>'
    });

    function clamp(value, low, high) {
      return Math.max(low, Math.min(high, value));
    }

    function formatTime(seconds) {
      const safeSeconds = Math.max(0, Number(seconds) || 0);
      const minutes = Math.floor(safeSeconds / 60);
      const remainder = Math.floor(safeSeconds % 60);
      return `${minutes}:${String(remainder).padStart(2, '0')}`;
    }

    function fitCanvas(canvas) {
      const ratio = Math.max(1, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.round(canvas.clientWidth));
      const height = Math.max(1, Math.round(canvas.clientHeight));
      const pixelWidth = Math.round(width * ratio);
      const pixelHeight = Math.round(height * ratio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      const context = canvas.getContext('2d');
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      return {context, width, height};
    }

    function activityThreshold() {
      const sensitivity = Number(sensitivitySlider.value || 6);
      // Higher sensitivity lowers the evidence required to mark a frame.
      return 0.82 - ((sensitivity - 1) * 0.055);
    }

    function activityMask() {
      const threshold = activityThreshold();
      const mask = activityScores.map(score => score >= threshold);
      // Bridge a one-frame hole between two active frames. Receiver fading can
      // otherwise make a continuous voice or Morse burst look fragmented.
      for (let index = 1; index < mask.length - 1; index += 1) {
        if (!mask[index] && mask[index - 1] && mask[index + 1]) mask[index] = true;
      }
      return mask;
    }

    function activitySummary() {
      if (!activityScores.length || !(activityDuration > 0)) return '';
      const mask = activityMask();
      let activeFrames = 0;
      let regions = 0;
      mask.forEach((active, index) => {
        if (active) {
          activeFrames += 1;
          if (index === 0 || !mask[index - 1]) regions += 1;
        }
      });
      const activeSeconds = activityDuration * activeFrames / mask.length;
      return `${regions} likely signal region${regions === 1 ? '' : 's'}; ` +
             `${formatTime(activeSeconds)} highlighted. Click the timeline to seek.`;
    }

    function drawActivityTimeline() {
      const {context, width, height} = fitCanvas(activityCanvas);
      context.fillStyle = '#000';
      context.fillRect(0, 0, width, height);

      if (activityScores.length) {
        const mask = activityMask();
        context.fillStyle = '#ffe600';
        mask.forEach((active, index) => {
          if (!active) return;
          const left = Math.floor(index * width / mask.length);
          const right = Math.ceil((index + 1) * width / mask.length);
          context.fillRect(left, 0, Math.max(1, right - left), height);
        });
      }

      context.strokeStyle = 'rgba(255,255,255,.20)';
      context.lineWidth = 1;
      for (let division = 1; division < 4; division += 1) {
        const x = Math.round(width * division / 4) + 0.5;
        context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
      }

      const duration = Number(player.duration) || activityDuration;
      if (duration > 0) {
        const playheadX = clamp((Number(player.currentTime) || 0) / duration, 0, 1) * width;
        context.strokeStyle = '#25dcff';
        context.lineWidth = 2;
        context.beginPath(); context.moveTo(playheadX, 0); context.lineTo(playheadX, height); context.stroke();

        context.font = '11px Consolas, monospace';
        context.fillStyle = 'rgba(255,255,255,.88)';
        context.textBaseline = 'bottom';
        context.fillText('0:00', 4, height - 3);
        const endLabel = formatTime(duration);
        const labelWidth = context.measureText(endLabel).width;
        context.fillText(endLabel, Math.max(4, width - labelWidth - 4), height - 3);
      }
    }

    function drawOscilloscope() {
      const {context, width, height} = fitCanvas(oscilloscopeCanvas);
      context.fillStyle = '#000';
      context.fillRect(0, 0, width, height);

      context.strokeStyle = 'rgba(70,255,115,.14)';
      context.lineWidth = 1;
      for (let division = 1; division < 4; division += 1) {
        const x = Math.round(width * division / 4) + 0.5;
        const y = Math.round(height * division / 4) + 0.5;
        context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
        context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
      }

      if (!analyser) {
        context.strokeStyle = '#2b713d';
        context.beginPath(); context.moveTo(0, height / 2); context.lineTo(width, height / 2); context.stroke();
        return;
      }

      if (!oscilloscopeData || oscilloscopeData.length !== analyser.fftSize) {
        oscilloscopeData = new Uint8Array(analyser.fftSize);
      }
      analyser.getByteTimeDomainData(oscilloscopeData);
      context.strokeStyle = '#67ff86';
      context.lineWidth = 2;
      context.beginPath();
      for (let index = 0; index < oscilloscopeData.length; index += 1) {
        const x = index * width / Math.max(1, oscilloscopeData.length - 1);
        const y = (oscilloscopeData[index] / 255) * height;
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      }
      context.stroke();
    }

    function visualizationLoop(timestamp) {
      updateNoiseReduction(timestamp);
      drawOscilloscope();
      if (timestamp - lastTimelinePaint >= 80) {
        drawActivityTimeline();
        lastTimelinePaint = timestamp;
      }
      window.requestAnimationFrame(visualizationLoop);
    }

    function setPlaybackGain(gainNode, target, instant = false) {
      if (!gainNode || !audioContext) return;
      const now = audioContext.currentTime;
      gainNode.gain.cancelScheduledValues(now);
      if (instant) {
        gainNode.gain.setValueAtTime(target, now);
      } else {
        gainNode.gain.setValueAtTime(gainNode.gain.value, now);
        gainNode.gain.linearRampToValueAtTime(target, now + 0.035);
      }
    }

    function resetNoiseReductionEstimator() {
      estimatedNoiseFloorDb = null;
      currentNoiseGateGain = 1;
      if (noiseGateGain && audioContext) {
        const now = audioContext.currentTime;
        noiseGateGain.gain.cancelScheduledValues(now);
        noiseGateGain.gain.setValueAtTime(1, now);
      }
    }

    function applyNoiseReductionState(instant = false) {
      const enabled = Boolean(noiseReductionCheckbox.checked);
      noiseReductionStatus.textContent = enabled
        ? 'On — adaptive speech/CW filtering'
        : 'Off — original audio';
      if (!audioContext || !dryPlaybackGain || !reducedPlaybackGain) return;
      setPlaybackGain(dryPlaybackGain, enabled ? 0 : 1, instant);
      setPlaybackGain(reducedPlaybackGain, enabled ? 1 : 0, instant);
      resetNoiseReductionEstimator();
    }

    function updateNoiseReduction(timestamp) {
      if (!noiseReductionCheckbox.checked || !noiseReductionMeter || !noiseGateGain || !audioContext) return;
      if (player.paused || player.ended) return;
      if (timestamp - lastNoiseReductionUpdate < 50) return;
      lastNoiseReductionUpdate = timestamp;

      if (!noiseReductionMeterData || noiseReductionMeterData.length !== noiseReductionMeter.fftSize) {
        noiseReductionMeterData = new Float32Array(noiseReductionMeter.fftSize);
      }
      noiseReductionMeter.getFloatTimeDomainData(noiseReductionMeterData);
      let sumSquares = 0;
      for (let index = 0; index < noiseReductionMeterData.length; index += 1) {
        sumSquares += noiseReductionMeterData[index] * noiseReductionMeterData[index];
      }
      const rms = Math.sqrt(sumSquares / Math.max(1, noiseReductionMeterData.length));
      const levelDb = 20 * Math.log10(rms + 1e-9);

      if (estimatedNoiseFloorDb === null) {
        estimatedNoiseFloorDb = levelDb;
      } else if (levelDb < estimatedNoiseFloorDb) {
        // Follow quieter noise estimates quickly.
        estimatedNoiseFloorDb += 0.22 * (levelDb - estimatedNoiseFloorDb);
      } else {
        // Follow increases very slowly so speech and CW do not become the new floor.
        estimatedNoiseFloorDb += 0.002 * (levelDb - estimatedNoiseFloorDb);
      }
      estimatedNoiseFloorDb = clamp(estimatedNoiseFloorDb, -90, -12);

      const snrDb = levelDb - estimatedNoiseFloorDb;
      const levelFraction = clamp((snrDb - 0.8) / 6.2, 0, 1);
      let targetGain = 0.27 + 0.73 * levelFraction;

      // Once the full-file analysis is ready, use its signal classification to
      // keep speech and Morse at full strength while reducing noise-only spans.
      if (activityScores.length && activityDuration > 0) {
        const position = clamp((Number(player.currentTime) || 0) / activityDuration, 0, 0.999999);
        const frameIndex = Math.floor(position * activityScores.length);
        const first = Math.max(0, frameIndex - 1);
        const last = Math.min(activityScores.length - 1, frameIndex + 1);
        let nearbyScore = 0;
        for (let index = first; index <= last; index += 1) {
          nearbyScore = Math.max(nearbyScore, activityScores[index]);
        }
        if (nearbyScore >= activityThreshold() * 0.92) targetGain = Math.max(targetGain, 0.96);
        else targetGain = Math.min(targetGain, 0.38);
      }

      const responseSpeed = targetGain > currentNoiseGateGain ? 0.65 : 0.18;
      currentNoiseGateGain += responseSpeed * (targetGain - currentNoiseGateGain);
      noiseGateGain.gain.setTargetAtTime(
        currentNoiseGateGain,
        audioContext.currentTime,
        targetGain > currentNoiseGateGain ? 0.012 : 0.09
      );
    }

    async function ensureAudioGraph() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('This browser does not provide Web Audio support.');
      if (!audioContext) {
        audioContext = new AudioContextClass();
        mediaSource = audioContext.createMediaElementSource(player);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.62;

        // Keep parallel dry and reduced paths so the checkbox can switch modes
        // cleanly without restarting or seeking the HTML audio player.
        dryPlaybackGain = audioContext.createGain();
        reducedPlaybackGain = audioContext.createGain();
        noiseGateGain = audioContext.createGain();
        noiseReductionMeter = audioContext.createAnalyser();
        noiseReductionMeter.fftSize = 1024;
        noiseReductionMeter.smoothingTimeConstant = 0;

        const highPass = audioContext.createBiquadFilter();
        highPass.type = 'highpass';
        highPass.frequency.value = 220;
        highPass.Q.value = 0.7;

        const lowPassOne = audioContext.createBiquadFilter();
        lowPassOne.type = 'lowpass';
        lowPassOne.frequency.value = 3300;
        lowPassOne.Q.value = 0.7;

        const lowPassTwo = audioContext.createBiquadFilter();
        lowPassTwo.type = 'lowpass';
        lowPassTwo.frequency.value = 3300;
        lowPassTwo.Q.value = 0.7;

        const presenceFilter = audioContext.createBiquadFilter();
        presenceFilter.type = 'peaking';
        presenceFilter.frequency.value = 1250;
        presenceFilter.Q.value = 0.75;
        presenceFilter.gain.value = 2.5;

        mediaSource.connect(dryPlaybackGain);
        dryPlaybackGain.connect(analyser);
        mediaSource.connect(highPass);
        highPass.connect(lowPassOne);
        lowPassOne.connect(lowPassTwo);
        lowPassTwo.connect(presenceFilter);
        presenceFilter.connect(noiseReductionMeter);
        noiseReductionMeter.connect(noiseGateGain);
        noiseGateGain.connect(reducedPlaybackGain);
        reducedPlaybackGain.connect(analyser);
        analyser.connect(audioContext.destination);
        applyNoiseReductionState(true);
      }
      if (audioContext.state === 'suspended') await audioContext.resume();
      return audioContext;
    }

    function fftInPlace(real, imaginary) {
      const size = real.length;
      for (let index = 1, reversed = 0; index < size; index += 1) {
        let bit = size >> 1;
        while (reversed & bit) { reversed ^= bit; bit >>= 1; }
        reversed ^= bit;
        if (index < reversed) {
          [real[index], real[reversed]] = [real[reversed], real[index]];
          [imaginary[index], imaginary[reversed]] = [imaginary[reversed], imaginary[index]];
        }
      }
      for (let length = 2; length <= size; length <<= 1) {
        const angle = -2 * Math.PI / length;
        const stepReal = Math.cos(angle);
        const stepImaginary = Math.sin(angle);
        for (let start = 0; start < size; start += length) {
          let twiddleReal = 1;
          let twiddleImaginary = 0;
          for (let offset = 0; offset < length / 2; offset += 1) {
            const even = start + offset;
            const odd = even + length / 2;
            const oddReal = real[odd] * twiddleReal - imaginary[odd] * twiddleImaginary;
            const oddImaginary = real[odd] * twiddleImaginary + imaginary[odd] * twiddleReal;
            real[odd] = real[even] - oddReal;
            imaginary[odd] = imaginary[even] - oddImaginary;
            real[even] += oddReal;
            imaginary[even] += oddImaginary;
            const nextReal = twiddleReal * stepReal - twiddleImaginary * stepImaginary;
            twiddleImaginary = twiddleReal * stepImaginary + twiddleImaginary * stepReal;
            twiddleReal = nextReal;
          }
        }
      }
    }

    function median(values) {
      const sorted = Array.from(values).sort((left, right) => left - right);
      if (!sorted.length) return 0;
      const middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }

    function robustScale(values, center, minimum) {
      const deviations = values.map(value => Math.abs(value - center));
      return Math.max(minimum, median(deviations) * 1.4826);
    }

    function positiveEvidence(zScore, start = 1.25, full = 6.0) {
      return clamp((zScore - start) / Math.max(0.01, full - start), 0, 1);
    }

    async function analyzeAudioBuffer(buffer, generation) {
      const duration = Number(buffer.duration) || 0;
      if (!(duration > 0)) throw new Error('The selected MP3 has no decodable audio.');

      const fftSize = 512;
      const nativeRate = buffer.sampleRate;
      const stride = Math.max(1, Math.floor(nativeRate / 8000));
      const analysisRate = nativeRate / stride;
      const frameCount = clamp(Math.ceil(duration / 0.12), 120, 6000);
      const channels = [];
      for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
        channels.push(buffer.getChannelData(channel));
      }
      const windowValues = new Float64Array(fftSize);
      for (let index = 0; index < fftSize; index += 1) {
        windowValues[index] = 0.5 - 0.5 * Math.cos(2 * Math.PI * index / (fftSize - 1));
      }

      const flatnessValues = new Float64Array(frameCount);
      const entropyValues = new Float64Array(frameCount);
      const peakValues = new Float64Array(frameCount);
      const rmsValues = new Float64Array(frameCount);
      const real = new Float64Array(fftSize);
      const imaginary = new Float64Array(fftSize);
      const firstBin = Math.max(1, Math.floor(90 * fftSize / analysisRate));
      const lastBin = Math.min(fftSize / 2 - 1, Math.ceil(3600 * fftSize / analysisRate));
      const usableBins = Math.max(1, lastBin - firstBin + 1);
      const epsilon = 1e-20;
      const powers = new Float64Array(usableBins);

      for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
        if (generation !== analysisGeneration) return null;
        const centerSample = Math.floor(((frameIndex + 0.5) / frameCount) * buffer.length);
        const firstSample = centerSample - Math.floor(fftSize * stride / 2);
        let sumSquares = 0;
        for (let sampleIndex = 0; sampleIndex < fftSize; sampleIndex += 1) {
          const nativeIndex = clamp(firstSample + sampleIndex * stride, 0, buffer.length - 1);
          let sample = 0;
          for (let channel = 0; channel < channels.length; channel += 1) {
            sample += channels[channel][nativeIndex];
          }
          sample /= Math.max(1, channels.length);
          sumSquares += sample * sample;
          real[sampleIndex] = sample * windowValues[sampleIndex];
          imaginary[sampleIndex] = 0;
        }
        rmsValues[frameIndex] = 20 * Math.log10(Math.sqrt(sumSquares / fftSize) + 1e-12);
        fftInPlace(real, imaginary);

        let totalPower = 0;
        let maximumPower = 0;
        let logPower = 0;
        for (let bin = firstBin; bin <= lastBin; bin += 1) {
          const power = real[bin] * real[bin] + imaginary[bin] * imaginary[bin] + epsilon;
          powers[bin - firstBin] = power;
          totalPower += power;
          maximumPower = Math.max(maximumPower, power);
          logPower += Math.log(power);
        }
        const meanPower = totalPower / usableBins;
        flatnessValues[frameIndex] = Math.exp(logPower / usableBins) / Math.max(epsilon, meanPower);
        peakValues[frameIndex] = 10 * Math.log10(maximumPower / Math.max(epsilon, meanPower));

        let entropy = 0;
        for (let index = 0; index < powers.length; index += 1) {
          const probability = powers[index] / Math.max(epsilon, totalPower);
          entropy -= probability * Math.log(probability + epsilon);
        }
        entropyValues[frameIndex] = entropy / Math.log(usableBins);

        if (frameIndex % 120 === 0) {
          const percent = Math.round(100 * frameIndex / frameCount);
          analysisStatus.textContent = `Analyzing signal structure… ${percent}%`;
          await new Promise(resolve => window.setTimeout(resolve, 0));
        }
      }

      const flatnessCenter = median(flatnessValues);
      const entropyCenter = median(entropyValues);
      const peakCenter = median(peakValues);
      const rmsCenter = median(rmsValues);
      const flatnessScale = robustScale(flatnessValues, flatnessCenter, 0.012);
      const entropyScale = robustScale(entropyValues, entropyCenter, 0.008);
      const peakScale = robustScale(peakValues, peakCenter, 0.7);
      const rmsScale = robustScale(rmsValues, rmsCenter, 0.8);
      const rawScores = new Float64Array(frameCount);

      for (let index = 0; index < frameCount; index += 1) {
        const flatnessEvidence = positiveEvidence(
          (flatnessCenter - flatnessValues[index]) / flatnessScale
        );
        const entropyEvidence = positiveEvidence(
          (entropyCenter - entropyValues[index]) / entropyScale
        );
        const peakEvidence = positiveEvidence(
          (peakValues[index] - peakCenter) / peakScale, 1.4, 7.0
        );
        const levelEvidence = positiveEvidence(
          (rmsValues[index] - rmsCenter) / rmsScale, 1.5, 7.0
        );
        const structuredEvidence = Math.max(
          0.55 * flatnessEvidence + 0.45 * entropyEvidence,
          0.72 * peakEvidence + 0.28 * levelEvidence
        );
        rawScores[index] = clamp(
          0.72 * structuredEvidence + 0.18 * peakEvidence + 0.10 * levelEvidence,
          0,
          1
        );
      }

      const smoothedScores = Array.from(rawScores, (score, index) => {
        const previous = rawScores[Math.max(0, index - 1)];
        const next = rawScores[Math.min(frameCount - 1, index + 1)];
        return clamp(0.62 * score + 0.19 * previous + 0.19 * next, 0, 1);
      });
      return {scores: smoothedScores, duration};
    }

    function percentile(values, fraction) {
      const sorted = Array.from(values).filter(Number.isFinite).sort((left, right) => left - right);
      if (!sorted.length) return 0;
      const position = clamp(fraction, 0, 1) * (sorted.length - 1);
      const lower = Math.floor(position);
      const upper = Math.ceil(position);
      if (lower === upper) return sorted[lower];
      const blend = position - lower;
      return sorted[lower] * (1 - blend) + sorted[upper] * blend;
    }

    function twoMeans(values, lowerFraction = 0.30, upperFraction = 0.85) {
      const finiteValues = Array.from(values).filter(Number.isFinite);
      if (!finiteValues.length) return {low: 0, high: 0};
      let low = percentile(finiteValues, lowerFraction);
      let high = percentile(finiteValues, upperFraction);
      for (let iteration = 0; iteration < 35; iteration += 1) {
        let lowTotal = 0;
        let lowCount = 0;
        let highTotal = 0;
        let highCount = 0;
        finiteValues.forEach(value => {
          if (Math.abs(value - high) < Math.abs(value - low)) {
            highTotal += value;
            highCount += 1;
          } else {
            lowTotal += value;
            lowCount += 1;
          }
        });
        const nextLow = lowCount ? lowTotal / lowCount : low;
        const nextHigh = highCount ? highTotal / highCount : high;
        if (Math.max(Math.abs(nextLow - low), Math.abs(nextHigh - high)) < 1e-7) {
          low = nextLow;
          high = nextHigh;
          break;
        }
        low = nextLow;
        high = nextHigh;
      }
      return low <= high ? {low, high} : {low: high, high: low};
    }

    function maskRuns(mask) {
      const runs = [];
      if (!mask.length) return runs;
      let start = 0;
      let active = Boolean(mask[0]);
      for (let index = 1; index <= mask.length; index += 1) {
        if (index === mask.length || Boolean(mask[index]) !== active) {
          runs.push({start, length: index - start, active});
          start = index;
          active = index < mask.length ? Boolean(mask[index]) : false;
        }
      }
      return runs;
    }

    function cleanMorseMask(mask, frameSeconds) {
      const cleaned = mask.slice();
      // A retune can briefly interrupt a mark. Bridge those short gaps without
      // deleting equally short dots at higher keying speeds.
      const shortRunFrames = Math.max(1, Math.floor(0.008 / frameSeconds));
      const shortGapFrames = Math.max(1, Math.floor(0.024 / frameSeconds));
      for (let pass = 0; pass < 2; pass += 1) {
        let runs = maskRuns(cleaned);
        runs.forEach((run, index) => {
          if (!run.active && index > 0 && index < runs.length - 1 && run.length <= shortGapFrames) {
            cleaned.fill(1, run.start, run.start + run.length);
          }
        });
        runs = maskRuns(cleaned);
        runs.forEach(run => {
          if (run.active && run.length <= shortRunFrames) {
            cleaned.fill(0, run.start, run.start + run.length);
          }
        });
      }
      return cleaned;
    }

    function repeatedMorseCandidate(text) {
      const tokens = String(text || '').split(/\\s+/).filter(Boolean);
      const normalizeStart = candidate => {
        if (!candidate) return null;
        const phraseTokens = candidate.text.split(' ');
        const cqIndex = phraseTokens.indexOf('CQ');
        if (cqIndex > 0) {
          candidate.text = phraseTokens.slice(cqIndex).concat(phraseTokens.slice(0, cqIndex)).join(' ');
        }
        return candidate;
      };
      const maximumLength = Math.min(24, Math.floor(tokens.length / 2));
      let bestRepeatedTwice = null;
      for (let length = maximumLength; length >= 4; length -= 1) {
        const occurrences = new Map();
        for (let start = 0; start + length <= tokens.length; start += 1) {
          const phraseTokens = tokens.slice(start, start + length);
          if (phraseTokens.some(token => token.includes('?'))) continue;
          const phrase = phraseTokens.join(' ');
          const positions = occurrences.get(phrase) || [];
          if (!positions.length || start - positions[positions.length - 1] >= length) positions.push(start);
          occurrences.set(phrase, positions);
        }
        let bestAtThisLength = null;
        occurrences.forEach((positions, phrase) => {
          if (positions.length < 2) return;
          if (!bestAtThisLength || positions.length > bestAtThisLength.count ||
              (positions.length === bestAtThisLength.count && phrase.length > bestAtThisLength.text.length)) {
            bestAtThisLength = {text: phrase, count: positions.length};
          }
        });
        // Three matching transmissions are much stronger evidence of the
        // actual repeated beacon text than a longer phrase seen only twice.
        if (bestAtThisLength?.count >= 3) return normalizeStart(bestAtThisLength);
        if (!bestRepeatedTwice && bestAtThisLength) bestRepeatedTwice = bestAtThisLength;
      }
      return normalizeStart(bestRepeatedTwice);
    }

    async function decodeMorseAudioBuffer(buffer, progressCallback = () => {}) {
      const duration = Number(buffer.duration) || 0;
      if (!(duration > 0)) throw new Error('The selected MP3 has no decodable audio.');

      // Each short frame searches the entire CW audio band for a narrow peak.
      // No fixed pitch is assumed, so a Doppler-shifted tone can move freely
      // between frames while its keyed timing remains intact.
      const fftSize = 128;
      const nativeRate = buffer.sampleRate;
      const stride = Math.max(1, Math.round(nativeRate / 8000));
      const analysisRate = nativeRate / stride;
      const requestedHopSeconds = Math.max(0.008, duration / 140000);
      const frameCount = Math.max(1, Math.ceil(duration / requestedHopSeconds));
      const frameSeconds = duration / frameCount;
      const channels = [];
      for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
        channels.push(buffer.getChannelData(channel));
      }

      const windowValues = new Float64Array(fftSize);
      for (let index = 0; index < fftSize; index += 1) {
        windowValues[index] = 0.5 - 0.5 * Math.cos(2 * Math.PI * index / (fftSize - 1));
      }
      // Include guard bins for the three-bin energy measurement at each edge.
      const firstBin = Math.max(0, Math.ceil(120 * fftSize / analysisRate) - 1);
      const lastBin = Math.min(fftSize / 2 - 1, Math.floor(3400 * fftSize / analysisRate) + 1);
      const bandBinCount = lastBin - firstBin + 1;
      if (bandBinCount < 8) throw new Error('The decoded sample rate is too low for Morse analysis.');

      const real = new Float64Array(fftSize);
      const imaginary = new Float64Array(fftSize);
      const bandPowers = new Float64Array(bandBinCount);
      const noisePowers = new Float64Array(bandBinCount);
      const prominenceDb = new Float32Array(frameCount);
      const peakFrequencies = new Float32Array(frameCount);
      const temporalProminence = new Float32Array(frameCount);
      const temporalScores = new Float32Array(frameCount * bandBinCount);
      const spectralPowers = new Float32Array(frameCount * bandBinCount);
      // Bounded by the frame cap (about 70 MB total spectral working storage
      // at 8 kHz). No recording-sized JavaScript object graph is created.
      const carrierMask = new Uint8Array(frameCount * bandBinCount);
      const epsilon = 1e-20;
      const noiseFall = 1 - Math.exp(-frameSeconds / 0.1);
      const noiseRise = 1 - Math.exp(-frameSeconds / 2);

      for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
        const centerSample = Math.floor(((frameIndex + 0.5) / frameCount) * buffer.length);
        const firstSample = centerSample - Math.floor(fftSize * stride / 2);
        for (let sampleIndex = 0; sampleIndex < fftSize; sampleIndex += 1) {
          const nativeIndex = clamp(firstSample + sampleIndex * stride, 0, buffer.length - 1);
          let sample = 0;
          for (let channel = 0; channel < channels.length; channel += 1) {
            sample += channels[channel][nativeIndex];
          }
          sample /= Math.max(1, channels.length);
          real[sampleIndex] = sample * windowValues[sampleIndex];
          imaginary[sampleIndex] = 0;
        }
        fftInPlace(real, imaginary);

        for (let bin = firstBin; bin <= lastBin; bin += 1) {
          const power = real[bin] * real[bin] + imaginary[bin] * imaginary[bin] + epsilon;
          const slot = bin - firstBin;
          bandPowers[slot] = power;
          spectralPowers[frameIndex * bandBinCount + slot] = power;
          if (frameIndex === 0) noisePowers[slot] = power;
          else {
            // Follow key-up noise quickly, but limit how much a keyed tone
            // can raise its own noise estimate. Use elapsed time so long-file
            // frame limits do not change the estimator's time constants.
            const previous = noisePowers[slot];
            noisePowers[slot] += power < previous
              ? noiseFall * (power - previous)
              : noiseRise * (Math.min(power, previous * 4) - previous);
          }
        }
        // Measure narrowband contrast against nearby noise, and retain a
        // separate key-up noise reference for frames straddling a retune.
        for (let bin = 1; bin < bandBinCount - 1; bin += 1) {
          const noise = (noisePowers[bin - 1] + noisePowers[bin] + noisePowers[bin + 1]) / 3;
          const peak = (bandPowers[bin - 1] + bandPowers[bin] + bandPowers[bin + 1]) / 3;
          let nearbyNoise = 0;
          let neighbors = 0;
          for (let offset = -8; offset <= 8; offset += 1) {
            const other = bin + offset;
            if (Math.abs(offset) <= 2 || other < 0 || other >= bandBinCount) continue;
            const value = bandPowers[other];
            nearbyNoise += value;
            neighbors += 1;
          }
          const contrast = peak / Math.max(epsilon,
            nearbyNoise / neighbors);
          const slot = frameIndex * bandBinCount + bin;
          // 1 = strongly tonal; the persistence pass upgrades birdies to 2.
          carrierMask[slot] = contrast >= 10 ? 1 : 0;
          // Compensate for the deliberately low-biased noise tracker.
          temporalScores[slot] = 10 * Math.log10(Math.max(1, peak / Math.max(epsilon, 3 * noise)));
        }

        if (frameIndex % 400 === 0) {
          progressCallback(Math.round(70 * frameIndex / frameCount));
          await new Promise(resolve => window.setTimeout(resolve, 0));
        }
      }

      // Remove sustained spectral ridges before choosing a peak, so a
      // stronger unkeyed birdie cannot hide a weaker keyed signal. A retune
      // starts a new ridge, and the whole persistent run is suppressed.
      const carrierFrames = Math.ceil(0.65 / frameSeconds);
      for (let bin = 1; bin < bandBinCount - 1; bin += 1) {
        let start = -1;
        let last = -1;
        for (let frame = 0; frame <= frameCount; frame += 1) {
          const present = frame < frameCount &&
            carrierMask[frame * bandBinCount + bin] === 1;
          if (present) {
            if (start < 0) start = frame;
            last = frame;
          }
          // Do not bridge key-up gaps in this pass: five dashes must not be
          // mistaken for one continuous carrier.
          if (start >= 0 && !present) {
            if (last - start + 1 >= carrierFrames) {
              for (let k = start; k <= last; k += 1) carrierMask[k * bandBinCount + bin] = 2;
            }
            start = -1;
          }
        }
        if (bin % 12 === 0) {
          progressCallback(70 + Math.round(15 * bin / bandBinCount));
          await new Promise(resolve => window.setTimeout(resolve, 0));
        }
      }
      // Mask the window's neighboring bins and edge frames as well, so
      // leakage at a birdie retune does not masquerade as a short CW mark.
      for (let frame = 0; frame < frameCount; frame += 1) {
        for (let bin = 1; bin < bandBinCount - 1; bin += 1) {
          if (carrierMask[frame * bandBinCount + bin] !== 2) continue;
          for (let f = Math.max(0, frame - 2); f <= Math.min(frameCount - 1, frame + 2); f += 1) {
            for (let b = bin - 1; b <= bin + 1; b += 1) {
              const slot = f * bandBinCount + b;
              if (carrierMask[slot] < 2) carrierMask[slot] = 3;
            }
          }
        }
      }
      for (let frame = 0; frame < frameCount; frame += 1) {
        let best = 0;
        let selected = 0;
        const offset = frame * bandBinCount;
        let maximum = 0;
        for (let bin = 1; bin < bandBinCount - 1; bin += 1) {
          const slot = offset + bin;
          if (carrierMask[slot] < 2 && spectralPowers[slot] >= spectralPowers[slot - 1] &&
              spectralPowers[slot] >= spectralPowers[slot + 1]) {
            maximum = Math.max(maximum, spectralPowers[slot]);
          }
        }
        for (let bin = 1; bin < bandBinCount - 1; bin += 1) {
          const slot = offset + bin;
          if (carrierMask[slot] >= 2 || spectralPowers[slot] < maximum * 0.1 ||
              spectralPowers[slot] < spectralPowers[slot - 1] ||
              spectralPowers[slot] < spectralPowers[slot + 1]) continue;
          let noise = 0;
          let count = 0;
          for (let delta = -8; delta <= 8; delta += 1) {
            const neighbor = bin + delta;
            if (Math.abs(delta) <= 2 || neighbor < 0 || neighbor >= bandBinCount ||
                carrierMask[offset + neighbor] >= 2) continue;
            noise += spectralPowers[offset + neighbor];
            count += 1;
          }
          if (count < 4) continue;
          const peak = (spectralPowers[slot - 1] + spectralPowers[slot] + spectralPowers[slot + 1]) / 3;
          const score = 10 * Math.log10(Math.max(1, peak / Math.max(epsilon, noise / count)));
          if (score > best) { best = score; selected = bin; }
        }
        prominenceDb[frame] = best;
        temporalProminence[frame] = temporalScores[offset + selected];
        peakFrequencies[frame] = (selected + firstBin) * analysisRate / fftSize;
        if (frame % 4000 === 0) {
          progressCallback(85 + Math.round(5 * frame / frameCount));
          await new Promise(resolve => window.setTimeout(resolve, 0));
        }
      }
      progressCallback(90);

      // At a large, well-supported pitch jump, spectral spreading can look
      // like key-up. Consult the temporal reference only around that jump;
      // do not let ordinary noise peaks create marks across the recording.
      for (let i = 1; i < frameCount; i += 1) {
        if (Math.abs(peakFrequencies[i] - peakFrequencies[i - 1]) < 500 ||
            temporalProminence[i] < 30 || temporalProminence[i - 1] < 30) continue;
        for (let j = Math.max(0, i - 2); j <= Math.min(frameCount - 1, i + 2); j += 1) {
          prominenceDb[j] = Math.max(prominenceDb[j], temporalProminence[j]);
        }
      }

      const globalClusters = twoMeans(prominenceDb);
      const globalSeparation = globalClusters.high - globalClusters.low;
      if (globalSeparation < 6.0) {
        throw new Error('No clearly keyed narrowband Morse tone was found.');
      }

      // Re-estimate the on/off threshold in short blocks. This follows fading
      // and changing receiver noise while the wide-band peak search follows Doppler.
      const rawMask = new Uint8Array(frameCount);
      const blockFrames = Math.max(1, Math.round(0.25 / frameSeconds));
      const contextFrames = Math.round(0.75 / frameSeconds);
      let hasKeying = false;
      const gateFrames = Math.round(5 / frameSeconds);
      for (let start = 0; start < frameCount; start += blockFrames) {
        const end = Math.min(frameCount, start + blockFrames);
        if (start % (4 * blockFrames) === 0) {
          // This coarse gate only identifies keyed stretches, so subsample
          // its statistics instead of repeatedly sorting every audio frame.
          const gateValues = [];
          for (let i = Math.max(0, start - gateFrames);
               i < Math.min(frameCount, start + gateFrames); i += 4) {
            gateValues.push(prominenceDb[i]);
          }
          const clusters = twoMeans(gateValues);
          hasKeying = clusters.high - clusters.low >= 6 && clusters.high >= 16;
        }
        if (!hasKeying) continue;
        const localValues = prominenceDb.slice(Math.max(0, start - contextFrames),
          Math.min(frameCount, end + contextFrames));
        const high = percentile(localValues, 0.85);
        const threshold = Math.max(12, high - 10);
        for (let index = start; index < end; index += 1) {
          if (prominenceDb[index] >= threshold) rawMask[index] = 1;
        }
      }

      const cleanedMask = cleanMorseMask(rawMask, frameSeconds);
      const runs = maskRuns(cleanedMask);
      const pulseDurations = runs
        .filter(run => run.active)
        .map(run => run.length * frameSeconds)
        .filter(value => value >= Math.max(0.012, frameSeconds) && value <= 0.65);
      if (pulseDurations.length < 8) {
        throw new Error('Too few regularly keyed pulses were found to decode Morse.');
      }

      const pulseClusters = twoMeans(pulseDurations, 0.25, 0.75);
      const clusterBoundary = (pulseClusters.low + pulseClusters.high) / 2;
      // Fade fragments and unrelated bursts should not move the speed of
      // an otherwise regular beacon. Fit the central part of each cluster.
      const trimmedMean = values => {
        const low = percentile(values, 0.2);
        const high = percentile(values, 0.8);
        const central = values.filter(value => value >= low && value <= high);
        return central.length ? central.reduce((sum, value) => sum + value, 0) / central.length : median(values);
      };
      const shortPulses = pulseDurations.filter(value => value <= clusterBoundary);
      const longPulses = pulseDurations.filter(value => value > clusterBoundary);
      if (shortPulses.length && longPulses.length) {
        pulseClusters.low = trimmedMean(shortPulses);
        pulseClusters.high = trimmedMean(longPulses);
      }
      const separatedPulses = pulseClusters.high / Math.max(0.001, pulseClusters.low) >= 1.65;
      // The analysis window and threshold shorten marks and lengthen spaces
      // (or vice versa). Fit that common bias as well as the keying speed.
      const dotSeconds = separatedPulses
        ? (pulseClusters.high - pulseClusters.low) / 2
        : percentile(pulseDurations, 0.25);
      const keyingBias = separatedPulses
        ? clamp(pulseClusters.low - dotSeconds, -0.5 * dotSeconds, 0.5 * dotSeconds) : 0;
      if (dotSeconds < 0.025 || dotSeconds > 0.50) {
        throw new Error('Keying pulses were found, but their timing does not resemble Morse code.');
      }

      const pulseBoundary = 2.0 * dotSeconds + keyingBias;
      const characterGapBoundary = 2.0 * dotSeconds - keyingBias;
      const wordGapBoundary = 5.0 * dotSeconds - keyingBias;
      const minimumPulse = Math.max(frameSeconds, 0.30 * dotSeconds);
      const decoded = [];
      const timingErrors = [];
      let currentPattern = '';
      let decodedCharacterCount = 0;
      let unknownCharacterCount = 0;
      let pulseCount = 0;

      function finishCharacter() {
        if (!currentPattern) return;
        const character = MORSE_CHARACTERS[currentPattern];
        decoded.push(character || '?');
        decodedCharacterCount += 1;
        if (!character) unknownCharacterCount += 1;
        currentPattern = '';
      }

      runs.forEach(run => {
        const runSeconds = run.length * frameSeconds;
        if (run.active) {
          if (runSeconds < minimumPulse) return;
          if (runSeconds > Math.max(0.65, 5 * dotSeconds)) {
            // A sustained carrier cannot be a dash at the measured speed.
            finishCharacter();
            if (decoded.length && decoded[decoded.length - 1] !== ' ') decoded.push(' ');
            return;
          }
          const dash = runSeconds >= pulseBoundary;
          currentPattern += dash ? '-' : '.';
          pulseCount += 1;
          const expectedUnits = dash ? 3 : 1;
          timingErrors.push(Math.abs((runSeconds - keyingBias) / dotSeconds - expectedUnits) / expectedUnits);
          return;
        }
        if (!currentPattern) return;
        if (runSeconds >= wordGapBoundary) {
          finishCharacter();
          if (decoded.length && decoded[decoded.length - 1] !== ' ') decoded.push(' ');
        } else if (runSeconds >= characterGapBoundary) {
          finishCharacter();
        }
      });
      finishCharacter();

      const text = decoded.join('').replace(/\\s+/g, ' ').trim();
      if (!text || decodedCharacterCount < 2) {
        throw new Error('Morse-like pulses were found, but no readable characters could be formed.');
      }

      const activeFrequencies = [];
      for (let index = 0; index < cleanedMask.length; index += 1) {
        if (cleanedMask[index]) activeFrequencies.push(peakFrequencies[index]);
      }
      const minimumFrequency = Math.round(percentile(activeFrequencies, 0.05));
      const maximumFrequency = Math.round(percentile(activeFrequencies, 0.95));
      const knownFraction = 1 - unknownCharacterCount / Math.max(1, decodedCharacterCount);
      const timingScore = 1 - clamp(median(timingErrors), 0, 1);
      const separationScore = clamp((globalSeparation - 5) / 18, 0, 1);
      const confidence = Math.round(100 * (
        0.42 * knownFraction + 0.33 * timingScore + 0.25 * separationScore
      ));
      const repeatedCandidate = repeatedMorseCandidate(text);
      progressCallback(100);

      return {
        text,
        repeatedCandidate,
        confidence: clamp(confidence, 0, 99),
        dotSeconds,
        wordsPerMinute: 1.2 / dotSeconds,
        minimumFrequency,
        maximumFrequency,
        pulseCount,
        unknownCharacterCount,
      };
    }

    function setMorseCellStatus(cell, message, error = false) {
      cell.replaceChildren();
      const status = document.createElement('span');
      status.className = error ? 'morse-error' : '';
      status.textContent = message;
      cell.appendChild(status);
    }

    function renderMorseResult(cell, result) {
      cell.replaceChildren();
      const details = document.createElement('details');
      details.open = true;
      const summary = document.createElement('summary');
      const preferredText = result.repeatedCandidate?.text || result.text;
      const excerpt = preferredText.length > 150 ? preferredText.slice(0, 147) + '…' : preferredText;
      summary.textContent = excerpt;
      details.appendChild(summary);

      const metadata = document.createElement('div');
      metadata.className = 'morse-result-meta';
      metadata.textContent = `Estimated ${result.wordsPerMinute.toFixed(1)} WPM; ` +
        `tone ${result.minimumFrequency}–${result.maximumFrequency} Hz; ` +
        `${result.pulseCount} pulses; signal/timing score ${result.confidence}/100 (not text accuracy).`;
      details.appendChild(metadata);

      if (result.repeatedCandidate) {
        const candidate = document.createElement('div');
        candidate.className = 'morse-candidate';
        candidate.textContent = `Repeated message candidate (${result.repeatedCandidate.count} occurrences): ` +
          result.repeatedCandidate.text;
        details.appendChild(candidate);
      }

      const fullText = document.createElement('pre');
      fullText.className = 'morse-text';
      fullText.textContent = result.text;
      details.appendChild(fullText);
      cell.appendChild(details);
    }

    function updateMorseSelectionState() {
      const boxes = Array.from(document.querySelectorAll('.morse-checkbox'));
      const selectedCount = boxes.filter(box => box.checked).length;
      morseDecodeButton.disabled = morseDecodeRunning || selectedCount === 0;
      if (selectAllMorse) {
        selectAllMorse.checked = boxes.length > 0 && selectedCount === boxes.length;
        selectAllMorse.indeterminate = selectedCount > 0 && selectedCount < boxes.length;
      }
    }

    async function decodeCheckedMorse() {
      const selected = Array.from(document.querySelectorAll('.morse-checkbox'))
        .filter(checkbox => checkbox.checked);
      if (!selected.length || morseDecodeRunning) return;
      morseDecodeRunning = true;
      updateMorseSelectionState();
      let successes = 0;

      try {
        const context = await ensureAudioGraph();
        for (let itemIndex = 0; itemIndex < selected.length; itemIndex += 1) {
          const checkbox = selected[itemIndex];
          const row = checkbox.closest('tr');
          const cell = row?.querySelector('[data-morse-result]');
          const name = checkbox.dataset.name || 'recording';
          const url = checkbox.dataset.url;
          if (!cell || !url) continue;
          checkbox.disabled = true;
          setMorseCellStatus(cell, 'Loading MP3…');
          morseBatchStatus.textContent = `Decoding ${itemIndex + 1} of ${selected.length}: ${name}`;
          try {
            const response = await fetch(url, {cache: 'no-store'});
            if (!response.ok) throw new Error(`MP3 request failed with HTTP ${response.status}.`);
            const encodedAudio = await response.arrayBuffer();
            setMorseCellStatus(cell, 'Decoding audio…');
            const decodedAudio = await context.decodeAudioData(encodedAudio.slice(0));
            const result = await decodeMorseAudioBuffer(decodedAudio, percent => {
              setMorseCellStatus(cell, `Following Doppler-shifted tone… ${percent}%`);
              morseBatchStatus.textContent =
                `Decoding ${itemIndex + 1} of ${selected.length}: ${name} (${percent}%)`;
            });
            renderMorseResult(cell, result);
            successes += 1;
          } catch (error) {
            setMorseCellStatus(cell, error.message || String(error), true);
          } finally {
            checkbox.disabled = false;
          }
        }
        morseBatchStatus.textContent =
          `Finished: ${successes} of ${selected.length} selected recording${selected.length === 1 ? '' : 's'} decoded. ` +
          'Automatic Morse decoding can contain errors when signals fade or overlap.';
      } catch (error) {
        morseBatchStatus.textContent = `Morse decoding is unavailable: ${error.message || error}`;
      } finally {
        selected.forEach(checkbox => { checkbox.disabled = false; });
        morseDecodeRunning = false;
        updateMorseSelectionState();
      }
    }

    async function analyzeRecording(url, generation) {
      try {
        analysisStatus.textContent = 'Loading MP3 for analysis…';
        const response = await fetch(url, {cache: 'no-store'});
        if (!response.ok) throw new Error(`The MP3 request failed with HTTP ${response.status}.`);
        const encodedAudio = await response.arrayBuffer();
        if (generation !== analysisGeneration) return;
        analysisStatus.textContent = 'Decoding MP3…';
        const context = await ensureAudioGraph();
        const decodedAudio = await context.decodeAudioData(encodedAudio.slice(0));
        if (generation !== analysisGeneration) return;
        const result = await analyzeAudioBuffer(decodedAudio, generation);
        if (!result || generation !== analysisGeneration) return;
        activityScores = result.scores;
        activityDuration = result.duration;
        analysisStatus.textContent = activitySummary();
        drawActivityTimeline();
      } catch (error) {
        if (generation !== analysisGeneration) return;
        activityScores = [];
        activityDuration = Number(player.duration) || 0;
        analysisStatus.textContent = `Automatic analysis is unavailable: ${error.message || error}`;
        drawActivityTimeline();
      }
    }

    async function selectRecording(button) {
      const url = button.dataset.url;
      const name = button.dataset.name || 'Recording';
      if (!url) return;
      document.querySelectorAll('tr.playing').forEach(row => row.classList.remove('playing'));
      button.closest('tr')?.classList.add('playing');
      analysisGeneration += 1;
      const generation = analysisGeneration;
      activityScores = [];
      activityDuration = 0;
      resetNoiseReductionEstimator();
      player.src = url;
      nowPlaying.textContent = `Now playing: ${name}`;
      drawActivityTimeline();
      try {
        await ensureAudioGraph();
      } catch (error) {
        analysisStatus.textContent = `Audio visualization is unavailable: ${error.message || error}`;
      }
      player.play().catch(() => { /* The user can press Play if autoplay is restricted. */ });
      analyzeRecording(url, generation);
    }

    document.querySelectorAll('.play-button, .recording-link').forEach(control => {
      control.addEventListener('click', event => {
        // Play filename links here, preserving the recording list and decode
        // results. Modified clicks still allow opening the MP3 separately.
        if (control.matches('a') && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
        event.preventDefault();
        selectRecording(control);
      });
    });

    document.querySelectorAll('.morse-checkbox').forEach(checkbox => {
      checkbox.addEventListener('change', updateMorseSelectionState);
    });
    if (selectAllMorse) {
      selectAllMorse.addEventListener('change', () => {
        document.querySelectorAll('.morse-checkbox').forEach(checkbox => {
          checkbox.checked = selectAllMorse.checked;
        });
        updateMorseSelectionState();
      });
    }
    morseDecodeButton.addEventListener('click', decodeCheckedMorse);

    function seekFromTimeline(clientX) {
      const duration = Number(player.duration) || activityDuration;
      if (!(duration > 0)) return;
      const bounds = activityCanvas.getBoundingClientRect();
      const fraction = clamp((clientX - bounds.left) / Math.max(1, bounds.width), 0, 1);
      player.currentTime = fraction * duration;
      drawActivityTimeline();
    }

    activityCanvas.addEventListener('click', event => seekFromTimeline(event.clientX));
    activityCanvas.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const duration = Number(player.duration) || activityDuration;
      if (!(duration > 0)) return;
      event.preventDefault();
      if (event.key === 'Home') player.currentTime = 0;
      else if (event.key === 'End') player.currentTime = duration;
      else player.currentTime = clamp(
        (Number(player.currentTime) || 0) + (event.key === 'ArrowRight' ? 5 : -5),
        0,
        duration
      );
      drawActivityTimeline();
    });

    sensitivitySlider.addEventListener('input', () => {
      sensitivityValue.value = sensitivitySlider.value;
      sensitivityValue.textContent = sensitivitySlider.value;
      if (activityScores.length) analysisStatus.textContent = activitySummary();
      drawActivityTimeline();
    });

    noiseReductionCheckbox.addEventListener('change', async () => {
      applyNoiseReductionState();
      try {
        await ensureAudioGraph();
        applyNoiseReductionState();
      } catch (error) {
        noiseReductionCheckbox.checked = false;
        noiseReductionStatus.textContent = `Unavailable: ${error.message || error}`;
      }
    });

    player.addEventListener('loadedmetadata', () => {
      if (!(activityDuration > 0)) activityDuration = Number(player.duration) || 0;
      drawActivityTimeline();
    });
    player.addEventListener('play', () => {
      ensureAudioGraph().catch(() => { /* Native playback remains available. */ });
    });

    function sortRecordings(sortMode) {
      const body = document.getElementById('recordingsBody');
      if (!body) return;
      const rows = Array.from(body.querySelectorAll('tr'));
      rows.sort((left, right) => {
        if (sortMode === 'filename') {
          return left.dataset.filename.localeCompare(right.dataset.filename, undefined, {
            numeric: true, sensitivity: 'base'
          });
        }
        const timeDifference = Number(right.dataset.mtime || 0) - Number(left.dataset.mtime || 0);
        return timeDifference || left.dataset.filename.localeCompare(right.dataset.filename, undefined, {
          numeric: true, sensitivity: 'base'
        });
      });
      rows.forEach(row => body.appendChild(row));
      document.getElementById('sortFilenameButton')?.classList.toggle('sort-active', sortMode === 'filename');
      document.getElementById('sortTimestampButton')?.classList.toggle('sort-active', sortMode === 'timestamp');
    }

    window.addEventListener('resize', () => {
      drawActivityTimeline();
      drawOscilloscope();
    });
    drawActivityTimeline();
    drawOscilloscope();
    updateMorseSelectionState();
    window.requestAnimationFrame(visualizationLoop);
  </script>
</body>
</html>
""".replace("__SOURCE__", source_text).replace("__CONTENT__", content).replace(
        "__UI_THEME__", _parse_ui_theme(ui_theme)
    )


def resolve_logs_directory(configured_location: Union[str, Path, None] = None) -> Path:
    """Resolve the program's logs folder, optionally using an injected path."""
    location = str(configured_location or "").strip()
    directory = Path(location).expanduser() if location else LOGS_PATH
    if not directory.is_absolute():
        directory = SCRIPT_DIR / directory
    return directory.resolve()


def list_log_files(configured_location: Union[str, Path, None] = None) -> tuple[Path, list[Path], str]:
    """Return safe, top-level regular files from the logs folder."""
    directory = resolve_logs_directory(configured_location)
    if not directory.exists():
        return directory, [], f"The logs folder does not exist: {directory}"
    if not directory.is_dir():
        return directory, [], f"The configured logs location is not a folder: {directory}"

    try:
        log_files = []
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            # Do not expose a symlink that resolves outside the logs folder.
            if entry.resolve().parent != directory:
                continue
            log_files.append(entry)
        log_files.sort(key=lambda path: (-path.stat().st_mtime, path.name.casefold()))
        message = "" if log_files else f"No files were found in {directory}."
        return directory, log_files, message
    except OSError as exc:
        return directory, [], f"Could not read the logs folder: {exc}"


def resolve_log_file(
        configured_location: Union[str, Path, None], requested_name: str) -> Optional[Path]:
    """Resolve one requested log file without allowing directory traversal."""
    directory = resolve_logs_directory(configured_location)
    name = str(requested_name or "")
    if not name or Path(name).name != name:
        return None

    try:
        candidate = (directory / name).resolve()
        if candidate.parent != directory or not candidate.is_file():
            return None
        return candidate
    except OSError:
        return None


def _format_file_size(size_bytes: int) -> str:
    """Return a compact human-readable file size."""
    size = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size_bytes} B"


def build_logs_html(
        configured_location: Union[str, Path, None] = None,
        ui_theme: str = DEFAULT_UI_THEME) -> str:
    """Build an independent, read-only listing of the logs folder."""
    directory, log_files, message = list_log_files(configured_location)
    rows = []
    for log_path in log_files:
        try:
            stat = log_path.stat()
        except OSError:
            continue
        safe_name = html.escape(log_path.name)
        file_url = "/log-file?name=" + quote(log_path.name, safe="")
        modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
        rows.append(
            '<tr><td><a href="{url}" target="_blank" rel="noopener">{name}</a></td>'
            '<td data-sort="{mtime:.6f}">{modified}</td><td>{size}</td></tr>'.format(
                url=file_url,
                name=safe_name,
                mtime=stat.st_mtime,
                modified=html.escape(modified.strftime("%Y-%m-%d %H:%M:%S %Z")),
                size=html.escape(_format_file_size(stat.st_size)),
            )
        )

    if message:
        content = f'<div class="logs-message">{html.escape(message)}</div>'
    else:
        content = (
            '<table><thead><tr><th>File</th><th>Last modified</th><th>Size</th></tr></thead>'
            '<tbody>' + "".join(rows) + "</tbody></table>"
        )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BQE WISP Logs</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; background: #b9b9b9; color: #111;
      font-family: "Courier New", Consolas, monospace; font-weight: 700;
    }
    header {
      position: sticky; top: 0; z-index: 2; display: flex; align-items: center;
      justify-content: space-between; gap: 18px; padding: 12px 16px;
      background: linear-gradient(180deg, #eeeeee, #bdbdbd);
      border-bottom: 2px ridge #d2d2d2; box-shadow: 0 2px 4px rgba(0,0,0,.25);
    }
    h1 { margin: 0; font-size: 24px; }
    .source { margin-top: 4px; font-size: 13px; overflow-wrap: anywhere; }
    button {
      padding: 7px 14px; border: 2px outset #e4e4e4; background: #d2d2d2;
      color: #111; font: inherit; cursor: pointer;
    }
    button:active { border-style: inset; }
    table { width: calc(100% - 32px); margin: 16px; border-collapse: collapse; background: #d2d2d2; }
    th, td { padding: 9px 12px; border: 2px inset #d0d0d0; text-align: left; }
    th { background: #c5c5c5; }
    td:nth-child(2), td:nth-child(3) { white-space: nowrap; }
    a { color: #05058a; overflow-wrap: anywhere; }
    a:hover, a:focus { color: #b00000; outline: 2px solid #05058a; outline-offset: 2px; }
    .logs-message { margin: 24px; padding: 18px; border: 2px inset #d0d0d0; background: #d2d2d2; }
    body.modern-ui { background: #071321; color: #edf2f7; font-family: "Segoe UI", Ubuntu, Arial, sans-serif; font-weight: 500; }
    body.modern-ui header { background: #102943; border: 0; border-bottom: 1px solid #3f7199; box-shadow: 0 8px 24px rgba(0,0,0,.24); }
    body.modern-ui h1 { color: #f7fbff; font-size: 21px; }
    body.modern-ui .source { color: #b8ddf5; }
    body.modern-ui button { border: 1px solid #477aa3; border-radius: 7px; background: #1b4569; color: #f7fbff; font-weight: 600; }
    body.modern-ui button:hover, body.modern-ui button:focus-visible { background: #256391; border-color: #66c7ff; outline: 2px solid rgba(50,215,255,.32); }
    body.modern-ui table { border: 1px solid #3f7199; border-radius: 9px; background: #102943; overflow: hidden; }
    body.modern-ui th, body.modern-ui td { border: 1px solid #315f80; }
    body.modern-ui th { background: #163b5c; color: #ccecff; }
    body.modern-ui tr:hover td { background: #173a5b; }
    body.modern-ui a { color: #66d9ff; }
    body.modern-ui a:hover, body.modern-ui a:focus { color: #fff27a; outline-color: #32d7ff; }
    body.modern-ui .logs-message { border: 1px solid #3f7199; border-radius: 9px; background: #102943; }
    @media (max-width: 700px) {
      header { align-items: flex-start; }
      table { width: calc(100% - 16px); margin: 8px; }
      th, td { padding: 7px 8px; }
      th:nth-child(2), td:nth-child(2) { display: none; }
    }
  </style>
</head>
<body class="__UI_THEME__-ui">
  <header>
    <div><h1>Logs</h1><div class="source">Folder: __SOURCE__</div></div>
    <button type="button" onclick="window.location.reload()">Refresh</button>
  </header>
  __CONTENT__
</body>
</html>
""".replace("__SOURCE__", html.escape(str(directory))).replace(
        "__CONTENT__", content
    ).replace("__UI_THEME__", _parse_ui_theme(ui_theme))


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
    tr.active.test-pass {
      background: #ffff00;
      color: #111;
      text-shadow: none;
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
    /* A second copy of this page can run as an FDT-console-only window. */
    body.detached-fdt-mode {
      overflow: hidden;
      background: #8f8f8f;
    }
    body.detached-fdt-mode .taskbar,
    body.detached-fdt-mode .console,
    body.detached-fdt-mode .about-modal,
    body.detached-fdt-mode #licenseModal,
    body.detached-fdt-mode #qthModal,
    body.detached-fdt-mode #radioModal,
    body.detached-fdt-mode #editPresetModal,
    body.detached-fdt-mode #presetModal,
    body.detached-fdt-mode #testPassModal,
    body.detached-fdt-mode #customScheduleModal {
      display: none !important;
    }
    body.detached-fdt-mode #fdtConsoleModal.open {
      display: flex !important;
      background: #8f8f8f;
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
    .statusbar-main {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      min-width: 0;
    }
    .status-details {
      flex: 0 0 auto;
      min-width: 0;
    }
    .recording-indicator {
      display: inline-flex;
      align-items: center;
      gap: 9px;
      padding: 6px 12px;
      border: 1px solid #168aff;
      border-radius: 6px;
      background: #082c54;
      color: #d7edff;
      font: bold 15px Arial, sans-serif;
      white-space: nowrap;
    }
    .recording-indicator[hidden] { display: none; }
    .recording-light {
      width: 13px;
      height: 13px;
      border-radius: 50%;
      background: #249fff;
      box-shadow: 0 0 9px #249fff;
      animation: bqe-recording-flash 1.2s ease-in-out infinite;
    }
    @keyframes bqe-recording-flash {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.2; }
    }
    @media (prefers-reduced-motion: reduce) {
      .recording-light { animation: none; }
    }
    .status-grid {
      display: grid;
      grid-template-columns: 145px 75px 65px 115px 120px 130px;
      gap: 8px;
      align-items: baseline;
    }
    .status-head { margin-bottom: 8px; }
    .tracking-state-indicators {
      display: flex;
      flex: 0 0 auto;
      gap: 8px;
      margin-left: auto;
    }
    .state-indicator {
      display: grid;
      grid-template-columns: 27px minmax(68px, 1fr);
      grid-template-rows: auto auto;
      align-items: center;
      min-width: 112px;
      padding: 5px 7px;
      border: 2px inset #d0d0d0;
      background: #c9c9c9;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      line-height: 1.05;
    }
    .state-check {
      display: inline-flex;
      grid-row: 1 / span 2;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border: 2px inset #d0d0d0;
      background: #8b0000;
      color: #fff;
      font-size: 17px;
      font-weight: 900;
      line-height: 1;
    }
    .state-label {
      font-size: 13px;
      font-weight: 900;
      text-transform: uppercase;
    }
    .state-value {
      color: #7a0000;
      font-size: 12px;
      font-weight: 900;
      text-transform: uppercase;
    }
    .state-indicator.enabled .state-check {
      background: #138a13;
      color: #fff;
    }
    .state-indicator.enabled .state-value { color: #075f07; }
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
    .antenna-tracking-submenu {
      min-width: 260px;
    }
    .config-submenu {
      min-width: 240px;
    }
    .submenu-group {
      position: relative;
    }
    .submenu-group > .nested-submenu {
      display: none !important;
      top: -3px;
      left: calc(100% - 1px);
      min-width: 270px;
    }
    .submenu-group:hover > .nested-submenu,
    .submenu-group:focus-within > .nested-submenu {
      display: block !important;
    }
    .submenu-group > button[aria-haspopup="true"]::after {
      content: "\25B6";
      float: right;
      margin-left: 16px;
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
    .submenu button:disabled,
    .submenu button:disabled:hover,
    .submenu button:disabled:focus {
      color: #777;
      background: #d5d5d5;
      cursor: default;
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
    /* Radio has longer property names.  Give the labels more room and
       use a narrower value column so the labels remain fully visible. */
    #radioFields {
      grid-template-columns: 270px 170px;
      justify-content: start;
    }
    #radioFields label {
      text-align: left;
      white-space: nowrap;
    }
    #radioFields input {
      width: 170px;
    }
    /* Give the Create Preset dialog extra room for long property names
       without changing the QTH or Radio configuration dialogs. */
    #presetFields {
      grid-template-columns: 310px minmax(0, 1fr);
    }
    #presetFields label {
      text-align: left;
    }
    #generalSettingsModal .config-box {
      width: min(1180px, calc(100vw - 36px));
      max-height: calc(100vh - 40px);
      display: flex;
      flex-direction: column;
    }
    .general-settings-table {
      display: grid;
      grid-template-columns: minmax(300px, 1.35fr) minmax(220px, 1fr) minmax(220px, 1fr);
      align-items: stretch;
      max-height: min(68vh, 720px);
      overflow: auto;
      border: 2px inset #d0d0d0;
      background: rgba(255,255,255,.2);
      margin: 10px 0 18px;
    }
    .general-settings-cell {
      min-width: 0;
      padding: 7px 9px;
      border-right: 1px solid rgba(0,0,0,.25);
      border-bottom: 1px solid rgba(0,0,0,.2);
      overflow-wrap: anywhere;
      font-size: 15px;
      line-height: 1.35;
    }
    .general-settings-header {
      position: sticky;
      top: 0;
      z-index: 2;
      background: #cfcfcf;
      font-size: 17px;
      font-weight: 900;
      border-bottom: 2px solid #777;
    }
    .general-settings-key,
    .general-settings-original {
      background: rgba(235,235,235,.72);
    }
    .general-settings-table input {
      width: 100%;
      min-width: 0;
      padding: 5px 7px;
      border: 2px inset #d0d0d0;
      background: #fff;
      color: #111;
      font-family: "Courier New", Consolas, monospace;
      font-size: 15px;
      font-weight: 900;
    }
    /* Current-vs-template differences are intentionally conspicuous in both
       classic and modern themes.  Apply the class to the key, both values, and
       the editable Current input so the whole comparison is easy to scan. */
    .general-settings-different {
      color: #ffd400 !important;
      font-weight: 900 !important;
      text-shadow: 0 0 1px rgba(0,0,0,.85);
    }
    .general-settings-table input.general-settings-different {
      color: #ffe45e !important;
      font-weight: 900 !important;
    }
    #satellitesEditorModal .config-box {
      width: min(1180px, calc(100vw - 36px));
      max-height: calc(100vh - 40px);
      display: flex;
      flex-direction: column;
    }
    .satellite-editor-selector {
      display: grid;
      grid-template-columns: 190px minmax(280px, 1fr);
      gap: 10px 12px;
      align-items: center;
      margin: 10px 0 4px;
    }
    .satellite-editor-selector label { font-size: 17px; font-weight: 900; }
    .satellite-editor-selector select {
      min-width: 0;
      padding: 6px 8px;
      border: 2px inset #d0d0d0;
      background: #f4f4f4;
      color: #111;
      font: inherit;
      font-weight: 900;
    }
    #addSatelliteModal .config-box {
      width: min(760px, calc(100vw - 36px));
      max-height: calc(100vh - 40px);
      overflow: auto;
    }
    #addSatelliteFields {
      grid-template-columns: 310px minmax(220px, 1fr);
    }
    #addSatelliteFields label { text-align: left; }

    .config-fields input,
    .config-fields select {
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
    #fdtConsoleFields {
      grid-template-columns: 315px 250px;
      justify-content: center;
    }
    #fdtConsoleFields label {
      white-space: nowrap;
    }
    #fdtConsoleFields input {
      width: 250px;
      background: #000;
      color: #ffea00;
      border-color: #555;
      caret-color: #ffea00;
    }
    #fdtConsoleFields .fdt-interval-control {
      width: 250px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 3px 10px;
      align-items: center;
    }
    #fdtConsoleFields input[type="range"] {
      grid-column: 1 / -1;
      width: 250px;
      height: 24px;
      padding: 0;
      border: 0;
      background: transparent;
      accent-color: #d9b300;
      cursor: pointer;
    }
    .fdt-interval-limits {
      grid-column: 1;
      display: flex;
      justify-content: space-between;
      color: #333;
      font: 700 12px Arial, sans-serif;
    }
    #fdtIntervalValue {
      grid-column: 2;
      grid-row: 2;
      min-width: 76px;
      color: #111;
      font: 900 15px "Courier New", Consolas, monospace;
      text-align: right;
    }
    #fdtConsoleModal .config-box {
      width: min(860px, calc(100vw - 36px));
      max-height: calc(100vh - 24px);
      overflow-y: auto;
    }
    .fdt-passband-heading {
      margin: -7px 0 8px;
      color: #222;
      font-size: 15px;
      text-align: center;
    }
    .fdt-dials {
      display: grid;
      grid-template-columns: repeat(2, minmax(280px, 1fr));
      gap: 12px;
      margin: 0 0 10px;
    }
    .tuning-dial {
      min-width: 0;
      padding: 7px 8px 4px;
      border: 3px inset #d0d0d0;
      background: linear-gradient(180deg, #2a2a2a, #020202 72%);
      color: #ffea00;
      text-align: center;
    }
    .tuning-dial.interactive { cursor: ew-resize; }
    .tuning-dial.interactive:focus {
      outline: 3px solid #00c8ff;
      outline-offset: 2px;
      box-shadow: 0 0 0 2px #00151d, 0 0 13px rgba(0,200,255,.85);
    }
    .tuning-dial.tuning { filter: brightness(1.22); }
    .tuning-dial-title {
      margin-bottom: 1px;
      color: #ffef63;
      font-size: 15px;
      font-weight: 900;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .tuning-dial svg {
      display: block;
      width: 100%;
      height: auto;
      max-height: 150px;
      margin: 0 auto;
      overflow: hidden;
    }
    .slide-rule-window {
      fill: #090b06;
      stroke: #a99836;
      stroke-width: 2;
      filter: drop-shadow(0 0 4px rgba(255,223,0,.28));
    }
    .slide-rule-baseline {
      stroke: #a99836;
      stroke-width: 1.5;
    }
    .tuning-dial-tick {
      stroke: #ffe94d;
      stroke-width: 1.2;
    }
    .tuning-dial-tick.major {
      stroke: #fff4a0;
      stroke-width: 2;
      filter: drop-shadow(0 0 2px rgba(255,233,77,.40));
    }
    .slide-rule-number {
      fill: #ffef63;
      font-family: "Courier New", Consolas, monospace;
      font-size: 10px;
      font-weight: 900;
    }
    .slide-rule-band-edge {
      stroke: #ff9f0a;
      stroke-width: 2;
      stroke-dasharray: 3 2;
    }
    .tuning-dial-needle {
      stroke: #ff3b30;
      stroke-width: 3;
      filter: drop-shadow(0 0 3px rgba(255,59,48,.62));
    }
    .slide-rule-index-pointer {
      fill: #ff3b30;
      stroke: #ffb0aa;
      stroke-width: 1;
      filter: drop-shadow(0 0 3px rgba(255,59,48,.55));
    }
    .slide-rule-readout {
      fill: #020300;
      stroke: #7e7226;
      stroke-width: 1.5;
    }
    .tuning-dial-edge,
    .tuning-dial-center {
      fill: #ffef63;
      font-family: "Courier New", Consolas, monospace;
      font-size: 12px;
      font-weight: 900;
    }
    .tuning-dial-center {
      fill: #7cff5b;
      stroke: #030303;
      stroke-width: 3px;
      paint-order: stroke;
      font-size: 16px;
    }
    .fdt-dial-status {
      min-height: 17px;
      margin: 0 0 4px;
      color: #333;
      font-size: 13px;
      text-align: center;
    }
    .tuning-dial.unavailable { opacity: .48; }
    @media (max-width: 700px) {
      .fdt-dials { grid-template-columns: 1fr; }
      #fdtConsoleFields {
        grid-template-columns: minmax(0, 1fr);
        justify-content: stretch;
      }
      #fdtConsoleFields label {
        text-align: left;
        white-space: normal;
      }
      #fdtConsoleFields input { width: 100%; }
      #fdtConsoleFields .fdt-interval-control,
      #fdtConsoleFields input[type="range"] { width: 100%; }
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
    #customScheduleFields {
      display: block;
      margin: 10px 0 18px;
    }
    .custom-schedule-list {
      max-height: min(52vh, 440px);
      overflow-y: auto;
      padding: 8px 10px;
      border: 2px inset #e4e4e4;
      background: #eeeeee;
    }
    .custom-schedule-option {
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 5px 3px;
      font-size: 16px;
      line-height: 1.25;
      cursor: pointer;
    }
    .custom-schedule-option input {
      flex: 0 0 auto;
      width: auto;
      margin: 0;
    }
    .custom-schedule-option .custom-schedule-catalog {
      margin-left: auto;
      padding-left: 12px;
      color: #555;
      font-size: 13px;
      white-space: nowrap;
    }
    #customScheduleMessage.custom-schedule-error {
      color: #8b0000;
      font-weight: 700;
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

    /* Modern theme overrides. The original CSS above remains the classic UI. */
    body.modern-ui {
      color-scheme: dark;
      --modern-canvas: #071321;
      --modern-surface: #102943;
      --modern-surface-raised: #163653;
      --modern-control: #1b4569;
      --modern-border: #3f7199;
      --modern-border-strong: #5fa9dc;
      --modern-text: #f7fbff;
      --modern-text-secondary: #d8edfb;
      --modern-text-muted: #a9cee7;
      --modern-cyan: #32d7ff;
      --modern-blue: #2a9df4;
      --modern-green: #57ff9a;
      --modern-yellow: #ffe45e;
      --modern-red: #ff4568;
      background: var(--modern-canvas);
      color: var(--modern-text);
      font-family: "Segoe UI", Ubuntu, "Noto Sans", Arial, sans-serif;
      font-size: 16px;
      font-weight: 500;
      overflow-x: hidden;
    }
    body.modern-ui .taskbar {
      max-width: none;
      min-height: 48px;
      padding: 5px 12px;
      background: #0d2339;
      border: 0;
      border-bottom: 1px solid var(--modern-border);
      box-shadow: 0 7px 22px rgba(0,0,0,.32);
    }
    body.modern-ui .taskbar::after {
      content: "BQE WISP";
      margin-left: auto;
      padding: 0 8px;
      color: var(--modern-cyan);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: .12em;
    }
    body.modern-ui .menu-button,
    body.modern-ui .submenu button,
    body.modern-ui .config-actions button,
    body.modern-ui .preset-button {
      font-family: "Segoe UI", Ubuntu, "Noto Sans", Arial, sans-serif;
    }
    body.modern-ui .menu-button {
      padding: 8px 10px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--modern-text-secondary);
      font-size: 14px;
      font-weight: 600;
    }
    body.modern-ui .menu-button:hover,
    body.modern-ui .menu-button:focus {
      background: #1a4a70;
      color: #fff;
      box-shadow: inset 0 0 0 1px #4c91bf;
    }
    body.modern-ui .submenu {
      padding: 6px;
      border: 1px solid var(--modern-border);
      border-radius: 8px;
      background: var(--modern-surface);
      box-shadow: 0 14px 30px rgba(0,0,0,.42);
    }
    body.modern-ui .submenu button {
      min-height: 34px;
      border-radius: 5px;
      color: var(--modern-text-secondary);
      font-size: 14px;
      font-weight: 500;
    }
    body.modern-ui .submenu button:hover,
    body.modern-ui .submenu button:focus {
      background: #1f5a84;
      color: #fff;
      box-shadow: inset 3px 0 var(--modern-cyan);
    }
    body.modern-ui .submenu button:disabled,
    body.modern-ui .submenu button:disabled:hover {
      background: transparent;
      color: #7694aa;
      opacity: 1;
    }
    body.modern-ui .menu-button.menu-click-flash,
    body.modern-ui .submenu button.menu-click-flash,
    body.modern-ui .preset-button.menu-click-flash {
      animation: none !important;
      background: #1f5a84 !important;
      color: #fff !important;
      box-shadow: inset 0 0 0 1px var(--modern-border-strong) !important;
    }
    body.modern-ui .submenu button.menu-command-running,
    body.modern-ui .submenu button.menu-command-running:hover,
    body.modern-ui .submenu button.menu-command-running:focus {
      background: rgba(42,157,244,.30) !important;
      color: #d9f4ff !important;
      box-shadow: inset 3px 0 var(--modern-cyan) !important;
    }
    body.modern-ui .console,
    body.modern-ui .console.map-visible {
      max-width: none;
      min-height: 650px;
      height: calc(100vh - 48px);
      padding: 14px;
      gap: 12px;
      background: var(--modern-canvas);
      grid-template-columns: 300px minmax(520px, 1fr);
      grid-template-rows: minmax(0, 1fr) auto;
    }
    body.modern-ui .console.presets-visible {
      grid-template-columns: 300px minmax(520px, 1fr) 240px;
      max-width: none;
    }
    body.modern-ui .console.map-visible {
      grid-template-columns: 300px minmax(500px, 1fr) minmax(360px, .8fr);
    }
    body.modern-ui .console.presets-visible.map-visible {
      grid-template-columns: 300px minmax(500px, 1fr) 220px minmax(360px, .8fr);
      max-width: none;
    }
    body.modern-ui .panel,
    body.modern-ui .radio-box {
      border: 1px solid var(--modern-border);
      border-radius: 10px;
      background: var(--modern-surface);
      box-shadow: 0 9px 26px rgba(0,0,0,.30);
    }
    body.modern-ui .left { padding: 12px; }
    body.modern-ui .clock-block {
      margin: 0 0 10px;
      padding: 9px 8px 11px;
      border-bottom: 1px solid var(--modern-border);
      color: var(--modern-text);
      font-family: Consolas, "Liberation Mono", monospace;
      font-size: 18px;
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
    }
    body.modern-ui #countdown { color: var(--modern-cyan); font-weight: 800; }
    body.modern-ui .gauge-wrap {
      margin: 9px 0;
      padding: 7px 4px 6px;
      border: 1px solid #315c7d;
      border-radius: 9px;
      background: #091c2e;
      color: var(--modern-text);
    }
    body.modern-ui .gauge-title,
    body.modern-ui .radio-box .box-title {
      color: #b9dcf3;
      font-family: "Segoe UI", Ubuntu, sans-serif;
      font-weight: 650;
      letter-spacing: .08em;
    }
    body.modern-ui .gauge-ring,
    body.modern-ui .gauge-arc { stroke: #62a6d2; filter: none; }
    body.modern-ui .gauge-minor { stroke: #477c9f; }
    body.modern-ui .gauge-major { stroke: #b8e3ff; }
    body.modern-ui .gauge-label {
      fill: #d9efff;
      font-family: Consolas, "Liberation Mono", monospace;
      font-weight: 600;
    }
    body.modern-ui .gauge-needle,
    body.modern-ui .gauge-boom { stroke: var(--modern-green); }
    body.modern-ui .gauge-hub { fill: #22dc78; stroke: #b2ffd1; filter: none; }
    body.modern-ui .label {
      color: var(--modern-text);
      font-family: Consolas, "Liberation Mono", monospace;
      font-size: 20px;
      font-variant-numeric: tabular-nums;
    }
    body.modern-ui .radio-readouts { gap: 8px; margin-top: 10px; }
    body.modern-ui .radio-box { min-height: 58px; padding: 6px; }
    body.modern-ui .radio-box .box-value {
      min-height: 34px;
      border: 1px solid #477a9e;
      border-radius: 6px;
      background: #050e17;
      color: var(--modern-yellow);
      font-family: Consolas, "Liberation Mono", monospace;
      font-size: 18px;
      font-variant-numeric: tabular-nums;
      text-shadow: none;
    }
    body.modern-ui .radio-box.mode .box-value { color: var(--modern-cyan); font-size: 20px; }
    body.modern-ui .schedule { padding: 0; scrollbar-color: #5b91b8 var(--modern-surface); }
    body.modern-ui table { border-collapse: separate; border-spacing: 0; }
    body.modern-ui th,
    body.modern-ui td { padding: 12px 15px; font-size: 16px; }
    body.modern-ui th {
      background: var(--modern-surface-raised);
      color: #d5edfd;
      border-bottom: 1px solid #5388ae;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    body.modern-ui td {
      border-bottom: 1px solid #284e6b;
      color: var(--modern-text-secondary);
      font-variant-numeric: tabular-nums;
    }
    body.modern-ui tbody tr:nth-child(even):not(.active) { background: rgba(58,167,255,.055); }
    body.modern-ui tr.active {
      background: rgba(231,91,91,.16);
      color: #fff;
      box-shadow: inset 4px 0 #e75b5b;
      text-shadow: none;
    }
    body.modern-ui tr.active.test-pass {
      background: rgba(240,180,76,.16);
      color: #f9d995;
      box-shadow: inset 4px 0 #f0b44c;
    }
    body.modern-ui tr.done td { color: #819fb5; }
    body.modern-ui .statusbar {
      padding: 12px 16px;
      color: var(--modern-text-secondary);
      font-family: Consolas, "Liberation Mono", monospace;
      font-size: 15px;
      font-variant-numeric: tabular-nums;
    }
    body.modern-ui .status-head {
      margin-bottom: 5px;
      color: #afd6ee;
      font-family: "Segoe UI", Ubuntu, sans-serif;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .05em;
      text-transform: uppercase;
    }
    body.modern-ui .state-indicator {
      min-width: 118px;
      padding: 6px 8px;
      border: 1px solid #4d7898;
      border-radius: 7px;
      background: #091c2e;
      color: #d8edfb;
      font-family: "Segoe UI", Ubuntu, "Noto Sans", Arial, sans-serif;
      box-shadow: inset 0 0 12px rgba(0,0,0,.18);
    }
    body.modern-ui .state-check {
      border: 1px solid #ff7895;
      border-radius: 5px;
      background: #a92140;
      color: #fff;
      box-shadow: 0 0 8px rgba(255,69,104,.28);
    }
    body.modern-ui .state-label { color: #c8e5f7; font-size: 11px; letter-spacing: .05em; }
    body.modern-ui .state-value { color: #ff9db1; font-size: 11px; letter-spacing: .03em; }
    body.modern-ui .state-indicator.enabled {
      border-color: #35b978;
      background: #09291d;
    }
    body.modern-ui .state-indicator.enabled .state-check {
      border-color: #9affc1;
      background: #13a85b;
      color: #fff;
      box-shadow: 0 0 9px rgba(87,255,154,.34);
    }
    body.modern-ui .state-indicator.enabled .state-value { color: var(--modern-green); }
    body.modern-ui .message {
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid #315c7d;
      color: #c1def0;
      font-family: "Segoe UI", Ubuntu, sans-serif;
      font-size: 14px;
      font-weight: 500;
    }

    /* Make live pass data vivid without changing the quieter idle palette. */
    body.modern-ui .left,
    body.modern-ui .gauge-wrap,
    body.modern-ui .radio-box,
    body.modern-ui .radio-box .box-value,
    body.modern-ui .statusbar {
      transition: border-color .25s ease, background-color .25s ease,
                  color .25s ease, box-shadow .25s ease;
    }
    body.modern-ui.pass-in-progress .left {
      border-color: var(--modern-cyan);
      box-shadow: 0 0 0 1px rgba(50,215,255,.32),
                  0 12px 34px rgba(0,155,224,.38);
    }
    body.modern-ui.pass-in-progress .clock-block {
      border-bottom-color: rgba(0,205,255,.42);
    }
    body.modern-ui.pass-in-progress #countdown,
    body.modern-ui.pass-in-progress #countdownLabel {
      color: #51e2ff;
      font-weight: 900;
      text-shadow: 0 0 10px rgba(40,215,255,.58);
    }
    body.modern-ui.pass-in-progress .gauge-wrap {
      border-color: #087fa9;
      background: radial-gradient(circle at 50% 42%, #092a38, #07121a 76%);
      box-shadow: inset 0 0 24px rgba(0,190,255,.08);
    }
    body.modern-ui.pass-in-progress .gauge-title { color: #70e9ff; }
    body.modern-ui.pass-in-progress .gauge-ring,
    body.modern-ui.pass-in-progress .gauge-arc {
      stroke: #32d7ff;
      filter: drop-shadow(0 0 3px rgba(22,200,255,.52));
    }
    body.modern-ui.pass-in-progress .gauge-minor { stroke: #28a7cf; }
    body.modern-ui.pass-in-progress .gauge-major { stroke: #a5f2ff; }
    body.modern-ui.pass-in-progress .gauge-label { fill: #e0faff; }
    body.modern-ui.pass-in-progress .gauge-needle,
    body.modern-ui.pass-in-progress .gauge-boom {
      stroke: var(--modern-green);
      filter: drop-shadow(0 0 3px rgba(93,255,139,.66));
    }
    body.modern-ui.pass-in-progress .gauge-hub {
      fill: #17e86a;
      stroke: #b5ffc9;
      filter: drop-shadow(0 0 5px rgba(23,232,106,.72));
    }
    body.modern-ui.pass-in-progress .label {
      color: var(--modern-green);
      font-weight: 900;
      text-shadow: 0 0 9px rgba(93,255,139,.44);
    }
    body.modern-ui.pass-in-progress .radio-box {
      border-color: #755d00;
      background: #202514;
    }
    body.modern-ui.pass-in-progress .radio-box .box-title { color: #fff08a; }
    body.modern-ui.pass-in-progress .radio-box .box-value {
      border-color: #a47e00;
      color: var(--modern-yellow);
      background: #100d00;
      text-shadow: 0 0 9px rgba(255,234,85,.48);
    }
    body.modern-ui.pass-in-progress .radio-box.mode {
      border-color: #007fac;
      background: #102631;
    }
    body.modern-ui.pass-in-progress .radio-box.mode .box-title { color: #8decff; }
    body.modern-ui.pass-in-progress .radio-box.mode .box-value {
      border-color: #008fbd;
      color: #6ee9ff;
      background: #061116;
      text-shadow: 0 0 9px rgba(94,231,255,.48);
    }
    body.modern-ui tr.active {
      background: linear-gradient(90deg, #fff36a, #ffd334 72%, #ffc526);
      box-shadow: inset 5px 0 #fffbd0, inset 0 1px rgba(255,255,255,.68),
                  inset 0 -1px rgba(130,88,0,.38), 0 0 13px rgba(255,213,52,.20);
    }
    body.modern-ui tr.active td {
      color: #211900;
      border-bottom-color: rgba(111,76,0,.48);
      font-weight: 750;
    }
    body.modern-ui tr.active td:first-child {
      color: #100c00;
      text-shadow: 0 1px rgba(255,255,255,.45);
    }
    body.modern-ui tr.active.test-pass {
      background: linear-gradient(90deg, #ffed68, #ffcc35 72%, #ffb829);
      color: #211500;
      box-shadow: inset 5px 0 #fff5ae, inset 0 1px rgba(255,255,255,.62),
                  inset 0 -1px rgba(130,69,0,.40);
    }
    body.modern-ui.pass-in-progress .statusbar {
      border-color: #006f95;
      background: linear-gradient(90deg, #122633, #18212b 70%);
      box-shadow: 0 8px 24px rgba(0,130,180,.17);
    }
    body.modern-ui.pass-in-progress #detailSatellite { color: #ff7895; font-weight: 800; }
    body.modern-ui.pass-in-progress #detailAz,
    body.modern-ui.pass-in-progress #detailEl { color: var(--modern-green); font-weight: 800; }
    body.modern-ui.pass-in-progress #detailRange,
    body.modern-ui.pass-in-progress #detailAltitude { color: #6ee9ff; font-weight: 800; }
    body.modern-ui.pass-in-progress #detailDoppler { color: var(--modern-yellow); font-weight: 800; }
    body.modern-ui.pass-in-progress .message { color: #d7f6ff; }
    body.modern-ui .preset-panel,
    body.modern-ui .map-panel { padding: 11px; }
    body.modern-ui .preset-title,
    body.modern-ui .map-title { color: #c9eaff; font-size: 14px; font-weight: 750; letter-spacing: .08em; }
    body.modern-ui .preset-button {
      min-height: 40px;
      border: 1px solid var(--modern-border);
      border-radius: 7px;
      background: var(--modern-control);
      color: var(--modern-text);
      font-size: 14px;
      font-weight: 600;
    }
    body.modern-ui .preset-button:hover:not(:disabled),
    body.modern-ui .preset-button:focus-visible:not(:disabled) {
      background: #23618e;
      border-color: var(--modern-cyan);
      color: #fff;
      outline: none;
    }
    body.modern-ui .preset-button:disabled { background: #0d2032; color: #7896aa; opacity: 1; }
    body.modern-ui #earthMap { border: 1px solid var(--modern-border); border-radius: 7px; }
    body.modern-ui .map-readouts {
      border: 1px solid var(--modern-border);
      border-radius: 7px;
      background: #091c2e;
      color: #c5e4f7;
    }
    body.modern-ui .map-readouts .map-value { color: var(--modern-green); text-shadow: none; }
    body.modern-ui .map-status { color: #b9dbf0; }
    body.modern-ui .config-modal,
    body.modern-ui .about-modal { background: rgba(2,6,10,.74); backdrop-filter: blur(3px); }
    body.modern-ui .config-box,
    body.modern-ui .about-box,
    body.modern-ui .license-box {
      border: 1px solid var(--modern-border-strong);
      border-radius: 11px;
      background: linear-gradient(145deg, #153653, #0e263e);
      color: var(--modern-text);
      box-shadow: 0 24px 70px rgba(0,0,0,.62), 0 0 28px rgba(42,157,244,.13);
    }
    body.modern-ui .config-title,
    body.modern-ui .about-title {
      color: #fff;
      font-weight: 750;
      text-shadow: 0 0 12px rgba(50,215,255,.20);
    }
    body.modern-ui .config-fields label,
    body.modern-ui .custom-schedule-option {
      color: #e4f3fc;
      font-weight: 700;
    }
    body.modern-ui .custom-schedule-option .custom-schedule-catalog {
      color: #acd2ea;
      font-weight: 600;
    }
    body.modern-ui .config-close,
    body.modern-ui .about-close {
      border: 1px solid #5684a5;
      border-radius: 6px;
      background: #173b59;
      color: #f2f9ff;
    }
    body.modern-ui .config-close:hover,
    body.modern-ui .config-close:focus,
    body.modern-ui .about-close:hover,
    body.modern-ui .about-close:focus {
      border-color: #ff7895;
      background: #8b2940;
      color: #fff;
    }
    body.modern-ui .config-fields input,
    body.modern-ui .config-fields select,
    body.modern-ui .license-content,
    body.modern-ui .custom-schedule-list {
      border: 1px solid #5585a6;
      border-radius: 6px;
      background: #061725;
      color: #f8fcff;
      font-family: Consolas, "Liberation Mono", monospace;
    }
    body.modern-ui .config-fields input:hover,
    body.modern-ui .config-fields select:hover { border-color: #74b7e4; }
    body.modern-ui .config-fields input:focus,
    body.modern-ui .config-fields select:focus {
      border-color: var(--modern-cyan);
      outline: 2px solid rgba(50,215,255,.28);
      box-shadow: 0 0 12px rgba(50,215,255,.16);
    }
    body.modern-ui .custom-schedule-option input { accent-color: var(--modern-blue); }
    body.modern-ui .config-message { color: #c4e2f3; font-weight: 600; }
    body.modern-ui .general-settings-table {
      border-color: #29495e;
      background: rgba(5, 17, 27, .7);
    }
    body.modern-ui .general-settings-header {
      background: #183549;
      color: #d8f1ff;
      border-bottom-color: #4d7996;
    }
    body.modern-ui .general-settings-key,
    body.modern-ui .general-settings-original {
      background: rgba(18, 43, 59, .92);
      color: #d6e8f3;
    }
    body.modern-ui .general-settings-cell {
      border-right-color: #31546b;
      border-bottom-color: #29495e;
    }
    /* Normal Current values use the standard Modern UI text color.  Yellow is
       reserved exclusively for settings that differ from the template. */
    body.modern-ui .general-settings-table input {
      background: #07131c;
      color: #d6e8f3;
      border-color: #466d86;
      caret-color: #d6e8f3;
    }

    /* Yellow is reserved exclusively for the key/value text of rows whose
       Current and Original values differ.  JavaScript also applies the text
       color directly so later theme rules cannot mask the indication. */
    body.modern-ui #generalSettingsTable .general-settings-different {
      color: #ffe45e !important;
      font-weight: 900 !important;
      text-shadow: 0 0 1px rgba(0,0,0,.85);
    }
    body.modern-ui #generalSettingsTable input.general-settings-different {
      color: #ffe45e !important;
      caret-color: #d6e8f3;
      font-weight: 900 !important;
    }
    body.modern-ui #customScheduleMessage.custom-schedule-error { color: #ff91a8; }
    body.modern-ui .about-line {
      border: 1px solid #477999;
      background: rgba(50,157,224,.12);
    }
    body.modern-ui .about-line strong { color: var(--modern-green); text-shadow: none; }
    body.modern-ui #fdtConsoleFields input {
      background: #040b11;
      color: var(--modern-yellow);
      text-shadow: none;
    }
    body.modern-ui #fdtConsoleFields input[readonly] { color: #ead879; border-color: #496175; }
    body.modern-ui #fdtConsoleFields input[type="range"] {
      border: 0;
      background: transparent;
      accent-color: var(--modern-yellow);
      box-shadow: none;
    }
    body.modern-ui .fdt-interval-limits { color: #9fc7dd; }
    body.modern-ui #fdtIntervalValue { color: var(--modern-yellow); }
    body.modern-ui .fdt-passband-heading { color: #c7e5f8; }
    body.modern-ui .tuning-dial {
      border: 1px solid #4e7d9e;
      border-radius: 9px;
      background: linear-gradient(180deg, #12344d, #030b13 76%);
      box-shadow: inset 0 0 20px rgba(26,153,220,.10);
    }
    body.modern-ui .tuning-dial.interactive:focus {
      outline-color: var(--modern-yellow);
      box-shadow: 0 0 0 2px #071019, 0 0 16px rgba(255,221,64,.72);
    }
    body.modern-ui .tuning-dial-title { color: #d6efff; }
    body.modern-ui .slide-rule-window {
      fill: #020b10;
      stroke: #3bbde5;
      filter: drop-shadow(0 0 4px rgba(50,215,255,.52));
    }
    body.modern-ui .slide-rule-baseline { stroke: #3189aa; }
    body.modern-ui .tuning-dial-tick { stroke: #78dfff; }
    body.modern-ui .tuning-dial-tick.major { stroke: #d2f7ff; }
    body.modern-ui .slide-rule-number { fill: #bfefff; }
    body.modern-ui .slide-rule-band-edge { stroke: var(--modern-yellow); }
    body.modern-ui .tuning-dial-needle {
      stroke: #ff5571;
      filter: drop-shadow(0 0 4px rgba(255,85,113,.72));
    }
    body.modern-ui .slide-rule-index-pointer { fill: #ff5571; stroke: #ffc2cc; }
    body.modern-ui .slide-rule-readout { fill: #02070b; stroke: #3e7997; }
    body.modern-ui .tuning-dial-edge { fill: #bfe9ff; }
    body.modern-ui .tuning-dial-center { fill: var(--modern-yellow); }
    body.modern-ui .fdt-dial-status { color: #b9dbf0; }
    body.modern-ui .config-actions button {
      border: 1px solid #5384a6;
      border-radius: 7px;
      background: var(--modern-control);
      color: var(--modern-text);
      font-weight: 600;
    }
    body.modern-ui .config-actions button[type="submit"] {
      border-color: #65caff;
      background: #087fc4;
      color: #fff;
    }
    body.modern-ui .config-actions button:hover,
    body.modern-ui .config-actions button:focus {
      border-color: var(--modern-cyan);
      background: #276b99;
      color: #fff;
      outline: 2px solid rgba(50,215,255,.25);
    }
    body.modern-ui .config-actions button[type="submit"]:hover,
    body.modern-ui .config-actions button[type="submit"]:focus { background: #079ce8; }
    body.modern-ui .config-actions button:disabled {
      border-color: #3d5a70;
      background: #14283a;
      color: #7895aa;
      cursor: not-allowed;
    }
    body.modern-ui .license-content { scrollbar-color: #5e9bc4 #0b2032; }
    body.modern-ui.detached-map-mode .console,
    body.modern-ui.detached-map-mode .console.map-visible,
    body.modern-ui.detached-fdt-mode,
    body.modern-ui.detached-fdt-mode #fdtConsoleModal.open { background: var(--modern-canvas); }

    @media (max-width: 1180px) {
      body.modern-ui .console,
      body.modern-ui .console.presets-visible,
      body.modern-ui .console.map-visible,
      body.modern-ui .console.presets-visible.map-visible {
        width: 100%;
        min-width: 0;
        height: auto;
        min-height: calc(100vh - 48px);
        grid-template-columns: minmax(270px, 34%) minmax(0, 1fr);
        grid-template-rows: auto auto auto;
      }
      body.modern-ui .left { grid-column: 1; grid-row: 1 / span 2; }
      body.modern-ui .schedule { grid-column: 2; grid-row: 1; min-height: 430px; }
      body.modern-ui .statusbar { grid-column: 2; grid-row: 2; }
      body.modern-ui .preset-panel,
      body.modern-ui .map-panel,
      body.modern-ui .console.presets-visible .map-panel {
        grid-column: 1 / -1;
        grid-row: auto;
        min-height: 420px;
      }
      body.modern-ui .preset-buttons { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
    }
    @media (max-width: 760px) {
      body.modern-ui .taskbar { flex-wrap: wrap; }
      body.modern-ui .taskbar::after { display: none; }
      body.modern-ui .console,
      body.modern-ui .console.presets-visible,
      body.modern-ui .console.map-visible,
      body.modern-ui .console.presets-visible.map-visible {
        display: flex;
        flex-direction: column;
        padding: 9px;
      }
      body.modern-ui .left { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
      body.modern-ui .clock-block,
      body.modern-ui .radio-readouts { grid-column: 1 / -1; }
      body.modern-ui .gauge-wrap { margin: 0; }
      body.modern-ui .schedule { min-height: 360px; overflow-x: auto; }
      body.modern-ui th, body.modern-ui td { padding: 10px 12px; font-size: 14px; }
      body.modern-ui .status-grid { grid-template-columns: 110px 50px 50px 90px 90px 100px; }
      body.modern-ui .tracking-state-indicators { margin-left: 0; }
    }

    /* Detached map sizing must win over every responsive main-console rule.
       Without these final overrides, popup-width media queries can collapse
       Leaflet's flex container to zero height even while map data is updating. */
    body.modern-ui.detached-map-mode {
      width: 100vw;
      height: 100vh;
      min-height: 0;
      overflow: hidden;
      background: var(--modern-canvas);
    }
    body.modern-ui.detached-map-mode .console,
    body.modern-ui.detached-map-mode .console.map-visible,
    body.modern-ui.detached-map-mode .console.presets-visible.map-visible {
      display: block !important;
      width: 100vw !important;
      max-width: none !important;
      height: 100vh !important;
      min-height: 0 !important;
      margin: 0 !important;
      padding: 0 !important;
      background: var(--modern-canvas);
    }
    body.modern-ui.detached-map-mode .map-panel,
    body.modern-ui.detached-map-mode .console.map-visible .map-panel,
    body.modern-ui.detached-map-mode .console.presets-visible.map-visible .map-panel {
      display: flex !important;
      position: relative;
      width: 100% !important;
      height: 100vh !important;
      min-height: 0 !important;
      padding: 10px !important;
    }
    body.modern-ui.detached-map-mode #earthMap {
      display: block;
      flex: 1 1 0;
      width: 100%;
      height: auto;
      min-height: 0 !important;
    }

    /* General-settings comparison: normal Current values use the standard
       theme text color.  Yellow is reserved ONLY for an actual difference. */
    body.modern-ui #generalSettingsTable input:not(.general-settings-different) {
      color: #d6e8f3 !important;
      caret-color: #d6e8f3 !important;
      -webkit-text-fill-color: #d6e8f3 !important;
    }

    /* Difference indication: only the key text and the two value texts turn
       bold yellow.  Do not change row/cell backgrounds or borders. */
    #generalSettingsTable .general-settings-key.general-settings-different,
    #generalSettingsTable .general-settings-original.general-settings-different,
    body.modern-ui #generalSettingsTable .general-settings-key.general-settings-different,
    body.modern-ui #generalSettingsTable .general-settings-original.general-settings-different {
      color: #ffe45e !important;
      font-weight: 900 !important;
      text-shadow: 0 0 1px rgba(0,0,0,.85) !important;
    }
    #generalSettingsTable input.general-settings-different,
    body.modern-ui #generalSettingsTable input.general-settings-different {
      color: #ffe45e !important;
      -webkit-text-fill-color: #ffe45e !important;
      font-weight: 900 !important;
    }
  </style>
</head>
<body class="__BQE_UI_THEME__-ui" data-bqe-ui-build="vivid-modern-ui-20260825">
  <nav class="taskbar" role="menubar" aria-label="Application menu">
    <div class="menu file-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="File options">File</button>
      <div class="submenu" role="menu" aria-label="File menu">
        <button type="button" role="menuitem" id="restartServerMenuItem">Restart Server</button>
        <button type="button" role="menuitem" id="exitMenuItem">Exit</button>
      </div>
    </div>
    <div class="menu audio-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Sound-card audio">Audio</button>
      <div class="submenu" role="menu" aria-label="Audio menu">
        <button type="button" role="menuitemcheckbox" aria-checked="false" id="streamAudioMenuItem">Stream Audio</button>
        <button type="button" role="menuitem" id="recordAudioMenuItem" disabled>Record Audio</button>
      </div>
    </div>
    <div class="menu view-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="View options">View</button>
      <div class="submenu" role="menu" aria-label="View menu">
        <button type="button" role="menuitemcheckbox" aria-checked="false" id="mapMenuItem">Map</button>
        <button type="button" role="menuitemcheckbox" aria-checked="false" id="presetMenuItem">Show Presets</button>
        <button type="button" role="menuitem" id="sstvFilesMenuItem">SSTV Gallery</button>
        <button type="button" role="menuitem" id="recordingsMenuItem">Recordings</button>
        <button type="button" role="menuitem" id="logsMenuItem">Logs</button>
        <button type="button" role="menuitem" id="fdtConsoleMenuItem" disabled>FDT Console</button>
      </div>
    </div>
    <div class="menu config-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Configuration options">Config</button>
      <div class="submenu config-submenu" role="menu" aria-label="Config menu">
        <button type="button" role="menuitem" id="configQthMenuItem">QTH</button>
        <button type="button" role="menuitem" id="configRadioMenuItem">Radio</button>
        <div class="submenu-group" role="none">
          <button type="button" role="menuitem" id="configPresetsMenuItem" aria-haspopup="true" aria-expanded="false">Presets</button>
          <div class="submenu nested-submenu" role="menu" aria-label="Preset configuration menu">
            <button type="button" role="menuitem" id="configCreatePresetMenuItem">Create Preset</button>
            <button type="button" role="menuitem" id="configEditPresetMenuItem">Edit existing Preset</button>
          </div>
        </div>
        <div class="submenu-group" role="none">
          <button type="button" role="menuitem" id="configAdvancedMenuItem" aria-haspopup="true" aria-expanded="false">Advanced</button>
          <div class="submenu nested-submenu" role="menu" aria-label="Advanced configuration menu">
            <button type="button" role="menuitem" id="configGeneralSettingsMenuItem">Edit general_settings.yaml</button>
            <button type="button" role="menuitem" id="configSatellitesMenuItem">Edit satellites.yaml</button>
            <button type="button" role="menuitem" id="configAddSatelliteMenuItem">Add new Satellite Definition</button>
          </div>
        </div>
      </div>
    </div>
    <div class="menu tracking-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Scheduling tools">Scheduling</button>
      <div class="submenu tracking-submenu" role="menu" aria-label="Scheduling menu">
        <button type="button" role="menuitem" id="updateKepsMenuItem">Update Keps</button>
        <button type="button" role="menuitem" id="schedulePassesMenuItem" aria-busy="false" title="Run bqe_schedule_passes.py --auto_schedule">Auto-Schedule Passes</button>
        <button type="button" role="menuitem" id="customSchedulePassesMenuItem" aria-busy="false" title="Choose satellite nicknames and schedule only those satellites">Custom Schedule Passes</button>
      </div>
    </div>
    <div class="menu antenna-tracking-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Antenna tracking controls">Tracking</button>
      <div class="submenu antenna-tracking-submenu" role="menu" aria-label="Tracking menu">
        <button type="button" role="menuitem" id="antennaTrackingMenuItem">Disable Antenna Tracking</button>
        <button type="button" role="menuitem" id="runTestPassMenuItem">Run test pass now</button>
        <button type="button" role="menuitem" id="endCurrentPassMenuItem">End Current Pass</button>
      </div>
    </div>
    <div class="menu tuning-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Radio tuning controls">Tuning</button>
      <div class="submenu" role="menu" aria-label="Tuning menu">
        <button type="button" role="menuitem" id="enableFdtMenuItem" disabled>Enable FDT</button>
      </div>
    </div>
    <div class="menu help-menu" role="none">
      <button class="menu-button" type="button" aria-haspopup="true" aria-expanded="false" title="Help">Help</button>
      <div class="submenu" role="menu" aria-label="Help menu">
        <button type="button" role="menuitem" id="environmentDiagnosticsMenuItem">Perform Environment Diagnostics</button>
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

  <dialog id="environmentDiagnosticsDialog" aria-labelledby="environmentDiagnosticsTitle" style="width:min(850px,90vw);max-height:85vh;padding:24px;border:1px solid #5384a5;border-radius:16px;background:#102538;color:#edf6ff;box-shadow:0 24px 80px #0009;">
    <h2 id="environmentDiagnosticsTitle" style="margin:0 0 8px">Environment Diagnostics</h2>
    <p id="environmentDiagnosticsSummary" role="status" style="color:#b9d9ef">Checking the server environment…</p>
    <div id="environmentDiagnosticsResults" tabindex="0" style="max-height:52vh;overflow:auto;background:#091825;border-radius:10px;padding:16px;font:13px/1.65 monospace;white-space:pre-wrap;overflow-wrap:anywhere;"></div>
    <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:18px">
      <button type="button" id="environmentDiagnosticsSave" disabled>Save as Text</button>
      <button type="button" id="environmentDiagnosticsClose">Close</button>
    </div>
  </dialog>
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

  <div class="config-modal" id="generalSettingsModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="generalSettingsTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="generalSettingsClose" aria-label="Close general settings editor">&times;</button>
      <div class="config-title" id="generalSettingsTitle">Edit general_settings.yaml</div>
      <form id="generalSettingsForm">
        <div class="general-settings-table" id="generalSettingsTable" role="table" aria-label="General settings comparison">
          <div class="general-settings-cell general-settings-header" role="columnheader">Key</div>
          <div class="general-settings-cell general-settings-header" role="columnheader">Current</div>
          <div class="general-settings-cell general-settings-header" role="columnheader">Original</div>
        </div>
        <div class="config-actions">
          <button type="submit" id="generalSettingsSaveButton">Save</button>
          <button type="button" id="generalSettingsCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="generalSettingsMessage" aria-live="polite"></div>
      </form>
    </div>
  </div>


  <div class="config-modal" id="satellitesEditorModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="satellitesEditorTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="satellitesEditorClose" aria-label="Close satellites editor">&times;</button>
      <div class="config-title" id="satellitesEditorTitle">Edit satellites.yaml</div>
      <form id="satellitesEditorForm">
        <div class="satellite-editor-selector">
          <label for="satellitesEditorSelect">Satellite Definition</label>
          <select id="satellitesEditorSelect" aria-label="Satellite definition"></select>
        </div>
        <div class="general-settings-table" id="satellitesEditorTable" role="table" aria-label="Satellite definition comparison">
          <div class="general-settings-cell general-settings-header" role="columnheader">Key</div>
          <div class="general-settings-cell general-settings-header" role="columnheader">Current</div>
          <div class="general-settings-cell general-settings-header" role="columnheader">Original</div>
        </div>
        <div class="config-actions">
          <button type="submit" id="satellitesEditorSaveButton">Save</button>
          <button type="button" id="satellitesEditorCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="satellitesEditorMessage" aria-live="polite"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="addSatelliteModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="addSatelliteTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="addSatelliteClose" aria-label="Close add satellite dialog">&times;</button>
      <div class="config-title" id="addSatelliteTitle">Add new Satellite Definition</div>
      <form id="addSatelliteForm">
        <div class="config-fields" id="addSatelliteFields">
          <label>Loading</label><input type="text" value="" disabled>
        </div>
        <div class="config-actions">
          <button type="submit" id="addSatelliteAddButton">Add</button>
          <button type="button" id="addSatelliteCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="addSatelliteMessage" aria-live="polite"></div>
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

  <div class="config-modal" id="editPresetModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="editPresetTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="editPresetClose" aria-label="Close Edit existing Preset dialog">&times;</button>
      <div class="config-title" id="editPresetTitle">Edit existing Preset</div>
      <form id="editPresetForm">
        <div class="config-fields">
          <label for="editPresetSelect">Preset file</label>
          <select id="editPresetSelect" aria-label="Preset file"></select>
        </div>
        <div class="config-fields" id="editPresetFields">
          <label>Loading</label><input type="text" value="" disabled>
        </div>
        <div class="config-actions">
          <button type="submit" id="editPresetSaveButton">Save</button>
          <button type="button" id="editPresetCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="editPresetMessage" aria-live="polite"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="testPassModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="testPassTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="testPassClose" aria-label="Close test pass dialog">&times;</button>
      <div class="config-title" id="testPassTitle">Run Test Pass Now</div>
      <form id="testPassForm">
        <div class="config-fields" id="testPassFields">
          <label for="testPassSatelliteSelect">Satellite</label>
          <select id="testPassSatelliteSelect" required disabled>
            <option value="">Loading satellites...</option>
          </select>
        </div>
        <div class="config-actions">
          <button type="submit" id="testPassRunButton">Run</button>
          <button type="button" id="testPassCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="testPassMessage"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="customScheduleModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="customScheduleTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="customScheduleClose" aria-label="Close custom schedule dialog">&times;</button>
      <div class="config-title" id="customScheduleTitle">Custom Schedule Passes</div>
      <form id="customScheduleForm">
        <div id="customScheduleFields">
          <div class="custom-schedule-list" id="customScheduleSatelliteList" aria-label="Satellite nicknames"></div>
        </div>
        <div class="config-actions">
          <button type="submit" id="customScheduleRunButton">Schedule</button>
          <button type="button" id="customScheduleCancelButton">Cancel</button>
        </div>
        <div class="config-message" id="customScheduleMessage" aria-live="polite"></div>
      </form>
    </div>
  </div>

  <div class="config-modal" id="fdtConsoleModal" role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="fdtConsoleTitle">
    <div class="config-box panel">
      <button class="config-close" type="button" id="fdtConsoleClose" aria-label="Close FDT Console">&times;</button>
      <div class="config-title" id="fdtConsoleTitle">FDT Console</div>
      <div class="fdt-passband-heading" id="fdtPassbandHeading">Waiting for active satellite passband data...</div>
      <div class="fdt-dials" id="fdtPassbandDials" aria-label="Transponder passband tuning dials">
        <div class="tuning-dial unavailable" id="fdtDownlinkDial" tabindex="0"
             role="spinbutton" aria-label="Downlink slide-rule frequency tuning dial"
             aria-describedby="fdtWheelHelp" aria-disabled="true">
          <div class="tuning-dial-title">Downlink at Satellite</div>
          <svg viewBox="0 0 360 148" role="img" aria-labelledby="fdtDownlinkDialTitle">
            <title id="fdtDownlinkDialTitle">Downlink slide-rule passband tuning scale</title>
            <defs><clipPath id="fdtDownlinkScaleClip"><rect x="10" y="10" width="340" height="76" rx="3"></rect></clipPath></defs>
            <rect class="slide-rule-window" x="10" y="10" width="340" height="76" rx="3"></rect>
            <g id="fdtDownlinkTicks" clip-path="url(#fdtDownlinkScaleClip)"></g>
            <line class="tuning-dial-needle" id="fdtDownlinkNeedle" x1="180" y1="7" x2="180" y2="89"></line>
            <path class="slide-rule-index-pointer" d="M 173 8 L 187 8 L 180 19 Z"></path>
            <rect class="slide-rule-readout" x="73" y="94" width="214" height="30" rx="3"></rect>
            <text class="tuning-dial-center" id="fdtDownlinkCenter" x="180" y="115" text-anchor="middle">-- MHz</text>
            <text class="tuning-dial-edge" id="fdtDownlinkLow" x="10" y="143" text-anchor="start">LOW --</text>
            <text class="tuning-dial-edge" id="fdtDownlinkHigh" x="350" y="143" text-anchor="end">HIGH --</text>
          </svg>
        </div>
        <div class="tuning-dial unavailable" id="fdtUplinkDial">
          <div class="tuning-dial-title">Uplink at Satellite</div>
          <svg viewBox="0 0 360 148" role="img" aria-labelledby="fdtUplinkDialTitle">
            <title id="fdtUplinkDialTitle">Uplink slide-rule passband tuning scale</title>
            <defs><clipPath id="fdtUplinkScaleClip"><rect x="10" y="10" width="340" height="76" rx="3"></rect></clipPath></defs>
            <rect class="slide-rule-window" x="10" y="10" width="340" height="76" rx="3"></rect>
            <g id="fdtUplinkTicks" clip-path="url(#fdtUplinkScaleClip)"></g>
            <line class="tuning-dial-needle" id="fdtUplinkNeedle" x1="180" y1="7" x2="180" y2="89"></line>
            <path class="slide-rule-index-pointer" d="M 173 8 L 187 8 L 180 19 Z"></path>
            <rect class="slide-rule-readout" x="73" y="94" width="214" height="30" rx="3"></rect>
            <text class="tuning-dial-center" id="fdtUplinkCenter" x="180" y="115" text-anchor="middle">-- MHz</text>
            <text class="tuning-dial-edge" id="fdtUplinkLow" x="10" y="143" text-anchor="start">LOW --</text>
            <text class="tuning-dial-edge" id="fdtUplinkHigh" x="350" y="143" text-anchor="end">HIGH --</text>
          </svg>
        </div>
      </div>
      <div class="fdt-dial-status" id="fdtWheelHelp">
        Click the downlink dial, then use the mouse wheel to tune in 200 Hz steps.
      </div>
      <div class="fdt-dial-status" id="fdtDialStatus" aria-live="polite"></div>
      <form id="fdtConsoleForm">
        <div class="config-fields" id="fdtConsoleFields">
          <label for="fdtReceiveFrequency">Downlink Freq with Doppler (MHz)</label>
          <input type="text" inputmode="decimal" id="fdtReceiveFrequency" value="" autocomplete="off">
          <label for="fdtDownlinkSatelliteFrequency">Downlink Frequency at Sat (Mhz)</label>
          <input type="text" id="fdtDownlinkSatelliteFrequency" value="" readonly>
          <label for="fdtCurrentDownlinkDoppler">Current downlink Doppler (Hz)</label>
          <input type="text" id="fdtCurrentDownlinkDoppler" value="" readonly>
          <label for="fdtUplinkFrequency">Uplink Freq with Doppler (MHz)</label>
          <input type="text" inputmode="decimal" id="fdtUplinkFrequency" value="" autocomplete="off">
          <label for="fdtUplinkSatelliteFrequency">Uplink Frequency at Sat (Mhz)</label>
          <input type="text" id="fdtUplinkSatelliteFrequency" value="" readonly>
          <label for="fdtRecalculationInterval">FDT cadence override</label>
          <div class="fdt-interval-control">
            <input type="range" id="fdtRecalculationInterval" min="4" max="30" step="1" value="10"
                   aria-describedby="fdtIntervalValue fdtIntervalHelp">
            <div class="fdt-interval-limits" aria-hidden="true"><span>4 sec</span><span>30 sec</span></div>
            <output id="fdtIntervalValue" for="fdtRecalculationInterval">Automatic YAML cadence</output>
            <div id="fdtIntervalHelp" class="field-help">
              By default FDT uses the same normal/high-elevation intervals as all other satellites.
              Moving the slider selects a fixed interval override.
            </div>
            <button type="button" id="fdtCadenceAutoButton">Use YAML cadence</button>
          </div>
        </div>
        <div class="config-actions">
          <button type="submit" id="fdtCalculateButton">Sync FDT</button>
          <button type="button" id="fdtDisableButton" disabled>Pause FDT</button>
          <button type="button" id="fdtConsoleCancelButton">Close</button>
        </div>
        <div class="config-message" id="fdtConsoleMessage" aria-live="polite"></div>
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
      <div class="statusbar-main">
        <div class="recording-indicator" id="audioRecordingIndicator" role="status" hidden>
          <span class="recording-light" aria-hidden="true"></span>
          <span>Recording</span>
        </div>
        <div class="status-details">
          <div class="status-grid status-head">
            <div>Satellite</div><div>Azm</div><div>El</div><div>Range</div><div>Altitude</div><div>Doppler</div>
          </div>
          <div class="status-grid">
            <div id="detailSatellite">--</div><div id="detailAz">--</div><div id="detailEl">--</div><div id="detailRange">--</div><div id="detailAltitude">--</div><div id="detailDoppler">--</div>
          </div>
        </div>
        <div class="tracking-state-indicators" role="group" aria-label="Active pass control status">
          <div class="state-indicator disabled" id="trackingStatusIndicator" role="status" aria-label="Tracking disabled">
            <span class="state-check" aria-hidden="true">&times;</span>
            <span class="state-label">Tracking</span>
            <strong class="state-value" id="trackingStatusText">Disabled</strong>
          </div>
          <div class="state-indicator disabled" id="tuningStatusIndicator" role="status" aria-label="Tuning disabled">
            <span class="state-check" aria-hidden="true">&times;</span>
            <span class="state-label">Tuning</span>
            <strong class="state-value" id="tuningStatusText">Disabled</strong>
          </div>
        </div>
      </div>
      <div class="message" id="message">Starting up...</div>
    </section>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const embeddedTestPassSatellites = __BQE_TEST_PASS_SATELLITES_JSON__;
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

    function updateStateIndicator(indicatorId, valueId, label, enabled) {
      const indicator = document.getElementById(indicatorId);
      const valueElement = document.getElementById(valueId);
      if (!indicator || !valueElement) { return; }
      const isEnabled = Boolean(enabled);
      const stateText = isEnabled ? 'Enabled' : 'Disabled';
      indicator.classList.toggle('enabled', isEnabled);
      indicator.classList.toggle('disabled', !isEnabled);
      indicator.setAttribute('aria-label', `${label} ${stateText.toLowerCase()}`);
      valueElement.textContent = stateText;
      const checkElement = indicator.querySelector('.state-check');
      if (checkElement) { checkElement.textContent = isEnabled ? '\u2713' : '\u00d7'; }
    }

    function clearMenuButtonRestoreStyle(button) {
      if (!button) { return; }
      button.classList.remove('menu-click-flash');
      button.style.background = '';
      button.style.color = '';
      button.style.boxShadow = '';
    }

    function menuButtonRestoreBackground(button) {
      if (document.body.classList.contains('modern-ui')) { return 'transparent'; }
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
        button.style.color = document.body.classList.contains('modern-ui') ? '#d8edfb' : '#111';
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
    const detachedFdtMode = new URLSearchParams(window.location.search).get('detached_fdt') === '1';
    let detachedMapWindow = null;
    let detachedFdtWindow = null;
    let sstvFilesWindow = null;
    let recordingsWindow = null;
    let audioRecordingActive = false;
    let audioRecordingCommandInFlight = false;
    let lastAudioRecordingError = null;
    let logsWindow = null;
    let detachedMapCloseMonitor = null;
    let detachedFdtCloseMonitor = null;
    let detachedMapResizeTimer = null;
    let presetCommandInFlight = false;
    let lastPresetRenderSignature = null;
    let antennaTrackingDisabled = false;
    let latestFdtPassband = {};
    let latestFdtConsoleStatus = {};
    let latestFdtEnabled = false;
    let latestFdtRecalculationInterval = 10;
    let latestFdtRecalculationIntervalOverride = null;
    let latestTrackingNormalInterval = 10;
    let latestTrackingHighInterval = 3;
    let latestTrackingHighElevation = 65;
    let fdtSliderIsBeingEdited = false;
    let fdtWheelCommandQueue = Promise.resolve();
    let fdtWheelPendingSteps = 0;

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

    function updateAntennaTrackingMenu(disabled) {
      antennaTrackingDisabled = Boolean(disabled);
      const button = document.getElementById('antennaTrackingMenuItem');
      if (!button) { return; }
      button.textContent = antennaTrackingDisabled
        ? 'Enable Antenna Tracking'
        : 'Disable Antenna Tracking';
      button.title = antennaTrackingDisabled
        ? 'Temporarily enable antenna tracking, overriding the satellite setting'
        : 'Temporarily disable antenna tracking, overriding the satellite setting';
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

    function setCustomSchedulePassesRunning(running) {
      const button = document.getElementById('customSchedulePassesMenuItem');
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

    function updateRecordingIndicator(recording = {}, external = {}) {
      const indicator = document.getElementById('audioRecordingIndicator');
      const capture = recording.active && recording.status === 'recording' ? recording
        : external.status === 'recording' ? external : null;
      indicator.hidden = !capture;
      indicator.title = capture?.output_file ? `Recording to ${capture.output_file}` : '';
    }

    async function refreshStatus() {
      try {
        const response = await fetch('/api/status', { cache: 'no-store' });
        const data = await response.json();
        const fdtAvailable = Boolean(data.fdt_available);
        const recording = data.audio_recording || {};
        updateRecordingIndicator(recording, data.external_audio_recording || {});
        audioRecordingActive = Boolean(recording.active);
        const recordAudioItem = document.getElementById('recordAudioMenuItem');
        if (recordAudioItem) {
          recordAudioItem.textContent = audioRecordingActive ? 'Stop Recording' : 'Record Audio';
          recordAudioItem.disabled = audioRecordingCommandInFlight || Boolean(data.shutdown_requested) ||
            (!audioRecordingActive && Boolean(data.pass_active || data.tracking_running));
          recordAudioItem.title = recording.error || recording.output_file || '';
        }
        if (recording.error && recording.error !== lastAudioRecordingError) {
          data.message = `Audio recording failed: ${recording.error}`;
        }
        lastAudioRecordingError = recording.error || null;
        document.body.classList.toggle(
          'pass-in-progress',
          Boolean(data.pass_active || data.tracking_running)
        );
        if (!fdtAvailable && closeFdtConsoleForEndedPass()) {
          return;
        }
        if (data.shutdown_requested && !data.restart_requested) {
          if (window.bqeStopAudioStreaming) window.bqeStopAudioStreaming();
          showServerExitedMessage();
          return;
        }
        setSchedulePassesRunning(
          Boolean(data.command_running) && text(data.last_command).toLowerCase() === 'schedule_passes'
        );
        setCustomSchedulePassesRunning(
          Boolean(data.command_running) && text(data.last_command).toLowerCase() === 'custom_schedule_passes'
        );
        updateAntennaTrackingMenu(Boolean(data.antenna_tracking_disabled));
        updateStateIndicator(
          'trackingStatusIndicator',
          'trackingStatusText',
          'Tracking',
          data.tracking_enabled
        );
        updateStateIndicator(
          'tuningStatusIndicator',
          'tuningStatusText',
          'Tuning',
          data.tuning_enabled
        );
        const endCurrentPassMenuItem = document.getElementById('endCurrentPassMenuItem');
        if (endCurrentPassMenuItem) {
          endCurrentPassMenuItem.disabled = !Boolean(data.tracking_running);
        }
        const enableFdtMenuItem = document.getElementById('enableFdtMenuItem');
        if (enableFdtMenuItem) {
          enableFdtMenuItem.disabled = !fdtAvailable;
        }
        const fdtConsoleMenuItem = document.getElementById('fdtConsoleMenuItem');
        if (fdtConsoleMenuItem) {
          fdtConsoleMenuItem.disabled = !fdtAvailable;
        }
        const fdtModal = document.getElementById('fdtConsoleModal');
        const fdtData = data.fdt_console_status || {};
        latestFdtEnabled = Boolean(data.fdt_enabled);
        const fdtDisableButton = document.getElementById('fdtDisableButton');
        if (fdtDisableButton) {
          fdtDisableButton.disabled = !fdtAvailable || !latestFdtEnabled;
        }
        latestFdtPassband = data.fdt_passband || {};
        latestFdtConsoleStatus = fdtData;
        const trackingNormalInterval = Number(data.tracking_sleep_interval_seconds);
        const trackingHighInterval = Number(data.tracking_sleep_interval_high_elevation_seconds);
        const trackingHighElevation = Number(data.tracking_high_pass_elevation);
        if (Number.isFinite(trackingNormalInterval)) { latestTrackingNormalInterval = trackingNormalInterval; }
        if (Number.isFinite(trackingHighInterval)) { latestTrackingHighInterval = trackingHighInterval; }
        if (Number.isFinite(trackingHighElevation)) { latestTrackingHighElevation = trackingHighElevation; }

        const fdtInterval = Number(data.fdt_recalculation_interval);
        if (Number.isFinite(fdtInterval)) { latestFdtRecalculationInterval = fdtInterval; }
        const overrideValue = data.fdt_recalculation_interval_override;
        const parsedOverride = overrideValue === null || overrideValue === undefined
          ? null : Number(overrideValue);
        latestFdtRecalculationIntervalOverride = Number.isFinite(parsedOverride) ? parsedOverride : null;
        if (!fdtSliderIsBeingEdited) {
          setFdtIntervalSliderValue(
            latestFdtRecalculationIntervalOverride ?? latestFdtRecalculationInterval,
            latestFdtRecalculationIntervalOverride !== null
          );
        }
        renderFdtPassband(latestFdtPassband, latestFdtConsoleStatus, latestFdtEnabled);
        if (fdtModal && fdtModal.classList.contains('open')) {
          const receiveField = document.getElementById('fdtReceiveFrequency');
          const downlinkSatelliteField = document.getElementById('fdtDownlinkSatelliteFrequency');
          const currentDopplerField = document.getElementById('fdtCurrentDownlinkDoppler');
          const uplinkField = document.getElementById('fdtUplinkFrequency');
          const uplinkSatelliteField = document.getElementById('fdtUplinkSatelliteFrequency');
          const receiveHz = Number(fdtData.radio_receive_frequency_hz);
          const downlinkSatelliteHz = Number(fdtData.downlink_frequency_hz);
          const currentDopplerHz = Number(fdtData.current_downlink_doppler_hz);
          const uplinkHz = Number(fdtData.radio_uplink_frequency_hz);
          const uplinkSatelliteHz = Number(fdtData.uplink_frequency_hz);
          if (receiveField && Number.isFinite(receiveHz)) {
            receiveField.value = (receiveHz / 1e6).toFixed(6);
          }
          if (downlinkSatelliteField && Number.isFinite(downlinkSatelliteHz)) {
            downlinkSatelliteField.value = (downlinkSatelliteHz / 1e6).toFixed(6);
          }
          if (currentDopplerField) {
            currentDopplerField.value = formatFdtDopplerHz(currentDopplerHz);
          }
          if (uplinkField && Number.isFinite(uplinkHz) && fdtData.radio_uplink_frequency_hz !== null) {
            uplinkField.value = (uplinkHz / 1e6).toFixed(6);
          }
          if (uplinkSatelliteField && Number.isFinite(uplinkSatelliteHz) && fdtData.uplink_frequency_hz !== null) {
            uplinkSatelliteField.value = (uplinkSatelliteHz / 1e6).toFixed(6);
          }
        }
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
        const satelliteAltitudeKm = firstFinite(
          status.satellite_altitude_km,
          status.satellite_height_km,
          status.altitude_km,
          status.height_km
        );
        set('detailSatellite', readout(status.satellite || data.current_satellite));
        set('detailRange', readout(status.range_km ? `${Number(status.range_km).toFixed(0)} km` : status.range));
        set('detailAltitude', Number.isFinite(satelliteAltitudeKm) ? `${satelliteAltitudeKm.toFixed(0)} km` : '--');
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
          tr.className = pass.status + (pass.test_pass ? ' test-pass' : '');
          addCell(tr, pass.satellite);
          addCell(tr, pass.satellite_type);
          addCell(tr, pass.el);
          addCell(tr, pass.start);
          addCell(tr, pass.finish);
          rows.appendChild(tr);
        }
        resetPassListScroll();
      } catch (err) {
        updateRecordingIndicator();
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

    function openSstvFilesWindow() {
      if (sstvFilesWindow && !sstvFilesWindow.closed) {
        sstvFilesWindow.focus();
        return;
      }

      sstvFilesWindow = window.open(
        '/sstv-files',
        'bqeWispSstvFiles',
        'popup=yes,width=1050,height=760,resizable=yes,scrollbars=yes'
      );
      if (!sstvFilesWindow) {
        set('message', 'The SSTV Gallery window was blocked by the browser. Allow popups for this site and try again.');
      }
    }

    function openRecordingsWindow() {
      if (recordingsWindow && !recordingsWindow.closed) {
        recordingsWindow.focus();
        return;
      }

      recordingsWindow = window.open(
        '/recordings',
        'bqeWispRecordings',
        'popup=yes,width=1100,height=760,resizable=yes,scrollbars=yes'
      );
      if (!recordingsWindow) {
        set('message', 'The Recordings window was blocked by the browser. Allow popups for this site and try again.');
      }
    }

    function openLogsWindow() {
      if (logsWindow && !logsWindow.closed) {
        logsWindow.focus();
        return;
      }

      logsWindow = window.open(
        '/logs',
        'bqeWispLogs',
        'popup=yes,width=1000,height=760,resizable=yes,scrollbars=yes'
      );
      if (!logsWindow) {
        set('message', 'The Logs window was blocked by the browser. Allow popups for this site and try again.');
      }
    }

    function setFdtConsoleMessage(message) {
      const element = document.getElementById('fdtConsoleMessage');
      if (element) { element.textContent = message || ''; }
    }

    function formatFdtDopplerHz(value) {
      const dopplerHz = Number(value);
      if (!Number.isFinite(dopplerHz)) { return ''; }
      return `${dopplerHz >= 0 ? '+' : ''}${dopplerHz.toFixed(2)}`;
    }

    function setFdtIntervalSliderValue(value, isOverride = latestFdtRecalculationIntervalOverride !== null) {
      const interval = Math.min(30, Math.max(4, Math.round(Number(value))));
      if (!Number.isFinite(interval)) { return; }
      const slider = document.getElementById('fdtRecalculationInterval');
      const output = document.getElementById('fdtIntervalValue');
      if (slider) { slider.value = String(interval); }
      if (output) {
        const text = isOverride
          ? `Override: ${interval} seconds`
          : `Auto: ${latestTrackingNormalInterval} s normally / ${latestTrackingHighInterval} s above ${latestTrackingHighElevation}°`;
        output.value = text;
        output.textContent = text;
      }
    }

    async function updateFdtRecalculationInterval() {
      const slider = document.getElementById('fdtRecalculationInterval');
      if (!slider) { return; }
      const requestedInterval = Number(slider.value);
      fdtSliderIsBeingEdited = true;
      setFdtConsoleMessage(`Setting fixed FDT cadence override to ${requestedInterval} seconds...`);
      const result = await runMenuCommand(
        'set_fdt_recalculation_interval',
        'Set FDT interval',
        false,
        { fdt_recalculation_interval: requestedInterval }
      );
      if (!result || !result.ok) {
        fdtSliderIsBeingEdited = false;
        setFdtIntervalSliderValue(
          latestFdtRecalculationIntervalOverride ?? latestFdtRecalculationInterval,
          latestFdtRecalculationIntervalOverride !== null
        );
        setFdtConsoleMessage(result?.message || 'Could not change the FDT recalculation interval.');
        return;
      }
      fdtSliderIsBeingEdited = false;
      latestFdtRecalculationInterval = Number(result.fdt_recalculation_interval);
      latestFdtRecalculationIntervalOverride = Number(result.fdt_recalculation_interval_override);
      setFdtIntervalSliderValue(latestFdtRecalculationIntervalOverride, true);
      setFdtConsoleMessage(result.message || 'FDT cadence override updated.');
    }

    async function resetFdtRecalculationIntervalToYaml() {
      setFdtConsoleMessage('Returning FDT cadence to the automatic YAML tracking rules...');
      const result = await runMenuCommand(
        'reset_fdt_recalculation_interval',
        'Use YAML cadence',
        false,
        {}
      );
      if (!result || !result.ok) {
        setFdtConsoleMessage(result?.message || 'Could not restore the YAML cadence.');
        return;
      }
      latestFdtRecalculationInterval = Number(result.fdt_recalculation_interval);
      latestFdtRecalculationIntervalOverride = null;
      setFdtIntervalSliderValue(latestFdtRecalculationInterval, false);
      setFdtConsoleMessage(result.message || 'FDT cadence returned to YAML automatic mode.');
    }

    function resetFdtConsoleState() {
      latestFdtPassband = {};
      latestFdtConsoleStatus = {};
      latestFdtEnabled = false;
      fdtWheelPendingSteps = 0;
      const dial = document.getElementById('fdtDownlinkDial');
      if (dial) { dial.classList.remove('tuning'); }
      for (const fieldId of [
        'fdtReceiveFrequency',
        'fdtDownlinkSatelliteFrequency',
        'fdtCurrentDownlinkDoppler',
        'fdtUplinkFrequency',
        'fdtUplinkSatelliteFrequency'
      ]) {
        const field = document.getElementById(fieldId);
        if (field) { field.value = ''; }
      }
      setFdtConsoleMessage('');
      renderFdtPassband({}, {}, false);
    }

    function closeFdtConsoleForEndedPass() {
      resetFdtConsoleState();
      if (detachedFdtMode) {
        const modal = document.getElementById('fdtConsoleModal');
        if (modal) {
          modal.classList.remove('open');
          modal.setAttribute('aria-hidden', 'true');
        }
        window.close();
        return true;
      }
      closeDetachedFdtWindow();
      return false;
    }

    function niceSlideRuleStep(rawStep) {
      if (!Number.isFinite(rawStep) || rawStep <= 0) { return NaN; }
      const exponent = Math.floor(Math.log10(rawStep));
      const magnitude = 10 ** exponent;
      const fraction = rawStep / magnitude;
      let niceFraction = 10;
      if (fraction <= 1) { niceFraction = 1; }
      else if (fraction <= 2) { niceFraction = 2; }
      else if (fraction <= 2.5) { niceFraction = 2.5; }
      else if (fraction <= 5) { niceFraction = 5; }
      return niceFraction * magnitude;
    }

    function slideRuleLabelDecimals(majorStepMhz) {
      if (majorStepMhz >= 0.001) { return 3; }
      if (majorStepMhz >= 0.0001) { return 4; }
      return 5;
    }

    function renderSlideRuleScale(groupId, low, high, center) {
      const group = document.getElementById(groupId);
      if (!group) { return; }
      group.replaceChildren();

      const svgNamespace = 'http://www.w3.org/2000/svg';
      const bandwidth = high - low;
      const scaleWidth = 300;
      const indexX = 180;
      const baseline = document.createElementNS(svgNamespace, 'line');
      baseline.setAttribute('x1', '10');
      baseline.setAttribute('y1', '76');
      baseline.setAttribute('x2', '350');
      baseline.setAttribute('y2', '76');
      baseline.setAttribute('class', 'slide-rule-baseline');
      group.appendChild(baseline);

      const frequencyToX = frequency => indexX + ((frequency - center) / bandwidth) * scaleWidth;
      for (const edgeFrequency of [low, high]) {
        const edgeX = frequencyToX(edgeFrequency);
        if (edgeX >= 8 && edgeX <= 352) {
          const edge = document.createElementNS(svgNamespace, 'line');
          edge.setAttribute('x1', edgeX.toFixed(2));
          edge.setAttribute('y1', '16');
          edge.setAttribute('x2', edgeX.toFixed(2));
          edge.setAttribute('y2', '78');
          edge.setAttribute('class', 'slide-rule-band-edge');
          group.appendChild(edge);
        }
      }

      const minorStep = niceSlideRuleStep(bandwidth / 30);
      if (!Number.isFinite(minorStep) || minorStep <= 0) { return; }
      const majorEvery = 5;
      const majorStep = minorStep * majorEvery;
      const labelDecimals = slideRuleLabelDecimals(majorStep);
      const epsilon = minorStep * 0.01;
      const firstTick = Math.ceil((low - epsilon) / minorStep) * minorStep;

      for (let frequency = firstTick, safety = 0;
           frequency <= high + epsilon && safety < 500;
           frequency += minorStep, safety += 1) {
        const x = frequencyToX(frequency);
        if (x < 6 || x > 354) { continue; }
        const tickNumber = Math.round(frequency / minorStep);
        const major = Math.abs(tickNumber % majorEvery) === 0;
        const tick = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        tick.setAttribute('x1', x.toFixed(2));
        tick.setAttribute('y1', major ? '37' : '54');
        tick.setAttribute('x2', x.toFixed(2));
        tick.setAttribute('y2', '76');
        tick.setAttribute('class', major ? 'tuning-dial-tick major' : 'tuning-dial-tick');
        group.appendChild(tick);

        if (major) {
          const label = document.createElementNS(svgNamespace, 'text');
          label.setAttribute('x', x.toFixed(2));
          label.setAttribute('y', '30');
          label.setAttribute('text-anchor', 'middle');
          label.setAttribute('class', 'slide-rule-number');
          label.textContent = frequency.toFixed(labelDecimals);
          group.appendChild(label);
        }
      }
    }

    function renderTuningDial(prefix, band) {
      const dial = document.getElementById(`${prefix}Dial`);
      const lowLabel = document.getElementById(`${prefix}Low`);
      const highLabel = document.getElementById(`${prefix}High`);
      const centerLabel = document.getElementById(`${prefix}Center`);
      const tickGroup = document.getElementById(`${prefix}Ticks`);

      const low = Number(band?.low_mhz);
      const high = Number(band?.high_mhz);
      const center = Number(band?.center_mhz);
      const valid = Number.isFinite(low) && Number.isFinite(high)
        && Number.isFinite(center) && high > low;
      if (dial) { dial.classList.toggle('unavailable', !valid); }
      if (!valid) {
        if (lowLabel) { lowLabel.textContent = 'LOW --'; }
        if (highLabel) { highLabel.textContent = 'HIGH --'; }
        if (centerLabel) { centerLabel.textContent = '-- MHz'; }
        if (tickGroup) { tickGroup.replaceChildren(); }
        return false;
      }

      renderSlideRuleScale(`${prefix}Ticks`, low, high, center);
      if (lowLabel) { lowLabel.textContent = `LOW ${low.toFixed(3)} MHz`; }
      if (highLabel) { highLabel.textContent = `HIGH ${high.toFixed(3)} MHz`; }
      if (centerLabel) { centerLabel.textContent = `${center.toFixed(6)} MHz`; }
      return true;
    }

    function renderFdtPassband(passband, fdtStatus = {}, fdtEnabled = false) {
      const data = passband && typeof passband === 'object' ? passband : {};
      const statusData = fdtStatus && typeof fdtStatus === 'object' ? fdtStatus : {};
      const downlinkSatelliteHz = Number(statusData.downlink_frequency_hz);
      const uplinkSatelliteHz = Number(statusData.uplink_frequency_hz);
      const downlinkBand = data.downlink && typeof data.downlink === 'object'
        ? { ...data.downlink }
        : data.downlink;
      const uplinkBand = data.uplink && typeof data.uplink === 'object'
        ? { ...data.uplink }
        : data.uplink;
      if (downlinkBand && Number.isFinite(downlinkSatelliteHz) && downlinkSatelliteHz > 0) {
        downlinkBand.center_mhz = downlinkSatelliteHz / 1e6;
      }
      if (uplinkBand && Number.isFinite(uplinkSatelliteHz) && uplinkSatelliteHz > 0) {
        uplinkBand.center_mhz = uplinkSatelliteHz / 1e6;
      }
      const downlinkAvailable = renderTuningDial('fdtDownlink', downlinkBand);
      const uplinkAvailable = renderTuningDial('fdtUplink', uplinkBand);
      const heading = document.getElementById('fdtPassbandHeading');
      const status = document.getElementById('fdtDialStatus');
      const downlinkDial = document.getElementById('fdtDownlinkDial');
      const wheelAvailable = Boolean(fdtEnabled) && downlinkAvailable && uplinkAvailable;
      if (downlinkDial) {
        downlinkDial.classList.toggle('interactive', wheelAvailable);
        downlinkDial.setAttribute('aria-disabled', wheelAvailable ? 'false' : 'true');
        if (downlinkBand && downlinkAvailable) {
          downlinkDial.setAttribute('aria-valuemin', Number(downlinkBand.low_mhz).toFixed(6));
          downlinkDial.setAttribute('aria-valuemax', Number(downlinkBand.high_mhz).toFixed(6));
          downlinkDial.setAttribute('aria-valuenow', Number(downlinkBand.center_mhz).toFixed(6));
          downlinkDial.setAttribute('aria-valuetext', `${Number(downlinkBand.center_mhz).toFixed(6)} MHz`);
        } else {
          downlinkDial.removeAttribute('aria-valuemin');
          downlinkDial.removeAttribute('aria-valuemax');
          downlinkDial.removeAttribute('aria-valuenow');
          downlinkDial.removeAttribute('aria-valuetext');
        }
      }

      if (data.error) {
        if (heading) { heading.textContent = 'Passband data unavailable'; }
        if (status) { status.textContent = data.error; }
        return;
      }
      if (!data.nickname) {
        if (heading) { heading.textContent = 'Waiting for active satellite passband data...'; }
        if (status) { status.textContent = ''; }
        return;
      }

      const transponder = text(data.transponder_type).trim();
      const satelliteName = text(data.satellite_name).trim();
      const details = [data.nickname];
      if (satelliteName && satelliteName !== data.nickname) { details.push(satelliteName); }
      if (transponder) { details.push(`${transponder} transponder`); }
      if (heading) { heading.textContent = details.join(' \u2022 '); }
      if (status) {
        const transponderKind = normalizedTransponderType(transponder);
        if (!downlinkAvailable || !uplinkAvailable) {
          status.textContent = 'One or more passband range or center-frequency keys are missing or invalid.';
        } else if (!fdtEnabled) {
          status.textContent = 'FDT is disabled. Press Sync FDT to begin continuous Doppler tuning.';
        } else if (transponderKind === 'INVERTING') {
          status.textContent = 'Wheel tuning moves uplink opposite to downlink for this INVERTING transponder.';
        } else if (transponderKind.startsWith('NON')) {
          status.textContent = 'Wheel tuning moves uplink with downlink for this NON-INVERTING transponder.';
        } else {
          status.textContent = 'Set transponder_type to INVERTING or NON-INVERTING to enable wheel tuning.';
        }
      }
    }

    function normalizedTransponderType(value) {
      return text(value).trim().toUpperCase().replace(/[^A-Z]/g, '');
    }

    function applyFdtCommandResult(result) {
      if (!result || !result.ok) { return; }
      latestFdtEnabled = true;
      latestFdtConsoleStatus = { ...latestFdtConsoleStatus, ...result };
      renderFdtPassband(latestFdtPassband, latestFdtConsoleStatus, latestFdtEnabled);
      const disableButton = document.getElementById('fdtDisableButton');
      if (disableButton) { disableButton.disabled = false; }

      const receiveHz = Number(result.radio_receive_frequency_hz);
      const downlinkSatelliteHz = Number(result.downlink_frequency_hz);
      const uplinkHz = Number(result.radio_uplink_frequency_hz);
      const uplinkSatelliteHz = Number(result.uplink_frequency_hz);
      const receiveField = document.getElementById('fdtReceiveFrequency');
      const downlinkSatelliteField = document.getElementById('fdtDownlinkSatelliteFrequency');
      const uplinkField = document.getElementById('fdtUplinkFrequency');
      const uplinkSatelliteField = document.getElementById('fdtUplinkSatelliteFrequency');
      if (receiveField && Number.isFinite(receiveHz)) {
        receiveField.value = (receiveHz / 1e6).toFixed(6);
      }
      if (downlinkSatelliteField && Number.isFinite(downlinkSatelliteHz)) {
        downlinkSatelliteField.value = (downlinkSatelliteHz / 1e6).toFixed(6);
      }
      if (uplinkField && Number.isFinite(uplinkHz)) {
        uplinkField.value = (uplinkHz / 1e6).toFixed(6);
      }
      if (uplinkSatelliteField && Number.isFinite(uplinkSatelliteHz)) {
        uplinkSatelliteField.value = (uplinkSatelliteHz / 1e6).toFixed(6);
      }
    }

    async function performFdtWheelStep(direction) {
      const dial = document.getElementById('fdtDownlinkDial');
      fdtWheelPendingSteps += 1;
      if (dial) { dial.classList.add('tuning'); }
      setFdtConsoleMessage(
        direction > 0 ? 'Tuning downlink up 200 Hz...' : 'Tuning downlink down 200 Hz...'
      );
      try {
        const result = await runMenuCommand(
          'tune_fdt_step',
          'FDT wheel tuning',
          false,
          { downlink_direction: direction }
        );
        if (!result || !result.ok) {
          setFdtConsoleMessage(result?.message || 'FDT wheel tuning failed.');
          return;
        }
        applyFdtCommandResult(result);
        setFdtConsoleMessage(result.message || 'FDT wheel tuning completed.');
      } finally {
        fdtWheelPendingSteps = Math.max(0, fdtWheelPendingSteps - 1);
        if (dial && fdtWheelPendingSteps === 0) { dial.classList.remove('tuning'); }
      }
    }

    function queueFdtWheelStep(direction) {
      fdtWheelCommandQueue = fdtWheelCommandQueue
        .then(() => performFdtWheelStep(direction))
        .catch((error) => {
          setFdtConsoleMessage(`FDT wheel tuning failed: ${error}`);
        });
    }

    function handleFdtDialWheel(event) {
      const dial = event.currentTarget;
      if (!latestFdtEnabled || !dial || document.activeElement !== dial || !dial.classList.contains('interactive')) {
        return;
      }
      if (event.deltaY === 0) { return; }
      event.preventDefault();
      queueFdtWheelStep(event.deltaY < 0 ? 1 : -1);
    }

    function handleFdtDialKey(event) {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') { return; }
      const dial = event.currentTarget;
      if (!latestFdtEnabled || !dial || !dial.classList.contains('interactive')) { return; }
      event.preventDefault();
      queueFdtWheelStep(event.key === 'ArrowUp' ? 1 : -1);
    }

    function stopDetachedFdtCloseMonitor() {
      if (detachedFdtCloseMonitor !== null) {
        window.clearInterval(detachedFdtCloseMonitor);
        detachedFdtCloseMonitor = null;
      }
    }

    function closeDetachedFdtWindow() {
      stopDetachedFdtCloseMonitor();
      if (detachedFdtWindow && !detachedFdtWindow.closed) {
        detachedFdtWindow.close();
      }
      detachedFdtWindow = null;
    }

    function openDetachedFdtWindow() {
      if (detachedFdtWindow && !detachedFdtWindow.closed) {
        detachedFdtWindow.focus();
        return true;
      }
      const fdtUrl = new URL(window.location.href);
      fdtUrl.searchParams.delete('detached_map');
      fdtUrl.searchParams.set('detached_fdt', '1');
      detachedFdtWindow = window.open(
        fdtUrl.toString(),
        'bqeWispDetachedFdtConsole',
        'popup=yes,width=900,height=720,resizable=yes,scrollbars=yes'
      );
      if (!detachedFdtWindow) {
        detachedFdtWindow = null;
        return false;
      }
      stopDetachedFdtCloseMonitor();
      detachedFdtCloseMonitor = window.setInterval(() => {
        if (!detachedFdtWindow || detachedFdtWindow.closed) {
          stopDetachedFdtCloseMonitor();
          detachedFdtWindow = null;
        }
      }, 500);
      return true;
    }

    function openFdtConsole() {
      if (!detachedFdtMode) {
        if (!openDetachedFdtWindow()) {
          set('message', 'The FDT Console window was blocked by the browser. Allow popups for this site and try again.');
        }
        return;
      }
      const modal = document.getElementById('fdtConsoleModal');
      if (!modal) { return; }
      const receiveField = document.getElementById('fdtReceiveFrequency');
      const downlinkSatelliteField = document.getElementById('fdtDownlinkSatelliteFrequency');
      const currentDopplerField = document.getElementById('fdtCurrentDownlinkDoppler');
      const uplinkField = document.getElementById('fdtUplinkFrequency');
      const uplinkSatelliteField = document.getElementById('fdtUplinkSatelliteFrequency');
      if (receiveField) { receiveField.value = ''; }
      if (downlinkSatelliteField) { downlinkSatelliteField.value = ''; }
      if (currentDopplerField) {
        currentDopplerField.value = formatFdtDopplerHz(
          latestFdtConsoleStatus.current_downlink_doppler_hz
        );
      }
      if (uplinkField) { uplinkField.value = ''; }
      if (uplinkSatelliteField) { uplinkSatelliteField.value = ''; }
      setFdtIntervalSliderValue(
        latestFdtRecalculationIntervalOverride ?? latestFdtRecalculationInterval,
        latestFdtRecalculationIntervalOverride !== null
      );
      setFdtConsoleMessage('');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      if (receiveField) { receiveField.focus(); }
    }

    function closeFdtConsole() {
      if (detachedFdtMode) {
        window.close();
        return;
      }
      const modal = document.getElementById('fdtConsoleModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setFdtConsoleMessage('');
      const menuItem = document.getElementById('fdtConsoleMenuItem');
      if (menuItem && !menuItem.disabled) { menuItem.focus(); }
    }

    async function submitFdtConsole(event) {
      if (event) { event.preventDefault(); }
      const receiveField = document.getElementById('fdtReceiveFrequency');
      const enteredFrequency = receiveField ? receiveField.value.trim() : '';
      const uplinkField = document.getElementById('fdtUplinkFrequency');
      const enteredUplinkFrequency = uplinkField ? uplinkField.value.trim() : '';
      setFdtConsoleMessage('Reading radio frequencies and calculating frequencies at the satellite...');
      const result = await runMenuCommand(
        'enable_fdt',
        'Sync FDT',
        false,
        {
          receive_frequency_mhz: enteredFrequency,
          uplink_frequency_mhz: enteredUplinkFrequency,
          fdt_recalculation_interval: latestFdtRecalculationIntervalOverride
        }
      );
      if (!result || !result.ok) {
        setFdtConsoleMessage(result?.message || 'FDT calculation failed.');
        return;
      }
      if (receiveField) {
        receiveField.value = (Number(result.radio_receive_frequency_hz) / 1e6).toFixed(6);
      }
      const downlinkSatelliteField = document.getElementById('fdtDownlinkSatelliteFrequency');
      if (downlinkSatelliteField) {
        downlinkSatelliteField.value = (Number(result.downlink_frequency_hz) / 1e6).toFixed(6);
      }
      if (uplinkField && result.radio_uplink_frequency_hz !== null) {
        uplinkField.value = (Number(result.radio_uplink_frequency_hz) / 1e6).toFixed(6);
      }
      const uplinkSatelliteField = document.getElementById('fdtUplinkSatelliteFrequency');
      if (uplinkSatelliteField && result.uplink_frequency_hz !== null) {
        uplinkSatelliteField.value = (Number(result.uplink_frequency_hz) / 1e6).toFixed(6);
      }
      latestFdtConsoleStatus = { ...latestFdtConsoleStatus, ...result };
      latestFdtEnabled = true;
      renderFdtPassband(latestFdtPassband, latestFdtConsoleStatus, latestFdtEnabled);
      const disableButton = document.getElementById('fdtDisableButton');
      if (disableButton) { disableButton.disabled = false; }
      setFdtConsoleMessage(result.message || 'FDT enabled.');
    }

    async function disableFdtFromConsole() {
      setFdtConsoleMessage('Pausing continuous FDT...');
      const result = await runMenuCommand('disable_fdt', 'Pause FDT', false);
      if (!result || !result.ok) {
        setFdtConsoleMessage(result?.message || 'Could not pause FDT.');
        return;
      }
      latestFdtEnabled = false;
      const currentDopplerHz = Number(latestFdtConsoleStatus.current_downlink_doppler_hz);
      latestFdtConsoleStatus = { enabled: false };
      if (Number.isFinite(currentDopplerHz)) {
        latestFdtConsoleStatus.current_downlink_doppler_hz = currentDopplerHz;
      }
      fdtWheelPendingSteps = 0;
      const dial = document.getElementById('fdtDownlinkDial');
      if (dial) { dial.classList.remove('tuning'); }
      for (const fieldId of [
        'fdtReceiveFrequency',
        'fdtDownlinkSatelliteFrequency',
        'fdtUplinkFrequency',
        'fdtUplinkSatelliteFrequency'
      ]) {
        const field = document.getElementById(fieldId);
        if (field) { field.value = ''; }
      }
      const disableButton = document.getElementById('fdtDisableButton');
      if (disableButton) { disableButton.disabled = true; }
      const currentDopplerField = document.getElementById('fdtCurrentDownlinkDoppler');
      if (currentDopplerField) {
        currentDopplerField.value = formatFdtDopplerHz(currentDopplerHz);
      }
      renderFdtPassband(latestFdtPassband, latestFdtConsoleStatus, false);
      setFdtConsoleMessage(result.message || 'FDT paused.');
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

    async function runMenuCommand(action, label, refreshAfter = true, extraPayload = {}) {
      set('message', `${label} requested...`);
      try {
        const response = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ action, ...extraPayload })
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

    function enableFdtFromMainMenu() {
      openFdtConsole();
      runMenuCommand('enable_fdt', 'Enable FDT', true);
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

    async function toggleAntennaTracking() {
      const action = antennaTrackingDisabled
        ? 'enable_antenna_tracking'
        : 'disable_antenna_tracking';
      const label = antennaTrackingDisabled
        ? 'Enable Antenna Tracking'
        : 'Disable Antenna Tracking';
      const result = await runMenuCommand(action, label, true);
      if (result && Object.prototype.hasOwnProperty.call(result, 'antenna_tracking_disabled')) {
        updateAntennaTrackingMenu(Boolean(result.antenna_tracking_disabled));
      }
      return result;
    }

    function setCustomScheduleMessage(message, isError = false) {
      const element = document.getElementById('customScheduleMessage');
      if (!element) { return; }
      element.textContent = message || '';
      element.classList.toggle('custom-schedule-error', Boolean(isError));
    }

    function closeCustomScheduleDialog() {
      const modal = document.getElementById('customScheduleModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      const menuItem = document.getElementById('customSchedulePassesMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    function openCustomScheduleDialog() {
      const modal = document.getElementById('customScheduleModal');
      const list = document.getElementById('customScheduleSatelliteList');
      if (!modal || !list) { return; }

      list.innerHTML = '';
      const satellites = Array.isArray(embeddedTestPassSatellites)
        ? embeddedTestPassSatellites
        : [];

      for (const satellite of satellites) {
        const nickname = satellite.nickname || '';
        if (!nickname) { continue; }

        const label = document.createElement('label');
        label.className = 'custom-schedule-option';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'customScheduleSatellite';
        checkbox.value = nickname;

        const nameSpan = document.createElement('span');
        const satelliteName = satellite.satellite_name && satellite.satellite_name !== nickname
          ? ` — ${satellite.satellite_name}`
          : '';
        nameSpan.textContent = `${nickname}${satelliteName}`;

        const catalogSpan = document.createElement('span');
        catalogSpan.className = 'custom-schedule-catalog';
        const catalog = satellite.catalog_number === null || satellite.catalog_number === undefined
          ? ''
          : String(satellite.catalog_number);
        catalogSpan.textContent = catalog ? `Catalog ${catalog}` : 'No catalog number';

        label.appendChild(checkbox);
        label.appendChild(nameSpan);
        label.appendChild(catalogSpan);
        list.appendChild(label);
      }

      if (!list.children.length) {
        const empty = document.createElement('div');
        empty.textContent = 'No satellites were found in bqe_config/satellites.yaml.';
        list.appendChild(empty);
      }

      const runButton = document.getElementById('customScheduleRunButton');
      if (runButton) { runButton.disabled = false; }
      setCustomScheduleMessage('Check one or more satellite nicknames, then press Schedule.');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      const firstCheckbox = list.querySelector('input[type="checkbox"]');
      if (firstCheckbox) { firstCheckbox.focus(); }
    }

    async function submitCustomSchedule(event) {
      event.preventDefault();

      // Read the checkboxes on EVERY submission.  If a previous validation
      // failed, the user can change the selections and press Schedule again;
      // this sends the corrected selection back through the catalog-number
      // validation before any scheduling process is started.
      const checked = Array.from(
        document.querySelectorAll('#customScheduleSatelliteList input[name="customScheduleSatellite"]:checked')
      ).map((element) => element.value);

      const runButton = document.getElementById('customScheduleRunButton');

      if (!checked.length) {
        if (runButton) { runButton.disabled = false; }
        setCustomScheduleMessage('Select at least one satellite.', true);
        return;
      }

      if (runButton) { runButton.disabled = true; }
      setCustomSchedulePassesRunning(true);
      setCustomScheduleMessage('Checking the current selections for duplicate catalog numbers...');

      const result = await runMenuCommand(
        'custom_schedule_passes',
        'Custom Schedule Passes',
        true,
        { nicknames: checked }
      );

      if (result && result.ok) {
        closeCustomScheduleDialog();
        return;
      }

      // A validation error is intentionally non-terminal.  Preserve the
      // current checkbox state, re-enable Schedule, and allow an immediate
      // retry after the user changes the selections.
      setCustomSchedulePassesRunning(false);
      if (runButton) { runButton.disabled = false; }

      const message = result && result.message
        ? result.message
        : 'Could not create the custom schedule. Change the selections and press Schedule again.';
      setCustomScheduleMessage(message, true);
    }

    function customScheduleSelectionChanged() {
      const runButton = document.getElementById('customScheduleRunButton');
      if (runButton) { runButton.disabled = false; }
      setCustomScheduleMessage(
        'Selection changed. Press Schedule to check the selections again.'
      );
    }

    function setTestPassMessage(message) {
      const element = document.getElementById('testPassMessage');
      if (element) { element.textContent = message || ''; }
    }

    function closeTestPassDialog() {
      const modal = document.getElementById('testPassModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      const menuItem = document.getElementById('runTestPassMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    async function openTestPassDialog() {
      const modal = document.getElementById('testPassModal');
      const select = document.getElementById('testPassSatelliteSelect');
      if (!modal || !select) { return; }

      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');

      // Normally the list is embedded into the page when the server starts. This
      // avoids leaving the dialog stuck on "Loading satellites..." if a secondary
      // command request is delayed or a browser has a stale page. Keep the command
      // API as a fallback for unusual packaging/path layouts.
      let satellites = Array.isArray(embeddedTestPassSatellites)
        ? embeddedTestPassSatellites
        : [];

      if (!satellites.length) {
        select.disabled = true;
        select.innerHTML = '<option value="">Loading satellites...</option>';
        setTestPassMessage('Loading satellites.yaml...');
        const result = await runMenuCommand(
          'get_test_pass_satellites',
          'Load test pass satellites',
          false
        );
        if (!result || !result.ok) {
          select.innerHTML = '<option value="">No satellites available</option>';
          setTestPassMessage(
            result && result.message ? result.message : 'Could not load satellites.yaml.'
          );
          return;
        }
        satellites = Array.isArray(result.satellites) ? result.satellites : [];
      }

      select.innerHTML = '';
      for (const satellite of satellites) {
        const option = document.createElement('option');
        option.value = satellite.nickname || '';
        const name = satellite.satellite_name && satellite.satellite_name !== satellite.nickname
          ? ` — ${satellite.satellite_name}`
          : '';
        option.textContent = `${satellite.nickname || ''}${name}`;
        select.appendChild(option);
      }
      select.disabled = satellites.length === 0;
      if (!satellites.length) {
        select.innerHTML = '<option value="">No satellites available</option>';
      }
      setTestPassMessage(
        satellites.length ? 'Select a satellite and press Run.' : 'No satellites were found.'
      );
      if (!select.disabled) { select.focus(); }
    }

    async function submitTestPass(event) {
      event.preventDefault();
      const select = document.getElementById('testPassSatelliteSelect');
      const nickname = select ? select.value : '';
      if (!nickname) {
        setTestPassMessage('Select a satellite first.');
        return;
      }

      const result = await runMenuCommand('run_test_pass', 'Run test pass', true, { nickname });
      if (result && result.ok) {
        closeTestPassDialog();
      } else {
        setTestPassMessage(result && result.message ? result.message : 'Could not schedule the test pass.');
      }
    }

    async function endCurrentPass() {
      return runMenuCommand('end_current_pass', 'End Current Pass', true);
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

    const REMOTE_CONFIG_READ_ONLY_MESSAGE =
      'Read only - configuration changes are only allowed from a browser running on the BQE WISP server computer using localhost or 127.0.0.1.';

    function applyConfigEditorAccess(containerSelector, saveButtonId, editable) {
      const allowEdit = editable !== false;
      document.querySelectorAll(containerSelector + ' input, ' + containerSelector + ' textarea, ' + containerSelector + ' select').forEach((control) => {
        control.disabled = !allowEdit;
        control.setAttribute('aria-disabled', allowEdit ? 'false' : 'true');
      });
      const saveButton = document.getElementById(saveButtonId);
      if (saveButton) {
        saveButton.disabled = !allowEdit;
        saveButton.setAttribute('aria-disabled', allowEdit ? 'false' : 'true');
        saveButton.title = allowEdit ? '' : REMOTE_CONFIG_READ_ONLY_MESSAGE;
      }
      return allowEdit;
    }

    function configEditorMessage(message, editable) {
      if (editable !== false) { return message || ''; }
      const prefix = message ? String(message).trim() + ' ' : '';
      if (prefix.includes(REMOTE_CONFIG_READ_ONLY_MESSAGE)) { return prefix.trim(); }
      return (prefix + REMOTE_CONFIG_READ_ONLY_MESSAGE).trim();
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
        const qthEditable = applyConfigEditorAccess('#qthFields', 'qthOkButton', result.editable);
        setQthMessage(configEditorMessage(result.message || 'QTH configuration loaded.', qthEditable));
        const firstInput = document.querySelector('#qthFields input[data-qth-key]');
        if (qthEditable && firstInput) { firstInput.focus(); firstInput.select(); }
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
      const saveButton = document.getElementById('qthOkButton');
      if (saveButton && saveButton.disabled) {
        setQthMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE);
        return;
      }
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


    function setGeneralSettingsMessage(message) {
      const messageElement = document.getElementById('generalSettingsMessage');
      if (messageElement) { messageElement.textContent = message || ''; }
    }

    function renderGeneralSettingsRows(rows) {
      const table = document.getElementById('generalSettingsTable');
      if (!table) { return; }
      table.innerHTML = '';

      for (const heading of ['Key', 'Current', 'Original']) {
        const header = document.createElement('div');
        header.className = 'general-settings-cell general-settings-header';
        header.setAttribute('role', 'columnheader');
        header.textContent = heading;
        table.appendChild(header);
      }

      function applyDifferenceHighlight(keyCell, currentCell, input, originalCell, isDifferent) {
        const different = Boolean(isDifferent);

        // Only the key and the two displayed values are highlighted.  Do not
        // color the Current cell/container itself; normal Current entries must
        // retain the standard UI text color.
        for (const element of [keyCell, input, originalCell]) {
          if (!element) { continue; }
          element.classList.toggle('general-settings-different', different);
          if (different) {
            // Apply directly as !important so neither Classic nor Modern theme
            // rules can accidentally override the comparison indication.
            element.style.setProperty('color', '#ffe45e', 'important');
            element.style.setProperty('font-weight', '900', 'important');
            if (element === input) {
              element.style.setProperty('-webkit-text-fill-color', '#ffe45e', 'important');
            }
          } else {
            element.style.removeProperty('color');
            element.style.removeProperty('font-weight');
            if (element === input) {
              element.style.removeProperty('-webkit-text-fill-color');
            }
          }
        }

        // Ensure the cell itself never inherits the difference color.
        if (currentCell) {
          currentCell.classList.remove('general-settings-different');
          currentCell.style.removeProperty('color');
          currentCell.style.removeProperty('font-weight');
        }
      }

      const safeRows = Array.isArray(rows) ? rows : [];
      for (const row of safeRows) {
        const key = String(row?.key ?? '');
        const originalValue = String(row?.original ?? '');

        const keyCell = document.createElement('div');
        keyCell.className = 'general-settings-cell general-settings-key';
        keyCell.setAttribute('role', 'cell');
        keyCell.textContent = key;
        table.appendChild(keyCell);

        const currentCell = document.createElement('div');
        currentCell.className = 'general-settings-cell general-settings-current';
        currentCell.setAttribute('role', 'cell');
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.generalSettingsKey = key;
        input.dataset.originalValue = originalValue;
        input.value = row?.current ?? '';
        input.setAttribute('aria-label', 'Current value for ' + key);
        currentCell.appendChild(input);
        table.appendChild(currentCell);

        const originalCell = document.createElement('div');
        originalCell.className = 'general-settings-cell general-settings-original';
        originalCell.setAttribute('role', 'cell');
        originalCell.textContent = originalValue;
        table.appendChild(originalCell);

        // Determine the initial highlight from the values actually displayed in
        // the browser, rather than relying only on a server-provided flag.  This
        // makes a visible 4-vs-3 (for example) unambiguously highlight even if an
        // older browser/server payload omits or mishandles the `different` flag.
        // Presence flags preserve highlighting for keys that exist in only one
        // of the two YAML files even when the displayed fallback values match.
        const currentPresent = row?.current_present !== false;
        const originalPresent = row?.original_present !== false;
        const initiallyDifferent = (
          currentPresent !== originalPresent
          || String(input.value) !== originalValue
          || Boolean(row?.different)
        );
        applyDifferenceHighlight(keyCell, currentCell, input, originalCell, initiallyDifferent);

        input.addEventListener('input', () => {
          const editedDifferent = (
            currentPresent !== originalPresent
            || String(input.value) !== String(input.dataset.originalValue ?? '')
          );
          applyDifferenceHighlight(keyCell, currentCell, input, originalCell, editedDifferent);
        });
      }
    }

    function collectGeneralSettingsFields() {
      const data = {};
      document.querySelectorAll('#generalSettingsTable input[data-general-settings-key]').forEach((input) => {
        data[input.dataset.generalSettingsKey] = input.value;
      });
      return data;
    }

    async function openGeneralSettingsDialog() {
      const modal = document.getElementById('generalSettingsModal');
      if (!modal) { return; }
      setGeneralSettingsMessage('Loading general settings...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      try {
        const response = await fetch('/api/config/general_settings', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not load general settings.');
        }
        renderGeneralSettingsRows(result.rows || []);
        const generalSettingsEditable = applyConfigEditorAccess('#generalSettingsTable', 'generalSettingsSaveButton', result.editable);
        setGeneralSettingsMessage(configEditorMessage(result.message || 'General settings loaded.', generalSettingsEditable));
        const firstInput = document.querySelector('#generalSettingsTable input[data-general-settings-key]');
        if (generalSettingsEditable && firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderGeneralSettingsRows([]);
        setGeneralSettingsMessage('General settings load failed: ' + err.message);
      }
    }

    function closeGeneralSettingsDialog() {
      const modal = document.getElementById('generalSettingsModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setGeneralSettingsMessage('');
      const menuItem = document.getElementById('configGeneralSettingsMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    async function saveGeneralSettingsDialog(event) {
      if (event) { event.preventDefault(); }
      const saveButton = document.getElementById('generalSettingsSaveButton');
      if (saveButton && saveButton.disabled) {
        setGeneralSettingsMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE);
        return;
      }
      setGeneralSettingsMessage('Saving general settings...');
      try {
        const response = await fetch('/api/config/general_settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ data: collectGeneralSettingsFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not save general settings.');
        }
        set('message', result.message || 'General settings saved.');
        closeGeneralSettingsDialog();
        refreshStatus();
      } catch (err) {
        setGeneralSettingsMessage('General settings save failed: ' + err.message);
      }
    }


    function setSatellitesEditorMessage(message) {
      const element = document.getElementById('satellitesEditorMessage');
      if (element) { element.textContent = message || ''; }
    }

    function renderSatellitesEditorChoices(choices, selectedIndex) {
      const selector = document.getElementById('satellitesEditorSelect');
      if (!selector) { return; }
      selector.innerHTML = '';
      const safeChoices = Array.isArray(choices) ? choices : [];
      for (const choice of safeChoices) {
        const option = document.createElement('option');
        option.value = String(choice?.index ?? 0);
        option.textContent = String(choice?.label ?? ('Satellite ' + (Number(choice?.index ?? 0) + 1)));
        if (Number(choice?.index ?? 0) === Number(selectedIndex ?? 0)) { option.selected = true; }
        selector.appendChild(option);
      }
    }

    function renderSatellitesEditorRows(rows) {
      const table = document.getElementById('satellitesEditorTable');
      if (!table) { return; }
      table.innerHTML = '';
      for (const heading of ['Key', 'Current', 'Original']) {
        const header = document.createElement('div');
        header.className = 'general-settings-cell general-settings-header';
        header.setAttribute('role', 'columnheader');
        header.textContent = heading;
        table.appendChild(header);
      }
      for (const row of (Array.isArray(rows) ? rows : [])) {
        const key = String(row?.key ?? '');
        const keyCell = document.createElement('div');
        keyCell.className = 'general-settings-cell general-settings-key';
        keyCell.textContent = key;
        table.appendChild(keyCell);

        const currentCell = document.createElement('div');
        currentCell.className = 'general-settings-cell general-settings-current';
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.satelliteKey = key;
        input.value = row?.current ?? '';
        input.setAttribute('aria-label', 'Current value for ' + key);
        currentCell.appendChild(input);
        table.appendChild(currentCell);

        const originalCell = document.createElement('div');
        originalCell.className = 'general-settings-cell general-settings-original';
        originalCell.textContent = row?.original ?? '';
        table.appendChild(originalCell);
      }
    }

    function collectSatellitesEditorFields() {
      const data = {};
      document.querySelectorAll('#satellitesEditorTable input[data-satellite-key]').forEach((input) => {
        data[input.dataset.satelliteKey] = input.value;
      });
      return data;
    }

    async function loadSatellitesEditorSelection(selectedIndex) {
      try {
        const response = await fetch('/api/config/satellites?index=' + encodeURIComponent(String(selectedIndex ?? 0)), { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) { throw new Error(result.message || 'Could not load satellites configuration.'); }
        renderSatellitesEditorChoices(result.choices || [], result.selected_index);
        renderSatellitesEditorRows(result.rows || []);
        const editable = applyConfigEditorAccess('#satellitesEditorTable', 'satellitesEditorSaveButton', result.editable);
        setSatellitesEditorMessage(configEditorMessage(result.message || 'Satellite definition loaded.', editable));
        const firstInput = document.querySelector('#satellitesEditorTable input[data-satellite-key]');
        if (editable && firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderSatellitesEditorRows([]);
        setSatellitesEditorMessage('Satellites configuration load failed: ' + err.message);
      }
    }

    async function openSatellitesEditorDialog() {
      const modal = document.getElementById('satellitesEditorModal');
      if (!modal) { return; }
      setSatellitesEditorMessage('Loading satellites configuration...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      await loadSatellitesEditorSelection(0);
    }

    function closeSatellitesEditorDialog() {
      const modal = document.getElementById('satellitesEditorModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setSatellitesEditorMessage('');
      const menuItem = document.getElementById('configSatellitesMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    async function saveSatellitesEditorDialog(event) {
      if (event) { event.preventDefault(); }
      const saveButton = document.getElementById('satellitesEditorSaveButton');
      if (saveButton && saveButton.disabled) { setSatellitesEditorMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE); return; }
      const selector = document.getElementById('satellitesEditorSelect');
      const selectedIndex = Number(selector?.value ?? 0);
      setSatellitesEditorMessage('Saving satellite definition...');
      try {
        const response = await fetch('/api/config/satellites', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ index: selectedIndex, data: collectSatellitesEditorFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) { throw new Error(result.message || 'Could not save satellite definition.'); }
        renderSatellitesEditorChoices(result.choices || [], result.selected_index);
        renderSatellitesEditorRows(result.rows || []);
        applyConfigEditorAccess('#satellitesEditorTable', 'satellitesEditorSaveButton', true);
        setSatellitesEditorMessage(result.message || 'Satellite definition saved.');
        set('message', result.message || 'Satellite definition saved.');
        refreshStatus();
      } catch (err) {
        setSatellitesEditorMessage('Satellite definition save failed: ' + err.message);
      }
    }

    function setAddSatelliteMessage(message) {
      const element = document.getElementById('addSatelliteMessage');
      if (element) { element.textContent = message || ''; }
    }

    function renderAddSatelliteFields(data) {
      const fieldsElement = document.getElementById('addSatelliteFields');
      if (!fieldsElement) { return; }
      fieldsElement.innerHTML = '';
      const fieldData = data && typeof data === 'object' ? data : {};
      for (const key of Object.keys(fieldData)) {
        const label = document.createElement('label');
        const safeId = 'addSatelliteField_' + key.replace(/[^A-Za-z0-9_-]/g, '_');
        label.setAttribute('for', safeId);
        label.textContent = key;
        const input = document.createElement('input');
        input.type = 'text';
        input.id = safeId;
        input.dataset.addSatelliteKey = key;
        input.value = fieldData[key] ?? '';
        fieldsElement.appendChild(label);
        fieldsElement.appendChild(input);
      }
    }

    function collectAddSatelliteFields() {
      const data = {};
      document.querySelectorAll('#addSatelliteFields input[data-add-satellite-key]').forEach((input) => {
        data[input.dataset.addSatelliteKey] = input.value;
      });
      return data;
    }

    async function openAddSatelliteDialog() {
      const modal = document.getElementById('addSatelliteModal');
      if (!modal) { return; }
      setAddSatelliteMessage('Loading new satellite template...');
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      try {
        const response = await fetch('/api/config/add_satellite', { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) { throw new Error(result.message || 'Could not load new satellite template.'); }
        renderAddSatelliteFields(result.data || {});
        const editable = applyConfigEditorAccess('#addSatelliteFields', 'addSatelliteAddButton', result.editable);
        setAddSatelliteMessage(configEditorMessage(result.message || 'New satellite template loaded.', editable));
        const firstInput = document.querySelector('#addSatelliteFields input[data-add-satellite-key]');
        if (editable && firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderAddSatelliteFields({});
        setAddSatelliteMessage('New satellite template load failed: ' + err.message);
      }
    }

    function closeAddSatelliteDialog() {
      const modal = document.getElementById('addSatelliteModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setAddSatelliteMessage('');
      const menuItem = document.getElementById('configAddSatelliteMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    async function addSatelliteDefinition(event) {
      if (event) { event.preventDefault(); }
      const addButton = document.getElementById('addSatelliteAddButton');
      if (addButton && addButton.disabled) { setAddSatelliteMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE); return; }
      setAddSatelliteMessage('Validating and adding satellite definition...');
      try {
        const response = await fetch('/api/config/add_satellite', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ data: collectAddSatelliteFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) { throw new Error(result.message || 'Could not add satellite definition.'); }
        set('message', result.message || 'Satellite definition added.');
        closeAddSatelliteDialog();
        refreshStatus();
      } catch (err) {
        setAddSatelliteMessage('Add satellite failed: ' + err.message);
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
        const radioEditable = applyConfigEditorAccess('#radioFields', 'radioOkButton', result.editable);
        setRadioMessage(configEditorMessage(result.message || 'Radio configuration loaded.', radioEditable));
        const firstInput = document.querySelector('#radioFields input[data-radio-key]');
        if (radioEditable && firstInput) { firstInput.focus(); firstInput.select(); }
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
      const saveButton = document.getElementById('radioOkButton');
      if (saveButton && saveButton.disabled) {
        setRadioMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE);
        return;
      }
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
        const presetEditable = applyConfigEditorAccess('#presetFields', 'presetOkButton', result.editable);
        setPresetMessage(configEditorMessage(result.message || 'Idle-task preset template loaded. Preset will be saved as <nickname>_preset.yaml.', presetEditable));
        const nicknameInput = document.querySelector('#presetFields input[data-preset-key="nickname"]');
        if (presetEditable && nicknameInput) { nicknameInput.focus(); nicknameInput.select(); }
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
      const saveButton = document.getElementById('presetOkButton');
      if (saveButton && saveButton.disabled) {
        setPresetMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE);
        return;
      }
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

    function setEditPresetMessage(message) {
      const messageElement = document.getElementById('editPresetMessage');
      if (messageElement) { messageElement.textContent = message || ''; }
    }

    function renderEditPresetFields(data) {
      const fieldsElement = document.getElementById('editPresetFields');
      if (!fieldsElement) { return; }
      fieldsElement.innerHTML = '';
      const fieldData = data && typeof data === 'object' ? data : {};
      const keys = Object.keys(fieldData);
      if (!keys.length) {
        const emptyMessage = document.createElement('div');
        emptyMessage.className = 'field-help';
        emptyMessage.textContent = 'No editable keys are present in this preset.';
        fieldsElement.appendChild(emptyMessage);
        return;
      }
      for (const key of keys) {
        const label = document.createElement('label');
        const safeId = 'editPresetField_' + key.replace(/[^A-Za-z0-9_-]/g, '_');
        label.setAttribute('for', safeId);
        label.textContent = key;

        const input = document.createElement('input');
        input.type = 'text';
        input.id = safeId;
        input.dataset.editPresetKey = key;
        input.value = fieldData[key] ?? '';

        fieldsElement.appendChild(label);
        fieldsElement.appendChild(input);
      }
    }

    function populateEditPresetSelect(files, selectedFilename) {
      const select = document.getElementById('editPresetSelect');
      if (!select) { return; }
      select.innerHTML = '';
      const filenames = Array.isArray(files) ? files : [];
      if (!filenames.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No preset YAML files found';
        select.appendChild(option);
        select.disabled = true;
        return;
      }
      select.disabled = false;
      for (const filename of filenames) {
        const option = document.createElement('option');
        option.value = String(filename);
        option.textContent = String(filename);
        select.appendChild(option);
      }
      select.value = filenames.includes(selectedFilename) ? selectedFilename : filenames[0];
    }

    function collectEditPresetFields() {
      const data = {};
      document.querySelectorAll('#editPresetFields input[data-edit-preset-key]').forEach((input) => {
        data[input.dataset.editPresetKey] = input.value;
      });
      return data;
    }

    async function loadEditPreset(filename = '') {
      const query = filename ? `?name=${encodeURIComponent(filename)}` : '';
      setEditPresetMessage('Loading preset...');
      try {
        const response = await fetch('/api/config/edit_preset' + query, { cache: 'no-store' });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not load preset.');
        }
        populateEditPresetSelect(result.files || [], result.filename || '');
        renderEditPresetFields(result.data || {});
        const sessionEditable = result.editable !== false;
        const presetEditable = applyConfigEditorAccess(
          '#editPresetFields',
          'editPresetSaveButton',
          sessionEditable && Boolean(result.filename)
        );
        const loadedMessage = result.message || 'Preset loaded.';
        setEditPresetMessage(
          result.filename ? configEditorMessage(loadedMessage, sessionEditable) : loadedMessage
        );
        const firstInput = document.querySelector('#editPresetFields input[data-edit-preset-key]');
        if (presetEditable && firstInput) { firstInput.focus(); firstInput.select(); }
      } catch (err) {
        renderEditPresetFields({});
        const saveButton = document.getElementById('editPresetSaveButton');
        if (saveButton) { saveButton.disabled = true; }
        setEditPresetMessage('Preset load failed: ' + err.message);
      }
    }

    async function openEditPresetDialog() {
      const modal = document.getElementById('editPresetModal');
      if (!modal) { return; }
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      await loadEditPreset();
    }

    function closeEditPresetDialog() {
      const modal = document.getElementById('editPresetModal');
      if (!modal) { return; }
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      setEditPresetMessage('');
      const menuItem = document.getElementById('configEditPresetMenuItem');
      if (menuItem) { menuItem.focus(); }
    }

    async function saveEditPresetDialog(event) {
      if (event) { event.preventDefault(); }
      const saveButton = document.getElementById('editPresetSaveButton');
      if (saveButton && saveButton.disabled) {
        setEditPresetMessage(REMOTE_CONFIG_READ_ONLY_MESSAGE);
        return;
      }
      const select = document.getElementById('editPresetSelect');
      const filename = select ? select.value : '';
      setEditPresetMessage('Saving preset...');
      try {
        const response = await fetch('/api/config/edit_preset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          body: JSON.stringify({ filename, data: collectEditPresetFields() })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.message || 'Could not save preset.');
        }
        set('message', result.message || 'Preset saved.');
        closeEditPresetDialog();
        refreshStatus();
      } catch (err) {
        setEditPresetMessage('Preset save failed: ' + err.message);
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
    const sstvFilesMenuItem = document.getElementById('sstvFilesMenuItem');
    if (sstvFilesMenuItem) {
      sstvFilesMenuItem.addEventListener('click', openSstvFilesWindow);
    }
    const recordingsMenuItem = document.getElementById('recordingsMenuItem');
    if (recordingsMenuItem) {
      recordingsMenuItem.addEventListener('click', openRecordingsWindow);
    }
    const recordAudioMenuItem = document.getElementById('recordAudioMenuItem');
    if (recordAudioMenuItem) {
      recordAudioMenuItem.addEventListener('click', async () => {
        if (audioRecordingCommandInFlight) { return; }
        const action = audioRecordingActive ? 'stop_audio_recording' : 'start_audio_recording';
        const label = audioRecordingActive ? 'Stop Recording' : 'Record Audio';
        audioRecordingCommandInFlight = true;
        recordAudioMenuItem.disabled = true;
        try {
          await runMenuCommand(action, label, false);
        } finally {
          audioRecordingCommandInFlight = false;
          await refreshStatus();
        }
      });
    }
    const logsMenuItem = document.getElementById('logsMenuItem');
    if (logsMenuItem) {
      logsMenuItem.addEventListener('click', openLogsWindow);
    }
    const fdtConsoleMenuItem = document.getElementById('fdtConsoleMenuItem');
    if (fdtConsoleMenuItem) {
      fdtConsoleMenuItem.addEventListener('click', openFdtConsole);
    }
    const fdtConsoleForm = document.getElementById('fdtConsoleForm');
    if (fdtConsoleForm) {
      fdtConsoleForm.addEventListener('submit', submitFdtConsole);
    }
    const fdtDisableButton = document.getElementById('fdtDisableButton');
    if (fdtDisableButton) {
      fdtDisableButton.addEventListener('click', disableFdtFromConsole);
    }
    const fdtIntervalSlider = document.getElementById('fdtRecalculationInterval');
    if (fdtIntervalSlider) {
      fdtIntervalSlider.addEventListener('input', () => {
        fdtSliderIsBeingEdited = true;
        setFdtIntervalSliderValue(fdtIntervalSlider.value, true);
      });
      fdtIntervalSlider.addEventListener('change', updateFdtRecalculationInterval);
    }
    const fdtCadenceAutoButton = document.getElementById('fdtCadenceAutoButton');
    if (fdtCadenceAutoButton) {
      fdtCadenceAutoButton.addEventListener('click', resetFdtRecalculationIntervalToYaml);
    }
    const fdtDownlinkDial = document.getElementById('fdtDownlinkDial');
    if (fdtDownlinkDial) {
      fdtDownlinkDial.addEventListener('click', () => {
        fdtDownlinkDial.focus({ preventScroll: true });
      });
      fdtDownlinkDial.addEventListener('wheel', handleFdtDialWheel, { passive: false });
      fdtDownlinkDial.addEventListener('keydown', handleFdtDialKey);
    }
    const fdtConsoleClose = document.getElementById('fdtConsoleClose');
    if (fdtConsoleClose) {
      fdtConsoleClose.addEventListener('click', closeFdtConsole);
    }
    const fdtConsoleCancelButton = document.getElementById('fdtConsoleCancelButton');
    if (fdtConsoleCancelButton) {
      fdtConsoleCancelButton.addEventListener('click', closeFdtConsole);
    }
    const fdtConsoleModal = document.getElementById('fdtConsoleModal');
    if (fdtConsoleModal) {
      fdtConsoleModal.addEventListener('click', (event) => {
        if (event.target === fdtConsoleModal) { closeFdtConsole(); }
      });
    }
    setPresetsVisible(false);

    if (detachedFdtMode) {
      document.body.classList.add('detached-fdt-mode');
      document.title = 'BQE WISP FDT Console';
      openFdtConsole();
    } else if (detachedMapMode) {
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
      window.addEventListener('beforeunload', () => {
        closeDetachedMapWindow();
        closeDetachedFdtWindow();
      });
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
    const customSchedulePassesMenuItem = document.getElementById('customSchedulePassesMenuItem');
    if (customSchedulePassesMenuItem) {
      customSchedulePassesMenuItem.addEventListener('click', openCustomScheduleDialog);
    }

    const antennaTrackingMenuItem = document.getElementById('antennaTrackingMenuItem');
    if (antennaTrackingMenuItem) {
      antennaTrackingMenuItem.addEventListener('click', toggleAntennaTracking);
    }
    const enableFdtMenuItem = document.getElementById('enableFdtMenuItem');
    if (enableFdtMenuItem) {
      enableFdtMenuItem.addEventListener('click', enableFdtFromMainMenu);
    }

    const runTestPassMenuItem = document.getElementById('runTestPassMenuItem');
    if (runTestPassMenuItem) {
      runTestPassMenuItem.addEventListener('click', openTestPassDialog);
    }
    const endCurrentPassMenuItem = document.getElementById('endCurrentPassMenuItem');
    if (endCurrentPassMenuItem) {
      endCurrentPassMenuItem.addEventListener('click', endCurrentPass);
    }
    const testPassForm = document.getElementById('testPassForm');
    if (testPassForm) {
      testPassForm.addEventListener('submit', submitTestPass);
    }
    const testPassClose = document.getElementById('testPassClose');
    if (testPassClose) {
      testPassClose.addEventListener('click', closeTestPassDialog);
    }
    const testPassCancelButton = document.getElementById('testPassCancelButton');
    if (testPassCancelButton) {
      testPassCancelButton.addEventListener('click', closeTestPassDialog);
    }
    const testPassModal = document.getElementById('testPassModal');
    if (testPassModal) {
      testPassModal.addEventListener('click', (event) => {
        if (event.target === testPassModal) { closeTestPassDialog(); }
      });
    }

    const customScheduleForm = document.getElementById('customScheduleForm');
    if (customScheduleForm) {
      customScheduleForm.addEventListener('submit', submitCustomSchedule);
    }
    const customScheduleSatelliteList = document.getElementById('customScheduleSatelliteList');
    if (customScheduleSatelliteList) {
      customScheduleSatelliteList.addEventListener('change', customScheduleSelectionChanged);
    }
    const customScheduleClose = document.getElementById('customScheduleClose');
    if (customScheduleClose) {
      customScheduleClose.addEventListener('click', closeCustomScheduleDialog);
    }
    const customScheduleCancelButton = document.getElementById('customScheduleCancelButton');
    if (customScheduleCancelButton) {
      customScheduleCancelButton.addEventListener('click', closeCustomScheduleDialog);
    }
    const customScheduleModal = document.getElementById('customScheduleModal');
    if (customScheduleModal) {
      customScheduleModal.addEventListener('click', (event) => {
        if (event.target === customScheduleModal) { closeCustomScheduleDialog(); }
      });
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


    const configGeneralSettingsMenuItem = document.getElementById('configGeneralSettingsMenuItem');
    if (configGeneralSettingsMenuItem) {
      configGeneralSettingsMenuItem.addEventListener('click', openGeneralSettingsDialog);
    }
    const generalSettingsForm = document.getElementById('generalSettingsForm');
    if (generalSettingsForm) {
      generalSettingsForm.addEventListener('submit', saveGeneralSettingsDialog);
    }
    const generalSettingsClose = document.getElementById('generalSettingsClose');
    if (generalSettingsClose) {
      generalSettingsClose.addEventListener('click', closeGeneralSettingsDialog);
    }
    const generalSettingsCancelButton = document.getElementById('generalSettingsCancelButton');
    if (generalSettingsCancelButton) {
      generalSettingsCancelButton.addEventListener('click', closeGeneralSettingsDialog);
    }
    const generalSettingsModal = document.getElementById('generalSettingsModal');
    if (generalSettingsModal) {
      generalSettingsModal.addEventListener('click', (event) => {
        if (event.target === generalSettingsModal) { closeGeneralSettingsDialog(); }
      });
    }


    const configSatellitesMenuItem = document.getElementById('configSatellitesMenuItem');
    if (configSatellitesMenuItem) {
      configSatellitesMenuItem.addEventListener('click', openSatellitesEditorDialog);
    }
    const satellitesEditorForm = document.getElementById('satellitesEditorForm');
    if (satellitesEditorForm) {
      satellitesEditorForm.addEventListener('submit', saveSatellitesEditorDialog);
    }
    const satellitesEditorSelect = document.getElementById('satellitesEditorSelect');
    if (satellitesEditorSelect) {
      satellitesEditorSelect.addEventListener('change', () => loadSatellitesEditorSelection(Number(satellitesEditorSelect.value || 0)));
    }
    const satellitesEditorClose = document.getElementById('satellitesEditorClose');
    if (satellitesEditorClose) {
      satellitesEditorClose.addEventListener('click', closeSatellitesEditorDialog);
    }
    const satellitesEditorCancelButton = document.getElementById('satellitesEditorCancelButton');
    if (satellitesEditorCancelButton) {
      satellitesEditorCancelButton.addEventListener('click', closeSatellitesEditorDialog);
    }
    const satellitesEditorModal = document.getElementById('satellitesEditorModal');
    if (satellitesEditorModal) {
      satellitesEditorModal.addEventListener('click', (event) => {
        if (event.target === satellitesEditorModal) { closeSatellitesEditorDialog(); }
      });
    }

    const configAddSatelliteMenuItem = document.getElementById('configAddSatelliteMenuItem');
    if (configAddSatelliteMenuItem) {
      configAddSatelliteMenuItem.addEventListener('click', openAddSatelliteDialog);
    }
    const addSatelliteForm = document.getElementById('addSatelliteForm');
    if (addSatelliteForm) {
      addSatelliteForm.addEventListener('submit', addSatelliteDefinition);
    }
    const addSatelliteClose = document.getElementById('addSatelliteClose');
    if (addSatelliteClose) {
      addSatelliteClose.addEventListener('click', closeAddSatelliteDialog);
    }
    const addSatelliteCancelButton = document.getElementById('addSatelliteCancelButton');
    if (addSatelliteCancelButton) {
      addSatelliteCancelButton.addEventListener('click', closeAddSatelliteDialog);
    }
    const addSatelliteModal = document.getElementById('addSatelliteModal');
    if (addSatelliteModal) {
      addSatelliteModal.addEventListener('click', (event) => {
        if (event.target === addSatelliteModal) { closeAddSatelliteDialog(); }
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

    const configEditPresetMenuItem = document.getElementById('configEditPresetMenuItem');
    if (configEditPresetMenuItem) {
      configEditPresetMenuItem.addEventListener('click', openEditPresetDialog);
    }
    const editPresetForm = document.getElementById('editPresetForm');
    if (editPresetForm) {
      editPresetForm.addEventListener('submit', saveEditPresetDialog);
    }
    const editPresetSelect = document.getElementById('editPresetSelect');
    if (editPresetSelect) {
      editPresetSelect.addEventListener('change', () => loadEditPreset(editPresetSelect.value));
    }
    const editPresetClose = document.getElementById('editPresetClose');
    if (editPresetClose) {
      editPresetClose.addEventListener('click', closeEditPresetDialog);
    }
    const editPresetCancelButton = document.getElementById('editPresetCancelButton');
    if (editPresetCancelButton) {
      editPresetCancelButton.addEventListener('click', closeEditPresetDialog);
    }
    const editPresetModal = document.getElementById('editPresetModal');
    if (editPresetModal) {
      editPresetModal.addEventListener('click', (event) => {
        if (event.target === editPresetModal) { closeEditPresetDialog(); }
      });
    }

    const diagnosticsDialog = document.getElementById('environmentDiagnosticsDialog');
    const diagnosticsSave = document.getElementById('environmentDiagnosticsSave');
    let diagnosticsReport = '';
    document.getElementById('environmentDiagnosticsMenuItem').addEventListener('click', async () => {
      const results = document.getElementById('environmentDiagnosticsResults');
      const summary = document.getElementById('environmentDiagnosticsSummary');
      diagnosticsReport = '';
      diagnosticsSave.disabled = true;
      results.textContent = '';
      summary.textContent = 'Checking the server environment…';
      diagnosticsDialog.showModal();
      try {
        const response = await fetch('/api/command', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'environment_diagnostics' })
        });
        const result = await response.json();
        if (!response.ok || !result.ok) { throw new Error(result.message || 'Diagnostics request failed.'); }
        diagnosticsReport = result.report;
        summary.textContent = result.warnings
          ? `Completed • ${result.warnings} warning(s) to review`
          : 'Completed • No warnings found';
        diagnosticsReport.split('\n').forEach(line => {
          const row = document.createElement('div');
          row.textContent = line;
          row.style.color = line.includes('[WARNING]') ? '#ffd479' : '#bdeddf';
          results.appendChild(row);
        });
        diagnosticsSave.disabled = false;
      } catch (err) {
        summary.textContent = 'Unable to complete Environment Diagnostics';
        results.textContent = String(err);
      }
    });
    document.getElementById('environmentDiagnosticsClose').addEventListener('click', () => diagnosticsDialog.close());
    diagnosticsSave.addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([diagnosticsReport], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `bqe_environment_diagnostics_${new Date().toISOString().replace(/[:.]/g, '-')}.txt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
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
        closeGeneralSettingsDialog();
        closeSatellitesEditorDialog();
        closeAddSatelliteDialog();
        closePresetDialog();
        closeEditPresetDialog();
        closeTestPassDialog();
        closeCustomScheduleDialog();
        closeFdtConsole();
      }
    });

    const refreshIntervalMs = __BQE_UI_REFRESH_INTERVAL_MS__;
    refreshStatus();
    setInterval(refreshStatus, refreshIntervalMs);
  </script>

  <script src="/audio-stream/player.js"></script>
</body>
</html>
"""


def build_index_html(settings: Optional[WebConsoleSettings] = None) -> str:
    """Build the web-console page using the configured browser refresh interval."""
    settings = settings or load_web_console_settings()
    satellites_json = json.dumps(
        load_test_pass_satellites_for_ui(),
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return (
        INDEX_HTML_TEMPLATE
        .replace("__BQE_UI_REFRESH_INTERVAL_MS__", str(settings.ui_refresh_interval_ms))
        .replace("__BQE_TEST_PASS_SATELLITES_JSON__", satellites_json)
        .replace("__BQE_UI_THEME__", settings.ui_theme)
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
        sstv_gallery_location: str = "",
        recordings_location: str = "",
        logs_location: Union[str, Path, None] = None,
        ui_theme: str = DEFAULT_UI_THEME,
        **kwargs: Any,
    ) -> None:
        self._status_payload_func = status_payload_func
        self._command_payload_func = command_payload_func
        self._index_html = index_html if index_html is not None else INDEX_HTML
        self._sstv_gallery_location = str(sstv_gallery_location or "")
        self._recordings_location = str(recordings_location or "")
        self._logs_location = str(logs_location or LOGS_PATH)
        self._ui_theme = _parse_ui_theme(ui_theme)
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep browser refreshes from cluttering the scheduler console.
        return

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file_with_range_support(self, file_path: Path, content_type: str) -> None:
        """Stream a file and honor a single HTTP byte range for browser seeking."""
        file_size = file_path.stat().st_size
        range_header = str(self.headers.get("Range") or "").strip()
        start = 0
        end = max(0, file_size - 1)
        status = 200

        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or file_size <= 0:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            start_text, end_text = match.groups()
            if not start_text and not end_text:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else file_size - 1
            else:
                suffix_length = int(end_text)
                if suffix_length <= 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = max(0, file_size - suffix_length)
                end = file_size - 1

            if start >= file_size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, file_size - 1)
            status = 206

        content_length = max(0, end - start + 1) if file_size else 0
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(content_length))
        self.end_headers()

        try:
            with file_path.open("rb") as source:
                source.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Normal when the browser stops or seeks within a recording.
            return

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

    def _is_local_config_request(self) -> bool:
        """Return True only for a browser connected through this machine's loopback interface.

        Do not trust Host, Origin, Referer, X-Forwarded-For, or similar request
        headers for this decision.  client_address is the peer address accepted by
        the HTTP server itself.
        """
        try:
            client_ip = str(self.client_address[0] or "").strip().split("%", 1)[0]
        except (AttributeError, IndexError, TypeError):
            return False

        # localhost reaches the server as a loopback IP address, not as the
        # literal host name.  Permit IPv4 localhost, IPv6 localhost, and the
        # IPv4-mapped IPv6 representation of 127.0.0.1.
        return client_ip in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}

    def _config_result_with_access(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Add read/write access information to a configuration-editor response."""
        result = dict(payload)
        editable = self._is_local_config_request()
        result["editable"] = editable
        if not editable:
            read_only_message = (
                "Read only - configuration changes are only allowed from a browser "
                "running on the BQE WISP server computer using localhost or 127.0.0.1."
            )
            existing_message = str(result.get("message") or "").strip()
            result["message"] = (
                f"{existing_message} {read_only_message}" if existing_message else read_only_message
            )
        return result

    def _reject_remote_config_edit(self, path: str) -> None:
        """Return HTTP 403 for a configuration write attempted from another machine."""
        try:
            client_ip = str(self.client_address[0] or "unknown")
        except (AttributeError, IndexError, TypeError):
            client_ip = "unknown"
        print(f"[WARNING] Rejected remote configuration edit from {client_ip}: {path}")
        body = json.dumps({
            "ok": False,
            "editable": False,
            "message": (
                "Configuration changes are only allowed from a browser running on "
                "the BQE WISP server computer using localhost or 127.0.0.1."
            ),
        }).encode("utf-8")
        self.send_bytes(403, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/audio-stream/pcm":
            AUDIO_STREAM.serve(self)
            return
        if path == "/audio-stream/player.js":
            self.send_bytes(200, "application/javascript; charset=utf-8",
                            (SCRIPT_DIR / "plugins" / "bqe_audio_stream" / "player.js").read_bytes())
            return
        if path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", self._index_html.encode("utf-8"))
            return

        if path == "/sstv-files":
            gallery_html = build_sstv_gallery_html(
                self._sstv_gallery_location,
                self._ui_theme,
            )
            self.send_bytes(200, "text/html; charset=utf-8", gallery_html.encode("utf-8"))
            return

        if path == "/sstv-image":
            requested_name = parse_qs(parsed_url.query).get("name", [""])[0]
            image_path = resolve_sstv_image_file(self._sstv_gallery_location, requested_name)
            if image_path is None:
                self.send_bytes(404, "text/plain; charset=utf-8", b"SSTV image not found")
                return
            try:
                self.send_bytes(200, "image/jpeg", image_path.read_bytes())
            except OSError as exc:
                message = f"Could not read SSTV image: {exc}".encode("utf-8", errors="replace")
                self.send_bytes(500, "text/plain; charset=utf-8", message)
            return

        if path == "/recordings":
            recordings_html = build_recordings_html(
                self._recordings_location,
                self._ui_theme,
            )
            self.send_bytes(200, "text/html; charset=utf-8", recordings_html.encode("utf-8"))
            return

        if path == "/recording-file":
            requested_name = parse_qs(parsed_url.query).get("name", [""])[0]
            recording_path = resolve_recording_file(
                self._recordings_location,
                requested_name,
            )
            if recording_path is None:
                self.send_bytes(404, "text/plain; charset=utf-8", b"Recording not found")
                return
            try:
                self.send_file_with_range_support(recording_path, "audio/mpeg")
            except OSError as exc:
                message = f"Could not read recording: {exc}".encode("utf-8", errors="replace")
                self.send_bytes(500, "text/plain; charset=utf-8", message)
            return

        if path == "/logs":
            logs_html = build_logs_html(self._logs_location, self._ui_theme)
            self.send_bytes(200, "text/html; charset=utf-8", logs_html.encode("utf-8"))
            return

        if path == "/log-file":
            requested_name = parse_qs(parsed_url.query).get("name", [""])[0]
            log_path = resolve_log_file(self._logs_location, requested_name)
            if log_path is None:
                self.send_bytes(404, "text/plain; charset=utf-8", b"Log file not found")
                return
            try:
                log_text = log_path.read_bytes().decode("utf-8-sig", errors="replace")
                self.send_bytes(
                    200,
                    "text/plain; charset=utf-8",
                    log_text.encode("utf-8"),
                )
            except OSError as exc:
                message = f"Could not read log file: {exc}".encode("utf-8", errors="replace")
                self.send_bytes(500, "text/plain; charset=utf-8", message)
            return

        if path == "/api/status":
            body = json.dumps(self._status_payload_func(), default=str).encode("utf-8")
            self.send_bytes(200, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/qth":
            result = self._config_result_with_access(read_qth_config_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/general_settings":
            result = self._config_result_with_access(read_general_settings_editor_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/satellites":
            selected_index = parse_qs(parsed_url.query).get("index", ["0"])[0]
            result = self._config_result_with_access(read_satellites_editor_payload(selected_index))
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/add_satellite":
            result = self._config_result_with_access(read_add_satellite_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/radio":
            result = self._config_result_with_access(read_radio_config_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/create_preset":
            result = self._config_result_with_access(read_create_preset_payload())
            status = 200 if result.get("ok", False) else 500
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/edit_preset":
            selected_filename = parse_qs(parsed_url.query).get("name", [""])[0]
            result = self._config_result_with_access(read_edit_preset_payload(selected_filename))
            status = 200 if result.get("ok", False) else 400
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

        # Configuration files may be viewed remotely, but they may only be
        # changed by a browser connected through localhost/127.0.0.1.  Applying
        # this to the whole /api/config/ namespace also protects future editors.
        if path.startswith("/api/config/") and not self._is_local_config_request():
            self._reject_remote_config_edit(path)
            return

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

        if path == "/api/config/general_settings":
            result = dict(write_general_settings_editor_payload(request_payload))
            status = 200 if result.get("ok", False) else 400
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/satellites":
            result = dict(write_satellites_editor_payload(request_payload))
            status = 200 if result.get("ok", False) else 400
            body = json.dumps(result, default=str).encode("utf-8")
            self.send_bytes(status, "application/json; charset=utf-8", body)
            return

        if path == "/api/config/add_satellite":
            result = dict(add_new_satellite_payload(request_payload))
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

        if path == "/api/config/edit_preset":
            result = dict(write_edit_preset_payload(request_payload))
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
