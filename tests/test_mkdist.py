import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import mkdist
from mkdist import build, digest


class DistributionTests(unittest.TestCase):
    def test_hamdrm_release_excludes_numerical_recipes(self):
        root = Path(__file__).resolve().parents[1]
        files, _ = mkdist.read_manifest(root, root / 'release_manifest.json')
        shipped = {target for _, target in files}
        prefix = 'plugins/bqe_hf_image_decoder/native/rxamadrm/'
        for name in ('ludcmp.c', 'lubksb.c', 'nrutil.c', 'nrutil.h', 'nr.h'):
            self.assertNotIn(prefix + name, shipped)
            self.assertFalse((root / prefix / name).exists())
        self.assertIn(prefix + 'bqe_matrix.c', shipped)
        self.assertIn(prefix + 'bqe_matrix.h', shipped)
        sources = (root / prefix / 'sources.txt').read_text().split()
        self.assertIn('bqe_matrix.c', sources)
        self.assertFalse({'ludcmp.c', 'lubksb.c', 'nrutil.c'} & set(sources))

    def test_current_release_ssdv_provenance_and_external_satdump(self):
        root = Path(__file__).resolve().parents[1]
        files, _ = mkdist.read_manifest(root, root / 'release_manifest.json')
        shipped = {target for _, target in files}
        self.assertIn('presets/2m_ssb_preset.yaml', shipped)
        self.assertFalse(any('/vendor/satdump/' in name.lower() or
                             Path(name).name.lower() in ('satdump', 'satdump.exe')
                             for name in shipped))
        mkdist.verify_ssdv_bundle(files)
        changed = self.root / 'modified-ssdv.exe'
        changed.write_bytes(b'unverified executable')
        replaced = [(changed if target.endswith('/ssdv.exe') else source, target)
                    for source, target in files]
        with self.assertRaisesRegex(ValueError, 'executable hash mismatch'):
            mkdist.verify_ssdv_bundle(replaced)
        changed_source = self.root / 'modified-ssdv.c'
        changed_source.write_bytes(b'unverified source')
        replaced = [(changed_source if target.endswith('/ssdv.c') else source, target)
                    for source, target in files]
        with self.assertRaisesRegex(ValueError, 'pinned source mismatch'):
            mkdist.verify_ssdv_bundle(replaced)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'code.py').write_text('print("hello")\n')
        (self.root / 'private.log').write_text('do not ship')
        self.manifest = self.root / 'release_manifest.json'

    def write(self, files, directories=None):
        self.manifest.write_text(json.dumps(dict(version=1, files=files, directories=directories or [])))

    def test_exact_copy_mapping_hashes_and_no_overwrite(self):
        self.write([dict(source='code.py', destination='app/main.py')], ['logs'])
        self.assertEqual(build(self.root, self.manifest, check=True), 1)
        output = build(self.root, self.manifest, '20261001')
        self.assertEqual(sorted(p.relative_to(output/'bqe_wisp').as_posix() for p in (output/'bqe_wisp').rglob('*') if p.is_file()), ['app/main.py'])
        self.assertTrue((output/'bqe_wisp/logs').is_dir())
        info = json.loads((output/'DISTRIBUTION.json').read_text())
        self.assertEqual(info['files'][0]['sha256'], digest(self.root/'code.py'))
        with self.assertRaises(FileExistsError): build(self.root, self.manifest, '20261001')

    def test_invalid_manifests_leave_no_output(self):
        for files in [['missing.py'], [dict(source='code.py', destination='../escape')],
                      ['code.py', 'code.py'], ['code.py', dict(source='code.py', destination='code.py/child')]]:
            self.write(files)
            with self.assertRaises(ValueError): build(self.root, self.manifest, '20261001')
            self.assertFalse((self.root/'dist_20261001').exists())
        self.assertFalse(list(self.root.glob('.dist-build-*')))

    def test_custom_directory_with_spaces_preserves_sources(self):
        self.write(['code.py'])
        original = digest(self.root / 'code.py')
        destination = self.root / 'Releases with spaces' / 'Beta 1'
        output = build(self.root, self.manifest, output_dir=destination)
        self.assertEqual(output, destination)
        self.assertEqual(digest(output / 'bqe_wisp/code.py'), original)
        self.assertEqual(digest(self.root / 'code.py'), original)
        with self.assertRaises(FileExistsError):
            build(self.root, self.manifest, output_dir=destination)
        self.assertFalse(list(destination.parent.glob('.dist-build-*')))

    def test_failed_copy_does_not_publish_or_remove_other_files(self):
        self.write(['code.py'])
        destination = self.root / 'Beta 2'
        with patch.object(mkdist.shutil, 'copy2', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                build(self.root, self.manifest, output_dir=destination)
        self.assertFalse(destination.exists())
        self.assertTrue((self.root / 'code.py').is_file())
        self.assertFalse(list(self.root.glob('.dist-build-*')))

    def test_cli_source_only_and_check_do_not_launch_builders(self):
        self.write(['code.py'])
        destination = self.root / 'CLI quoted path'
        with patch.object(mkdist, 'ROOT', self.root), patch.object(mkdist, 'prepare_builders') as prepare:
            mkdist.main(['--manifest', str(self.manifest), '--output-dir', str(destination), '--check'])
            self.assertFalse(destination.exists())
            mkdist.main(['--manifest', str(self.manifest), '--output-dir', str(destination), '--source-only'])
            prepare.assert_not_called()
            self.assertTrue((destination / 'bqe_wisp/code.py').is_file())

    def test_platform_dispatch_uses_quoted_path_arguments(self):
        release = self.root / 'Executable Release'
        guide = release / 'bqe_wisp/documentation/BQE_Wisp_Quick_Start_Guide.pdf'
        guide.parent.mkdir(parents=True)
        guide.write_bytes(b'PDF guide')
        for platform, name in [('windows', 'BQE_Wisp.exe'), ('linux', 'BQE_Wisp')]:
            (release / f'bqe_wisp_{platform}').mkdir(parents=True)
            (release / f'bqe_wisp_{platform}' / name).write_bytes(b'executable')
        class Host:
            work_root = '/Linux Work'
            def __init__(self): self.calls = []
            def path(self, path): return '/linux/' + Path(path).as_posix()
            def run(self, args): self.calls.append(args)
        host = Host()
        with patch.object(mkdist, 'command') as run:
            result = mkdist.executables(self.root, release, [
                ('windows', 'Windows Python', 'Windows Runtime'),
                ('linux', host, '/Linux Python', '/Linux Runtime', '/Codec2 Source')])
        args = run.call_args.args[0]
        self.assertEqual(args[0], 'Windows Python')
        self.assertEqual(args[2], release / 'bqe_wisp')
        self.assertEqual(args[-1], release / 'bqe_wisp_windows')
        linux_args = host.calls[0]
        self.assertEqual(linux_args[linux_args.index('--output') + 1],
                         host.path(release / 'bqe_wisp_linux'))
        self.assertIn('/Linux Runtime', host.calls[0])
        self.assertIn('/Codec2 Source', host.calls[0])
        self.assertEqual(result, [release / 'bqe_wisp_windows/BQE_Wisp.exe',
                                  release / 'bqe_wisp_linux/BQE_Wisp'])
        for executable in result:
            with zipfile.ZipFile(release / ('BQE_Wisp_windows.zip' if executable.suffix == '.exe' else 'BQE_Wisp_linux.zip')) as archive:
                self.assertEqual(set(archive.namelist()), {executable.name, guide.name})
                self.assertEqual(archive.read(executable.name), b'executable')
                self.assertEqual(archive.read(guide.name), b'PDF guide')
                self.assertIsNone(archive.testzip())
                if executable.name == 'BQE_Wisp':
                    self.assertEqual(archive.getinfo(executable.name).external_attr >> 16, 0o100755)

    def test_flat_image_zip_validation_and_no_overwrite(self):
        output = self.root / 'bqe_wisp_windows'
        output.mkdir()
        executable = output / 'BQE_Wisp.exe'
        executable.write_bytes(b'executable')
        with self.assertRaises(FileNotFoundError):
            mkdist.image_zip(self.root, executable)
        guide = self.root / 'bqe_wisp/documentation/BQE_Wisp_Quick_Start_Guide.pdf'
        guide.parent.mkdir(parents=True)
        guide.write_bytes(b'guide')
        nested = output / 'metadata'
        nested.mkdir()
        (nested / 'BQE_Wisp.build.json').write_bytes(b'{}')
        archive_path = mkdist.image_zip(self.root, executable)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn('BQE_Wisp.build.json', archive.namelist())
            self.assertTrue(all('/' not in name for name in archive.namelist()))
        self.assertEqual(archive_path.name, 'BQE_Wisp_windows.zip')
        before = archive_path.read_bytes()
        with self.assertRaises(FileExistsError):
            mkdist.image_zip(self.root, executable)
        self.assertEqual(archive_path.read_bytes(), before)
        (nested / 'BQE_Wisp.exe').write_bytes(b'duplicate')
        with self.assertRaisesRegex(ValueError, 'Duplicate filename'):
            mkdist.image_zip(self.root, executable)

    def test_linux_host_wsl_path_conversion(self):
        with patch.object(mkdist.sys, 'platform', 'win32'), patch.object(mkdist, 'command', return_value='/mnt/c/BQE Data') as run:
            host = mkdist.LinuxHost('Ubuntu-20.04')
            self.assertEqual(host.path(self.root), '/mnt/c/BQE Data')
            self.assertEqual(run.call_args.args[0][:6],
                             ['wsl.exe', '--distribution', 'Ubuntu-20.04', '--exec', 'wslpath', '-a'])

    def test_windows_cannot_be_built_on_linux(self):
        with patch.object(mkdist.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ValueError, 'Windows executables must be built on Windows'):
                mkdist.prepare_builders(self.root, SimpleNamespace(target='both'))

    def test_release_satellites_preserve_working_bytes(self):
        # Use the real release mappings so substituting a neutral satellite
        # file in the allowlist cannot silently bypass this regression.
        data = json.loads((Path(__file__).resolve().parents[1] / 'release_manifest.json').read_text())
        targets = {'bqe_config/satellites.yaml', 'templates/satellites_template.yaml'}
        entries = [entry for entry in data['files']
                   if (entry if isinstance(entry, str) else entry['destination']) in targets]
        self.assertEqual(len(entries), 2)
        contents = b'# Keep station choices and comments\r\nSAT:\r\n  auto_schedule: true\r\n  path: C:\\station\\images\r\n'
        for target in targets:
            path = self.root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents + ('# ' + target).encode())
        neutral = self.root / 'release_defaults/bqe_config/satellites.yaml'
        neutral.parent.mkdir(parents=True)
        neutral.write_text('SAT:\n  auto_schedule: false\n')
        self.write(entries)
        output = build(self.root, self.manifest, '20261003')
        for target in targets:
            expected = contents + ('# ' + target).encode()
            self.assertEqual((output / 'bqe_wisp' / target).read_bytes(), expected)
            self.assertEqual((self.root / target).read_bytes(), expected)


if __name__ == '__main__':
    unittest.main()
