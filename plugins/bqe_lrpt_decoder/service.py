"""Capture untouched wideband IQ, then queue a reproducible image-decoding job."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import threading
import time
import uuid
from .config import Options
from .engine import executable, write_json, launch_job
from plugins.bqe_sdr.sources import source


class Session:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = dict(state='idle', error=None, samples=0, seconds=0)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, values):
        options = Options.from_dict(values)
        executable(options.satdump)  # Fail before consuming disk or opening the dongle.
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Stop the current capture first')
            self.stop_event.clear()
            self.state = dict(state='starting', error=None, samples=0, seconds=0,
                              options=options.as_dict())
            self.thread = threading.Thread(target=self._capture, args=(options,), daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def new_job(self, options):
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        folder = self.output/name
        folder.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        return folder, dict(id=name, options=options.as_dict(), state='capturing', error=None,
            started=now, captured_at=now, products=str(folder/'products'),
            sample_rate=options.sample_rate, frequency=options.frequency, format='cu8', images=[])

    def _capture(self, options):
        folder, job = None, None
        count, size = 0, 0
        try:
            folder, job = self.new_job(options)
            reserve = options.min_free_mb*1024**2
            if shutil.disk_usage(folder).free < reserve + options.sample_rate*2*options.duration:
                raise ValueError('Not enough free disk space for the capture limit plus reserve')
            data = folder/'capture.sigmf-data'
            job['iq_file'] = str(data)
            write_json(folder/'job.json', job)
            capture = source(options.source_settings(), self.stop_event)
            with capture as stream, data.open('xb') as output:
                if stream.datatype != 'cu8':
                    raise ValueError('Live LRPT sources must produce unsigned 8-bit IQ')
                self.update(state='capturing', job=folder.name)
                last = 0
                for raw in stream:
                    if self.stop_event.is_set():
                        break
                    if len(raw) % 2:
                        raise ValueError('Incomplete IQ sample')
                    remaining = max(0, int(options.duration*options.sample_rate)-count)
                    raw = raw[:remaining*2]
                    output.write(raw)
                    count += len(raw)//2
                    size += len(raw)
                    self.update(samples=count, seconds=count/options.sample_rate, bytes=size)
                    if time.monotonic()-last > 2:
                        if shutil.disk_usage(folder).free < reserve:
                            raise ValueError('Capture stopped to preserve the free-space reserve')
                        last = time.monotonic()
                    if count >= options.duration*options.sample_rate:
                        break
                if getattr(capture, 'error', None):
                    raise capture.error
            if not count:
                raise ValueError('No IQ samples received')
            meta = {'global': {'core:datatype':'cu8', 'core:sample_rate':options.sample_rate,
                              'core:version':'1.2.5', 'core:recorder':'BQE LRPT'},
                    'captures':[{'core:sample_start':0, 'core:frequency':options.frequency,
                                 'core:datetime':job['started']}], 'annotations':[]}
            write_json(data.with_suffix('.sigmf-meta'), meta)
            job.update(state='queued', samples=count, bytes=size)
            write_json(folder/'job.json', job)
            launch_job(folder/'job.json')
            self.update(state='queued')
        except Exception as exc:
            self.update(state='error', error=str(exc))
            if job is not None:
                job.update(state='error', error=str(exc), samples=count, bytes=size)
                write_json(folder/'job.json', job)

    def replay(self, filename, values):
        """Replay single-center SigMF files without altering or copying their IQ."""
        options = Options.from_dict(values)
        executable(options.satdump)
        path = Path(filename).expanduser().resolve()
        if path.suffix == '.sigmf-meta':
            path = path.with_suffix('.sigmf-data')
        meta = json.loads(path.with_suffix('.sigmf-meta').read_text(encoding='utf-8'))
        captures = meta.get('captures', [])
        if len(captures) != 1 or captures[0].get('core:sample_start') != 0:
            raise ValueError('Replay requires a single-center SigMF capture')
        rate = int(meta['global']['core:sample_rate'])
        center = float(captures[0]['core:frequency'])
        if rate < 240000 or rate > 2400000:
            raise ValueError('Replay sample rate must be 240 kS/s–2.4 MS/s')
        if abs(center-options.frequency) > 1:
            raise ValueError('Replay must be centered on the selected LRPT frequency; off-center IQ needs frequency shifting first')
        datatype = meta['global']['core:datatype']
        formats = {'cu8':('cu8',2), 'cf32_le':('cf32',8), 'ci16_le':('cs16',4)}
        if datatype not in formats:
            raise ValueError('Replay supports cu8, ci16_le and cf32_le IQ')
        fmt, width = formats[datatype]
        size = path.stat().st_size
        if not size or size % width:
            raise ValueError('IQ file is empty or truncated')
        folder, job = self.new_job(options)
        job.update(state='queued', iq_file=str(path), sample_rate=rate, format=fmt,
                   bytes=size, samples=size//width, captured_at=captures[0].get('core:datetime'))
        write_json(folder/'job.json', job)
        launch_job(folder/'job.json')
        return job

    def jobs(self):
        jobs = []
        for path in sorted(self.output.glob('*/job.json'), reverse=True)[:100]:
            try:
                jobs.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
        return jobs
