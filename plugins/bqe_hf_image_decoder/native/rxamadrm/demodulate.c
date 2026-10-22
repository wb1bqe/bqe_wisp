/* 
*     file demodulate.c
*
*     convert if-signal to baseband
*
*     PA0MBO  - M.BOS
*
*     Date Feb 21st 2009
*
*     code filters from  MATLAB code Diorama
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


#define NFILT 6
#define SNR_TIMEOUT 3
#define FAC_NOT_VALID 4
#define SNR_MIN_DB 5
#define FAC_NOT_VALID_TIMEOUT 4
#include "bqe_matrix.h"

#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <malloc.h>
#include "drmdefs.h"
#include "structtemplates.h"
#include "drmproto.h"
#include <float.h>
#include <math.h>
#include <fftw3.h>

struct cmplxnmbr
{
  float re;
  float im;
};


#define PI (4.0*atan(1.0))
extern float acq_signal[BUFSIZE];
extern int input_samples_buffer_request;
extern int inpbufwidx;
extern int symbols_per_frame_list[4];
extern int time_ref_cells_k_list[4][21];
extern int time_ref_cells_theta_1024_list[4][21];
extern int y_list[4];
extern int symbols_per_2D_window_list[4];
extern int symbols_to_delay_list[4];
extern float cpsd[129];
extern float psd[513];
extern int N_symbols_frequency_pilot_search;
extern int K_min_K_max_list[2][24];
extern float samplerate_offset_estimation;
extern int N_symbols_mode_detection;
extern int time_offset_log_last;
extern int transmission_frame_buffer_data_valid;
extern int fac_valid;
extern int no_of_unused_carriers_list[4];
extern int freq_ref_cells_k_list[4][3];
extern int freq_ref_cells_theta_1024_list[4][3];
extern int x_list[4];
extern int k0_list[4];
extern int W_1024_list[4][5][10];
extern int Z_256_list[4][5][10];
extern int Q_1024_list[4];
extern int power_boost_list[4][6][4];
extern int mode_and_occupancy_code_table[24];
extern int robustness_mode;
extern int spectrum_occupancy_estimation;
extern int spectrum_occupancy;
extern int symbols_per_frame;
extern int time_ref_cells_cnt_list[4];
extern int K_modulo;
extern int K_dc;
extern float mean_energy_of_used_cells;
extern int FAC_cells_k[65];
extern float transmission_frame_buffer[82980];
extern float channel_transfer_function_buffer[82980];
extern int transmission_frame_buffer_wptr;
extern int lFAC;
extern int runstate;
extern float WMERFAC;
int demodulate( /*@null@ */ float *sigin, int n, int init_statics)
{
  static float rs_buffer[BUFSIZE];
  float h0[] = { -0.00053074293540, -0.02605448687217, 0.05728889428838,
    0.17832217729899, 0.01307590580499, -0.01753406399373
  };
  float h1[] = { -0.00303414161658, -0.02792461412931, 0.10798310809997,
    0.15241687973164, -0.01607462410569, -0.00879892438897
  };
  float h2[] = { -0.00879892438897, -0.01607462410569, 0.15241687973164,
    0.10798310809997, -0.02792461412931, -0.00303414161658
  };
  float h3[] =
    { -0.01753406399373, 0.01307590580499, 0.17832217729899, 0.05728889428838,
    -0.02605448687217, -0.00053074293540
  };
  static float Zi_filt_d0[] = { 0.0, 0.0, 0.0, 0.0, 0.0 };
  static float Zi_filt_d1[] = { 0.0, 0.0, 0.0, 0.0, 0.0 };
  static float Zi_filt_d2[] = { 0.0, 0.0, 0.0, 0.0, 0.0 };
  static float Zi_filt_d3[] = { 0.0, 0.0, 0.0, 0.0, 0.0 };
  static float /*@only@ */ *rs0, *rs1, *rs2, *rs3;
  static float /*@only@ */ *bufin1, *bufin2, *bufin3, *bufin4;
  int i, j, k, l, p, m;
  static float time_offset;
  static float frequency_offset_fractional_init;
  static int time_offset_integer;

  mode_info mode_block;
  static int Tgh;
  static int counter = 0;
  static int Zi_get_ofdm_symbol, delta_time_offset_integer;
  static float freq_offset_init, time_offset_fractional_init;
  static float time_offset_fractional;
  static float delta_time_offset_I;
  static float delta_time_offset_I_init;
  int t_smp;
  static float Zi[6], symbol_temp[2 * Tu_A], symbol_buffer[Tu_A * 2 * 26];	/* pa0mbo check later */
  float freq_offset_integer;
  struct cmplxnmbr S_buffer[288][20];	/* pa0mbo check */
  float spectrum_occupancy_indicator[6];
  int sp_idx, K_min_, K_max_, K_dc_indx, K_dc_plus2_indx;
  int K_min_indx, K_min_minus4_indx, K_max_indx, K_max_plus1_indx;
  float tmp1, tmp2, tmp3, tmp4, tmp5, tmp6;
  float energy_ratio_K2_to_K0, energy_ratio_K_max_to_K_max_p1;
  float energy_ratio_K_min_to_K_min_m4;
  float phi_freq_correction_last;
  int N_symbols_needed, symbol0, symbol_no_to_equalize;
  int ntwee, nn, mtwee, cnt_actual_pilots_rel_indx;
  float temp1, temp2;
  int ntc_indx;
  int trxbuf_indx, symbuf_indx;
  float tmpreal, tmpimag, hoek;
  float FAC_cells_sequence[200];
  float FAC_squared_noise_sequence[200];
  float squared_weight_sequence[200];
  float sum_MERFAC, sum_WMERFAC, sum_weight_FAC, SNR_dB;
  float MERFAC;
  float t1, t2;
  float freq_offset;
  static int SNR_timeout_counter = 0;
  float h_absq[256];

  static fftw_complex ss[256], S[256];
  static /*@only@ */ fftw_plan p1;
  int lH;
  float freq_offset_log[100];
  float time_offset_log[100];
  static FILE *fpsd;
  static int TIME_SYNC_ENABLE = 1;
  static int FREQ_SYNC_ENABLE = 1;
  static int is_time_sync = 0;
  static int is_frequency_sync = 0;
  static int is_frame_sync = 0;
  int N_samples_needed;
  static int rsbufwidx;
  int cnt_time_ref_cells;
  int s, nnn, p_min, p_max, theta_1024;
  int rndcnt;
  float a;
  double rest;
  int indx, temp;
  int k_index1, k_index2, t1_pos, t2_pos;
  int k1_pos, k2_pos;
  double xsinc1, xsinc2;
  double f_cut_t, f_cut_k;
  double f_D_max;
  double tau_max;
  int gain_ref_cells_per_window;
  int NP, sortbrkpnt;
  int sorttmp[1024], sorttmp2[1024];
  int Ts_list[] = { Ts_A, Ts_B, Ts_C, Ts_D };
  int Tu_list[] = { Tu_A, Tu_B, Tu_C, Tu_D };
  int Tg_list[] = { Tg_A, Tg_B, Tg_C, Tg_D };
  static int symbufwidx;
  static int symbol_counter;
  static int SNR_time_out_counter;
  static int fac_not_valid_counter;
  static int mode_and_occupancy_code_last;
  int smp_rate_conv_fft_phase_diff;
  float smp_rate_conv_fft_phase_offset;
  static int do_synchronize;
  static int smp_rate_conv_in_out_delay;
  static int Ts, Tu, Tg;
  float sigmaq_noise_list[4];
  float sigmaq_noise;
  int no_of_used_cells_per_frame_list[24];
  static int mode_and_occupancy_code;
  static int freq_ref_cells_k[3];
  static int freq_ref_cells_theta_1024[3];
  static int time_ref_cells_k[21];
  static int time_ref_cells_theta_1024[21];
  static int K_min, K_max;
  static int carrier_per_symbol;
  static int power_boost[4];
  static int x, y, k0;
  static int Q_1024;
  static int gain_ref_cells_k[712];
  static int gain_ref_cells_theta_1024[712];
  static float gain_ref_cells_a[712];
  static int training_cells_k[5][712];
  static int cnt_tr_cells[5];
  static int symbols_per_2D_window;
  static int symbols_to_delay;
  static int gain_ref_cells_subset[5][209];
  static int gain_ref_cells_subset_index[5][209];
  static int cnt_next_pilot_cells[5];
  static int next_pilot_cells_k_index[5][40];
  static int next_pilot_cells_k[5][40];
  static float W_symbol_blk[5][208][229];
  static float W_symbol[208];
  static float W_pilots_blk[5][208][205];
  static float W_pilots[208];
  static float PHI[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT];
  static float PHI_INV[BQE_MATRIX_LIMIT][BQE_MATRIX_LIMIT];
  static float THETA[208];
  static float next_pilots[70];	/* complex */
  static int gain_ref_cells_per_frame;
  static int gain_ref_cells_per_y_symbols;
  static int gain_ref_cells_subset_nn[209];
  static int training_cells_relative_index[209];
  static int actual_pilots_relative_index[209];
  static float delta_freq_offset;
  static float H[458];
  static float MIN_ABS_H;
  static float normalized_training_cells[1424];
  static float actual_pilots[458];


  /*
     initialize static vars and alloc mem for rs and dec. by 4 bufin buffers 
   */
  if (init_statics == 1)

    {
      N_symbols_mode_detection = 20;
      N_symbols_frequency_pilot_search = 15;
      time_offset_log_last = 0;
      symbol_counter = 0;
      rs0 = malloc(sizeof(float) * (n / 4));
      if (rs0 == NULL)

	{
	  printf("not enough memory \n");
	  exit(EXIT_FAILURE);
	}
      rs1 = malloc(sizeof(float) * (n / 4));
      if (rs1 == NULL)

	{
	  printf("not enough memory \n");
	  exit(EXIT_FAILURE);
	}
      rs2 = malloc(sizeof(float) * (n / 4));
      if (rs2 == NULL)

	{
	  printf("not enough memory \n");
	  exit(EXIT_FAILURE);
	}
      rs3 = malloc(sizeof(float) * (n / 4));
      if (rs3 == NULL)

	{
	  printf("not enough memory \n");
	  exit(EXIT_FAILURE);
	}
      bufin4 = malloc(sizeof(float) * (n / 4));
      if (bufin4 == NULL)

	{
	  printf("not enough memory\n");
	  exit(EXIT_FAILURE);
	}
      bufin3 = malloc(sizeof(float) * (n / 4));
      if (bufin3 == NULL)

	{
	  printf("not enough memeory\n");
	  exit(EXIT_FAILURE);
	}
      bufin2 = malloc(sizeof(float) * (n / 4));
      if (bufin2 == NULL)

	{
	  printf("not enough memeory\n");
	  exit(EXIT_FAILURE);
	}
      bufin1 = malloc(sizeof(float) * (n / 4));
      if (bufin1 == NULL)

	{
	  printf("not enough memory\n");
	  exit(EXIT_FAILURE);
	}
      N_samples_needed = N_symbols_mode_detection * 320;
      input_samples_buffer_request = 4 * N_samples_needed;
      /* printf("lab1 inpreq %d \n", input_samples_buffer_request); */
      inpbufwidx = 0;
      SNR_time_out_counter = SNR_TIMEOUT;
      fac_not_valid_counter = FAC_NOT_VALID;
      mode_and_occupancy_code_last = -1;
      is_time_sync = 0;
      is_frequency_sync = 0;
      is_frame_sync = 0;
      rsbufwidx = 0;
      symbufwidx = 0;
      samplerate_offset_estimation = 0;
      smp_rate_conv_fft_phase_diff = 0;
      smp_rate_conv_fft_phase_offset = 0;
      transmission_frame_buffer_data_valid = 0;
      p1 = fftw_plan_dft_1d(256, ss, S, FFTW_FORWARD, FFTW_ESTIMATE);
      /* showFAC(NULL, lFAC, NULL, 1, 1);  */
      /* fpsd = fopen("psd.dat", "w"); */
      return (0);
    }

  /*
     normal run state 
   */
  transmission_frame_buffer_data_valid = 0;
  if (fac_valid == 0)

    {
      printf("fac not valid\n");	 
      fac_not_valid_counter--;
      if (fac_not_valid_counter <= 0)

	{
	  do_synchronize = 1;
	  fac_not_valid_counter = FAC_NOT_VALID;
	 /* printf("in demodulate ! fac valid and counter <=0\n");	  */
	}
    }

  else

    {
      if (fac_valid == 1)

	{
	  /* printf("in demodulate fac valid == 1\n"); */
	  fac_not_valid_counter = FAC_NOT_VALID;
	}
    }
  if (do_synchronize == 1)

    {
      is_time_sync = 0;
      is_frequency_sync = 0;
      is_frame_sync = 0;
      symbufwidx = 0;
      smp_rate_conv_in_out_delay = 0;
      samplerate_offset_estimation = 0;
      smp_rate_conv_fft_phase_diff = 0;
      smp_rate_conv_fft_phase_offset = 0;
      do_synchronize = 0;
      inpbufwidx =0;        /* added 25 nov 2011 */
      rsbufwidx =0 ;      /* added 26 apr 2012 */
      input_samples_buffer_request = 25600 ; /* added 25 nov 2011 */
      printf("do_sync\n");	/* */
    }
  m = NFILT;
  l = m - 1;
   /* printf(" n at entry demodulate%d  inpbufwidx is %d \n ", n, inpbufwidx); 	  */

  /*
     demodulate to baseband 
   */
  if (inpbufwidx > 0)

    {
      n = inpbufwidx;

      /*
         selection of subsamples by 4 
       */
      for (i = 0; i < n / 4; i++)

	{
	  bufin4[i] = sigin[i * 4 + 3];
	  bufin3[i] = sigin[i * 4 + 2];
	  bufin2[i] = sigin[i * 4 + 1];
	  bufin1[i] = sigin[i * 4];
	}

      /*
         now filter these subsamples separately  
       */
      filter(bufin4, rs0, h0, Zi_filt_d0, n / 4, m);
      filter(bufin3, rs1, h1, Zi_filt_d1, n / 4, m);
      filter(bufin2, rs2, h2, Zi_filt_d2, n / 4, m);
      filter(bufin1, rs3, h3, Zi_filt_d3, n / 4, m);

      /*
         fill complex result buffers, flipped or not 
       */
      /*
         format complex numbers  real and imag part sequential per complex number 
       */
      for (i = 0; i < n / 4; i++)

	{
	  rs_buffer[(i + rsbufwidx) * 2] = rs0[i] - rs2[i];
	  rs_buffer[(i + rsbufwidx) * 2 + 1] = rs1[i] - rs3[i];	/* todo for flipped spectrum rs3-rs1 */
	}
      rsbufwidx += n / 4;	/* index of next complex number to fill rs_buffer */
      /* printf("in demodulate after fill of rs_buffer rsbufwidx is %d\n", rsbufwidx);	  */

      /*
         now plot power spectral density of real audio data and complex data
         after demodulation 
       */
      psdcmean(rs_buffer, cpsd, 256, 25);	/* cpsd global  pa0mbo */

      /*
         psdmean(acq_signal, psd, 1024, 25);      globals   pa0mbo 
       */
      psdmean(sigin, psd, 1024, 25);	/* globals   pa0mbo */
      /* fprintf(fpsd, "--------------- psd ------------\n");
      for (i = 0; i < 512; i++ )
	fprintf(fpsd, "%g \n", psd[i]);
      fprintf(fpsd, " ----  end ---\n");
      fprintf(fpsd, "==== cpsd\n");
      for (i = 0; i < 128; i++)
	fprintf(fpsd, "%g \n", cpsd[i]);
      fprintf(fpsd, "== end\n");	 */

      printf("psd ");
      for (i=0; i < 512 ; i+=4)
        printf("{ %6.4f %4d } ", psd[i],i);
      printf(" \n");
      input_samples_buffer_request = 0;
    }				/* end demodulation to baseband */
  if (is_time_sync == 0)

    {
      is_frequency_sync = 0;

      /*
         enough data for time sync ? 
       */
      N_samples_needed = N_symbols_mode_detection * 320 - rsbufwidx;
      if (N_samples_needed > 0)

	{
	  input_samples_buffer_request = N_samples_needed * 4;
	 /* printf("lab2 inpreq %d \n", input_samples_buffer_request); */
	  inpbufwidx = 0;
	  return (0);
	}
      spectrum_occupancy = -1;	/* -1 denotes unknown */
      getmode(rs_buffer, n / 4, &mode_block, 0);
      robustness_mode = mode_block.mode_indx;
      time_offset = mode_block.time_offset;
      samplerate_offset_estimation = mode_block.sample_rate_offset;
      frequency_offset_fractional_init = mode_block.freq_offset_fract;
      time_offset_integer = (int) floor(time_offset + 0.5);
      if ((fabsf(samplerate_offset_estimation) > 200.0E-5)
	  && (robustness_mode != 99))

	{

	  /*
	     sample_rate offset too large 
	   */
	  /* printf("too large ? %g en fabsf %g\n", samplerate_offset_estimation,
		 fabsf(samplerate_offset_estimation));  */
	  N_samples_needed = N_symbols_mode_detection * 320;
	  input_samples_buffer_request = 4 * N_samples_needed;
	  /* printf("lab3 inpreq %d \n", input_samples_buffer_request); */
	  rsbufwidx = 0;
	  inpbufwidx = 0;	/* reset buffer indices  was commented out 10-2-2008 now in */
	  return (0);
	}
      if (robustness_mode != 99)

	{
          printf("%d robustnessmode\n", robustness_mode);
	  is_time_sync = 1;
	  Ts = Ts_list[robustness_mode];
	  Tu = Tu_list[robustness_mode];
	  Tg = Tg_list[robustness_mode];
	  Tgh = (int) floor(Tg / 2 + 0.5);
	  symbols_per_frame = symbols_per_frame_list[robustness_mode];
	  K_dc = Tu / 2;
	  K_modulo = Tu;
	  for (i = 0; i < 21; i++)
	    time_ref_cells_k[i] = time_ref_cells_k_list[robustness_mode][i];
	  for (i = 0; i < 21; i++)
	    time_ref_cells_theta_1024[i] =
	      time_ref_cells_theta_1024_list[robustness_mode][i];
	  y = y_list[robustness_mode];
	  symbols_per_2D_window = symbols_per_2D_window_list[robustness_mode];
	  symbols_to_delay = symbols_to_delay_list[robustness_mode];

	  /*
	     symbol align rs_buffer 
	   */
	  rsbufwidx = rsbufwidx - time_offset_integer;
	  /* printf("na align rs rsbufwidx is %d\n", rsbufwidx);	 */
	  for (i = 0; i < rsbufwidx; i++)	

	    {
	      rs_buffer[i * 2] = rs_buffer[(i + time_offset_integer) * 2];
	      rs_buffer[i * 2 + 1] =
		rs_buffer[(i + time_offset_integer) * 2 + 1];
	    }
	  counter++;
	  (void) getofdm(NULL, 0.0, 0.0, 0.0, Ts, Tu, NULL, NULL, 1, 1, 1);	/* initialisation */
	}

      else

	{
	  samplerate_offset_estimation = 0.0;
	  rsbufwidx -= 320 * N_symbols_mode_detection;
	  for (i = 0; i < rsbufwidx; i++)	/* pa0mbo was rsbufwidx-1 ? */

	    {
	      rs_buffer[i * 2] =
		rs_buffer[(320 * N_symbols_mode_detection + i) * 2];
	      rs_buffer[i * 2 + 1] =
		rs_buffer[(320 * N_symbols_mode_detection + i) * 2 + 1];
	    }
	  N_samples_needed = N_symbols_mode_detection * 320 - rsbufwidx;

	  
	     /* printf("xx debug N_samples needed after in else clause %d\n", N_samples_needed); 
	     */
	  if (N_samples_needed > 0)

	    {
	      input_samples_buffer_request = N_samples_needed * 4;
	      inpbufwidx = 0;
	    }

	  else

	    {
	      input_samples_buffer_request = 0;
	    }
	    /* printf("lab4 inpreq %d\n", input_samples_buffer_request); */
	  return 0;
	}
      printf("mode is %d GREEN FOR TIME SYNC\n", robustness_mode);
    }				/* end of case no time sync */

  /*
     integer frequency-offset correction 
   */
  if (is_frequency_sync == 0)

    {
      is_frame_sync = 0;

      /*
         enough f=date for pilot search? 
       */
      N_samples_needed =
	(N_symbols_frequency_pilot_search + 1) * Ts - (rsbufwidx);
      if (N_samples_needed > 0)

	{
	  input_samples_buffer_request = N_samples_needed * 4;
	  inpbufwidx = 0;
          printf("lab5 inpreq %d \n", input_samples_buffer_request);
	  /*
	     printf("Gone to get new audio data in pilot serach for integer freq corr\n"); 
	   */
	  return (0);
	}
      Zi_get_ofdm_symbol = 0;
      Zi[0] = -1.0;
      delta_time_offset_integer = 0;
      freq_offset_init = frequency_offset_fractional_init;
      time_offset_fractional_init = time_offset - time_offset_integer;
      delta_time_offset_I_init = samplerate_offset_estimation * Ts;
      t_smp = 0;

      /*
         printf("inp params getofdm %g %g %g %d %d\n", time_offset_fractional_init,
         freq_offset_init, delta_time_offset_I_init, Ts, Tu) ;   
       */
      for (i = 0; i < N_symbols_frequency_pilot_search; i++)

	{
	  delta_time_offset_integer =
	    getofdm(&rs_buffer[2 * t_smp], time_offset_fractional_init,
		    freq_offset_init, delta_time_offset_I_init, Ts, Tu, Zi,
		    symbol_temp, 0, TIME_SYNC_ENABLE, FREQ_SYNC_ENABLE);
	  for (j = 0; j < K_modulo; j++)

	    {
	      symbol_buffer[(i * K_modulo + j) * 2] = symbol_temp[j * 2];
	      symbol_buffer[(i * K_modulo + j) * 2 + 1] =
		symbol_temp[j * 2 + 1];
	    }
	  t_smp = t_smp + Ts + delta_time_offset_integer;
	}

      /*
         printf("Zi[4] is %g\n", Zi[4]);  
       */
      freq_offset_integer =
	getfoffsint(symbol_buffer, N_symbols_frequency_pilot_search, K_dc,
		    K_modulo, Tu);

      /*
         printf("freq_offs_integer = %g\n", freq_offset_integer);    
       */

      /*
         prepare for new round 
       */
      delta_time_offset_integer = 0;
      freq_offset_init =
	-freq_offset_integer + frequency_offset_fractional_init;
      time_offset_fractional_init = 0.0;
      Zi[0] = -1.0;

      /*
         detmn frequency occupancy 
       */
      /*
         start with reshaping symbol_buffer to S_buffer 
       */
      for (i = 0; i < N_symbols_frequency_pilot_search; i++)

	{
	  for (j = 0; j < K_modulo; j++)

	    {
	      (S_buffer[j][i]).re = symbol_buffer[(j + i * K_modulo) * 2];
	      (S_buffer[j][i]).im = symbol_buffer[(j + i * K_modulo) * 2 + 1];

	      /*
	         printf("%g \n", sqrt( (S_buffer[j][i]).re*(S_buffer[j][i]).re+
	         (S_buffer[j][i]).im*(S_buffer[j][i]).im));   
	       */
	      /*
	         printf("%g %g \n", symbol_buffer[(j+i*K_modulo)*2],
	         symbol_buffer[(j+i*K_modulo)*2+1]); 
	       */
	    }
	}

      /*
         now per freq occupancy mode 
       */
      for (sp_idx = 0; sp_idx < 2; sp_idx++)

	{
	  K_min_ = K_min_K_max_list[0][sp_idx + robustness_mode * 6];
	  K_max_ = K_min_K_max_list[1][sp_idx + robustness_mode * 6];

	  if (K_min_ != K_max_)

	    {

	      /*
	         printf(" freq_offset_int %g Tu %d floorf is %g \n",
	         freq_offset_integer, Tu, floor(freq_offset_integer/(2.0*PI)) + Tu/2);  
	       */
	      K_dc_indx = (int) floor(freq_offset_integer / (2.0 * PI) + 0.5) + Tu / 2;	/* */

	      /*
	         K_dc_indx = Tu/2  ;     
	       */
	      K_dc_indx = K_dc_indx % Tu;
	      K_dc_plus2_indx = (K_dc_indx + 2 + Tu) % Tu;
	      K_min_indx = (K_dc_indx + K_min_ + Tu) % Tu;
	      K_min_minus4_indx = (K_min_indx - 4 + Tu) % Tu;
	      K_max_indx = (K_dc_indx + K_max_ + Tu) % Tu;
	      K_max_plus1_indx = (K_max_indx + 1 + Tu) % Tu;

	      /*
	         calc energy ratios 
	       */
	      tmp1 = 0.0;
	      tmp2 = 0.0;
	      tmp3 = 0.0;
	      tmp4 = 0.0;
	      tmp5 = 0.0;
	      tmp6 = 0.0;
	      for (i = 0; i < N_symbols_frequency_pilot_search; i++)

		{
		  tmp1 +=
		    (S_buffer[K_dc_plus2_indx][i]).re *
		    (S_buffer[K_dc_plus2_indx][i]).re +
		    (S_buffer[K_dc_plus2_indx][i]).im *
		    (S_buffer[K_dc_plus2_indx][i]).im;
		  tmp2 +=
		    (S_buffer[K_dc_indx][i]).re *
		    (S_buffer[K_dc_indx][i]).re +
		    (S_buffer[K_dc_indx][i]).im * (S_buffer[K_dc_indx][i]).im;
		  tmp3 +=
		    (S_buffer[K_max_indx][i]).re *
		    (S_buffer[K_max_indx][i]).re +
		    (S_buffer[K_max_indx][i]).im *
		    (S_buffer[K_max_indx][i]).im;
		  tmp4 +=
		    (S_buffer[K_max_plus1_indx][i]).re *
		    (S_buffer[K_max_plus1_indx][i]).re +
		    (S_buffer[K_max_plus1_indx][i]).im *
		    (S_buffer[K_max_plus1_indx][i]).im;
		  tmp5 +=
		    (S_buffer[K_min_indx][i]).re *
		    (S_buffer[K_min_indx][i]).re +
		    (S_buffer[K_min_indx][i]).im *
		    (S_buffer[K_min_indx][i]).im;
		  tmp6 +=
		    (S_buffer[K_min_minus4_indx][i]).re *
		    (S_buffer[K_min_minus4_indx][i]).re +
		    (S_buffer[K_min_minus4_indx][i]).im *
		    (S_buffer[K_min_minus4_indx][i]).im;
		}
	      energy_ratio_K2_to_K0 = tmp1 / tmp2;
	      energy_ratio_K_max_to_K_max_p1 = tmp3 / tmp4;
	      energy_ratio_K_min_to_K_min_m4 = tmp5 / tmp6;
	      spectrum_occupancy_indicator[sp_idx] =
		energy_ratio_K_min_to_K_min_m4 +
		energy_ratio_K_max_to_K_max_p1;
	      /* printf("spocc %d tmp1 %g,  tmp2 %g, tmp3 %g, tmp4 %g, tmp5 %g,  tmp6 %g\n",
                sp_idx, tmp1, tmp2, tmp3, tmp4, tmp5, tmp6);	 */
	    }

	  else
	    spectrum_occupancy_indicator[sp_idx] = 0.0;
	}

      /*
         detmn max in spectrum_occupancy_indicator and its index 
       */
      tmp1 = 0.0;
      for (sp_idx = 0; sp_idx < 2; sp_idx++)

	{
	  if (spectrum_occupancy_indicator[sp_idx] > tmp1)

	    {
	      tmp1 = spectrum_occupancy_indicator[sp_idx];
	      spectrum_occupancy_estimation = sp_idx;
	    }
	}
      is_frequency_sync = 1;
      printf("%d GREEN SPECTRUM OCCUPANCY\n",
	     spectrum_occupancy_estimation);
    }				/* end case no frequency sync */

  /*
     frame synchronization 
   */
  if (is_frame_sync == 0)

    {

      /*
         enough data ? 
       */
      N_symbols_needed = symbols_per_frame + symbols_per_2D_window - 1;
      N_samples_needed = (N_symbols_needed + 1) * Ts - (rsbufwidx);
      if (N_samples_needed > 0)

	{
	  input_samples_buffer_request = N_samples_needed * 4;
	  inpbufwidx = 0;

	  /*
	     printf("N_samples_needed is %d in frame sync \n", N_samples_needed);
	     printf("Gone to fetch new audio data in case no frame sync \n"); 
	   */
	  /* printf("lab6 inpreq %d \n", input_samples_buffer_request); */
	  return (0);
	}

      /*
         printf("reached frame sync code  befor eentering getofdm loop\n");
         printf("Zi[o] is %g freq_offs_init %g delta_time_offs %g Ts %d \n",
         Zi[0], freq_offset_init, delta_time_offset_I_init, Ts);  
       */
      t_smp = 0;
      for (i = 0; i < symbols_per_frame; i++)

	{
	  delta_time_offset_integer =
	    getofdm(&rs_buffer[2 * t_smp], time_offset_fractional_init,
		    freq_offset_init, delta_time_offset_I_init, Ts, Tu, Zi,
		    symbol_temp, 0, TIME_SYNC_ENABLE, FREQ_SYNC_ENABLE);
	  for (j = 0; j < K_modulo; j++)

	    {
	      symbol_buffer[(i * K_modulo + j) * 2] = symbol_temp[j * 2];
	      symbol_buffer[(i * K_modulo + j) * 2 + 1] =
		symbol_temp[j * 2 + 1];
	    }
	  t_smp = t_smp + Ts + delta_time_offset_integer;
	  phi_freq_correction_last = Zi[1];

	  /*
	     printf("symbol counter in loop symbols per frame is %d phi_freq_corr %g\n",i, 
	     Zi[1]);   
	   */
	}

      /*
         search first symbol of frame using time ref cells 
       */
      symbol0 =
	getsymbolidx(symbol_buffer, symbols_per_frame, time_ref_cells_k,
		     time_ref_cells_theta_1024, K_dc, K_modulo, 21);
      symbol_no_to_equalize =
	((symbol0 - symbols_to_delay +
	  symbols_per_frame) % symbols_per_frame) + 1;

      is_frame_sync = 1;
      symbol_counter = 0;

      /*
         frame align rs_buffer 
       */
      if (symbol_no_to_equalize != 1)

	{
	  rsbufwidx -= (symbol_no_to_equalize - 1) * Ts;
	  for (j = 0; j < rsbufwidx; j++)	/* pa0mbo was rsbufwidx  22-4-07 now better */

	    {
	      rs_buffer[j * 2] =
		rs_buffer[((symbol_no_to_equalize - 1) * Ts + j) * 2];
	      rs_buffer[j * 2 + 1] =
		rs_buffer[((symbol_no_to_equalize - 1) * Ts + j) * 2 + 1];
	    }
	}
      symbufwidx = 0;
      Zi[0] = -1.0;
      t_smp = 0;
      for (i = 0; i < symbols_per_2D_window; i++)

	{
	  delta_time_offset_integer =
	    getofdm(&rs_buffer[2 * t_smp], time_offset_fractional_init,
		    freq_offset_init, delta_time_offset_I_init, Ts, Tu, Zi,
		    symbol_temp, 0, TIME_SYNC_ENABLE, FREQ_SYNC_ENABLE);
	  for (j = 0; j < K_modulo; j++)

	    {
	      symbol_buffer[(i * K_modulo + j) * 2] = symbol_temp[j * 2];
	      symbol_buffer[(i * K_modulo + j) * 2 + 1] =
		symbol_temp[j * 2 + 1];
	    }
	  symbufwidx++;
	  t_smp += Ts + delta_time_offset_integer;
	  phi_freq_correction_last = Zi[1];

	  /*
	     printf("phi_freq ofm %g\n", phi_freq_correction_last);   
	   */
	}

      /*
         symbol align rs_buffer 
       */
      rsbufwidx -= t_smp;
      for (i = 0; i < rsbufwidx; i++)	/* pa0mbo was rxbufwidx 22-4-07 */

	{
	  rs_buffer[i * 2] = rs_buffer[(i + t_smp) * 2];
	  rs_buffer[i * 2 + 1] = rs_buffer[(i + t_smp) * 2 + 1];
	}
    }				/* end frame sync ? */
  printf("GREEN 2nd TIME SYNC \n");

  /*
     channel estimation based on pilots 
   */
  /*
     for time being opnieuw 
   */

  /*
     printf("voor channel est t_smp=%d rs_buffer_writeptr=%d delta_toffsI %d Tu=%d K_modulo=%d\n",
     t_smp , rsbufwidx , delta_time_offset_integer, Tu, K_modulo); 
   */

  /*
     enough data ? 
   */
  N_symbols_needed = symbols_per_frame + symbols_per_2D_window - symbufwidx;	/* pa0mbo 24-4-07 */
  N_samples_needed = (N_symbols_needed + 1) * Ts - (rsbufwidx);	/* pa0mbo was rsbufwidx-1 */
  if (N_samples_needed > 0)

    {
      input_samples_buffer_request = N_samples_needed * 4;
      inpbufwidx = 0;
      /* printf("lab7 inpreq %d \n", input_samples_buffer_request); */
      return (0);
    }

  /*
     now set the parameter spectrum_occupancy 
   */
  if (spectrum_occupancy <= 0)

    {
      if (spectrum_occupancy_estimation < 0)
	spectrum_occupancy = 3;

      else
	spectrum_occupancy = spectrum_occupancy_estimation;
    }
  if (spectrum_occupancy > 3)
    spectrum_occupancy = 3;	/* 4 and 5 not yet supported */
  mode_and_occupancy_code =
    mode_and_occupancy_code_table[robustness_mode * 6 + spectrum_occupancy];
  if (mode_and_occupancy_code < 0)

    {
      spectrum_occupancy = 1;
      mode_and_occupancy_code =
	mode_and_occupancy_code_table[robustness_mode * 6 +
				      spectrum_occupancy];
    }
  if (mode_and_occupancy_code != mode_and_occupancy_code_last)

    {
      K_min = K_min_K_max_list[0][spectrum_occupancy + robustness_mode * 6];
      K_max = K_min_K_max_list[1][spectrum_occupancy + robustness_mode * 6];
      carrier_per_symbol = K_max - K_min + 1;
      (void) getofdmsync(NULL, Ts, Tu, NULL, K_max - K_min + 1, 0, NULL, NULL, 1, 1, 1);	/* initialisation */

      /*
         reformat pilot index stuff into th K_dc/K_modulo block 
       */
      /*
         first call listsinit to get gain_ref_cells_k etc 
       */
      /*
         Jan 5th 2009 changed listsinit() call to an include of all code from listsinit.c 
       */


/*
*   File listsinit.c
*
*   initializes the lists needed
*   in various parts of i.e. demodulate.c
*
*   Most data from equalization_init.m
*   MATLAB-code by Andreas Dittrich 2005
*
*   Author M.Bos PA0MBO
*   date Jan 15th 2007
*
*   preliminary version as a main program
*   for testing purposes. It uses drm.h
*   instead of drmglobs.h
*
*   later to change into subroutine with params
*   int robustness_mode   and int spectrum_occupancy_estimation
*/
      MIN_ABS_H = 8.0E-5 * 8.0E-5;
      k = 0;
      for (i = 0; i < 4; i++)	/* number of robustness modes */

	{
	  for (j = 0; j < 6; j++)	/* number of spectrumoccupancies */

	    {
	      no_of_used_cells_per_frame_list[k++] =
		(K_min_K_max_list[1][j + i * 6] -
		 K_min_K_max_list[0][j + i * 6] + 1 -
		 no_of_unused_carriers_list[i]) * symbols_per_frame_list[i];
	    }
	}
      sigmaq_noise_list[0] = (float) pow(10.0, -16.0 / 10.0);
      sigmaq_noise_list[1] = (float) pow(10.0, -14.0 / 10.0);
      sigmaq_noise_list[2] = (float) pow(10.0, -14.0 / 10.0);
      sigmaq_noise_list[3] = (float) pow(10.0, -12.0 / 10.0);

      /*
         debug 
         printf("============== no_of_used_cells_per_frame_list\n");
         for (i=0; i < k; i++)
         printf("%d \n", no_of_used_cells_per_frame_list[i]);   
         printf("listsinit: robmode %d , spectocest %d, mode_andOcc %d\n",
         robustness_mode, spectrum_occupancy_estimation, mode_and_occupancy_code);  
       */


/* */


/*    mode_and_occupancy_code= mode_and_occupancy_code_table[robustness_mode*6 + 
             spectrum_occupancy_estimation];  */
      Tu = Tu_list[robustness_mode];
      Ts = Ts_list[robustness_mode];
      Tg = Ts - Tu;
      sigmaq_noise = sigmaq_noise_list[robustness_mode];

      /*
         printf("sigmaq_noise uit lijst is %g\n", sigmaq_noise);  
       */
      symbols_per_frame = symbols_per_frame_list[robustness_mode];
      for (i = 0; i < 3; i++)

	{
	  freq_ref_cells_k[i] = freq_ref_cells_k_list[robustness_mode][i];
	  freq_ref_cells_theta_1024[i] =
	    freq_ref_cells_theta_1024_list[robustness_mode][i];
	}
      cnt_time_ref_cells = time_ref_cells_cnt_list[robustness_mode];
      for (i = 0; i < cnt_time_ref_cells; i++)

	{
	  time_ref_cells_k[i] = time_ref_cells_k_list[robustness_mode][i];
	  time_ref_cells_theta_1024[i] =
	    time_ref_cells_theta_1024_list[robustness_mode][i];
	}
      K_min =
	K_min_K_max_list[0][spectrum_occupancy_estimation +
			    robustness_mode * 6];
      K_max =
	K_min_K_max_list[1][spectrum_occupancy_estimation +
			    robustness_mode * 6];
      carrier_per_symbol = K_max - K_min + 1;
      for (i = 0; i < 4; i++)

	{
	  power_boost[i] =
	    power_boost_list[robustness_mode][spectrum_occupancy_estimation]
	    [i];
	}
      x = x_list[robustness_mode];
      y = y_list[robustness_mode];
      k0 = k0_list[robustness_mode];
      Q_1024 = Q_1024_list[robustness_mode];
      mean_energy_of_used_cells =
	(float) (no_of_used_cells_per_frame_list
		 [spectrum_occupancy_estimation + robustness_mode * 6] + 3 +
		 cnt_time_ref_cells);

      /*
         debug 
         printf("mean_energy_of_used_cells tmp0 is %f\n", mean_energy_of_used_cells); 
       */
      rndcnt = 0;
      for (s = 0; s < symbols_per_frame; s++)

	{
	  nnn = s % y;
	  m = (int) floor((double) (s / y));
	  p_min = (int) ceil((double) ((K_min - k0 - x * nnn) / (x * y)));
	  p_max = (int) floor((double) ((K_max - k0 - x * nnn) / (x * y)));
	  for (p = p_min; p <= p_max; p++)

	    {
	      k = k0 + x * nnn + x * y * p;
	      theta_1024 =
		(4 * Z_256_list[robustness_mode][nnn][m] +
		 p * W_1024_list[robustness_mode][nnn][m] + p * p * (1 +
								     s) *
		 Q_1024) % 1024;
	      a = sqrtf(2.0);

	      /*
	         power boost ? 
	       */
	      for (i = 0; i < 4; i++)

		{
		  if (k == power_boost[i])
		    a = 2;
		}

	      /*
	         is time ref cell ? 
	       */
	      if (s == 0)

		{
		  for (i = 0; i < cnt_time_ref_cells; i++)

		    {
		      if (k == time_ref_cells_k[i])

			{
			  indx = i;
			  theta_1024 = time_ref_cells_theta_1024[indx];
			  a = sqrtf(2.0);
			  mean_energy_of_used_cells -= 1.0;
			}
		    }
		}

	      /*
	         is frequence reference cell? 
	       */
	      for (i = 0; i < 3; i++)

		{
		  if (k == freq_ref_cells_k[i])

		    {
		      indx = i;
		      theta_1024 = freq_ref_cells_theta_1024[indx];
		      if (robustness_mode == 3)

			{
			  theta_1024 = (theta_1024 + 512 * s) % 1024;
			}
		      a = sqrtf(2.0);
		      mean_energy_of_used_cells -= 1.0;
		    }
		}
	      gain_ref_cells_k[rndcnt] = k + s * carrier_per_symbol;

	      /*
	         debugging 
	         printf("rndcnt=%d x = %d nnn = %d m = %d y = %d p = %d k = %d s = %d carrierps = %d gainref = %d theta_1024=%d\n",
	         rndcnt, x,nnn, m,  y, p, k, s,  carrier_per_symbol, gain_ref_cells_k[rndcnt], theta_1024) ;  
	       */
	      gain_ref_cells_theta_1024[rndcnt] = theta_1024;
	      gain_ref_cells_a[rndcnt++] = a;
	      mean_energy_of_used_cells =
		(float) (mean_energy_of_used_cells - 1.0 + a * a);
	}}
      /*
         debug 
         printf("mean_energy_of_used cells tmp is %f\n", mean_energy_of_used_cells); 
       */
      mean_energy_of_used_cells /=
	no_of_used_cells_per_frame_list[spectrum_occupancy_estimation +
					robustness_mode * 6];

      /*
         debugging  

         printf("mean_energy of used celss is %g\n", mean_energy_of_used_cells);
         printf("rndcnt is %d\n", rndcnt);
         printf("========== gain_ref_cells_k\n");
         for (i=0; i < rndcnt; i++)
         printf("%d\n", gain_ref_cells_k[i]);
         printf("================= gain_ref_cells_theta_1024\n");
         for (i=0; i < rndcnt; i++)
         printf("%d\n",gain_ref_cells_theta_1024[i]);
         printf("================= gain_ref_cells_a\n");
         for (i=0; i < rndcnt; i++)
         printf("%g\n",gain_ref_cells_a[i]);
         printf("=======power boost \n");
         for (i=0; i< 4; i++)
         printf("%d\n", power_boost[i]);
         printf("======\n");
         printf("K_min is %d K_max is %d K_dc= %d\n", K_min, K_max, K_dc);
         printf("carrier_per_symbol is %d\n", carrier_per_symbol);
         printf("mean_energy_of_used_cells is %g\n", mean_energy_of_used_cells); 
         printf("=====FAC_cells_k\n");
         for (i=0; i < 65; i++)
         printf("%d\n", FAC_cells_k[i]);    
       */

      /*
         precompute 2-D Wiener filter matrix 
       */
      /*
         uses gain_ref_cells etc 
       */
      /*
         and rndcnt 
       */
      symbols_per_2D_window = symbols_per_2D_window_list[robustness_mode];
      symbols_to_delay = symbols_to_delay_list[robustness_mode];
      f_cut_t = 0.0675 / (1.0 * (double) y);
      f_cut_k = 1.75 * (float) Tg / (float) Tu;
      f_D_max = f_cut_t * 12000.0 / (float) Ts / 2.0;
      tau_max = f_cut_k * (float) Tu / 12000.0 / 2.0;

      /*
         printf("xxx yyy carrier_per_symbol= %d, symbols_per_2D_window=%d\n",
         carrier_per_symbol, symbols_per_2D_window);  
       */

      /*
         start nnn-loop 
       */
      for (nnn = 0; nnn < y; nnn++)

	{
	  for (i = 0; i < rndcnt; i++)

	    {
	      training_cells_k[nnn][i] = (gain_ref_cells_k[i] - K_min +
					  (symbols_per_frame -
					   nnn) * carrier_per_symbol) %
		(symbols_per_frame * carrier_per_symbol) + K_min;

	      /*
	         printf("training_cells_k[%d][%d] = %d \n", nnn, i, training_cells_k[nnn][i]);  
	       */
	    }
	  cnt_tr_cells[nnn] = 0;

	  /*
	     printf("listsinit carrier_per_symb %d symb2D %d\n", carrier_per_symbol, symbols_per_2D_window);  
	   */
	  for (i = 0; i < rndcnt; i++)

	    {
	      if ((training_cells_k[nnn][i] - K_min) <
		  (carrier_per_symbol * symbols_per_2D_window))

		{
		  gain_ref_cells_subset_index[nnn][cnt_tr_cells[nnn]] = i;
		  gain_ref_cells_subset[nnn][(cnt_tr_cells[nnn])++] =
		    training_cells_k[nnn][i];
		}
	    }

	  /*
	     printf("cnt_tr_cells[%d] = %d\n", nnn, cnt_tr_cells[nnn]);   
	   */
	  cnt_next_pilot_cells[nnn] = 0;
	  for (i = 0; i < rndcnt; i++)

	    {
	      if (((training_cells_k[nnn][i] - K_min) >=
		   (carrier_per_symbol *
		    symbols_per_2D_window)) && ((training_cells_k[nnn][i] -
						 K_min) <
						carrier_per_symbol *
						(symbols_per_2D_window + 1)))

		{
		  next_pilot_cells_k_index[nnn][cnt_next_pilot_cells[nnn]] =
		    i;
		  next_pilot_cells_k[nnn][(cnt_next_pilot_cells[nnn])++] =
		    ((training_cells_k[nnn][i] -
		      K_min) % carrier_per_symbol) + K_min;
		}
	    }

	  /*
	     now sort training cells in subset if necessary 
	   */
	  sortbrkpnt = 0;
	  for (i = 1; i < cnt_tr_cells[nnn]; i++)

	    {
	      if (gain_ref_cells_subset[nnn][i] < gain_ref_cells_subset[nnn][i - 1])	/* break in data ? */
		sortbrkpnt = i;
	    }
	  if (sortbrkpnt > 0)

	    {

	      /*
	         keep first part in sorttmp 
	       */
	      for (i = 0; i < sortbrkpnt; i++)

		{
		  sorttmp[i] = gain_ref_cells_subset[nnn][i];
		  sorttmp2[i] = gain_ref_cells_subset_index[nnn][i];	/* pa0mbo added 22-jan-2009 */
		}

	      /*
	         now  shift smaller ones to start of vector 
	       */
	      for (i = 0; i < cnt_tr_cells[nnn] - sortbrkpnt; i++)

		{
		  gain_ref_cells_subset[nnn][i] =
		    gain_ref_cells_subset[nnn][i + sortbrkpnt];
		  gain_ref_cells_subset_index[nnn][i] =
		    gain_ref_cells_subset_index[nnn][i + sortbrkpnt];
		}

	      /*
	         replace last part from tmp 
	       */
	      for (i = cnt_tr_cells[nnn] - sortbrkpnt; i < cnt_tr_cells[nnn];
		   i++)

		{
		  gain_ref_cells_subset[nnn][i] =
		    sorttmp[i + sortbrkpnt - cnt_tr_cells[nnn]];
		  gain_ref_cells_subset_index[nnn][i] =
		    sorttmp2[i + sortbrkpnt - cnt_tr_cells[nnn]];
		}
	      sortbrkpnt = 0;
	    }

	  /*
	     copy to training_cells_k 
	   */
	  for (i = 0; i < cnt_tr_cells[nnn]; i++)

	    {
	      training_cells_k[nnn][i] = gain_ref_cells_subset[nnn][i];
	    }

	  /*
	     debugging 

	     for (i=0; i < cnt_tr_cells[nnn] ; i++)
	     {
	     printf("grcsindx[%d][%d] = %d grcs = %d\n",
	     nnn, i, gain_ref_cells_subset_index[nnn][i], gain_ref_cells_subset[nnn][i]);
	     }   
	   */
	  gain_ref_cells_per_window = cnt_tr_cells[nnn];
	  for (k_index1 = 0; k_index1 < gain_ref_cells_per_window; k_index1++)

	    {
	      for (k_index2 = 0; k_index2 < gain_ref_cells_per_window;
		   k_index2++)

		{
		  k1_pos =
		    (((gain_ref_cells_subset[nnn][k_index1] -
		       K_min)) % carrier_per_symbol) + K_min;
		  t1_pos =
		    (gain_ref_cells_subset[nnn][k_index1] -
		     K_min) / carrier_per_symbol;
		  k2_pos =
		    ((gain_ref_cells_subset[nnn][k_index2] -
		      K_min)) % carrier_per_symbol + K_min;
		  t2_pos =
		    ((gain_ref_cells_subset[nnn][k_index2] -
		      K_min)) / carrier_per_symbol;
		  xsinc1 = (k1_pos - k2_pos) * f_cut_k;
		  xsinc2 = (t1_pos - t2_pos) * f_cut_t;
		  if (k1_pos == k2_pos)
		    xsinc1 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc1);
		      xsinc1 = rest / (PI * xsinc1);
		    }
		  if (fabs(xsinc2) < DBL_EPSILON)
		    xsinc2 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc2);
		      xsinc2 = rest / (PI * xsinc2);
		    }
		  PHI[k_index1][k_index2] = (float) (xsinc1 * xsinc2);
	  }} for (i = 0; i < gain_ref_cells_per_window; i++)

	    {
	      PHI[i][i] +=
		sigmaq_noise * 2.0 /
		((gain_ref_cells_a[gain_ref_cells_subset_index[nnn][i]]) *
		 (gain_ref_cells_a[gain_ref_cells_subset_index[nnn][i]]));
	    }

	  /*
	     debugging 

	     printf("---PHI-matrix \n");
	     printf("first row\n");
	     for (j=0; j < gain_ref_cells_per_window; j++)
	     printf("%g\n", PHI[0][j]);
	     printf("second column\n");
	     for (j=0; j < gain_ref_cells_per_window; j++)
	     printf("%g\n", PHI[j][1]);  
	   */

          /* BQE GPL replacement: zero-based Gauss-Jordan inversion. */
          NP = gain_ref_cells_per_window;
          if (bqe_matrix_inverse(NP, PHI, PHI_INV) != 0) {
              fprintf(stderr, "HamDRM channel-estimation matrix inversion failed\n");
              return -1;
          }

	  /*
	     debugging  

	     printf("---PHIinv-matrix n = %d\n",n);
	     printf("first row\n");
	     for (j=0; j < gain_ref_cells_per_window; j++)
	     printf("%g\n", PHI_INV[0][j]);
	     printf("second column\n");
	     for (j=0; j < gain_ref_cells_per_window; j++)
	     printf("%g\n", PHI_INV[j][1]);    
	   */
	  /*
	     end matrix PHI inversion 
	   */
	  for (k_index1 = 0; k_index1 < (K_max - K_min + 1); k_index1++)

	    {
	      for (k_index2 = 0; k_index2 < gain_ref_cells_per_window;
		   k_index2++)

		{
		  k1_pos = k_index1 + K_min;
		  t1_pos = symbols_to_delay;

		  /*
		     k2_pos = fmod(training_cells_k[nnn][k_index2]-K_min, K_max-K_min+1) + K_min; 
		   */
		  k2_pos =
		    (training_cells_k[nnn][k_index2] - K_min) % (K_max -
								 K_min + 1) +
		    K_min;

		  /*
		     t2_pos = (int)floor((training_cells_k[nnn][k_index2]-K_min)/(K_max-K_min+1)); 
		   */
		  t2_pos =
		    (training_cells_k[nnn][k_index2] - K_min) / (K_max -
								 K_min + 1);
		  xsinc1 = (k1_pos - k2_pos) * f_cut_k;
		  xsinc2 = (t1_pos - t2_pos) * f_cut_t;
		  if (k1_pos == k2_pos)
		    xsinc1 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc1);
		      xsinc1 = rest / (PI * xsinc1);
		    }
		  if (t1_pos == t2_pos)
		    xsinc2 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc2);
		      xsinc2 = rest / (PI * xsinc2);
		    }
		  THETA[k_index2] = (float) (xsinc1 * xsinc2);
		}
	      /*
	         calc matrix product THETA*PHI_INV 
	       */
	      for (j = 0; j < NP; j++)

		{
		  W_symbol[j] = 0.0;
		  for (k = 0; k < NP; k++)
		    W_symbol[j] += THETA[k] * PHI_INV[k][j];
		}

	      /*
	         printf("xxx k_index1 = %d nnn = %d en nu W_symbolblk\n",
	         k_index1, nnn );   
	       */
	      for (j = 0; j < NP; j++)

		{
		  W_symbol_blk[nnn][j][k_index1] = W_symbol[j];

		  /*
		     debugging   
		     printf("%g\n", W_symbol[j]*32768);   
		   */
		}
	    }
	  for (k_index1 = 0; k_index1 < cnt_next_pilot_cells[nnn]; k_index1++)

	    {

	      /*
	         printf("xxxx THETA bij k_index1=%d \n", k_index1); 
	       */
	      for (k_index2 = 0; k_index2 < gain_ref_cells_per_window;
		   k_index2++)

		{
		  k1_pos = next_pilot_cells_k[nnn][k_index1];
		  t1_pos = symbols_per_2D_window - 1;

		  /*
		     k2_pos = fmod(training_cells_k[nnn][k_index2] - K_min, K_max-K_min+1) + K_min; 
		   */
		  k2_pos =
		    (training_cells_k[nnn][k_index2] - K_min) % (K_max -
								 K_min + 1) +
		    K_min;

		  /*
		     t2_pos= (int)floor((training_cells_k[nnn][k_index2]-K_min)/(K_max-K_min+1)); 
		   */
		  t2_pos =
		    (training_cells_k[nnn][k_index2] - K_min) / (K_max -
								 K_min + 1);
		  xsinc1 = (k1_pos - k2_pos) * f_cut_k;
		  xsinc2 = (t1_pos - t2_pos) * f_cut_t;
		  if (k1_pos == k2_pos)
		    xsinc1 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc1);
		      xsinc1 = rest / (PI * xsinc1);
		    }
		  if (t1_pos == t2_pos)
		    xsinc2 = 1.0;

		  else

		    {
		      rest = sin(PI * xsinc2);
		      xsinc2 = rest / (PI * xsinc2);
		    }
		  THETA[k_index2] = (float) (xsinc1 * xsinc2);

		  /*
		     printf("THETA[%d] = %g\n", k_index2, THETA[k_index2]); 
		   */
		}		/* end k_index2-loop */

	      /*
	         calc matrix product THETA*PHI_INV 
	       */
	      for (j = 0; j < NP; j++)

		{
		  W_pilots[j] = 0.0;
		  for (k = 0; k < NP; k++)
		    W_pilots[j] += THETA[k] * PHI_INV[k][j];
		}

	      /*
	         printf("xxx k_index1 = %d nnn = %d en nu W_pilotsblk\n",
	         k_index1, nnn );   
	       */
	      for (j = 0; j < NP; j++)

		{
		  W_pilots_blk[nnn][j][k_index1] = W_pilots[j];

		  /*
		     debugging  
		     printf("%g\n", W_pilots[j]);   
		   */
		}
	    }			/* end k_index1-loop */
	}			/* end nnn-loop  pa0mbo 26-5-2007 */

      /*
         debugging 
         printf("=== training cells k en subset\n");
         printf("K_min=%d , symbols_per_frame = %d , carrier_per_symbol = %d symbols_per_2D %d\n",
         K_min, symbols_per_frame,  carrier_per_symbol, symbols_per_2D_window);
         for (nnn=0; nnn < y; nnn++)
         {
         printf("xxxxnnn = %d\n",nnn);
         for (i=0; i <rndcnt; i++)
         printf("%d \n", training_cells_k[nnn][i]);

         printf("xxx  now list gain_ref_cells_subset with count subset = %d\n", cnt_tr_cells[nnn]);
         for (i=0; i < cnt_tr_cells[nnn]; i++)
         printf("index %d content %d \n", gain_ref_cells_subset_index[nnn][i], 
         gain_ref_cells_subset[nnn][i]);

         printf("xxx count next pilots %d\n", cnt_next_pilot_cells[nnn]);
         for (i=0; i < cnt_next_pilot_cells[nnn]; i++)
         {
         printf("index pilot %d content next pilot  %d \n", next_pilot_cells_k_index[nnn][i],
         next_pilot_cells_k[nnn][i]);
         }
         }
         printf("==== matrix PHI gain ref cells per window is %d\n", 
         gain_ref_cells_per_window);
         for (i=0; i < gain_ref_cells_per_window; i++)
         for (j=0; j < gain_ref_cells_per_window; j++)
         printf("PHI_INV[%d][%d] = %g\n", i,j, PHI_INV[i][j]);  
       */

      /*
         end debugging 
       */

      /*
         debugging  
         printf("xxx gain_ref_cells_k \n");
         printf("K_min=%d carrier_per_symbol=%d, K_modulo=%d \n",
         K_min, carrier_per_symbol, K_modulo);   
       */
      for (i = 0; i < rndcnt; i++)

	{

	  /*
	     temp= floor( (float)(gain_ref_cells_k[i]-K_min)/((float)carrier_per_symbol))*
	     (float)(K_modulo-carrier_per_symbol);  
	   */
	  temp =
	    ((gain_ref_cells_k[i] -
	      K_min) / (carrier_per_symbol)) * (K_modulo -
						carrier_per_symbol);
	  gain_ref_cells_k[i] += temp + K_dc - 1;

	  /*
	     printf("gain_ref_cells_k[%d]=%d temp[%d]=%d\n", 
	     i, gain_ref_cells_k[i],i,temp);   
	   */
	}
      lFAC = mkfacmap(robustness_mode, K_dc, K_modulo, FAC_cells_k);
      mode_and_occupancy_code_last = mode_and_occupancy_code;

      /*
         for (i=0; i< carrier_per_symbol; i++)
         {
         H_last[i]= 0.0 ;   
         }   
       */
      for (i = 0; i < cnt_tr_cells[y-1]; i++)

	{
	  next_pilots[2 * i] = 0.0;	/* real part */
	  next_pilots[2 * i + 1] = 0.0;	/* imag */
	}
      gain_ref_cells_per_frame = rndcnt;
      gain_ref_cells_per_y_symbols = rndcnt / (symbols_per_frame / y);

      /*
         printf("gain_ref_cells_per_frame=%d per_y_symbols %d\n", rndcnt, gain_ref_cells_per_y_symbols) ;  
       */

      /*
         debugging  also end of included listsinit code
       */
      /* printf("na listsinit rob_mode= %d, specocc=%d,  specestim= %d mode_code= %d\n",
        robustness_mode, spectrum_occupancy, spectrum_occupancy_estimation, mode_and_occupancy_code);	 */
    }

  /*
     in matlab code here for display toctic_equalization = 0 etc 
   */
  t_smp = 0;

  /*
     lets equalize one frame 
   */


