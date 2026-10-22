"""Process-based status for managed plugins in the current BQE process."""
from pathlib import PurePosixPath
from threading import Lock
from weakref import WeakSet

_receivers = WeakSet()
_lock = Lock()


def register(receiver):
    with _lock:
        _receivers.add(receiver)


def program_label(process):
    """Name known BQE helper scripts without exposing command-line arguments."""
    args = getattr(process, 'args', ())
    for arg in args if isinstance(args, (list, tuple)) else ():
        name = PurePosixPath(str(arg).replace('\\', '/')).stem
        if name.startswith('bqe_'):
            return name[4:].replace('_', ' ').replace('decoder', '').strip().upper()
    return 'External program'


def running_plugins(extra=()):
    # Never take receiver.lock here: device startup may hold it for a minute.
    with _lock:
        receivers = list(_receivers)
    processes = [(r.key, r.label, r.process) for r in receivers]
    processes.extend(extra)
    result = []
    for key, label, process in processes:
        if process is not None and process.poll() is None:
            result.append(dict(id=key, label=label, pid=process.pid))
    return sorted(result, key=lambda item: (item['label'].casefold(), item['id']))
