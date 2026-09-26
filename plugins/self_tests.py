"""Audited, offline plugin checks for Help > Environment Diagnostics.

Do not replace this allowlist with unittest discovery: full development suites
include live-server, subprocess, and workstation-specific configuration tests.
"""
import importlib
import importlib.util
import io
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUN_LOCK = threading.Lock()
TIMEOUT = 20
# Exact methods keep future hardware/integration tests out of user diagnostics.
SUITES = {
    'recorder': ('Audio recorder', 'bqe_sound_recorder', 'tests/test_audio_recording.py', ['numpy','lameenc','?soundcard'], [
        'RecordingFileTests.test_direct_input_uses_shared_backend_and_encodes_signal',
        'RecordingFileTests.test_finalization_names_and_collision_handling']),
    'sstv': ('SSTV', 'bqe_sstv_decoder', 'plugins/bqe_sstv_decoder/tests/test_decoder.py',
             ['numpy','PIL','soundfile','sstv','pysstv.color'], [
        'DecoderTests.test_chunk_boundaries_and_consecutive_transmissions',
        'DecoderTests.test_partial_and_noise', 'DecoderTests.test_44100_header_detection']),
    'telemetry': ('Telemetry', 'bqe_tlm_decoder', 'plugins/bqe_tlm_decoder/tests/test_decoder.py',
                  ['numpy','scipy','soundfile','reedsolo'], [
        'DecoderTests.test_reference_fec_and_damaged_symbols',
        'DecoderTests.test_ax25_crc_and_audio', 'DecoderTests.test_audio_usp_all_rates_and_polarities']),
    'ssdv': ('SSDV', 'bqe_ssdv_decoder', 'plugins/bqe_ssdv_decoder/tests/test_decoder.py',
             ['numpy','scipy','PIL','soundfile','reedsolo'], [
        'DecoderTests.test_framing_polarity_and_bad_crc',
        'DecoderTests.test_reordered_duplicate_fragments_recover_one_complete_image',
        'DecoderTests.test_missing_data_is_never_claimed_complete',
        'DecoderTests.test_audio_at_both_rates_and_polarities']),
    'sdr': ('SDR', 'bqe_sdr', 'plugins/bqe_sdr/tests/test_dsp.py', ['numpy','scipy'], [
        'DSPTests.test_each_mode_recovers_expected_tone',
        'DSPTests.test_arbitrary_block_boundaries_preserve_samples_and_phase',
        'DSPTests.test_frequency_translation_follows_doppler',
        'DSPTests.test_opposite_sideband_rejection']),
    'lrpt': ('LRPT', 'bqe_lrpt_decoder', 'plugins/bqe_lrpt_decoder/tests/test_lrpt.py', ['numpy'], [
        'LRPTTests.test_settings_validate_and_keep_full_iq',
        'LRPTTests.test_command_72_80_and_cli_versions',
        'LRPTTests.test_worker_distinguishes_images_noise_and_failure']),
}


def missing_dependency(exc):
    return isinstance(exc, ImportError) and not str(getattr(exc, 'name', '') or '').startswith(('plugins', '_bqe_diagnostic'))


