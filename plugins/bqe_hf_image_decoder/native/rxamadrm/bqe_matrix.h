/* SPDX-License-Identifier: GPL-3.0-or-later */
/* BQE matrix inversion interface, authored for BQE Wisp in 2026. */
#ifndef BQE_MATRIX_H
#define BQE_MATRIX_H

#define BQE_MATRIX_LIMIT 208

/* Invert the leading n-by-n block. Input and output have 208-float rows.
 * Returns zero on success; nonzero for invalid/nonfinite input, allocation
 * failure, numerically singular input, or a result outside float range.
 * Input is preserved and output is unchanged on failure. Aliasing is allowed.
 */
int bqe_matrix_inverse(int n,
                       const float input[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT],
                       float output[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT]);

#endif
