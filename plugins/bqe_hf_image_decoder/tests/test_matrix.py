"""Compare the native BQE inverter against NumPy and Wiener covariances.

Set BQE_MATRIX_LIBRARY to a DLL/.so compiled from bqe_matrix.c, or install a
C compiler to build a temporary shared library automatically on Linux.
"""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

LIMIT = 208
SOURCE = Path(__file__).resolve().parents[1] / 'native/rxamadrm/bqe_matrix.c'


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        library = os.environ.get('BQE_MATRIX_LIBRARY')
        if not library:
            compiler = shutil.which('cc')
            if os.name == 'nt' or not compiler:
                raise unittest.SkipTest('Set BQE_MATRIX_LIBRARY to a compiled native test library')
            cls.temp = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.temp.cleanup)
            library = str(Path(cls.temp.name) / 'matrix.so')
            subprocess.run([compiler, '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-shared', '-fPIC', str(SOURCE), '-lm', '-o', library], check=True)
        cls.library = ctypes.CDLL(str(Path(library).resolve()))
        cls.inverse = cls.library.bqe_matrix_inverse
        pointer = np.ctypeslib.ndpointer(dtype=np.float32, flags='C_CONTIGUOUS')
        cls.inverse.argtypes = [ctypes.c_int, pointer, pointer]
        cls.inverse.restype = ctypes.c_int

    def check_inverse(self, matrix, alias=False):
        n = len(matrix)
        source = np.zeros((LIMIT, LIMIT), dtype=np.float32)
        source[:n, :n] = matrix
        original = source.copy()
        output = source if alias else np.full_like(source, 123)
        self.assertEqual(self.inverse(n, source, output), 0)
        reference = np.linalg.inv(original[:n, :n].astype(np.float64))
        np.testing.assert_allclose(output[:n, :n], reference, rtol=2e-5, atol=2e-5)
        np.testing.assert_allclose(original[:n, :n].astype(np.float64) @ output[:n, :n],
                                   np.eye(n), rtol=0, atol=3e-4)
        if not alias:
            np.testing.assert_array_equal(source, original)
            self.assertTrue(np.all(output[n:, :] == 123))
            self.assertTrue(np.all(output[:n, n:] == 123))

    def test_pivoting_aliasing_and_row_scaling(self):
        self.check_inverse([[0, 2, 1], [1, 0, 3], [4, 1, 0]])
        self.check_inverse([[0, 1], [1, 0]], alias=True)
        self.check_inverse(np.diag([1e-20, 1e20]))

    def test_random_positive_definite_matrices_through_maximum_size(self):
        rng = np.random.default_rng(31)
        for n in (1, 2, 8, 64, LIMIT):
            with self.subTest(n=n):
                x = rng.normal(size=(n, n))
                self.check_inverse(x @ x.T + np.eye(n))

    def test_wiener_filter_covariances(self):
        # Same sinc-product covariance family as the demodulator, with
        # pilot-energy-dependent diagonal noise and multiple window sizes.
        for n in (12, 48, LIMIT):
            for noise in (0.001, 0.1):
                with self.subTest(n=n, noise=noise):
                    frequency = np.arange(n) % 26
                    time = np.arange(n) // 26
                    matrix = (np.sinc((frequency[:, None] - frequency) * 0.15) *
                              np.sinc((time[:, None] - time) * 0.2))
                    matrix += np.diag(noise * 2 / np.where(np.arange(n) % 3, 2, 4))
                    self.check_inverse(matrix)

    def test_invalid_inputs_leave_output_unchanged(self):
        cases = [np.zeros((2, 2)), np.ones((2, 2)), [[1, 0], [0, np.nan]],
                 [[1, 0], [0, np.inf]], [[np.nextafter(np.float32(0), np.float32(1))]]]
        for matrix in cases:
            n = len(matrix)
            source = np.zeros((LIMIT, LIMIT), dtype=np.float32)
            source[:n, :n] = matrix
            output = np.full_like(source, 123)
            self.assertNotEqual(self.inverse(n, source, output), 0)
            self.assertTrue(np.all(output == 123))
        for n in (0, -1, LIMIT + 1):
            self.assertNotEqual(self.inverse(n, source, output), 0)
            self.assertTrue(np.all(output == 123))


if __name__ == '__main__':
    unittest.main()
