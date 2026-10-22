"""Build on Linux using a prepared relocatable Python runtime and source release."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--codec2-source', type=Path,
                        help='Prepare native decoders in private build copies before bundling')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        parser.error('Build inside x86-64 Linux.')
    release, runtime, work, output = (p.resolve() for p in (args.release, args.runtime, args.work, args.output))
    if not (release / 'bqe_wisp.py').is_file() or not (runtime / 'bin/python3').is_file():
        parser.error('Source release or private runtime is missing.')
    if work.exists() or (output / 'BQE_Wisp').exists():
        raise FileExistsError('Choose fresh work/output paths.')
    work.mkdir(parents=True)
    if args.codec2_source:
        # prepare_native adds shared libraries to the runtime. Always give it
        # private copies, never the reusable runtime or selected source release.
        copied_runtime = work / 'runtime'
        shutil.copytree(runtime, copied_runtime, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        native_work = work / 'native'
        subprocess.run([sys.executable, str(Path(__file__).with_name('prepare_native.py')),
                        str(release), '--runtime', str(copied_runtime),
                        '--codec2-source', str(args.codec2_source.resolve()),
                        '--work', str(native_work)], check=True)
        release = native_work / release.parent.name / 'bqe_wisp'
        runtime = copied_runtime
    payload = work / 'payload'
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.venv', '*.o')
    shutil.copytree(release, payload / 'bqe_wisp', ignore=ignore)
    shutil.copytree(runtime, payload / '_python', symlinks=True, ignore=ignore)
    subprocess.run([str(payload / '_python/bin/python3'), '-B', '-c',
                    'import sys; sys.path.insert(0,sys.argv[1]); import check_dependencies; '
                    'raise SystemExit(check_dependencies.main(python_only=True))',
                    str(payload / 'bqe_wisp')], check=True)
    shutil.copy2(Path(__file__).with_name('smoke.py'), payload / 'bqe_wisp/_standalone_smoke.py')
    files = {p.relative_to(payload).as_posix(): digest(p) for p in payload.rglob('*') if p.is_file()}
    archive = work / 'payload.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=6) as target:
        for item in sorted(payload.iterdir()):
            target.add(item, arcname=item.name)
    packages = json.loads(subprocess.check_output([str(runtime / 'bin/python3'), '-m', 'pip', 'list', '--format=json']))
    metadata = dict(build_id=release.parent.name + '-linux-x86_64', files=files, packages=packages,
                    python=subprocess.check_output([str(runtime / 'bin/python3'), '--version'], text=True).strip(),
                    payload_sha256=digest(archive), architecture='x86_64', glibc_minimum=platform.libc_ver()[1])
    (work / 'bundle.json').write_text(json.dumps(metadata, indent=2))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
                    '--paths', str(Path(__file__).resolve().parents[1]),
                    '--name', 'BQE_Wisp', '--distpath', str(output), '--workpath', str(work / 'pyinstaller'),
                    '--specpath', str(work), '--add-data', f'{archive}:.',
                    '--add-data', f'{work / "bundle.json"}:.', str(Path(__file__).with_name('launcher.py'))], check=True)
    executable = output / 'BQE_Wisp'
    metadata.update(executable_sha256=digest(executable), executable_bytes=executable.stat().st_size)
    (output / 'BQE_Wisp.build.json').write_text(json.dumps(metadata, indent=2))
    print(f'Built: {executable}')


if __name__ == '__main__':
    main()
