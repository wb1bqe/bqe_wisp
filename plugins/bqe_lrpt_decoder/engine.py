"""Run the official SatDump pipeline without a shell, preserving logs and IQ."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def executable(configured=''):
    candidates = [configured] if configured else [os.environ.get('BQE_SATDUMP', ''),
        str(ROOT/'vendor'/'satdump'/'satdump.exe') if os.name == 'nt' else '', shutil.which('satdump')]
    for name in filter(None, candidates):
        found = shutil.which(str(name)) or str(name)
        if Path(found).is_file():
            return str(Path(found).resolve())
    raise ValueError('SatDump CLI not found. Install SatDump or set lrpt.satdump / BQE_SATDUMP; see README.')


def command(job):
    o = job['options']
    pipeline = 'meteor_m2-x_lrpt' + ('_80k' if int(o['symbol_rate']) == 80000 else '')
    args = [executable(o.get('satdump', ''))]
    if o.get('cli_version', '1') == '2':
        args.append('pipeline')
    args += [pipeline, 'baseband', job['iq_file'], job['products'],
             '--samplerate', str(job['sample_rate']), '--baseband_format', job['format'],
             '--satellite_number', o['satellite'], '--dc_block', '--rs_usecheck', 'true',
             '--fill_missing', 'false']
    if job.get('captured_at'):
        timestamp = datetime.fromisoformat(job['captured_at'].replace('Z','+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError('IQ capture timestamp must include its UTC offset')
        args += ['--start_timestamp', str(timestamp.timestamp())]
    return args


def decode_job(path):
    path = Path(path).resolve()
    job = json.loads(path.read_text(encoding='utf-8'))
    if job.get('state') not in ('queued', 'captured'):
        raise ValueError('This job is not queued for decoding')
    try:
        args = command(job)
        job.update(state='decoding', error=None, command=args)
        write_json(path, job)
        Path(job['products']).mkdir(parents=True, exist_ok=True)
        with (path.parent/'satdump.log').open('wb') as log:
            # Use the portable installation directory so its pipelines/resources are found.
            completed = subprocess.run(args, cwd=str(Path(args[0]).parent), stdout=log,
                stderr=subprocess.STDOUT, timeout=job['options']['decode_timeout'],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        images = [str(p.relative_to(path.parent)) for p in Path(job['products']).rglob('*')
                  if p.suffix.lower() in ('.png', '.jpg', '.jpeg') and p.stat().st_size > 0]
        job.update(returncode=completed.returncode, images=images)
        if completed.returncode != 0:
            raise RuntimeError(f'SatDump exited with code {completed.returncode}; see satdump.log')
        job['state'] = 'completed' if images else 'no_images'
        job['message'] = 'Images decoded' if images else 'No images recovered. Check signal, frequency, rate and satellite availability.'
    except Exception as exc:
        job.update(state='error', error=str(exc))
    job['finished'] = datetime.now(timezone.utc).isoformat()
    write_json(path, job)
    return job


def launch_job(path):
    """A separate worker releases the radio immediately for the next pass."""
    with (Path(path).parent/'worker.log').open('ab') as log:
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                      cwd=str(ROOT.parents[1]))
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs['start_new_session'] = True
        return subprocess.Popen([sys.executable, str(ROOT/'bqe_lrpt_decoder.py'),
                                 '--decode-job', str(path)], **kwargs)
