import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import check_dependencies as checks


class DependencyRequirementsTests(unittest.TestCase):
    def test_file_preflight_checks_nested_includes_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / 'plugins/example'
            plugin.mkdir(parents=True)
            (root / 'requirements.txt').write_text('-r plugins/example/requirements.txt\n')
            with self.assertRaises(FileNotFoundError):
                checks.requirement_files(root)
            (plugin / 'requirements.txt').write_text('-r extra.txt\n')
            (plugin / 'extra.txt').write_text('numpy>=1.24\n')
            self.assertEqual(len(checks.requirement_files(root)), 3)
            (root / 'requirements.txt').write_text('')
            with self.assertRaisesRegex(ValueError, 'not included'):
                checks.requirement_files(root)

    def test_dependency_regression_test_is_shipped(self):
        import mkdist
        root = Path(__file__).resolve().parents[1]
        files, _ = mkdist.read_manifest(root, root / 'release_manifest.json')
        shipped = {target for _, target in files}
        self.assertIn('tests/test_dependency_requirements.py', shipped)
        for path in checks.requirement_files(root):
            self.assertIn(path.relative_to(root).as_posix(), shipped)

    def test_missing_plugin_and_incompatible_transitive_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / 'plugins/speech'
            plugin.mkdir(parents=True)
            (root / 'requirements.txt').write_text('-r plugins/speech/requirements.txt\n')
            (plugin / 'requirements.txt').write_text('faster-whisper>=1.2,<2\n')
            dependency = SimpleNamespace(metadata={'Name': 'faster-whisper'},
                                         requires=['av<17', 'absent; extra == "test"'])
            def installed(name):
                if name == 'av':
                    return '17.0'
                raise PackageNotFoundError(name)
            with patch.object(checks, 'version', side_effect=installed), \
                    patch.object(checks, 'distributions', return_value=[dependency]):
                errors = checks.requirement_errors(root)
            self.assertEqual(len(errors), 2, errors)
            self.assertIn('missing faster-whisper', errors[0])
            self.assertIn('installed 17.0', errors[1])

    def test_all_plugin_files_must_be_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / 'plugins/new_plugin'
            plugin.mkdir(parents=True)
            (root / 'requirements.txt').write_text('')
            (plugin / 'requirements.txt').write_text('numpy')
            with patch.object(checks, 'distributions', return_value=[]):
                errors = checks.requirement_errors(root)
            self.assertEqual(len(errors), 1)
            self.assertIn('not included', errors[0])


if __name__ == '__main__':
    unittest.main()
