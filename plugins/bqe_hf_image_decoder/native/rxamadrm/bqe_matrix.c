/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Authored for BQE Wisp, 2026. No Numerical Recipes code is used here.
 * Standard Gauss-Jordan elimination of the augmented matrix [A | I],
 * with row scaling, partial pivoting and double-precision working storage.
 */
#include "bqe_matrix.h"
#include <float.h>
#include <math.h>
#include <stdlib.h>

int bqe_matrix_inverse(int n,
                       const float input[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT],
                       float output[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT])
{
    if (n < 1 || n > BQE_MATRIX_LIMIT || !input || !output)
        return 1;
    const int width = 2 * n;
    double *work = calloc((size_t)n * width, sizeof(*work));
    if (!work)
        return 2;
    int result = 3;
    for (int row = 0; row < n; ++row) {
        double scale = 0;
        for (int col = 0; col < n; ++col) {
            if (!isfinite(input[row][col]))
                goto done;
            scale = fmax(scale, fabs((double)input[row][col]));
        }
        if (scale == 0)
            goto done;
        for (int col = 0; col < n; ++col)
            work[row * width + col] = input[row][col] / scale;
        work[row * width + n + row] = 1.0 / scale;
    }
    for (int col = 0; col < n; ++col) {
        int pivot = col;
        for (int row = col + 1; row < n; ++row)
            if (fabs(work[row * width + col]) > fabs(work[pivot * width + col]))
                pivot = row;
        if (fabs(work[pivot * width + col]) <= 8 * n * DBL_EPSILON)
            goto done;
        if (pivot != col) {
            for (int k = 0; k < width; ++k) {
                double saved = work[col * width + k];
                work[col * width + k] = work[pivot * width + k];
                work[pivot * width + k] = saved;
            }
        }
        double diagonal = work[col * width + col];
        for (int k = 0; k < width; ++k)
            work[col * width + k] /= diagonal;
        work[col * width + col] = 1;
        for (int row = 0; row < n; ++row) {
            if (row == col)
                continue;
            double factor = work[row * width + col];
            for (int k = 0; k < width; ++k)
                work[row * width + k] -= factor * work[col * width + k];
            work[row * width + col] = 0;
        }
    }
    for (int row = 0; row < n; ++row)
        for (int col = 0; col < n; ++col) {
            double value = work[row * width + n + col];
            if (!isfinite(value) || fabs(value) > FLT_MAX)
                goto done;
        }
    for (int row = 0; row < n; ++row)
        for (int col = 0; col < n; ++col)
            output[row][col] = (float)work[row * width + n + col];
    result = 0;
done:
    free(work);
    return result;
}
