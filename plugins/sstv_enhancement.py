"""Dependency-free supervisor for offline SSTV gallery enhancement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid


class EnhancementJob:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.directory = None

    def snapshot(self):
        with self.lock:
            state = dict(state='idle', outputs=[])
            if self.directory:
                try:
                    state = json.loads((self.directory/'status.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    state = dict(state='running', outputs=[], message='Starting enhancement')
                if self.process and self.process.poll() is not None and state['state'] == 'running':
                    state.update(state='error', message='Enhancement worker exited unexpectedly; originals preserved')
            return state

    def start(self, project, directory, names):
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('An enhancement job is already running')
            project, directory = Path(project).resolve(), Path(directory).resolve()
            if (not isinstance(names, list) or not 2 <= len(names) <= 24 or
                    not all(isinstance(name, str) for name in names) or len(set(names)) != len(names)):
                raise ValueError('Select between 2 and 24 different images')
            for name in names:
                path = (directory/name).resolve()
                if (Path(name).name != name or path.parent != directory or
                        path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.bmp'} or not path.is_file()):
                    raise ValueError('Selected image is unavailable or outside the gallery')
            job = project/'logs'/'sstv-enhancement'/uuid.uuid4().hex
            job.mkdir(parents=True)
            (job/'request.json').write_text(json.dumps(dict(directory=str(directory), names=names)), encoding='utf-8')
            python = project/'plugins'/'bqe_sstv_decoder'/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            command = [str(python) if python.exists() else sys.executable,
                       '-m', 'plugins.bqe_sstv_decoder.enhance', '--job', str(job)]
            kwargs = dict(cwd=str(project), stdin=subprocess.DEVNULL)
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            with (job/'worker.log').open('wb') as log:
                self.process = subprocess.Popen(command, stdout=log, stderr=log, **kwargs)
            self.directory = job
            return self.snapshot()

    def cancel(self):
        with self.lock:
            if self.directory and self.process and self.process.poll() is None:
                (self.directory/'cancel').touch()
            return self.snapshot()


ENHANCEMENT_JOB = EnhancementJob()