def install_guards(scratch):
    """Defence against an accidental real device, network or subprocess call."""
    scratch = Path(scratch).resolve()
    def audit(event, args):
        if event in ('socket.__new__', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork'):
            raise RuntimeError('Self-tests cannot access networks or start other programs')
        if event == 'ctypes.dlopen' and any(word in str(args[0]).lower() for word in ('rtlsdr','libusb','hamlib','portaudio','pulse')):
            raise RuntimeError('Self-tests cannot load hardware drivers')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            mode, flags = args[1], args[2]
            writing = (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
            if writing:
                path = Path(os.fsdecode(args[0])).resolve()
                if path == Path(os.devnull).resolve():
                    return
                if scratch != path and scratch not in path.parents:
                    raise RuntimeError(f'Self-tests may only write temporary files: {path}')
    sys.addaudithook(audit)


def run_child(key, scratch):
    sys.path.insert(0, str(ROOT))
    # Python 3.8 may run uname while initializing this cache. Complete that
    # read-only OS probe before prohibiting subprocesses in the actual tests.
    platform.uname()
    install_guards(scratch)
    label, plugin, filename, dependencies, names = SUITES[key]
    for dependency in dependencies:
        try:
            if dependency.startswith('?'):
                if importlib.util.find_spec(dependency[1:]) is None:
                    raise ModuleNotFoundError(dependency[1:])
            else:
                importlib.import_module(dependency)
        except (ImportError, OSError) as exc:
            return dict(status='warning', message=f'Missing/unavailable dependency {dependency}: {exc}; tests skipped', tests=0)
    try:
        spec = importlib.util.spec_from_file_location('_bqe_diagnostic_tests', ROOT/filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite = unittest.TestLoader().loadTestsFromNames(names, module)
        output = io.StringIO()
        class Result(unittest.TextTestResult):
            dependency_errors = 0
            def addError(self, test, error):
                if missing_dependency(error[1]):
                    self.dependency_errors += 1
                super().addError(test, error)
        result = unittest.TextTestRunner(stream=output, verbosity=2, resultclass=Result).run(suite)
        failed = len(result.failures)+len(result.errors)-result.dependency_errors
        status = 'failed' if failed else 'warning' if result.dependency_errors or result.skipped else 'passed'
        return dict(status=status, tests=result.testsRun, message=(
            f'{result.testsRun} tests; {len(result.failures)} failures; '
            f'{len(result.errors)-result.dependency_errors} errors; {result.dependency_errors} missing dependencies; {len(result.skipped)} skipped'),
            details=output.getvalue()[-12000:])
    except Exception as exc:
        return dict(status='warning' if missing_dependency(exc) else 'failed',
                    message=f'{type(exc).__name__}: {exc}', tests=0, details=traceback.format_exc()[-12000:])


def run_suite(key, should_defer=lambda: False, timeout=TIMEOUT):
    label, plugin, filename, dependencies, names = SUITES[key]
    if should_defer():
        return dict(status='warning', message='Skipped: a pass is active or BQE is shutting down', tests=0)
    if not (ROOT/filename).is_file():
        return dict(status='warning', message='Test files are not installed; skipped', tests=0)
    local = ROOT/'plugins'/plugin/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python = str(local) if local.is_file() else sys.executable
    with tempfile.TemporaryDirectory(prefix='bqe-self-test-') as folder:
        result_path = Path(folder)/'result.json'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                   TMP=folder, TEMP=folder, TMPDIR=folder,
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
        with (Path(folder)/'worker.log').open('wb') as log:
            try:
                process = subprocess.Popen([python, str(Path(__file__).resolve()), '--child', key, str(result_path)],
                    cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except OSError as exc:
                return dict(status='warning', message=f'Python runtime unavailable; skipped: {exc}', tests=0)
            start = time.monotonic()
            while process.poll() is None:
                reason = 'Skipped: interrupted for a satellite pass or shutdown' if should_defer() else (
                    f'Timed out after {timeout} seconds' if time.monotonic()-start >= timeout else None)
                if reason:
                    process.kill(); process.wait()
                    return dict(status='warning' if reason.startswith('Skipped') else 'failed', message=reason, tests=0)
                time.sleep(.1)
        try:
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if process.returncode != 0:
                raise ValueError(f'Worker exited with code {process.returncode}')
        except (OSError, ValueError) as exc:
            result = dict(status='failed',message=f'Self-test worker failed: {exc}',tests=0,
                          details=(Path(folder)/'worker.log').read_text(encoding='utf-8',errors='replace')[-12000:])
        result['runtime'] = python
        return result


def run_plugin_self_tests(should_defer=lambda: False):
    if not RUN_LOCK.acquire(blocking=False):
        return '[WARNING] Plugin self-tests already running; duplicate request skipped.\n'
    try:
        lines = ['[INFO] Plugin self-tests: selected offline checks only; no hardware, network or external decoders.',
                 '[INFO] Missing dependencies are warnings. Each suite has a 20-second limit.']
        for key, spec in SUITES.items():
            result = run_suite(key, should_defer)
            marker = {'passed':'PASS','warning':'WARNING','failed':'FAIL'}[result['status']]
            lines.append(f'[{marker}] {spec[0]}: {result["message"]}')
            if result.get('runtime'):
                lines.append(f'    Python: {result["runtime"]}')
            if result.get('details'):
                lines.extend('    '+line for line in result['details'].splitlines())
        lines.append('[INFO] Self-tests do not certify hardware reception or installed external decoder binaries.')
        return '\n'.join(lines)+'\n'
    finally:
        RUN_LOCK.release()


if __name__ == '__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--child' and sys.argv[2] in SUITES:
        destination = Path(sys.argv[3]).resolve()
        result = run_child(sys.argv[2], destination.parent)
        destination.write_text(json.dumps(result), encoding='utf-8')
    else:
        print(run_plugin_self_tests())
