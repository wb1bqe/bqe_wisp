/*
*  file:   monorec.c 
*
* record signal from audio system ALSA
*
* PA0MBO  - M.Bos  
* Date Feb 21st 2009
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



#include "drmdefs.h"

#define PI  (4*atan(1.000))
#define MAX_FFT 2.0E6

extern float acq_signal[BUFSIZE];
extern int emptyptr;
extern short int tmpbuf[100000];
extern volatile int bufcnt;
extern int bufsize;
extern int input_samples_buffer_request;
extern int inpbufwidx;
extern float powerspectrum[1024];





extern unsigned long pixels_waterfall[300] ;
extern int width_waterfall, height_waterfall;
extern int screen_num;
extern float subfilt1[24];
extern float subfilt2[24];
extern float subfilt3[24];
extern float subfilt4[24];
extern float Zifilt1d[24] ;
extern float Zifilt2d[24] ;
extern float Zifilt3d[24] ;
extern float Zifilt4d[24] ;
extern float Zifilt1uI[24] ;
extern float Zifilt2uI[24] ;
extern float Zifilt3uI[24] ;
extern float Zifilt4uI[24] ;
extern float Zifilt1uQ[24] ;
extern float Zifilt2uQ[24] ;
extern float Zifilt3uQ[24] ;
extern float Zifilt4uQ[24] ;

void filter(float *, float *, float *, float *, int, int);
int gen_power(float *, float *, int);

int monorec(int nr_samps, /*@out@ */ long *bufaucnt)
{
  static int cnt = 0;
  float temp[BUFSIZE];
  float temp4[BUFSIZE / 4];
  int flag ;
  float fft_strength;
  unsigned long pixvalue;
  int chunks, remaining, j;


  static float B_Inphase[83] =
    { -0.000013096429498, 0.000000663887755, -0.000069635287780,
-0.000011931707517, -0.000144533153108, -0.000025373980217,
    -0.000391789228659, -0.000232426477943, -0.000478143393960,
      -0.000487558968789, -0.000685650336825, -0.001354704880460,
      -0.000546201344479,
    -0.002342397998132, -0.000346673289958, -0.004288405547670,
      0.000076514567549, -0.006214057213030, 0.000471140764452,
      -0.008869259301553,
    0.000310928936475, -0.010853542810835, -0.000618723760850,
      -0.012558669622822, -0.003528318293013, -0.012565638823234,
      -0.008458796942648,
    -0.010998473138919, -0.016909533624041, -0.007029340421051,
      -0.028648813220546, -0.000916074544044, -0.045239295406484,
      0.007126861010101,
    -0.067146558987974, 0.016470713456071, -0.099200587662524,
      0.025927955112860, -0.157545675961710, 0.034759206835915,
      -0.403756659009051,
    0.872044314623357, 0.298152956415124, 0.045762401414113,
      0.057682372386628, 0.046612304642337, 0.009812557897304,
      0.044738697480695,
    -0.007877026958678, 0.039793922738241, -0.014019542011307,
      0.033363547774391, -0.014228247558443, 0.025586601071249,
      -0.011722710151015,
    0.018169441690637, -0.007774072820479, 0.011227152592688,
      -0.004254206945683, 0.005868718139545, -0.001002308783387,
      0.001901375136305,
    0.000729336127630, -0.000472570900136, 0.001969539594901,
      -0.001560255803054, 0.001905716822635, -0.001878235948282,
      0.001813958429404,
    -0.001475079548897, 0.001117144708583, -0.001143142395249,
      0.000739068897549, -0.000539580485663, 0.000277728151810,
      -0.000341625345467,
    0.000100860733753, -0.000052333537017, 0.000001973643242,
      -0.000040013168559, -0.000013316614262, 0.000006340573991,
      -0.000004973750149
  };
  static float B_Quad[83] =
    { 0.000000000000000, -0.000006581551523, -0.000014192774668,
0.000038355453153, -0.000061469802717, 0.000046155098672,
    -0.000269873790862, 0.000273874035623, -0.000496517495085,
      0.000383120218360, -0.001080412050939, 0.000679569009380,
      -0.001431859472243,
    0.000632904747289, -0.002103194937170, 0.000269803608080,
      -0.002028650339142, -0.000864482682011, -0.001935633229621,
      -0.003130320749406,
    -0.000660757624173, -0.006511157948451, 0.000829477180331,
      -0.011499838339182, 0.003150251021340, -0.017295118095470,
      0.004931046332886,
    -0.024156288476640, 0.005730140929231, -0.030544691199518,
      0.003619186767370, -0.036441773169957, -0.003417440897749,
      -0.040091021199004,
    -0.019051201727783, -0.041600273509535, -0.051332869304505,
      -0.039743071204460, -0.127032560999817, -0.035198771562362,
      -0.488057960837085,
    -0.584664836150859, 0.558860293285832, -0.018787192344116,
      0.193986224985834, -0.008891772659445, 0.111263353226189,
      0.000562232648398,
    0.069350999397921, 0.008632835092782, 0.043147713646936,
      0.014687263334153, 0.025127872636149, 0.018102917799341,
      0.013466436394485,
    0.019348490199617, 0.005952331932605, 0.018192510371929,
      0.002067567347773, 0.015985137219339, 0.000257349155187,
      0.012513166744168,
    -0.000036691339210, 0.009393580004274, 0.000299271454802,
      0.006076795140808, 0.000699657108499, 0.003864640455567,
      0.001119449029703,
    0.001926535004930, 0.001049068709402, 0.000995120503174,
      0.001044597409952, 0.000305528639009, 0.000630886520545,
      0.000111000803475,
    0.000464927709514, 0.000005944149118, 0.000157049271650,
      -0.000003528848644, 0.000069808136642, 0.000001885388254,
      0.000012115208419,
  };
  float sigdown1[BUFSIZE / 4];
  float sigdown2[BUFSIZE / 4];
  float sigdown3[BUFSIZE / 4];
  float sigdown4[BUFSIZE / 4];
  float sigdown[BUFSIZE / 4];
  float sigdwndup[13000];
  static float ZfI[82] =
    { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
  };
  static float ZfQ[82] =
    { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
  };
  int i, lpcnt;
  float sinfact, cosfact;
  float sI[BUFSIZE / 4];
  float sQ[BUFSIZE / 4];
  float upsampI[BUFSIZE];
  float upsampQ[BUFSIZE];
  float pI1[BUFSIZE / 4], pI2[BUFSIZE / 4], pI3[BUFSIZE / 4],
    pI4[BUFSIZE / 4];
  float pQ1[BUFSIZE / 4], pQ2[BUFSIZE / 4], pQ3[BUFSIZE / 4],
    pQ4[BUFSIZE / 4];

  if (nr_samps <= 0 || nr_samps > BUFSIZE) exit(2);
  if (fread(temp, sizeof(float), nr_samps, stdin) != nr_samps) exit(0);
  /* now convert baseband audio to usb with Fc= 12000 Hz */
  /* start with downsampling by 4 */
  for (i = 0; i < nr_samps; i += 4)
    temp4[i / 4] = temp[i];
  filter(temp4, sigdown1, subfilt1, Zifilt1d, nr_samps / 4, LP_LENGTH);
  for (i = 1; i < nr_samps; i += 4)
    temp4[i / 4] = temp[i];
  filter(temp4, sigdown2, subfilt2, Zifilt2d, nr_samps / 4, LP_LENGTH);
  for (i = 2; i < nr_samps; i += 4)
    temp4[i / 4] = temp[i];
  filter(temp4, sigdown3, subfilt3, Zifilt3d, nr_samps / 4, LP_LENGTH);
  for (i = 3; i < nr_samps; i += 4)
    temp4[i / 4] = temp[i];
  filter(temp4, sigdown4, subfilt4, Zifilt4d, nr_samps / 4, LP_LENGTH);
  for (i = 0; i < nr_samps / 4; i++)
    {
      sigdown[i] = sigdown1[i] + sigdown2[i] + sigdown3[i] + sigdown4[i];
      sigdwndup[i]=sigdown[i];
    }
  /* Hilbert transform */
  filter(sigdown, sI, B_Inphase, ZfI, nr_samps / 4, 83);
  filter(sigdown, sQ, B_Quad, ZfQ, nr_samps / 4, 83);

  /* Now back to Fs = 48000 Hz */
  filter(sI, pI1, subfilt1, Zifilt1uI, nr_samps / 4, LP_LENGTH);
  filter(sI, pI2, subfilt2, Zifilt2uI, nr_samps / 4, LP_LENGTH);
  filter(sI, pI3, subfilt3, Zifilt3uI, nr_samps / 4, LP_LENGTH);
  filter(sI, pI4, subfilt4, Zifilt4uI, nr_samps / 4, LP_LENGTH);
  filter(sQ, pQ1, subfilt1, Zifilt1uQ, nr_samps / 4, LP_LENGTH);
  filter(sQ, pQ2, subfilt2, Zifilt2uQ, nr_samps / 4, LP_LENGTH);
  filter(sQ, pQ3, subfilt3, Zifilt3uQ, nr_samps / 4, LP_LENGTH);
  filter(sQ, pQ4, subfilt4, Zifilt4uQ, nr_samps / 4, LP_LENGTH);
  lpcnt = 0;
  for (i = 0; i < nr_samps / 4; i++)

    {
      upsampI[lpcnt] = pI1[i];
      upsampQ[lpcnt++] = pQ1[i];
      upsampI[lpcnt] = pI2[i];
      upsampQ[lpcnt++] = pQ2[i];
      upsampI[lpcnt] = pI3[i];
      upsampQ[lpcnt++] = pQ3[i];
      upsampI[lpcnt] = pI4[i];
      upsampQ[lpcnt++] = pQ4[i];
    }

  /* now USB modulation to Fc = 12000 Hz */
  sinfact = -1.0;
  cosfact = -1.0;
  for (i = 0; i < nr_samps; i++)

    {
      if (i % 2)

	{
	  sinfact *= -1.0;
	  acq_signal[i] = -sinfact * upsampQ[i];
	}

      else

	{
	  cosfact *= -1;
	  acq_signal[i] = cosfact * upsampI[i];
	}
    }
  input_samples_buffer_request = 0;
  inpbufwidx = nr_samps;
  return (nr_samps);
}

