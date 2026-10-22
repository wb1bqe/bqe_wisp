"""Local configuration backups and validated ZIP restores."""
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import threading
import zipfile

LOCK = threading.Lock()
MAX_BYTES = 512 * 1024 * 1024
APRS_PRIVATE = "plugins/bqe_aprs_decoder/aprs-is.private.json"


def browse(location=""):
    directory = Path(location).expanduser() if location else Path.home()
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Choose a folder.")
    entries = []
    for item in directory.iterdir():
        try:
            if item.is_dir() or item.suffix.lower() == ".zip" and item.is_file():
                entries.append(dict(name=item.name, path=str(item), directory=item.is_dir()))
        except OSError:
            continue
    drives = [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
              if Path(f"{letter}:\\").exists()] if os.name == "nt" else ["/"]
    return dict(path=str(directory), parent=str(directory.parent), drives=drives,
                entries=sorted(entries, key=lambda item: (not item["directory"], item["name"].lower())))


def _plain_path(path):
    """Reject links/junctions so a restore cannot escape through existing folders."""
    for item in (path, *path.parents):
        if (item.is_symlink() or
                item.exists() and getattr(item.lstat(), "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError(f"Linked folders/files are not supported: {item}")


def backup(config, destination):
    config, destination = Path(config).absolute(), Path(destination).expanduser().resolve(strict=True)
    _plain_path(config)
    if not config.is_dir() or not destination.is_dir():
        raise ValueError("The configuration and destination folders must exist.")
    if destination == config.resolve() or config.resolve() in destination.parents:
        raise ValueError("Choose a backup destination outside bqe_config.")
    with LOCK:
        name = "bqe_config_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f") + ".zip"
        output = destination / name
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(config, "bqe_config/")
                for item in sorted(config.rglob("*")):
                    _plain_path(item)
                    archive.write(item, "bqe_config/" + item.relative_to(config).as_posix())
                credential = config.parent / APRS_PRIVATE
                _plain_path(credential)
                if credential.exists():
                    archive.write(credential, APRS_PRIVATE)
            # Exclusive creation avoids ever replacing an existing backup.
            with output.open("xb") as stream, temporary.open("rb") as source:
                try:
                    shutil.copyfileobj(source, stream)
                except Exception:
                    stream.close()
                    output.unlink()
                    raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return dict(message=f"Backup saved to {output}", path=str(output))


def restore(config, archive_path, *, data_roots=None):
    config = Path(config).absolute()
    _plain_path(config)
    def target_for(relative):
        if data_roots is None:
            return config.parent / relative
        name = relative.as_posix()
        for prefix, folder in data_roots.items():
            if name == prefix or name.startswith(prefix + "/"):
                return folder / Path(*relative.parts[len(PurePosixPath(prefix).parts):])
        raise ValueError("ZIP contains files outside the downloaded-data folders (LRPT is excluded).")

    with LOCK, zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as work:
        members = archive.infolist()
        max_entries, max_bytes = (10000, MAX_BYTES) if data_roots is None else (250000, 64 * 1024**3)
        if not members or len(members) > max_entries or sum(m.file_size for m in members) > max_bytes:
            raise ValueError(f"Backup is empty or exceeds the restore limit ({max_entries:,} entries / {max_bytes // 1024**2:,} MB).")
        plan, seen = [], set()
        stage, rollback = Path(work) / "new", Path(work) / "old"
        for member in members:
            name = member.orig_filename
            parts = name.rstrip("/").split("/")
            if ("\\" in name or "\x00" in name or
                    (data_roots is None and parts[0] != "bqe_config" and name != APRS_PRIVATE) or
                    any(p in ("", ".", "..") or ":" in p or p.endswith((" ", ".")) or
                        any(c in p for c in '<>"|?*') or
                        p.split(".")[0].upper() in
                        {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                         *[f"LPT{i}" for i in range(1, 10)]}
                        for p in parts) or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("ZIP contains an unsafe or unsupported path.")
            if data_roots is None and len(parts) == 1:
                if not member.is_dir():
                    raise ValueError("Backup must contain a bqe_config folder.")
                continue
            if name == APRS_PRIVATE and member.is_dir():
                raise ValueError("APRS credential must be a file.")
            relative = Path(*PurePosixPath(*parts).parts)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError("ZIP contains duplicate paths.")
            seen.add(key)
            target = target_for(relative)
            if data_roots is not None and relative.as_posix() in data_roots and not member.is_dir():
                raise ValueError("A downloaded-data root must be a folder.")
            _plain_path(target)
            if target.exists() and target.is_dir() != member.is_dir():
                raise ValueError(f"File/folder conflict: {relative}")
            staged = stage / relative
            if member.is_dir():
                staged.mkdir(parents=True, exist_ok=True)
            else:
                staged.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, staged.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
            plan.append((relative, member.is_dir()))
        # All entries and CRCs are checked before touching live settings.
        written, created = [], []
        def make_directory(directory):
            missing = []
            while not directory.exists():
                missing.append(directory)
                directory = directory.parent
            for directory in reversed(missing):
                directory.mkdir()
                created.append(directory)
        try:
            if data_roots is None:
                make_directory(config)
            for relative, is_directory in plan:
                target = target_for(relative)
                if is_directory:
                    make_directory(target)
                    continue
                make_directory(target.parent)
                previous = rollback / relative
                existed = target.exists()
                if existed:
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, previous)
                written.append((target, previous if existed else None))
                shutil.copyfile(stage / relative, target)
                if relative.as_posix() == APRS_PRIVATE:
                    target.chmod(0o600)
        except Exception:
            for target, previous in reversed(written):
                if previous:
                    shutil.copy2(previous, target)
                else:
                    target.unlink(missing_ok=True)
            for directory in reversed(created):
                directory.rmdir()
            raise
    count = sum(not directory for _, directory in plan)
    message = ("Restart BQE WISP to apply all restored settings." if data_roots is None
               else "Refresh the galleries, recordings and APRS/telemetry views to see the restored data.")
    return dict(message=f"Restored {count} files. {message}",
                files=count)


def serve(handler, config, page=False, downloaded=False):
    host = handler.headers.get("Host", "")
    origin = handler.headers.get("Origin")
    port = handler.server.server_port
    if (not handler._is_local_config_request() or
            host not in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}") or
            origin and origin != "http://" + host):
        handler.send_bytes(403, "application/json",
                           b'{"message":"Admin settings are available only on localhost."}')
        return
    if page:
        handler.send_bytes(200, "text/html; charset=utf-8",
                           Path(__file__).with_name("settings_backup.html").read_bytes())
        return
    try:
        if (handler.headers.get("Content-Type", "").split(";")[0] != "application/json" or
                not 0 < int(handler.headers.get("Content-Length", "0")) <= 65536):
            raise ValueError("A small JSON request is required.")
        payload = handler.read_json_payload()
        if not isinstance(payload, dict) or not isinstance(payload.get("path", ""), str):
            raise ValueError("Invalid request.")
        action = payload.get("action")
        location = payload.get("path", "")
        if action == "browse":
            result = browse(location)
        elif action == "backup" and location:
            if downloaded:
                from plugins.downloaded_data_backup import backup_data
                result = backup_data(Path(config).parent, location, handler._sstv_gallery_location,
                                     handler._recordings_location)
            else:
                result = backup(config, location)
        elif action == "restore" and location:
            if downloaded:
                from plugins.downloaded_data_backup import restore_data
                result = restore_data(Path(config).parent, location, handler._sstv_gallery_location,
                                      handler._recordings_location)
            else:
                result = restore(config, location)
        else:
            raise ValueError("Choose a folder or backup ZIP.")
        handler.send_bytes(200, "application/json", json.dumps(result).encode())
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        handler.send_bytes(400, "application/json", json.dumps(dict(message=str(exc))).encode())
