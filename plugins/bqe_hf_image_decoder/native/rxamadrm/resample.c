
/*
*
*    File resample.c
*
*    source filtering to adjust datasampling period
*    of soundsystem
*
*    Coded after resample.cpp
*    from dreamsoftware By Folker Fischer
*
*    Author M.Bos - PA0MBO
*
*    Date Feb 21st 2009
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
#include <stdlib.h>
#include <math.h>
#include "ResampleFilter.h"
extern int input_samples_buffer_request;
int
resample(float *prInput, /*@out@ */ float *prOutput, float rRation,
	 int iNBlockSize, int init)
{
  static int iHistorySize;
  static int rBlockDuration;
  int i, im;
  static float rtOut;
  static float *tmpbuf;
  int ik, ip1, ip2, in1, in2;
  static float rTStep, ry1, ry2, rxInt;

  if (init == 1)

    {
      iHistorySize = RES_FILT_NUM_TAPS_PER_PHASE + 1;
      tmpbuf = malloc((iNBlockSize + iHistorySize) * sizeof(float));
      if (tmpbuf == NULL)

	{
	  printf("CANNOT MALLOC TMPBUF IN RESAMPLE\n");
	  exit(EXIT_FAILURE);
	}
      rBlockDuration =
	(iNBlockSize + RES_FILT_NUM_TAPS_PER_PHASE) * INTERP_DECIM_I_D;
      rtOut = (float) (RES_FILT_NUM_TAPS_PER_PHASE * INTERP_DECIM_I_D);
      return (0);
    }

  /*  move old data to history part of buffer and shift_in  new data */
 /*  printf("in resample iHistorySize is %d iBlocksize %d \n", iHistorySize, iNBlockSize); */
  for (i = 0; i < iHistorySize; i++)
    tmpbuf[i] = tmpbuf[i + iNBlockSize];
  for (i = 0; i < iNBlockSize; i++)
    tmpbuf[i + iHistorySize] = prInput[i];
  rTStep = ((float) INTERP_DECIM_I_D) / rRation;

  /* printf("rTStep is %g rRation is %g\n", rTStep, rRation); */
  im = 0;
  if (input_samples_buffer_request == 0)	/* no resampling needed, just copy */

    {
      im = iNBlockSize;
      for (i = 0; i < iNBlockSize; i++)
	prOutput[i] = prInput[i];
      return (im);
    }

  /* main loop */
  do

    {
      ik = (int) rtOut;
      ip1 = ik % INTERP_DECIM_I_D;
      ip2 = (ik + 1) % INTERP_DECIM_I_D;

      /* SAMPLE POSITIONS IN INPUT VECTOR */
      in1 = (int) (ik / INTERP_DECIM_I_D);
      in2 = (int) ((ik + 1) / INTERP_DECIM_I_D);
      ry1 = 0.0;
      ry2 = 0.0;
      for (i = 0; i < RES_FILT_NUM_TAPS_PER_PHASE; i++)

	{
	  ry1 += fResTaps1To1[ip1][i] * tmpbuf[in1 - i];
	  ry2 += fResTaps1To1[ip2][i] * tmpbuf[in2 - i];
	}

      /* lin interpolation */
      rxInt = rtOut - (int) rtOut;
      prOutput[im] = (ry2 - ry1) * rxInt + ry1;
      im++;
      rtOut += rTStep;
    }
  while (rtOut < (float) rBlockDuration);
  rtOut -= iNBlockSize * INTERP_DECIM_I_D;
  return (im);
}
