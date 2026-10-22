"""First-run installation choice shared by the Windows and Linux launchers."""
import json
import os
from pathlib import Path
import sys
import tempfile


def normalized(value):
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    if not text:
        raise ValueError('Enter a folder path')
    return Path(os.path.expandvars(text)).expanduser().resolve()


def record_path(default, metadata):
    # Use the payload fingerprint, since different builds can share a folder name.
    key = metadata['payload_sha256']
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Invalid bundle fingerprint')
    return default.parent / 'installations' / (key + '.json')


def matches(destination, metadata):
    try:
        marker = json.loads((destination / '.bundle-ready.json').read_text(encoding='utf-8'))
        return marker.get('payload_sha256') == metadata['payload_sha256']
    except (OSError, ValueError):
        return False


def validate(destination, metadata, *, allow_nonempty_parent=False):
    if not destination.exists():
        return
    if not destination.is_dir():
        raise ValueError('That path is a file, not a folder')
    if matches(destination, metadata):
        return
    if (destination / '.bundle-ready.json').exists():
        if allow_nonempty_parent:
            raise ValueError(f'This parent contains another BQE Wisp build. Rename or move the old installation '
                             f'(including {destination / "bqe_wisp"}, _python and .bundle-* files) '
                             'before rerunning, or choose another --dir parent folder.')
        raise ValueError('That folder contains another build; choose a new folder')
    pending = destination / '.bundle-pending'
    if pending.is_file() and pending.read_text(encoding='ascii') == metadata['payload_sha256']:
        return  # Retry an interrupted extraction of this exact payload.
    if allow_nonempty_parent:
        application = destination / 'bqe_wisp'
        if application.exists() or application.is_symlink():
            raise ValueError(f'BQE Wisp folder already exists: {application}. '
                             'Rename the old folder to something else (for example bqe_wisp_old) '
                             'before rerunning the installer.')
        for name in ('_python', '.bundle-pending', '.bundle-ready.tmp'):
            item = destination / name
            if item.exists() or item.is_symlink():
                raise ValueError(f'Installation path already exists: {item}. Rename or move it '
                                 'before rerunning, or choose another --dir parent folder.')
        return
    if any(p.name != '.extract.lock' for p in destination.iterdir()):
        raise ValueError('Choose an empty or new folder; existing files will not be overwritten')


def choose_destination(requested, default, metadata, *, allow_nonempty_parent=False, first_run_destination=None):
    """Prefer explicit and remembered locations, then an optional first-run default."""
    default = normalized(default)
    if requested is not None:
        destination = normalized(requested)
        validate(destination, metadata, allow_nonempty_parent=allow_nonempty_parent)
        return destination
    record = record_path(default, metadata)
    if record.is_file():
        try:
            saved = json.loads(record.read_text(encoding='utf-8'))
            destination = normalized(saved['directory'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('Saved installation location is unreadable; specify --dir "PATH"') from exc
        if not matches(destination, metadata):
            raise ValueError('Saved installation is missing or belongs to another build; specify --dir "PATH"')
        return destination
    # Preserve installations made by previous launchers in the default location.
    if matches(default, metadata):
        return default
    if first_run_destination is not None:
        destination = normalized(first_run_destination)
        validate(destination, metadata, allow_nonempty_parent=allow_nonempty_parent)
        return destination
    raise ValueError('First run needs an installation folder. Launch with --dir "PATH" '
                     '(relative to the current directory, or a full path)')


def remember_destination(destination, default, metadata):
    """Remember only successful extraction, without editing application settings."""
    if not matches(destination, metadata):
        raise ValueError('Cannot remember an installation before extraction succeeds')
    record = record_path(normalized(default), metadata)
    temporary = None
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=record.parent,
                                         prefix='.location-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'directory': str(destination), 'payload_sha256': metadata['payload_sha256']}, stream)
        temporary.replace(record)
    except OSError as exc:
        print(f'Could not remember the installation folder: {exc}. Use --dir "{destination}" next time.', file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
