"""Saved reception data: images, decoded packets, recordings and APRS receipts."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from plugins.settings_backup import LOCK, _plain_path, restore


def data_roots(root, gallery="", recordings=""):
    root = Path(root).absolute()
    roots = {"sstv_gallery": root / "sstv_gallery", "decoded": root / "decoded"}
    plugins = root / "plugins"
    for relative in ("plugins/bqe_sound_recorder/recordings",
                     "plugins/bqe_sdr/recordings",
                     "plugins/bqe_aprs_decoder/upload_receipts"):
        roots[relative] = root / relative
    if plugins.is_dir():
        for plugin in sorted(plugins.iterdir()):
            if plugin.is_dir() and plugin.name.casefold() != "bqe_lrpt_decoder":
                roots[f"plugins/{plugin.name}/decoded"] = plugin / "decoded"
    if gallery:
        configured = Path(gallery).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        configured = configured.absolute()
        if configured.resolve() != roots["sstv_gallery"].resolve():
            roots["configured_sstv_gallery"] = configured
    if recordings:
        configured = Path(recordings).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        configured = configured.absolute()
        if configured.resolve() != roots["plugins/bqe_sound_recorder/recordings"].resolve():
            roots["configured_sound_recordings"] = configured
    weather = (plugins / "bqe_lrpt_decoder").resolve()
    for path in roots.values():
        _plain_path(path)
        resolved = path.resolve()
        # An overly broad configured gallery must not sweep in program files/weather.
        if (resolved == root.resolve() or resolved in root.resolve().parents or
                resolved == plugins.resolve() or resolved == weather or
                weather in resolved.parents or resolved in weather.parents):
            raise ValueError("The saved-data folder overlaps program or LRPT weather folders.")
        if resolved == (root / 'bqe_config').resolve() or (root / 'bqe_config').resolve() in resolved.parents:
            raise ValueError("The saved-data folder overlaps configuration files.")
        if plugins.is_dir() and any(resolved == plugin.resolve() for plugin in plugins.iterdir() if plugin.is_dir()):
            raise ValueError("The saved-data folder overlaps a plugin program folder.")
    return roots


def _walk(folder):
    """Include hidden files and empty folders; fail instead of silently skipping."""
    def failed(error):
        raise error
    yield folder
    for directory, dirs, files in os.walk(folder, followlinks=False, onerror=failed):
        for name in sorted(dirs + files):
            child = Path(directory) / name
            _plain_path(child)
            yield child


def backup_data(root, destination, gallery="", recordings=""):
    roots = data_roots(root, gallery, recordings)
    destination = Path(destination).expanduser().resolve(strict=True)
    if not destination.is_dir():
        raise ValueError("Choose a destination folder.")
    for folder in roots.values():
        if destination == folder.resolve() or folder.resolve() in destination.parents:
            raise ValueError("Choose a backup destination outside the downloaded-data folders.")
    with LOCK:
        folders = {name: folder for name, folder in roots.items() if folder.is_dir()}
        if not folders:
            raise ValueError("No SSTV gallery, decoded-data, recording or APRS receipt folders were found.")
        output = destination / ("bqe_downloaded_data_" +
                                datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f") + ".zip")
        temporary = None
        count, size = 0, 0
        try:
            with tempfile.NamedTemporaryFile(dir=destination, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for prefix, folder in folders.items():
                    for item in _walk(folder):
                        relative = item.relative_to(folder).as_posix()
                        name = prefix if relative == "." else prefix + "/" + relative
                        before = item.stat()
                        size += before.st_size if item.is_file() else 0
                        count += 1
                        if count > 250000 or size > 64 * 1024**3:
                            raise ValueError("Downloaded data exceeds 250,000 entries / 64 GB.")
                        archive.write(item, name)
                        after = item.stat()
                        if item.is_file() and (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise ValueError("Data changed during backup. Stop reception/decoding and try again.")
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
    return dict(message=f"Downloaded-data backup saved to {output}. LRPT weather data excluded.",
                path=str(output))


def restore_data(root, archive_path, gallery="", recordings=""):
    roots = data_roots(root, gallery, recordings)
    # Keep the current machine's configured locations authoritative; ZIPs cannot
    # specify arbitrary absolute restore paths.
    return restore(Path(root) / "bqe_config", archive_path, data_roots=roots)
