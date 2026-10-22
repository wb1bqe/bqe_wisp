/*
*     File drmtst.c
*     
*     test program to drive subroutines used
*     in drm receiver
*
*     PA0MBO  - M.BOS
*     Date May 8th 2012
*
*/

/*************************************************************************
*
*                           PA0MBO
*
*    COPYRIGHT (C)  2012  M.Bos 
*
*    This file is part of the distribution package RXAMADRM
*
*    This package is free software and you can redistribute is
*    and/or modify it under the terms of the GNU General Public License
*
*    More details can be found in the accompanying file COPYING
*************************************************************************/


#include <stdio.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
#include <stdlib.h>
#include <math.h>
#include "drm.h"
#include "drmproto.h"
#include "drmdefs.h"

int monorec(int nr_samps, long *bufaucnt);

int main(void)
{
  #ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  #endif
  setvbuf(stdout, NULL, _IOLBF, 0);
  int i, n;
  int im;
  float rRation = 1.0;
  float resamp_signal[2 * BUFSIZE];
  FILE *fplowpass;
  int resread;

  if (  ( fplowpass = fopen("lowpass.dat", "r"))==NULL)
  {
       printf("Cannot open lowpass.dat \n");
       exit(1);
  }

  for (i=0; i < LP_LENGTH  ; i++)
  {
      fscanf(fplowpass, "%g", &subfilt1[i]);
      fscanf(fplowpass, "%g", &subfilt2[i]);
      fscanf(fplowpass, "%g", &subfilt3[i]);
      resread =  fscanf(fplowpass, "%g", &subfilt4[i]);
  }
  if (resread != 1 )
  {
     printf("failed to read lowpass data \n");
     exit(1);
  }

  /* init monorec filters and filterstates */
  for (i=0 ; i < 24; i++)
  {
      Zifilt1d[i] = 0.0;
      Zifilt2d[i] = 0.0;
      Zifilt3d[i] = 0.0;
      Zifilt4d[i] = 0.0;
      Zifilt1uI[i]= 0.0;
      Zifilt2uI[i]= 0.0;
      Zifilt3uI[i]= 0.0;
      Zifilt4uI[i]= 0.0;
      Zifilt1uQ[i]= 0.0;
      Zifilt2uQ[i]= 0.0;
      Zifilt3uQ[i]= 0.0;
      Zifilt4uQ[i]= 0.0;
  }
  n = BUFSIZE;

  /* initialisations */
  /* init_waterfall(); */                  /* init the waterfall window */


  (void) demodulate(NULL, n, 1);	/* init static vars in demodulate */
  getmode(NULL, n / 4, NULL, 1);


  

/*     getofdm(NULL, 0.0, 0.0, 0.0, 320, 288, NULL, NULL, 1, NULL); 
     getofdmsync(NULL, 320, 288 , NULL, 200, 0,  NULL,
                 NULL, 1);  */
  rRation = 1.000;
  samplerate_offset_estimation = 0.0;
  im = resample(acq_signal, resamp_signal, rRation, n, 1);
  runstate = -2;		/* POWER_ON */
  channel_decoding();
  source_decoding();
  runstate = -1;		/* INIT */
  channel_decoding();
  source_decoding();
  runstate = 0;			/* FIRSTRUN */
  source_decoding();
  runstate = 1;			/* NORMAL RUN */
  i = 0;
  while (1 == 1)

    {
      if ((inpbufwidx == 0) && (input_samples_buffer_request > 0)) 
      { 
	n = monorec(input_samples_buffer_request, bufaucnt);
        printf("na monorec \n");
      }
      else
	{
	  n = input_samples_buffer_request;
	  printf("else clause input_samples_buffer_request is %d inpbufwidx %d\n", n, inpbufwidx);
	}
      rRation = (float) (1.0 / (1.0 + samplerate_offset_estimation));
      im = resample(acq_signal, resamp_signal, rRation, n, 0);
      /* printf(" im = %d runstate %d\n", im, runstate); */
      if (demodulate(resamp_signal, im, 0) != 0)
          return EXIT_FAILURE;
      channel_decoding();
      i++;
      source_decoding();
      printf("BQE_BLOCK\n"); fflush(stdout);
    } return (0);
}
