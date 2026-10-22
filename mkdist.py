"""Build a source release and standalone Windows/Linux executables.

Python 3.10+, stdlib only. Configuration is never parsed or sanitized here;
the default manifest ships the current bqe_config/satellites.yaml unchanged.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError(f'Invalid manifest path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError(f'Unsafe manifest path: {value!r}')
    if any(p.endswith((' ', '.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', p)
           for p in path.parts) or any(c in value for c in '*?<>|"\r\n'):
        raise ValueError(f'Nonportable manifest path: {value!r}')
    return path


def read_manifest(root, manifest):
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('files'), list) or not data['files']:
        raise ValueError('Manifest must have version 1 and a nonempty files list')
    files, used = [], set()
    for entry in data['files']:
        source, target = (entry, entry) if isinstance(entry, str) else (entry['source'], entry['destination'])
        src, dst = relative(source), relative(target)
        key = str(dst).casefold()
        if key in used:
            raise ValueError(f'Duplicate destination: {dst}')
        used.add(key)
        path = root.joinpath(*src.parts)
        if any(parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction())
               for parent in [path, *path.parents] if parent != root and root in parent.parents):
            raise ValueError(f'Linked source is not permitted: {src}')
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f'Missing or external source: {src}')
        files.append((path, str(dst)))
    dirs = [str(relative(d)) for d in data.get('directories', [])]
    for target in [dst for _, dst in files] + dirs:
        if any(str(p).casefold() in used for p in PurePosixPath(target).parents if str(p) != '.'):
            raise ValueError(f'File/directory collision: {target}')
    if any(d.casefold() in used for d in dirs):
        raise ValueError('Directory conflicts with a file')
    return files, dirs


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify_ssdv_bundle(files):
    """Reject releases whose SSDV binary no longer matches its pinned build."""
    paths = {target: source for source, target in files}
    prefix = 'plugins/bqe_ssdv_decoder/vendor/ssdv/'
    if prefix + 'ssdv.exe' not in paths:
        return
    try:
        lock = json.loads(paths[prefix + 'SOURCE.json'].read_text())
        record = json.loads(paths[prefix + 'BUILD-WINDOWS.json'].read_text())
        if record['revision'] != lock['revision'] or record['source_sha256_lf'] != lock['sha256_lf']:
            raise ValueError('source revision/build record mismatch')
        for name, expected in lock['sha256_lf'].items():
            source = paths[prefix + str(relative(name))]
            actual = hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            if actual != expected:
                raise ValueError(f'pinned source mismatch: {name}')
        if digest(paths[prefix + 'ssdv.exe']) != record['binary_sha256']:
            raise ValueError('Windows executable hash mismatch')
    except (KeyError, ValueError) as exc:
        raise ValueError(f'SSDV build verification failed: {exc}') from exc


def build(root, manifest, stamp=None, check=False, output_dir=None):
    root = Path(root).resolve()
    files, directories = read_manifest(root, Path(manifest))
    verify_ssdv_bundle(files)
    if check:
        return len(files)
    stamp = stamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if not re.fullmatch(r'[0-9]{8}(?:T[0-9]{6}Z)?', stamp):
        raise ValueError('Datestamp must be YYYYMMDD or YYYYMMDDTHHMMSSZ')
    destination = (Path(output_dir).expanduser().resolve() if output_dir is not None
                   else root / ('dist_' + stamp))
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite existing release: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.dist-build-', dir=destination.parent)).resolve()
    try:
        tree = stage / 'bqe_wisp'
        tree.mkdir()
        for directory in directories:
            (tree / directory).mkdir(parents=True, exist_ok=True)
        inventory = []
        for source, name in files:
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = digest(source)
            # Preserve source bytes, including satellite flags, comments, and
            # station-specific values. Only explicit manifest mappings select
            # defaults for the other first-run configuration files.
            shutil.copy2(source, target)
            if digest(target) != expected:
                raise OSError(f'Source changed or copy verification failed: {source}')
            if target.suffix == '.sh':
                target.chmod(target.stat().st_mode | 0o111)
            inventory.append(dict(path=name, bytes=target.stat().st_size, sha256=expected))
        (stage / 'DISTRIBUTION.json').write_text(json.dumps(dict(
            created_at=datetime.now(timezone.utc).isoformat(), files=inventory), indent=2) + '\n', encoding='utf-8')
        # Rename only a complete build; never merge with an earlier release.
        stage.rename(destination)
        return destination
    finally:
        # Only our generated staging directory, beside the resolved destination.
        if stage.exists() and stage.parent == destination.parent and stage.name.startswith('.dist-build-'):
            shutil.rmtree(stage)


def command(args, capture=False):
    """Argument arrays preserve spaces and never pass user paths to a shell."""
    print('Running: ' + subprocess.list2cmdline([str(a) for a in args]), flush=True)
    if capture:
        return subprocess.check_output([str(a) for a in args], text=True, encoding='utf-8').strip()
    subprocess.run([str(a) for a in args], check=True)


class LinuxHost:
    """Native Linux or the selected Windows Subsystem for Linux distribution."""
    def __init__(self, distro=None):
        self.prefix = ['wsl.exe'] + (['--distribution', distro] if distro else []) + ['--exec'] if sys.platform == 'win32' else []
        if sys.platform not in ('win32', 'linux'):
            raise ValueError('Linux builds require Linux or Windows with WSL')

    def run(self, args, capture=False):
        return command(self.prefix + list(args), capture)

    def path(self, path):
        path = str(Path(path).expanduser().resolve())
        return self.run(['wslpath', '-a', '-u', path], capture=True) if self.prefix else path


def prepare_builders(root, args):
    """Check requested platforms before publishing any release files."""
    target = args.target or ('both' if sys.platform == 'win32' else 'linux')
    targets = ('windows', 'linux') if target == 'both' else (target,)
    builders = []
    if 'windows' in targets:
        if sys.platform != 'win32':
            raise ValueError('Windows executables must be built on Windows; use --target linux on Linux')
        python = Path(args.windows_python or root / '.build_windows/tooling/Scripts/python.exe').resolve()
        runtime = Path(args.windows_runtime or root / '.build_windows/payload/_python').resolve()
        if not python.is_file() or not (runtime / 'python.exe').is_file():
            raise ValueError('Windows tooling/runtime missing. Set --windows-python and --windows-runtime; see packaging_windows/README.md')
        command([python, '-c', 'import PyInstaller'])
        # The pristine embeddable runtime's _pth is configured only in the
        # packager's copy, so explicitly expose its prepared packages here.
        command([runtime / 'python.exe', '-c',
                 'import sys; sys.path[:0]=sys.argv[1:]; import check_dependencies; '
                 'sys.exit(check_dependencies.main(python_only=True))',
                 runtime / 'Lib/site-packages', root])
        builders.append(('windows', python, runtime))
    if 'linux' in targets:
        host = LinuxHost(args.wsl_distro)
        # Defaults reuse this project's previously prepared Linux toolchain.
        base = args.linux_build_root.rstrip('/')
        host.work_root = args.linux_work_root or base + '/work'
        python = args.linux_python or base + '/tooling/bin/python'
        runtime = args.linux_runtime or base + '/runtime/python'
        codec2 = args.codec2_source or host.path(root / 'logs/hf-modem-research/codec2')
        host.run([python, '-c', 'import PyInstaller,platform,sys; '
                  'assert sys.platform == "linux" and platform.machine() == "x86_64", "Linux x86-64 is required"'])
        host.run([runtime + '/bin/python3', '-c',
                  'import sys; sys.path.insert(0,sys.argv[1]); import check_dependencies; '
                  'sys.exit(check_dependencies.main(python_only=True))', host.path(root)])
        host.run(['test', '-f', codec2 + '/CMakeLists.txt'])
        builders.append(('linux', host, python, runtime, codec2))
    return builders


def image_zip(release, executable):
    """Flatten one image folder and the release guide into a distributable ZIP."""
    guide = release / 'bqe_wisp/documentation/BQE_Wisp_Quick_Start_Guide.pdf'
    if not guide.is_file():
        raise FileNotFoundError(f'Quick Start Guide missing: {guide}')
    output = executable.parent
    archive_path = release / (output.name.replace('bqe_wisp_', 'BQE_Wisp_', 1) + '.zip')
    sources = sorted(path for path in output.rglob('*') if path.is_file()) + [guide]
    names = set()
    for source in sources:
        name = source.name.casefold()
        if name in names:
            raise ValueError(f'Duplicate filename in flat image ZIP: {source.name}')
        if source.is_symlink():
            raise ValueError(f'Linked file in image output: {source}')
        names.add(name)
    # Exclusive creation preserves an existing archive. Remove partial ZIPs on failure.
    with archive_path.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for source in sources:
                    info = zipfile.ZipInfo.from_file(source, arcname=source.name)
                    if source == executable and executable.suffix != '.exe':
                        info.create_system = 3
                        info.external_attr = 0o100755 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with source.open('rb') as data, archive.open(info, 'w', force_zip64=True) as target:
                        shutil.copyfileobj(data, target)
        except BaseException:
            stream.close()
            archive_path.unlink(missing_ok=True)
            raise
    return archive_path


def executables(root, release, builders):
    """Reuse platform packagers; publish artifacts in --output-dir."""
    for builder in builders:
        platform = builder[0]
        output = release / f'bqe_wisp_{platform}'
        work = release / '.build' / platform
        if platform == 'windows':
            _, python, runtime = builder
            command([python, root / 'packaging_windows/build.py', release / 'bqe_wisp',
                     '--runtime', runtime, '--work', work, '--output', output])
        else:
            _, host, python, runtime, codec2 = builder
            # Keep thousands of Linux runtime files on a Linux filesystem.
            # Copying them through WSL's Windows mount is extremely slow and
            # may not preserve Unix symlink/permission semantics. Only final
            # artifacts and the source release go to the requested directory.
            linux_work = host.work_root.rstrip('/') + '/' + uuid.uuid4().hex
            host.run([python, host.path(root / 'packaging_linux/build.py'), host.path(release / 'bqe_wisp'),
                      '--runtime', runtime, '--work', linux_work, '--output', host.path(output),
                      '--codec2-source', codec2])
    paths = [release / f'bqe_wisp_{b[0]}' / ('BQE_Wisp.exe' if b[0] == 'windows' else 'BQE_Wisp') for b in builders]
    for path in paths:
        if not path.is_file():
            raise OSError(f'Builder did not produce {path}')
    for path in paths:
        image_zip(release, path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'release_manifest.json')
    parser.add_argument('--datestamp', help='Optional YYYYMMDD or YYYYMMDDTHHMMSSZ; default: current UTC time')
    parser.add_argument('--check', action='store_true', help='Validate every manifest input without copying')
    parser.add_argument('--output-dir', type=Path,
                        help='Build output directory on this computer; --dir belongs to the executable on the target computer')
    parser.add_argument('--source-only', action='store_true', help='Only create the manifest-controlled source release')
    parser.add_argument('--target', choices=('windows', 'linux', 'both'), help='Default: both on Windows (Linux via WSL), linux on Linux')
    parser.add_argument('--windows-python', help='Windows Python with PyInstaller installed')
    parser.add_argument('--windows-runtime', help='Prepared Windows embeddable Python directory')
    parser.add_argument('--wsl-distro', help='WSL distribution for Linux builds; default: default WSL distribution')
    parser.add_argument('--linux-build-root', default='/tmp/bqe-linux-build', help='Existing Linux toolchain directory (Linux path)')
    parser.add_argument('--linux-work-root', help='Linux scratch parent; default: <linux-build-root>/work; retained for diagnostics')
    parser.add_argument('--linux-python', help='Linux Python with PyInstaller installed (Linux path)')
    parser.add_argument('--linux-runtime', help='Prepared relocatable Linux Python directory (Linux path)')
    parser.add_argument('--codec2-source', help='Codec2 source tree (Linux path); defaults to the local research source tree')
    args = parser.parse_args(argv)
    result = None
    try:
        if args.check:
            print(f'Manifest valid: {build(ROOT, args.manifest, check=True)} files')
            return
        # Validate manifest before starting toolchain checks.
        build(ROOT, args.manifest, check=True)
        builders = [] if args.source_only else prepare_builders(ROOT, args)
        result = build(ROOT, args.manifest, args.datestamp, output_dir=args.output_dir)
        paths = executables(ROOT, result, builders)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        retained = f'\nSource release and build diagnostics retained at: {result}' if result else ''
        parser.exit(1, f'Build failed: {exc}{retained}\n')
    print(f'Created source release: {result / "bqe_wisp"}')
    for path in paths:
        print(f'Created executable: {path}')
        print(f'Created image ZIP: {result / (path.parent.name.replace("bqe_wisp_", "BQE_Wisp_", 1) + ".zip")}')


if __name__ == '__main__':
    main()
