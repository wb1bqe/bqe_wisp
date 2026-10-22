"""Package a release plus a prepared embeddable Python runtime with PyInstaller."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='New build folder; never overwritten')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    release, runtime, work, output = (p.resolve() for p in (args.release, args.runtime, args.work, args.output))
    assert (release / 'bqe_wisp.py').is_file(), 'Release entry point missing'
    assert (runtime / 'python.exe').is_file(), 'Private Python runtime missing'
    if work.exists() or (output / 'BQE_Wisp.exe').exists():
        raise FileExistsError('Choose new work/output paths; existing builds are never overwritten.')
    work.mkdir(parents=True)
    payload = work / 'payload'
    application = payload / 'bqe_wisp'
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.venv')
    shutil.copytree(release, application, ignore=ignore)
    shutil.copytree(runtime, payload / '_python', ignore=ignore)
    # Isolated module search: no dependency on Python installed on the host,
    # PYTHONPATH, or the working directory of an individual plugin worker.
    (payload / '_python/python313._pth').write_text(
        'python313.zip\n.\nLib/site-packages\n../bqe_wisp\n', encoding='utf-8')
    subprocess.run([str(payload / '_python/python.exe'), '-B', '-c',
                    'import patch_soundcard; patch_soundcard.apply_patch()'], check=True)
    subprocess.run([str(payload / '_python/python.exe'), '-B', '-c',
                    'import check_dependencies; raise SystemExit(check_dependencies.main(python_only=True))'],
                   check=True)
    source_files = {p.relative_to(release).as_posix(): digest(p) for p in release.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    for name, expected in source_files.items():
        assert digest(application / name) == expected, name
    shutil.copy2(Path(__file__).with_name('smoke.py'), application / '_standalone_smoke.py')
    (application / 'STANDALONE_SOURCE.json').write_text(json.dumps({
        'source_release': release.parent.name, 'files': source_files}, indent=2), encoding='utf-8')
    command = [str(payload / '_python/python.exe'), '-c',
               'import importlib.metadata,json; print(json.dumps(sorted('
               '[(d.metadata["Name"],d.version) for d in importlib.metadata.distributions()])))']
    packages = json.loads(subprocess.check_output(command, text=True))
    files = {p.relative_to(payload).as_posix(): digest(p) for p in payload.rglob('*') if p.is_file()}
    archive = work / 'payload.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zip_file:
        for path in sorted(payload.rglob('*')):
            zip_file.write(path, path.relative_to(payload).as_posix())
    metadata = dict(build_id=release.parent.name, python='3.13.5 x64',
                    payload_sha256=digest(archive), files=files, packages=packages)
    (work / 'bundle.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
                    '--paths', str(Path(__file__).resolve().parents[1]),
                    '--name', 'BQE_Wisp', '--distpath', str(output), '--workpath', str(work / 'pyinstaller'),
                    '--specpath', str(work), '--add-data', f'{archive};.',
                    '--add-data', f'{work / "bundle.json"};.', str(Path(__file__).with_name('launcher.py'))], check=True)
    executable = output / 'BQE_Wisp.exe'
    metadata['executable_sha256'] = digest(executable)
    metadata['executable_bytes'] = executable.stat().st_size
    (output / 'BQE_Wisp.build.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(f'Built: {executable}', flush=True)


if __name__ == '__main__':
    main()
