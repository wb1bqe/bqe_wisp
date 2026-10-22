
/*
*    File filter1.c
*
* implements filter routine a la MATLAB
* for real data in vector sigin[]
*
* PA0MBO - M.Bos
* Date Feb 21st 2009
*
* result is stored in y[]
* nx is number of elements in input (sigin) and mih is length of
* the filter vector h[]
*
*/


/*************************************************************************
*
*                           PA0MBO
*
*    COPYRIGHT (C)  2009  M.Bos 
*
*    This file is part of the distribution package RXAMADRM
*
*    This package is free software and you can redistribute is
*    and/or modify it under the terms of the GNU General Public License
*
*    More details can be found in the accompanying file COPYING
*************************************************************************/


#include <stdio.h>
void filter1(float *sigin, float *y, float *h, int nx, int mh)
{
  int i, j;

  for (i = 0; i < nx; i++)

    {
      y[i] = 0.0;
      for (j = 0; ((j <= i) && (j < mh)); j++)

	{
	  y[i] += h[j] * sigin[i - j];
	}
    }
}