/*      printf("symb_per_frame %d, symb_to_delay %d gainrefcpery %d gainrperframe %d\n",
        symbols_per_frame, symbols_to_delay, gain_ref_cells_per_y_symbols,
             gain_ref_cells_per_frame);
     printf("net voor i-loop symbufwidx=%d\n", symbufwidx);  */
  for (i = 0; i < symbols_per_frame; i++)

    {
      symbol_counter++;

      /*
         shifted symbol index 
       */
      nn = (i - symbols_to_delay + symbols_per_frame) % symbols_per_frame;
      ntwee = nn % y;
      mtwee = nn / y;

      /*
         printf("in symbols_per_frame loop cnt_tr_cells[%d] = %d \n", ntwee, cnt_tr_cells[ntwee]);
         printf("nn=%d ntwee=%d mtwee=%d\n", nn, ntwee, mtwee);
         printf("K_modulo=%d \n", K_modulo);   
       */
      for (j = 0; j < cnt_tr_cells[ntwee]; j++)

	{
	  gain_ref_cells_subset_nn[j] =
	    (mtwee * gain_ref_cells_per_y_symbols +
	     gain_ref_cells_subset_index[ntwee][j]) %
	    gain_ref_cells_per_frame;
	  training_cells_relative_index[j] =
	    (gain_ref_cells_k[gain_ref_cells_subset_nn[j]] +
	     (symbols_per_frame -
	      nn) * K_modulo) % (K_modulo * symbols_per_frame);

	  /*
	     printf("grcsnn[%d] = %d trcrelidx[%d] = %d  nn=%d smblpfr= %d K_mod=%d\n",
	     j, gain_ref_cells_subset_nn[j],
	     j, training_cells_relative_index[j], nn, symbols_per_frame, K_modulo);    
	   */
	}
      cnt_actual_pilots_rel_indx = 0;

      /*
         printf("xxx normalized training cells power\n");    
       */
      for (j = 0; j < cnt_tr_cells[ntwee]; j++)

	{
	  if ((training_cells_relative_index[j] -
	       (symbols_per_2D_window - 1) * K_modulo) >= 0)

	    {
	      actual_pilots_relative_index[cnt_actual_pilots_rel_indx++] = j;

	      /*
	         printf("actual_pil_rel_indx=%d\n", actual_pilots_relative_index[cnt_actual_pilots_rel_indx-1]);   
	       */
	    }
	  ntc_indx = training_cells_relative_index[j] + i * K_modulo + 1;	/* pa0mbo in  matlab +1 =OK trcrindx 1 lager dan in M   */

	  /*
	     bovenstaande regel met K_modulo +1 geeft duidelijk betere resultaten 22-4-2007
	     dan wanneer de +1 wordt weggelaten
	     klopt als K_dc is aangepast  18-5-2007 
	   */

	  /*
	     printf("ntc_indx=%d sym_real %g sym_imag %g \n", ntc_indx, symbol_buffer[2*ntc_indx],
	     symbol_buffer[2*ntc_indx+1]);  
	   */
	  hoek =
	    (float) (2.0 * PI *
		     (float)
		     gain_ref_cells_theta_1024[gain_ref_cells_subset_nn[j]] /
		     1024.0);
	  tmpreal =
	    (float) (cos(hoek) /
		     gain_ref_cells_a[gain_ref_cells_subset_nn[j]]);
	  tmpimag =
	    (float) (-sin(hoek) /
		     gain_ref_cells_a[gain_ref_cells_subset_nn[j]]);
	  normalized_training_cells[2 * j] = symbol_buffer[2 * ntc_indx] * tmpreal - symbol_buffer[2 * ntc_indx + 1] * tmpimag;	/* real part */
	  normalized_training_cells[2 * j + 1] = symbol_buffer[2 * ntc_indx + 1] * tmpreal + symbol_buffer[2 * ntc_indx] * tmpimag;	/* imag part */

	  /*
	     debugging 
	     printf("%g\n", sqrt(symbol_buffer[2*ntc_indx]*symbol_buffer[2*ntc_indx]+
	     symbol_buffer[2*ntc_indx+1]*symbol_buffer[2*ntc_indx+1])) ;   
	   */

	  /*
	     debugging  
	     showbuf[j]= sqrt(normalized_training_cells[2*j]*normalized_training_cells[2*j]+
	     normalized_training_cells[2*j+1]*normalized_training_cells[2*j+1]); 

	     printf("%g \n", showbuf[j]);    
	   */

	  /*
	     printf("%d %g\n",  (ntc_indx % K_modulo) -K_dc +1, showbuf[j] )  ;   
	   */

	  /*
	     printf("j %d grfcsth1024[ %d ]= %d grfcla[%d]= %g\n", j, gain_ref_cells_subset_nn[j],
	     gain_ref_cells_theta_1024[gain_ref_cells_subset_nn[j]],
	     gain_ref_cells_subset_nn[j],
	     gain_ref_cells_a[gain_ref_cells_subset_nn[j]]);   
	   */
	}
      /*
         debugging 
         printf("symbol_buffer_writeptr = %d\n", symbufwidx);  
       */
      /*
         printf("xxx actual_pilots cnt = %d\n", cnt_actual_pilots_rel_indx);   
       */
      for (j = 0; j < cnt_actual_pilots_rel_indx; j++)

	{
	  actual_pilots[2 * j] = normalized_training_cells[2 * (actual_pilots_relative_index[j])];	/* real part */
	  actual_pilots[2 * j + 1] = normalized_training_cells[2 * (actual_pilots_relative_index[j]) + 1];	/* imag */

	  /*
	     printf("%g\n",  sqrt(actual_pilots[2*j]*actual_pilots[2*j]+
	     actual_pilots[2*j+1]*actual_pilots[2*j+1]));   
	   */
	}
      temp1 = 0.0;
      temp2 = 0.0;
      for (j = 0; j < cnt_actual_pilots_rel_indx; j++)

	{
	  temp1 += actual_pilots[2 * j] * next_pilots[2 * j] + actual_pilots[2 * j + 1] * next_pilots[2 * j + 1];	/* real part */
	  temp2 +=
	    actual_pilots[2 * j] * next_pilots[2 * j + 1] -
	    actual_pilots[2 * j + 1] * next_pilots[2 * j];
	}
      if (i != 0)
	delta_freq_offset = (float) atan2(temp2, temp1 + MIN_ABS_H);

      /*
         debugging  
         printf("delta_freq_offset = %g\n", delta_freq_offset);  
       */
      /*
         debugging 
         printf("xxx W_symbol_blk[%d][][] =\n", ntwee);
         for (j=0; j <K_max-K_min+1; j++)
         {
         printf("--- j = %d\n", j);
         for (k=0; k < cnt_tr_cells[ntwee]; k++)
         printf("%g\n", W_symbol_blk[ntwee][k][j]*32768);
         }   
       */

      /*
         channel estimation with precomputed wiener matrix from listsinit 
       */

      /*
         printf("in channel estimation wiener K_min= %d K_max = %d ntwee= %d cnt_tr_cells = %d\n",
         K_min, K_max, ntwee, cnt_tr_cells[ntwee]) ;   
       */
      for (j = 0; j < K_max - K_min + 1; j++)

	{
	  H[2 * j] = 0.0;
	  H[2 * j + 1] = 0.0;
	  for (k = 0; k < cnt_tr_cells[ntwee]; k++)

	    {
	      H[2 * j] +=
		normalized_training_cells[2 * k] * W_symbol_blk[ntwee][k][j];
	      H[2 * j + 1] +=
		normalized_training_cells[2 * k +
					  1] * W_symbol_blk[ntwee][k][j];
	    }
	}

      /*
         debugging  
         printf(" xxx channel estimation power K_max=%d K_min=%d \n", K_max, K_min);
         for (j=0; j < K_max-K_min+1; j++)
         {
         printf("%d %g\n",  j+ K_min ,
         20.0*log(sqrt(H[2*j]*H[2*j] + H[2*j+1]*H[2*j+1])+1.0E-5))/log(10) ;  
         }  
       */

      /*
         debugging plot of impulse response function 
       */
      lH = K_max - K_min + 1;

      /*
         printf("xxx response function lH = %d\n",lH);   
       */
      for (j = 0; j < lH; j++)

	{
	  temp1 = (float) (1.0 - cos((j + 1) / (lH + 1.0) * 2.0 * PI));
	  ss[j][0] = H[2 * j] * temp1;	/* real part */
	  ss[j][1] = -H[2 * j + 1] * temp1;	/* imag part */

	  /*
	     printf("%g  %g %g \n", ss[j].re, ss[j].im, temp1);  
	   */
      }
      for (j = lH; j < 256; j++)

	{
	  ss[j][0] = 0.0;
	  ss[j][1] = 0.0;
	}			/* zero fill to 256 elements */
      fftw_execute(p1);
      for (j = 0; j < 256; j++)

	{
	  h_absq[j] =
	    (float) sqrt(S[j][0] * S[j][0] + S[j][1] * S[j][1]) / lH;

	}

/*      for (j=0; j< 128; j++)
      {
         printf("%g %g \n", (j-128.0)/256*Tu/12.0, h_absq[128+j]);
      }
      for (j=128; j < 256; j++)
      {
         printf("%g %g \n", (j-128.0)/256*Tu/12.0, h_absq[j-128] );
      } 
     printf("ooo\n");   */

      /*
         printf("xxx phase channel estim \n"); 
         for (j=0; j < K_max-K_min+1; j++)
         {
         printf("%d %g\n", j+K_min, atan2(H[2*j], H[2*j+1])/PI);
         }
         printf("ooo \n");   
       */
      for (j = 0; j < cnt_actual_pilots_rel_indx; j++)

	{
	  next_pilots[2 * j] = 0.0;
	  next_pilots[2 * j + 1] = 0.0;
	  for (k = 0; k < cnt_tr_cells[ntwee]; k++)

	    {
	      next_pilots[2 * j] +=
		normalized_training_cells[2 * k] * W_pilots_blk[ntwee][k][j];
	      next_pilots[2 * j + 1] +=
		normalized_training_cells[2 * k +
					  1] * W_pilots_blk[ntwee][k][j];
	    }
	}

      /*
         debugging 18-5-2007  
         printf("xxx symbol_buffer just before equaliza \n");
         printf("K_min = %d K_max = %d K_modulo = %d K_dc = %d\n", 
         K_min, K_max, K_modulo, K_dc);  

         for (j=0; j < K_modulo ; j++)
         {
         printf("%g\n", sqrt(symbol_buffer[2*j]*symbol_buffer[2*j]+
         symbol_buffer[2*j+1]*symbol_buffer[2*j+1]));
         }    
       */

      /*
         equalize 
       */
      /*
         printf("xxx chantrf\n");   
       */
      /*
         test of new code 29-5-2007 
       */
      for (j = K_min; j <= K_max; j++)

	{
	  trxbuf_indx =
	    transmission_frame_buffer_wptr + i * K_modulo + K_dc + j;
	  symbuf_indx = (i + symbols_to_delay) * K_modulo + K_dc + j;
	  tmp1 =
	    H[2 * (j - K_min)] * H[2 * (j - K_min)] + H[2 * (j - K_min) +
							1] * H[2 * (j -
								    K_min) +
							       1] + MIN_ABS_H;
	  tmp2 = H[2 * (j - K_min)] / tmp1;
	  tmp3 = -H[2 * (j - K_min) + 1] / tmp1;
	  transmission_frame_buffer[2 * trxbuf_indx] =
	    symbol_buffer[2 * symbuf_indx] * tmp2 -
	    symbol_buffer[2 * symbuf_indx + 1] * tmp3;
	  transmission_frame_buffer[2 * trxbuf_indx + 1] =
	    symbol_buffer[2 * symbuf_indx] * tmp3 +
	    /* pa0mbo changed 10 aug 2007 */
	    symbol_buffer[2 * symbuf_indx + 1] * tmp2;
	  channel_transfer_function_buffer[2 * trxbuf_indx] =
	    H[2 * (j - K_min)];
	  channel_transfer_function_buffer[2 * trxbuf_indx + 1] =
	    H[2 * (j - K_min) + 1];

	  /*
	     printf("%g\n", sqrt(channel_transfer_function_buffer[2*trxbuf_indx]*
	     channel_transfer_function_buffer[2*trxbuf_indx]+
	     channel_transfer_function_buffer[2*trxbuf_indx+1]*
	     channel_transfer_function_buffer[2*trxbuf_indx+1]));    
	   */
	}

      /*
         debugging 
         printf("bij wegzetten in trxframebuf K_min %d K_modulo %d K_dc %d\n",
         K_min, K_modulo, K_dc);  
       */

      /*
         get next symbol 
       */
      delta_time_offset_integer =
	getofdmsync(&rs_buffer[2 * t_smp], Ts, Tu, H, K_max - K_min + 1,
		    delta_freq_offset, Zi, symbol_temp, 0, TIME_SYNC_ENABLE,
		    FREQ_SYNC_ENABLE);

      /*
         printf("xxx symbol_temp \n");   
       */
      for (j = 0; j < K_modulo; j++)

	{
	  symbol_buffer[(symbufwidx * K_modulo + j) * 2] = symbol_temp[j * 2];
	  symbol_buffer[(symbufwidx * K_modulo + j) * 2 + 1] =
	    symbol_temp[j * 2 + 1];

	  /*
	     debugging  
	     printf("%g\n", sqrt(symbol_temp[j*2]*symbol_temp[j*2]+
	     symbol_temp[j*2+1]*symbol_temp[j*2+1]));   
	   */
	}
      symbufwidx++;

      /*
         printf("after storing in symbol_buf symbufwidx = %d delta_time_offset_int = %d\n", symbufwidx,
         delta_time_offset_integer);  
       */
      t_smp += Ts + delta_time_offset_integer;	/* next symbol */
      freq_offset_log[i] = Zi[4];
      time_offset_log[i] =
	(float) time_offset_log_last + (float) delta_time_offset_integer +
	Zi[5];
      time_offset_log_last += delta_time_offset_integer;

      /*
         printf("i= %d Zi 2= %g 4 = %g\n", i, Zi[2], Zi[4]) ;  
       */
    }				/* end i-loop over symbols_per_frame */
  /*
     printf("xxx freq offset log \n");
     for (i=0; i < symbols_per_frame; i++)
     printf("%g\n", -freq_offset_log[i]*12000.0/Tu/(2.0*PI));
     printf("xxx time offset log \n");
     for (i=0; i < symbols_per_frame; i++ )
     printf("%g\n", time_offset_log[i]/12.0);
     printf("---\n");    
   */

  /*
     printf("xxx symbolbuf  voor swap  \n"); 

     for (j=0;  j < symbufwidx*K_modulo; j++)
     printf("%g\n", sqrt(symbol_buffer[2*j]*symbol_buffer[2*j]+
     symbol_buffer[2*j+1]*symbol_buffer[2*j+1]));
     printf("ooo \n");  
   */

  /*
     debugging  

     printf("xxx complete trx buf\n");
     for (j=0; j <  symbols_per_frame*K_modulo ; j++)
     printf("%g \n", sqrt( transmission_frame_buffer[2*j]*transmission_frame_buffer[2*j]+
     transmission_frame_buffer[2*j+1]*transmission_frame_buffer[2*j+1]));
     end debugging 
   */

  /*
     debugging  
     printf("xxx symb van getofdmsync \n");
     for (i=0; i < symbufwidx*K_modulo; i++)
     printf("%g \n ", sqrt(symbol_buffer[i*2]*symbol_buffer[i*2]+
     symbol_buffer[i*2+1]*symbol_buffer[i*2+1]));
     printf("oooo\n");
     end debugging 
   */
  /*
     fac_valid=0; 
   */
  freq_offset = Zi[4];
  time_offset_fractional = Zi[5];
  delta_time_offset_I = Zi[2];
  printf("GREEN on channel estimation\n");

  /*
     clock too slow or too fast: adjust playing speed  
   */
  smp_rate_conv_fft_phase_diff = 4 * (t_smp - N_symbols_needed * Ts);
  smp_rate_conv_fft_phase_offset = 4 * time_offset_fractional;
  smp_rate_conv_in_out_delay += smp_rate_conv_fft_phase_diff;

  /*
     display results 
   */
  printf("freq offset %4.1f Hz, smpl rate offset %-4.1f ppm \n",
	 -freq_offset * 12000.0 / (float) Tu / (2.0 * PI),
	 (1.0 / (delta_time_offset_I / Ts + 1.0) - 1.0) * 1.0E6);

  /*
     symbol align rs_buffer 
   */
  rsbufwidx -= t_smp;
  for (i = 0; i < rsbufwidx; i++)

    {
      rs_buffer[i * 2] = rs_buffer[(i + t_smp) * 2];
      rs_buffer[i * 2 + 1] = rs_buffer[(i + t_smp) * 2 + 1];
    }

  /*
     swap symbol buffer 
   */
  symbufwidx -= symbols_per_frame;
  for (i = 0; i < symbufwidx * K_modulo; i++)

    {
      symbol_buffer[i * 2] =
	symbol_buffer[2 * symbols_per_frame * K_modulo + 2 * i];
      symbol_buffer[i * 2 + 1] =
	symbol_buffer[2 * symbols_per_frame * K_modulo + 2 * i + 1];
    }

  /*
     printf("xxx symbolbuf na swap \n");

     for (j=0;  j < symbufwidx*K_modulo; j++)
     printf("%g\n", sqrt(symbol_buffer[2*j]*symbol_buffer[2*j]+
     symbol_buffer[2*j+1]*symbol_buffer[2*j+1]));
     printf("ooo \n");  
   */

  /*
     printf("xxx FAC_cells_sequence\n");     
   */

  /*
     SNR estimation using FAC  cells 
   */
  /*
     mean energy of used cells set in listsinit()  
   */
  sum_MERFAC = 0.0;
  sum_WMERFAC = 0.0;
  sum_weight_FAC = 0.0;
  for (i = 0; i < lFAC; i++)

    {

      /*
         printf("%d \n", FAC_cells_k[i]);   
       */
      trxbuf_indx = transmission_frame_buffer_wptr + FAC_cells_k[i];	/* pa0mbo 18-5-2007 checked  */
      FAC_cells_sequence[i * 2] = transmission_frame_buffer[2 * trxbuf_indx];
      FAC_cells_sequence[i * 2 + 1] =
	transmission_frame_buffer[2 * trxbuf_indx + 1];

      /*
         debugging  
         showbuf[i]= sqrt(FAC_cells_sequence[i*2]*FAC_cells_sequence[i*2]+
         FAC_cells_sequence[i*2+1]*FAC_cells_sequence[i*2+1]); 
         printf("%d %g %g\n", i,     FAC_cells_sequence[i*2], FAC_cells_sequence[i*2+1]);  
       */
      /*
         printf(" %g\n", fabs(FAC_cells_sequence[i*2])-sqrt(0.5));   
       */
      t1 = (float) (fabs(FAC_cells_sequence[i * 2]) - sqrt(0.5));
      t1 = t1 * t1;
      t2 = (float) (fabs(FAC_cells_sequence[i * 2 + 1]) - sqrt(0.5));
      t2 = t2 * t2;
      FAC_squared_noise_sequence[i] = t1 + t2;
      t1 = channel_transfer_function_buffer[trxbuf_indx * 2];
      t1 = t1 * t1;
      t2 = channel_transfer_function_buffer[trxbuf_indx * 2 + 1];
      t2 = t2 * t2;
      squared_weight_sequence[i] = t1 + t2;
      sum_MERFAC += FAC_squared_noise_sequence[i];
      sum_WMERFAC +=
	FAC_squared_noise_sequence[i] * (squared_weight_sequence[i] +
					 1.0E-10);
      sum_weight_FAC += squared_weight_sequence[i];
    }
  /*
     display FAC_cells 
   */

    
     /* showFAC(FAC_cells_sequence, lFAC, "hallo", 1, 0);     */
  

  /* output for fac constellation display in Tk/Tcl canvas */
  printf("FAC-constellation ");
  for (i=0; i < lFAC; i++)
     printf("{ %6.3f %6.3f } ", FAC_cells_sequence[2*i], 
           FAC_cells_sequence[2*i+1]);
  printf(" \n");
   


  MERFAC = (float) (log(sum_MERFAC / lFAC + 1.0E-10));
  MERFAC /= (log(10.0));
  MERFAC *= -10.0;

  /*
     MERFAC = -10.0*(log(sum_MERFAC/lFAC + 1.0E-10))/(log(10.0)); 
   */
  WMERFAC =
    (float) (log
	     (sum_WMERFAC /
	      (mean_energy_of_used_cells *
	       (sum_weight_FAC + lFAC * 1.0E-10))));
  WMERFAC /= (log(10.0));
  WMERFAC *= -10.0;

  /*
     WMERFAC = -10.0*log(sum_WMERFAC/(mean_energy_of_used_cells*
     (sum_weight_FAC+lFAC*1.0E-10)))/log(10.0); 
   */
  SNR_dB = WMERFAC;
  printf("SNR-FAC = %2.1f\n",  SNR_dB);
  N_samples_needed = (symbols_per_frame + 1) * Ts - rsbufwidx;

  /*
     printf("xxx N_samples needed at end of demodulate = %d\n",
     N_samples_needed);   
   */
  if (SNR_dB < SNR_MIN_DB)

    {
      SNR_timeout_counter--;
      if (SNR_timeout_counter <= 0)

	{
	  do_synchronize = 1;
	  SNR_timeout_counter = SNR_TIMEOUT;
	}
      transmission_frame_buffer_data_valid = 0;
      fac_not_valid_counter--;
      if (fac_not_valid_counter <= 0)

	{
	  do_synchronize = 1;
	  fac_not_valid_counter = FAC_NOT_VALID_TIMEOUT;
	}
    }

  else

    {
      SNR_timeout_counter = SNR_TIMEOUT;
      transmission_frame_buffer_data_valid = 1;
      fac_valid = 1;
    }
  if (do_synchronize == 1)
    N_samples_needed = N_symbols_mode_detection * 320 - rsbufwidx;

  else
    N_samples_needed = (symbols_per_frame + 1) * Ts - rsbufwidx;
  if (N_samples_needed > 0)

    {
      input_samples_buffer_request = N_samples_needed * 4;
      inpbufwidx = 0;
      /* printf("lab8 inpreq %d \n", input_samples_buffer_request); */
    }

  else
    input_samples_buffer_request = 0;

  
    /* printf("freq sync %d time_sync %d frame_sync %d fac_valid %d inpbufwidx %d inp_samples_buffer_request %d\n",
     is_frequency_sync,is_time_sync, is_frame_sync, fac_valid, inpbufwidx, input_samples_buffer_request);   
    */
  return (0);
}
