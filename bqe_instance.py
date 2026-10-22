"""Process-lifetime scheduler lock, released by the OS even after a crash."""
import os
from pathlib import Path


class SchedulerAlreadyRunning(RuntimeError):
    pass


class SchedulerLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        # Windows byte locks require a byte to lock. Never truncate or unlink
        # this file: other processes must always lock the same underlying file.
        if self.path.stat().st_size == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise SchedulerAlreadyRunning(
                'BQE WISP is already running for this installation. '
                'Use the existing scheduler, or close it before starting another.'
            ) from exc
        self.stream = stream
        return self

    def __exit__(self, *unused):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
