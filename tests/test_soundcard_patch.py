"""Exercise the real installed 0.4.6 COM class without loading audio DLLs."""
import ast
import builtins
from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import patch_soundcard as fix

ROOT = Path(__file__).resolve().parents[1]


class SoundCardPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = distribution('SoundCard')
        assert package.version == '0.4.6', 'Tests require the project-pinned SoundCard'
        cls.source = Path(package.locate_file('soundcard/mediafoundation.py')).read_text(encoding='utf-8')
        # Exercise pristine source even on environments already patched by setup.
        cls.source = cls.source.replace('        self.com_loaded = False\n        COINIT_MULTITHREADED',
                                        '        COINIT_MULTITHREADED')
        cls.source = cls.source.replace(
            '            if hr not in (0, 1):  # S_OK and S_FALSE both require CoUninitialize.\n'
            '                self.check_error(hr)\n', '            self.check_error(hr)\n')

    def com_class(self, result, windows='10', error=None):
        tree = ast.parse(fix.patched_source(self.source))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == '_COMLibrary')
        ole = Mock()
        ole.CoInitialize.return_value = ole.CoInitializeEx.return_value = result
        ole.CoInitialize.side_effect = ole.CoInitializeEx.side_effect = error
        namespace = dict(_ole32=ole, _ffi=SimpleNamespace(NULL=None),
                         platform=SimpleNamespace(win32_ver=lambda: (windows,)))
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<SoundCard COM>', 'exec'), namespace)
        return namespace['_COMLibrary'], ole

    def test_success_balanced_for_both_windows_paths(self):
        for windows in ('8', '10'):
            for result in (0, 1):
                with self.subTest(windows=windows, result=result):
                    cls, ole = self.com_class(result, windows)
                    obj = cls()
                    self.assertTrue(obj.com_loaded)
                    del obj
                    ole.CoUninitialize.assert_called_once_with()
                    if windows == '8':
                        ole.CoInitialize.assert_called_once_with(None)
                    else:
                        ole.CoInitializeEx.assert_called_once_with(None, 0)

    def test_changed_mode_does_not_uninitialize(self):
        cls, ole = self.com_class(-2147417850)
        obj = cls()
        self.assertFalse(obj.com_loaded)
        del obj
        ole.CoUninitialize.assert_not_called()

    def test_failed_construction_has_safe_destructor(self):
        for result, error in ((-2147024882, None), (0, OSError('DLL failure'))):
            cls, ole = self.com_class(result, error=error)
            obj = cls.__new__(cls)
            with self.assertRaises((RuntimeError, OSError)):
                obj.__init__()
            self.assertFalse(obj.com_loaded)
            del obj
            ole.CoUninitialize.assert_not_called()

    def test_patch_is_idempotent_and_preserves_other_source(self):
        changed = fix.patched_source(self.source)
        self.assertEqual(fix.patched_source(changed), changed)
        self.assertEqual(changed.count('self.com_loaded = False'), self.source.count('self.com_loaded = False') + 1)
        self.assertEqual(changed.split('    def __del__')[1], self.source.split('    def __del__')[1])
        with self.assertRaises(RuntimeError):
            fix.patched_source(self.source.replace('self.check_error(hr)', 'self.check_error(other)'))

    def test_install_step_preserves_crlf_and_reruns(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'mediafoundation.py'
            target.write_bytes(self.source.replace('\n', '\r\n').encode())
            package = SimpleNamespace(version='0.4.6', locate_file=lambda name: target)
            with patch.object(fix.sys, 'platform', 'win32'), patch.object(fix, 'distribution', return_value=package):
                self.assertTrue(fix.apply_patch())
                first = target.read_bytes()
                self.assertFalse(fix.apply_patch())
                self.assertEqual(target.read_bytes(), first)
                self.assertNotIn(b'\n', first.replace(b'\r\n', b''))
                package.version = '0.4.7'
                with self.assertRaisesRegex(RuntimeError, 'Review COM patch'):
                    fix.apply_patch()
                self.assertEqual(target.read_bytes(), first)

    def test_non_windows_does_not_inspect_or_modify_package(self):
        with patch.object(fix.sys, 'platform', 'linux'), patch.object(fix, 'distribution') as lookup:
            self.assertFalse(fix.apply_patch())
        lookup.assert_not_called()

    def test_tracker_final_exit_without_site_helpers(self):
        tree = ast.parse((ROOT / 'bqe_track_continuously.py').read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        final = main.body[-1]
        code = ast.unparse(final)
        run = subprocess.run([sys.executable, '-S', '-c',
                              'import sys, builtins; assert not hasattr(builtins, "exit"); ' + code],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == '__main__':
    unittest.main()
