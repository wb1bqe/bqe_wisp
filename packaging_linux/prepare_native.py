"""Build bundled Linux decoders and collect their non-glibc shared libraries."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--codec2-source', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    app, runtime, source, work = (p.resolve() for p in (args.release, args.runtime, args.codec2_source, args.work))
    work.mkdir(parents=True, exist_ok=False)
    # Keep all build output off the original source/release.
    shutil.copytree(app, work / app.parent.name / 'bqe_wisp')
    app = work / app.parent.name / 'bqe_wisp'
    native = app / 'plugins/bqe_hf_image_decoder/native'
    ssdv = app / 'plugins/bqe_ssdv_decoder/vendor/ssdv'
    subprocess.run([sys.executable, str(ssdv / 'build.py'), '--target', 'linux'], check=True)
    # Sources may have CRLF after a Windows checkout.
    script = native / 'rxamadrm/build.sh'
    script.write_text(script.read_text())
    subprocess.run(['sh', str(script)], check=True)
    subprocess.run(['cmake', '-S', str(source), '-B', str(work / 'codec2'),
                    '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=ON', '-DUNITTEST=OFF'], check=True)
    subprocess.run(['cmake', '--build', str(work / 'codec2'), '--target', 'codec2', '-j', '4'], check=True)
    codec = next(p for p in (work / 'codec2/src').glob('libcodec2.so.*') if not p.is_symlink())
    shutil.copy2(codec, native / 'libcodec2.so')
    libdir = runtime / 'lib'
    libdir.mkdir(exist_ok=True)
    seeds = [native / 'hamdrm', native / 'libcodec2.so', ssdv / 'ssdv',
             Path('/usr/lib/x86_64-linux-gnu/libpulse.so.0')]
    skip = re.compile(r'^(?:libc|libm|libdl|libpthread|librt|libresolv|libutil|libanl)\.so|^ld-linux')
    visited = set()
    notices = app / 'THIRD_PARTY_LINUX'
    notices.mkdir(exist_ok=True)
    libraries = {}
    while seeds:
        path = seeds.pop()
        if path in visited:
            continue
        visited.add(path)
        if path.name.startswith('lib') and path.parent != native:
            shutil.copy2(path, libdir / path.name, follow_symlinks=True)
            owner = subprocess.check_output(['dpkg-query', '-S', '*/' + path.name], text=True).splitlines()[0].split(': ')[0]
            package = owner.split(':')[0]
            copyright_file = Path('/usr/share/doc') / package / 'copyright'
            if copyright_file.is_file():
                shutil.copy2(copyright_file, notices / (package + '.copyright'))
            libraries[path.name] = dict(package=owner, source_path=str(path))
        result = subprocess.check_output(['ldd', str(path)], text=True)
        if 'not found' in result:
            raise RuntimeError(result)
        for name, target in re.findall(r'^\s*(\S+) => (/\S+)', result, re.MULTILINE):
            if not skip.search(name):
                seeds.append(Path(target))
    (notices / 'libraries.json').write_text(json.dumps(libraries, indent=2))
    for path in app.rglob('*'):
        if path.is_file() and path.suffix in ('.exe', '.dll', '.def', '.o'):
            path.unlink()
    print(f'Prepared Linux release: {app}')


if __name__ == '__main__':
    main()
