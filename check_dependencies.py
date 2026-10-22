"""Installer smoke checks. Does not open radios, enumerate audio devices or download files."""
import importlib
from importlib.metadata import version, distributions
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PACKAGES = {
    'numpy': 'numpy', 'scipy': 'scipy', 'yaml': 'PyYAML', 'requests': 'requests',
    'skyfield.api': 'skyfield', 'PIL.Image': 'Pillow', 'soundfile': 'soundfile',
    'reedsolo': 'reedsolo', 'soxr': 'soxr', 'sstv': 'sstv', 'lameenc': 'lameenc',
    'aprslib': 'aprslib',
    'faster_whisper': 'faster-whisper', 'av': 'av', 'onnxruntime': 'onnxruntime',
    'ctranslate2': 'ctranslate2', 'piper': 'piper-tts', 'sgp4.api': 'sgp4',
}


def requirement_files(root=ROOT):
    """Check the dependency file tree without requiring installed packages."""
    root = Path(root).resolve()
    seen = set()

    def read(path):
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if line.startswith('-r '):
                read(path.parent / line[3:].strip())

    read(root / 'requirements.txt')
    missing = [path for path in sorted((root / 'plugins').glob('*/requirements.txt'))
               if path.resolve() not in seen]
    if missing:
        raise ValueError('Plugin requirements not included by root requirements: ' +
                         ', '.join(str(path) for path in missing))
    return seen


def requirement_errors(root=ROOT):
    """Validate every plugin requirement and installed transitive dependency."""
    from packaging.requirements import Requirement
    errors, seen = [], set()

    def check(text, source):
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
            return
        try:
            installed = version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f'{source}: missing {requirement}')
            return
        if installed not in requirement.specifier:
            errors.append(f'{source}: requires {requirement}; installed {installed}')

    def read(path):
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if line.startswith('-r '):
                read(path.parent / line[3:].strip())
            elif line:
                check(line, path.relative_to(root))

    read(root / 'requirements.txt')
    for path in sorted((root / 'plugins').glob('*/requirements.txt')):
        if path.resolve() not in seen:
            errors.append(f'Plugin requirements not included by root requirements: {path}')
    for distribution in distributions():
        for requirement in distribution.requires or []:
            check(requirement, distribution.metadata['Name'])
    return errors


def main(python_only=False):
    if sys.version_info < (3, 10) or (os.name == 'nt' and sys.maxsize <= 2**32):
        print('ERROR: Use Python 3.10+, with 64-bit Python on Windows.')
        return 1
    failures = []
    try:
        failures.extend(requirement_errors())
        for error in failures:
            print('ERROR: ' + error)
    except Exception as exc:
        failures.append(str(exc))
        print(f'ERROR: Cannot validate dependency versions: {exc}')
    packages = dict(PACKAGES)
    if os.name == 'nt':
        packages['sounddevice'] = 'sounddevice'
    for module, package in packages.items():
        try:
            loaded = importlib.import_module(module)
            if module == 'sstv' and not callable(getattr(loaded, 'decode', None)):
                raise RuntimeError('Wrong SSTV package: expected sstv==0.1.0 (unexcellent/sstv-py)')
            print(f'OK: {package} {version(package)}')
        except Exception as exc:
            failures.append(package)
            print(f'ERROR: {package}: {exc}')
    if python_only:
        # SoundCard's Linux import needs a desktop audio session. Its package
        # version is checked above; device initialization is not a build check.
        print('Python dependency checks FAILED.' if failures else 'Python dependency checks passed.')
        return 1 if failures else 0
    # SoundCard connects to PulseAudio during import on Linux. Keep that probe
    # isolated and bounded; a headless install can have the library but no session.
    try:
        print(f'Installed: SoundCard {version("SoundCard")}')
        probe = subprocess.run([sys.executable, '-c', 'import soundcard', 'BQE dependency check'],
                               capture_output=True, text=True, timeout=20)
        if probe.returncode:
            print('WARNING: SoundCard could not initialize. ' + probe.stderr.strip())
            if os.name == 'nt':
                failures.append('SoundCard')
            else:
                print('Use a desktop PulseAudio/PipeWire-Pulse session for sound-card capture.')
    except Exception as exc:
        failures.append('SoundCard')
        print(f'ERROR: SoundCard: {exc}')

    native = [
        ('Hamlib rigctl', ROOT / ('rigctl.exe' if os.name == 'nt' else 'rigctl'), 'rigctl'),
        ('Hamlib rigctld', ROOT / ('rigctld.exe' if os.name == 'nt' else 'rigctld'), 'rigctld'),
        ('SSDV renderer (SO-127 and HF FreeDV images)', ROOT / 'plugins/bqe_ssdv_decoder/vendor/ssdv' /
         ('ssdv.exe' if os.name == 'nt' else 'ssdv'), 'ssdv'),
        ('HamDRM receiver', ROOT / 'plugins/bqe_hf_image_decoder/native' /
         ('hamdrm.exe' if os.name == 'nt' else 'hamdrm'), None),
        ('SatDump (LRPT)', ROOT / 'plugins/bqe_lrpt_decoder/vendor/satdump/satdump.exe'
         if os.name == 'nt' else ROOT / '__no_bundled_linux_satdump__', 'satdump'),
    ]
    for label, path, command in native:
        found = str(path) if path.is_file() else shutil.which(command) if command else None
        print(f'Found: {label}: {found}' if found else
              f'WARNING: {label} not found in the default locations; see DEPENDENCIES.md.')
    for label, module, function in [
        ('Codec2 / FreeDV', 'plugins.bqe_hf_image_decoder.freedv', 'library'),
        ('RTL-SDR USB library', 'plugins.bqe_sdr.sources', 'load_library'),
    ]:
        try:
            getattr(importlib.import_module(module), function)()
            print(f'OK: {label} loads (no hardware opened).')
        except Exception as exc:
            print(f'WARNING: {label}: {exc}')
    for path in sorted((ROOT / 'plugins').glob('*/.venv')):
        print(f'NOTICE: Existing plugin environment takes precedence over the root environment: {path}. '
              'Update it with that plugin\'s requirements.txt if needed.')
    print('Python dependency checks FAILED.' if failures else 'Python dependency checks passed.')
    print('Native warnings affect the named features; configured custom paths may still work.')
    return 1 if failures else 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--files-only']:
        try:
            files = requirement_files()
            print(f'Dependency file preflight passed: {len(files)} requirement files.')
        except (OSError, ValueError) as exc:
            print(f'ERROR: {exc}')
            raise SystemExit(1)
    else:
        raise SystemExit(main())
