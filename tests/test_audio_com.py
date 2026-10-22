"""COM lifetime regressions independent of installed decoder packages."""
import ctypes
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from plugins.audio_devices import audio_com


class AudioComTests(unittest.TestCase):
    def test_balances_success_even_when_capture_fails(self):
        for result in (0, 1):
            ole = Mock()
            ole.CoInitializeEx.return_value = result
            with patch('plugins.audio_devices.sys.platform', 'win32'), \
                    patch('plugins.audio_devices.ctypes.WinDLL', return_value=ole, create=True):
                with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                    with audio_com():
                        raise RuntimeError('capture failed')
            ole.CoUninitialize.assert_called_once_with()

    def test_existing_apartment_is_not_uninitialized(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = -2147417850  # RPC_E_CHANGED_MODE
        with patch('plugins.audio_devices.sys.platform', 'win32'), \
                patch('plugins.audio_devices.ctypes.WinDLL', return_value=ole, create=True):
            with audio_com():
                pass
        ole.CoUninitialize.assert_not_called()

    def test_failure_is_reported_without_uninitializing(self):
        ole = Mock()
        ole.CoInitializeEx.return_value = -2147024882  # E_OUTOFMEMORY
        with patch('plugins.audio_devices.sys.platform', 'win32'), \
                patch('plugins.audio_devices.ctypes.WinDLL', return_value=ole, create=True):
            with self.assertRaisesRegex(OSError, '0x8007000E'):
                with audio_com():
                    self.fail('Must not start capture')
        ole.CoUninitialize.assert_not_called()

    def test_non_windows_is_noop(self):
        with patch('plugins.audio_devices.sys.platform', 'linux'), \
                patch('plugins.audio_devices.ctypes.WinDLL', create=True) as loader:
            with audio_com():
                pass
        loader.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows COM required')
    def test_real_windows_worker_lifetime(self):
        results = []
        def worker():
            ole = ctypes.WinDLL('ole32')
            ole.CoGetApartmentType.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
            ole.CoGetApartmentType.restype = ctypes.c_int32
            def apartment():
                kind, qualifier = ctypes.c_int(), ctypes.c_int()
                return ole.CoGetApartmentType(ctypes.byref(kind), ctypes.byref(qualifier)) & 0xffffffff
            results.append(apartment())
            with audio_com():
                results.append(apartment())
            results.append(apartment())
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [0x800401F0, 0, 0x800401F0])


if __name__ == '__main__':
    unittest.main()
