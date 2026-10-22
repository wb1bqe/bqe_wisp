/*
*     file sourcedecode.c
*
*     author M.Bos - PA0MBO
*     date Feb 21st 2009
*
*     coded after MATLAB  diorama-1.1.1 version
*     authors Andreas Dittrich and Torsten Schorr 2005
*
*     No audio decoding
*    
*     last update Oct 10th 2012
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
#include <string.h>
#include <math.h>
#include <sys/types.h>
#include "drmdefs.h"
#include "structtemplates.h"
#include "drmproto.h"
#include <float.h>

#define RUN_COMMAND_NONE 0
#define RUN_COMMAND_RESTART -1
#define RUN_COMMAND_QUIT 99

#define RUN_STATE_FIRSTRUN 0
#define RUN_STATE_NORMAL 1
#define RUN_STATE_LASTRUN 99
#define RUN_STATE_STOP 100


/* typedef short int16_t ; */
extern int runstate;
extern struct mplex_desc multiplex_description;
extern struct audio_info audio_information;
extern struct appl_info application_information;
extern struct stream_info stream_information;
struct dflttmsg default_text_message;
extern struct dfltdunitasmbly default_data_unit_assembly;
extern struct dfltMOTdirasmbly default_MOT_directory_assembly;
extern struct dfltMOTobjasmbly default_MOT_object_assembly;
extern struct dfltMOTobjasmblyinfo default_MOT_object_assembly_information;
extern struct dfltMOTobj default_MOT_object;
extern int channel_decoded_data_buffer_data_valid;
extern double channel_decoded_data_buffer[110000];
extern int audio_data_flag;
extern int length_decoded_data;

char* bqe_itoa(int val, int base){
	
	static char buf[32] = {0};
	
	int i = 30;
	
	for(; val && i ; --i, val /= base)
	
		buf[i] = "0123456789abcdef"[val % base];
	
	return &buf[i+1];
	
}


/* asciitoutf8.c
*
*   Author M.Bos  - PA0MBO
*
*   Dat Nov 5th 2014
*************************************/
int asciitoutf8(unsigned char *argv, unsigned char *result)
{
   int i, lengte ;
   int count=0;

   lengte = strlen(argv);
   for (i=0; i< lengte; i++)
   {
     if (argv[i] < 128)
     {
          result[count++]= argv[i] ;
     }
     else if ((argv[i]> 128) && (argv[i] < 192))
     {
          result[count++] = 0xc2;
          result[count++] = argv[i];
          i++;
     }
     else if (argv[i] > 191)
     {
          result[count++]= 0xc3;
          result[count++]= argv[i]-0x40;
     }
     else 
     {
        printf("unknown character\n");
        result[count]='\0';
        return -1;
     }
   }
     result[count]='\0';
 //    printf("%s",result);
     return count;
}



   



void
source_decoding (void)
{
  int SOURCEDECODING = 1;
  static float convenience_noise_gain;
  static int convenience_noise_wav_ptr;
  static int channel_mode;
  static short int zeros_3840x1_int16[3840];
  static short int zeros_1x3840_int16[3840];
  int i, j, k;
  static int frame_errors_total;
  int news_counter;
  static int16_t last_temp[4000];
  static int initial_source_decoding;
  static int audio_fer;
  static struct dflttmsg text_message;
  static struct dfltdunitasmbly data_units_assembly[4];
  static struct dfltMOTobjasmblyinfo MOT_objects_assembly_information[4];
  static struct dfltMOTobjasmbly MOT_objects_assembly[4][30];
  static struct dfltMOTdirasmbly MOT_directories_assembly[4];
  static int CRC_error_counter;
  static int CRC_OK_counter;
  int N_partA, N_partB;
  int frameinfo_errors;
  int service_ID;
  int Start_partA, Start_partB;
  int application_domain;
  int *application_data;
  static int data_unit_counter;
  int packet_mode;
  int packet_length;
  unsigned char packet_buffer[6000];
  int packet_buffer_length;
  int packet_buffer_index;
  unsigned char header;
  int length_packet_buffer;
  unsigned char packet[3000];
  double checksum;
  int data_unit_indicator;
  int first_last_flag;
  int PPI;
  int continuity_index;
  int useful_bytes;
  static unsigned char data_units[100][12000];
  static int data_units_assignment[100];
  int packet_ID;
  static int corresponding_service;
  static int MOT_object_counter;
  static struct dfltMOTobj MOT_objects[20];
  static struct dfltMOTobj MOT_object;
  static int cntMOTobjs;
  int user_application_type;
  int length_application_data;
  static unsigned char MSC_data_group_header[4];
  int next_data;
  int CRC_OK;
  static unsigned char segment_field[4];
  static int last_segment;
  static int segment_number;
  int data_group_type;
  unsigned char user_access_field[3];
  int length_indicator;
  static int MOT_object_index;
  static int number_of_MOT_objects;
  static struct dfltMOTobjasmbly MOT_object_assembly;
  unsigned char segmentation_header[2];
  int cnt;
  struct dfltMOTdirasmbly MOT_directory_assembly;
  unsigned char directory_header[13];
  long int directory_size;
  int number_of_objects;
  int carousel_period;
  static int segment_size;
  static int tot_segments;
  int directory_extension_length;
  unsigned char directory_extension[100];
  int directory_entry_index;
  static int entry_number;
  static int entry_transport_IDs[100];
  static int cntIDs;
  int transport_ID;
  unsigned char MOT_header_part[10];
  int header_size;
  unsigned int deprecated_objects[100];
  int cnt_deprecated_objects;
  int is_in;
  int last_object;
  char service_name[100];
  char filename[100];
  char metadata_filename[120];
  static unsigned long bqe_file_id = 0;
  static int data_unit_index;
  char altname[80];
  int lname = 80;
  unsigned char uncompressed_data[1000000];
  int ldata_out = 10000;
  double lhs2;
  static int cnt_data_unit;
  static int data_units_cnts[100];
  int hulp, tmpindx;
  FILE *fpout;
  FILE *fperasures;
  FILE *fpbsrbin;
  static struct shadowMOTobj shdwMOTobjs[100];
  static struct shadowMOTobj defaultshadowMOT;
  static int cntshadowMOTs = 0;
  int shadowMOTindx=-1;
  static struct dfltMOTobj fullshadowMOT;
  int bytecnt;
  int byteptr;
  int incompl_seg_length ;
  unsigned char newfilename[200] ;
  int lengthorgfname=0;



  if (runstate == RUN_STATE_POWER_ON)

    {

      /*
         printf("sourcedecode: POWER_ON\n"); 
       */
      /*
         wavread(CONVENIENCE_NOISE_FILENAME, convenience_noise_wav, &dim_noise_wav); 
       */
      convenience_noise_gain = 0;
      convenience_noise_wav_ptr = 0;
      channel_mode = 1;
      for (i = 0; i < 3840; i++)

	{
	  zeros_3840x1_int16[i] = 0;
	  zeros_1x3840_int16[i] = 0;
	}
      default_text_message.stream_no = -1;
      default_text_message.current_toggle = 0;
      default_text_message.first_last_flag = 0;
      default_text_message.command_flag = 0;
      default_text_message.field1 = 0;
      default_text_message.field2 = 0;
      for (i = 0; i < 8; i++)
	(default_text_message.segments)[i][0] = '\0';
      strcpy (default_text_message.string, "Receiving...");
      default_text_message.CRC_error = 0;
      default_text_message.first_seg_received = 0;
      default_text_message.current_segment[0] = '\0';
      default_text_message.current_segment_no = 0;
      default_text_message.current_segment_length = 0;
      default_data_unit_assembly.ID = 0;
      default_data_unit_assembly.first_packet_received = 0;
      default_data_unit_assembly.packet_ID = 0;
      default_data_unit_assembly.continuity_index = -1;
      default_data_unit_assembly.application_domain = 0;
      default_data_unit_assembly.application_data[0] = '\0';
      default_data_unit_assembly.CRC_error = 0;
      default_data_unit_assembly.data_unit[0] = '\0';
      default_data_unit_assembly.cnt_data_unit = 0;
      default_MOT_directory_assembly.transport_ID = 0;
      default_MOT_directory_assembly.continuity_index = -1;
      default_MOT_directory_assembly.current_segment_no = -1;
      default_MOT_directory_assembly.body_complete = 0;
      default_MOT_directory_assembly.body[0] = '\0';
      default_MOT_directory_assembly.bodycnt = 0;
      default_MOT_object_assembly.transport_ID = 0;
      default_MOT_object_assembly.header_continuity_index = -1;
      default_MOT_object_assembly.current_header_segment_no = -1;
      default_MOT_object_assembly.body_continuity_index = -1;
      default_MOT_object_assembly.current_body_segment_no = -1;
      default_MOT_object_assembly.header_complete = 0;
      default_MOT_object_assembly.body_complete = 0;
      default_MOT_object_assembly.delete = 0;
      default_MOT_object_assembly.header[0] = '\0';
      default_MOT_object_assembly.hdrcnt = 0;
      default_MOT_object_assembly.body[0] = '\0';
      default_MOT_object_assembly.bodycnt = 0;
      default_MOT_object_assembly_information.transport_ID[0] = -1;
      default_MOT_object_assembly_information.cntID = 0;
      default_MOT_object.ID = 0;
      default_MOT_object.content_type = 0;
      default_MOT_object.content_subtype = 0;
      default_MOT_object.creation_time = 0;
      default_MOT_object.start_validity = 0;
      default_MOT_object.expire_time = 0;
      default_MOT_object.trigger_time = 0;
      default_MOT_object.version_number = 0;
      default_MOT_object.repetition_distance[0] = '\0';
      default_MOT_object.group_reference[0] = '\0';
      default_MOT_object.priority = 0;
      strcpy (default_MOT_object.label, "no label");
      strcpy (default_MOT_object.content_name, "unnamed content");
      strcpy (default_MOT_object.content_description, "undefined content");
      default_MOT_object.mime_type[0] = '\0';
      default_MOT_object.compression_type = '\0';
      default_MOT_object.header[0] = '\0';
      default_MOT_object.body[0] = '\0';
      default_MOT_object.error = 0;
      news_counter = 0;
      frame_errors_total = 0;
      defaultshadowMOT.hdr_segment_count = 0;
      defaultshadowMOT.hdr_highest_segm = 1999;
      for (i = 0; i < 10; i++)
	(defaultshadowMOT.hdr_segment_lengths)[i] = 0;
      defaultshadowMOT.body_segment_count = 0;
      defaultshadowMOT.body_highest_segm = 1999;
      defaultshadowMOT.body_cnt = 0;
      defaultshadowMOT.header_cnt = 0;
      defaultshadowMOT.header_complete = 0;
      defaultshadowMOT.body_complete = 0;

      return;
    }

  else if (runstate == RUN_STATE_INIT)

    {

      /*
         printf("sourcedecode: RUN_STATE_INIT\n");  
       */
      convenience_noise_gain = 0;
      convenience_noise_wav_ptr = 0;
      initial_source_decoding = 1;
      audio_fer = 0;
      return;
    }

  else if (runstate == RUN_STATE_FIRSTRUN)

    {

      /*
         printf("sourcedecode: FIRSTRUN\n");  
       */
      text_message = default_text_message;
      for (i = 0; i < 4; i++)

	{
	  data_units_assembly[i] = default_data_unit_assembly;
	  MOT_objects_assembly_information[i] =
	    default_MOT_object_assembly_information;
	  MOT_directories_assembly[i] = default_MOT_directory_assembly;
	}
      for (i = 0; i < 4; i++)

	{
	  for (j = 0; j < 30; j++)
	    MOT_objects_assembly[i][j] = default_MOT_object_assembly;
	}
      /*
         printf("=====   bodycnt %d\n", MOT_objects_assembly[0][0].bodycnt); 
       */
      MOT_object_assembly = MOT_objects_assembly[0][0];
      /*
         printf("=====   bodycnt %d\n", MOT_object_assembly.bodycnt); 
       */
      for (i = 0; i < 3840; i++)
	last_temp[i] = 0;

      /*
         journaline_decode(0); 
       */
      initial_source_decoding = 1;
      audio_fer = 0;
    }
  frameinfo_errors = 0;
  if (SOURCEDECODING != 1)

    {
      return;
    }

  
  /*   printf("na !SOURCEDECODING\n");  
  */
  if (channel_decoded_data_buffer_data_valid != 1)

    {
      /*
         printf("sourcedecode: ! channel_decoded_data_buffer_valid etc \n");     
       */
      for (i = 0; i < 3840; i++)
	last_temp[i] = 0;
      text_message = default_text_message;
      for (i = 0; i < 4; i++)

	{
	  data_units_assembly[i] = default_data_unit_assembly;
	  MOT_objects_assembly_information[i] =
	    default_MOT_object_assembly_information;

	  /*
	     MOT_objects_assembly[i] = default_MOT_object_assembly;  
	   */
	  for (i = 0; i < 4; i++)
	    {
	      for (j = 0; j < 30; j++)
	            MOT_objects_assembly[i][j] = default_MOT_object_assembly;
	      MOT_directories_assembly[i] = default_MOT_directory_assembly;
	    }
	}
      initial_source_decoding = 1;
      audio_fer = 0;
      return;
    }
  CRC_error_counter = 0;
  CRC_OK_counter = 0;
  if (audio_data_flag == 0)

    {
      printf ("audio decoding not implemented in rxamadrm !\n");
     /* exit (EXIT_FAILURE); */
      return ;

    }

  else
    /*
       audio_data_flag =1 
     */
    {

      /*
         Decoding of datastream 
       */
      for (i = 0; i < 4; i++)
	application_information.bytes_per_frame[i] = 0;
      service_ID = 0;
      application_domain =
	application_information.application_domain[service_ID];
      application_data = application_information.application_data[service_ID];	/* pointer ! */

      /*
         debugging  
         printf("==== application_data\n");
         for (i = 0; i < 16; i++)
         printf("%d, ", application_data[i]);
         printf("\n");           
       */
      Start_partA = 0;
      Start_partB = 0;
      N_partA = 0;
      N_partB = (int) (length_decoded_data / 8);
      data_unit_counter = 0;
      packet_mode = 1;		/* only packet mode in rxamadrm pa0mbo */
      if (packet_mode == 1)	/* packet mode */

	{
	  packet_length = N_partB - 3;
	  bits2bytes (&channel_decoded_data_buffer[Start_partB], N_partB * 8,
		      packet_buffer);
	  packet_buffer_length = N_partB;

	  length_packet_buffer = N_partB;
	  packet_buffer_index = 0;
	  while (packet_buffer_index + 1 <= length_packet_buffer)

	    {
	      header = packet_buffer[packet_buffer_index];
	      packet_ID = (header & 0x30) >> 4;
	      /*
	         printf("header %d packet_ID = %d packet_buffer_index %d\n",
	         header, packet_ID, packet_buffer_index); 
	       */
	      corresponding_service = 0;
	      /*
	         printf
	         (" in while lus A packet_buffer_index %d length packet_buffer %d\n",
	         packet_buffer_index, length_packet_buffer);
	         printf("corresp.serv %d packet_length %d packet_ID %d\n",
	         corresponding_service, packet_length, packet_ID); 
	       */
	      if (packet_buffer_index + packet_length + 3 >
		  length_packet_buffer)

		{
		  printf ("pbi is %d packet_length = %d, lpb = %d\n",
			  packet_buffer_index, packet_length,
			  length_packet_buffer);
		  goto labelout1;
		}
	      for (j = 0; j < packet_length + 3; j++)

		{
		  packet[j] = packet_buffer[packet_buffer_index + j];
		}

	      /*
	         application_information.bytes_per_frame[corresponding_service] +=
	         packet_length;   
	       */
	      crc16_bytewise (&checksum, packet, packet_length + 3);
	      printf ("crc16_bytewise checksum = %g \n", checksum);
	      if (fabs (checksum) <= DBL_EPSILON)

		{
		  CRC_OK_counter++;
		  data_unit_indicator =
		    application_information.
		    data_unit_indicator[corresponding_service];
		  /*
		     assembly of data unit 
		   */
		  if (data_unit_indicator == 1)	/* data unit transfer mode - MOT etc */

		    {
		      first_last_flag = (header & 0xC0) >> 6;
		      PPI = (header & 0x8) >> 3;
		      continuity_index = (header & 0x7);
		      /* 
		         printf(" header %d first_last_flag %d continuity_index %d PPI %d  ipbindx %d\n",
		         header, first_last_flag, continuity_index, PPI, packet_buffer_index);  
		      */
		      if (first_last_flag < 2)

			{

			  /*
			     debugging 
			     printf("=== data first/last < 2 ...contindx %d continuity_index %d corresp service %d\n",
			     data_units_assembly[corresponding_service].continuity_index, continuity_index, corresponding_service);  
			   */
			  if ((data_units_assembly[corresponding_service].
			       continuity_index != -1)
			      &&
			      (((data_units_assembly[corresponding_service].
				 continuity_index + 1) % 8) ==
			       continuity_index)
			      && (data_units_assembly[corresponding_service].
				  first_packet_received == 1))

			    {

			      /*
			         debugging 
			       */
			      printf ("=== packet_length = %d\n",
				      packet_length);
			      if (PPI == 0)

				{
				  for (j = 0; j < packet_length; j++)

				    {
				      data_units_assembly
					[corresponding_service].
					data_unit[data_units_assembly
						  [corresponding_service].
						  cnt_data_unit++] =
					packet[j + 1];
				    }
				}

			      else

				{
				  useful_bytes = packet[1];
				  printf ("useful_bytes %d \n", useful_bytes);
				  if (!
				      ((useful_bytes == 1)
				       && (packet[2] == 0)))

				    {
				      for (j = 0; j < useful_bytes; j++)

					{
					  data_units_assembly
					    [corresponding_service].
					    data_unit[data_units_assembly
						      [corresponding_service].
						      cnt_data_unit++] =
					    packet[j + 2];
					}
				    }
				}
			      data_units_assembly[corresponding_service].
				continuity_index++;
			      if (first_last_flag == 1)

				{
				  data_units_assignment[data_unit_counter] =
				    corresponding_service;
				  printf
				    ("flflg = 1 data_unit_counter %d , cnt_data_unit %d corresp serv %d\n",
				     data_unit_counter,
				     data_units_assembly
				     [corresponding_service].cnt_data_unit,
				     corresponding_service);
				  for (j = 0;
				       j <
				       data_units_assembly
				       [corresponding_service].cnt_data_unit;
				       j++)

				    {
				      data_units[data_unit_counter][j] =
					data_units_assembly
					[corresponding_service].data_unit[j];
				    }
				  data_units_cnts[data_unit_counter] =
				    data_units_assembly
				    [corresponding_service].cnt_data_unit;
				  data_units_assembly[corresponding_service] =
				    default_data_unit_assembly;
				  data_unit_counter++;
				}
			    }	/* end clause data_units ... != 1 etc */

			  else

			    {
			      data_units_assembly[corresponding_service] =
				default_data_unit_assembly;
			      printf ("nog wat extra's ?\n");

			    }
			}	/* end clause first_last_flag < 2  */

		      else
			/*
			   first_last_flag >= 2 
			 */
			{
			  if (data_units_assembly[corresponding_service].
			      continuity_index != -1)

			    {
			      data_units_assembly[corresponding_service] =
				default_data_unit_assembly;
			    }
			  data_units_assembly[corresponding_service].
			    continuity_index = continuity_index;
			  data_units_assembly[corresponding_service].
			    first_packet_received = 1;
			  if (PPI == 0)

			    {
			      for (j = 0; j < packet_length; j++)

				{
				  data_units_assembly[corresponding_service].
				    data_unit[data_units_assembly
					      [corresponding_service].
					      cnt_data_unit++] =
				    packet[1 + j];
				}
			    }

			  else

			    {
			      useful_bytes = packet[1];
			      /*
			         printf("PPI %d useful_bytes = %d\n", PPI,
			         useful_bytes); 
			       */
			      if (!((useful_bytes == 1) && (packet[2] == 0)))

				{
				  for (j = 0; j < useful_bytes; j++)

				    {
				      data_units_assembly
					[corresponding_service].
					data_unit[data_units_assembly
						  [corresponding_service].
						  cnt_data_unit++] =
					packet[j + 2];
				    }
				}
			    }
			  if (first_last_flag == 3)

			    {

			      /*
			         data_unit_counter++;  
			       */
			      data_units_assignment[data_unit_counter] =
				corresponding_service;
			      for (j = 0;
				   j <
				   data_units_assembly[corresponding_service].
				   cnt_data_unit; j++)

				{
				  data_units[data_unit_counter][j] =
				    data_units_assembly
				    [corresponding_service].data_unit[j];
				}
			      data_units_cnts[data_unit_counter] =
				data_units_assembly[corresponding_service].
				cnt_data_unit;
			      data_units_assembly[corresponding_service] =
				default_data_unit_assembly;
			      /*
			         printf
			         (" in clause flflg=3 data_unit_counter %d\n",
			         data_unit_counter);  
			       */
			      data_unit_counter++;
			    }
			}
		    }		/* data_unit_indicator == 1 */
		}		/* crc16_bytewise(packet) ==0 )  */

	      else

		{
		  CRC_error_counter++;
		}
	      packet_buffer_index += packet_length + 3;
	    }			/* end while (packet_buffer_index <= length(packet_buffer) */
	labelout1:;

	  /*
	     data unit assembly finished if there were any 
	   */
	}			/* end if packet_mode ==1 ) */
      MOT_object_counter = 0;
      cntMOTobjs = 0;
      for (data_unit_index = 0; data_unit_index < data_unit_counter;
	   data_unit_index++)

	{
	  corresponding_service = data_units_assignment[data_unit_index];
	  application_domain = 1;
	  user_application_type = 3;
	  length_application_data =
	    application_information.appl_data_length[corresponding_service];
	  cnt_data_unit = data_units_cnts[data_unit_index];
	  /* 
	     printf("xxx data_unit_index %d cnt_data_unit %d corresp.serv %d\n",
	     data_unit_index, cnt_data_unit, corresponding_service); 
	  */

	  /* 
	     printf("corresp.serv %d,  appl domain %d, user appl type %d \n",
	     corresponding_service, application_domain,
	     user_application_type);
	     printf("length_appl data %d cnt data unit %d\n",
	     length_application_data, cnt_data_unit);   
	  */
	  if (application_domain == 0)	/* DRM apllication */

	    {
	      if (length_application_data > 16)

		{
		  for (j = 0; j < length_application_data - 16; j++)

		    {
		      application_data[j] = application_data[16 + j];
		    }
		}
	    }

	  else if (application_domain == 1)	/* DAB application domain */

	    {
	      for (j = 0; j < length_application_data - 16; j++)

		{
		  application_data[j] = application_data[16 + j];
		}
	      if ((user_application_type == 2) || (user_application_type == 3))	/* MOT slideshow or broadcast website */

		{
		  for (i = 0; i < 4; i++)
		    MSC_data_group_header[i] = data_units[data_unit_index][i];
		  next_data = 2 + 2 * ((MSC_data_group_header[0] & 0x80) >> 7);	/* extension field? */

		  /* 
		     
		     printf("MSC_data_group_header %x %x %x %x  next_data = %d data_unit_index %d \n", 
		     MSC_data_group_header[3], MSC_data_group_header[2],
		     MSC_data_group_header[1], MSC_data_group_header[0],
		     next_data, data_unit_index);          
		  */
		  CRC_OK = 1;
		  if ((MSC_data_group_header[0] & 0x40) != 0)

		    {
		      crc16_bytewise (&checksum, data_units[data_unit_index],
				      cnt_data_unit);
		      printf ("crc16bytewise data_units = %g \n", checksum);
		      if (fabs (checksum) <= DBL_EPSILON)

			{
			  CRC_OK = 1;
			  CRC_OK_counter++;
			}

		      else

			{
			  CRC_OK = 0;
			  CRC_error_counter++;
			}
		    }
		  if (CRC_OK == 1)

		    {
		      if ((MSC_data_group_header[0] & 0x20) != 0)

			{
			  segment_field[0] =
			    data_units[data_unit_index][next_data];
			  segment_field[1] =
			    data_units[data_unit_index][next_data + 1];
			  last_segment = (segment_field[0] & 0x80) >> 7;
			  segment_number =
			    segment_field[1] +
			    256 * (segment_field[0] & 0x7F);
			  /*
			     printf(" last_segm in true deel %d segment nr %d\n",
			     last_segment, segment_number);  
			   */
			  printf ("cursegm %d\n", segment_number);
			  next_data += 2;
			}

		      else

			{
			  last_segment = 1;
			  segment_number = 0;
			  printf ("last segment in else \n");
			}
		      data_group_type = (MSC_data_group_header[0] & 0xF);
		      continuity_index =
			(MSC_data_group_header[1] & 0xF0) >> 4;
		      
		         /* printf
		         ("last_segment %d segment_number %d data_group_type %d cont index %d\n",
		         last_segment, segment_number, data_group_type,
		         continuity_index);  */
		       
		      if ((last_segment == 1) && (segment_number != 0))
			{
			  printf ("total segments = %d\n", segment_number);
			  tot_segments = segment_number;
			}

		      /*
		         no consideration of repetition index yet 
		       */
		      if ((MSC_data_group_header[0] & 0x10) != 0)

			{
			  user_access_field[0] =
			    data_units[data_unit_index][next_data];
			  user_access_field[1] =
			    data_units[data_unit_index][next_data + 1];
			  user_access_field[2] =
			    data_units[data_unit_index][next_data + 2];
			  length_indicator = (user_access_field[0] & 0xF);
			  next_data += 1 + length_indicator;
			  if ((length_indicator >= 2)
			      && ((user_access_field[0] & 0x10) != 0))

			    {
			      transport_ID =
				user_access_field[1] * 256 +
				user_access_field[2];
			      
				/* printf
				("length_indicator %d transport_Id %d \n",
				 length_indicator, transport_ID);	*/
                             
			    }

			  else

			    {
			      transport_ID = -1;
			    }
			}

		      else

			{
			  transport_ID = -1;
			}
		      if ((data_group_type == 3) || (data_group_type == 4))

			{
			  /*
			     pa0mbo new code for shadowMOTobj 
			   */
			  /*
			     find shadowMOTobj in array 
			   */
			  shadowMOTindx = -1;
			  for (j = 0; j < cntshadowMOTs; j++)
			    {
			      if (transport_ID ==
				  (shdwMOTobjs[j]).transport_ID) 
                              {
				shadowMOTindx = j;
                                printf("curseg is %d transport_ID is %d\n", segment_number,   transport_ID);  /* pa0mbo 9 okt 2012 */
                             /*   printf("shadowMOTindx is %d\n", shadowMOTindx); */
                              }
			    }
			  if (shadowMOTindx == -1)	/* does not yet exist create new entry */
			    {
                             if ( cntshadowMOTs >= 0 )  /* new shadow MOT to be constructde but old still present */
			     {
				 /*    printf("debugging new transport_ID started?\n"); */
				 /* pa0mbo 2-7-2012 new code to write existing shadowMOT to file */
				     shadowMOTindx = cntshadowMOTs -1 ;
                                     printf("shadowMOTindx is %d \n", shadowMOTindx); 

                            if ( (fperasures = fopen("erasures.dat", "w"))==NULL)
                            {
                                printf("Cannot open erasures.dat for write \n");
                                exit(1);
                            }
                            /* lev 2 */                           
                            /* preliminaries for writing bsr.bin */

                            if ( (fpbsrbin = fopen("bsr.bin", "w")) == NULL)
                            {
                                printf("Cannot open bsr.bin for write \n");
                                exit(1);
                            }
                            fprintf(fpbsrbin,"%d\n", transport_ID);
                            fprintf(fpbsrbin, "H_OK\n");
                           /* make shadow MOT Object */ 

			  fullshadowMOT = default_MOT_object;
			  fullshadowMOT.hdrcnt = 0;
			  fullshadowMOT.bodycnt = 0;
			  cnt =
			    (shdwMOTobjs[shadowMOTindx]).hdr_segment_count;
			   printf("debug hdr_segment_cnt is %d  shdwMOTindx %d cntshdwMOTs %d hdr_complete %d\n",
                                  cnt, shadowMOTindx, cntshadowMOTs, (shdwMOTobjs[shadowMOTindx]).header_complete);  

			  if ( !(shdwMOTobjs[shadowMOTindx]).header_complete)
				  goto labuitnohdr;


			/*  fprintf(fperasures, "hdr_segment_count is %d \n", cnt);  */
                          byteptr=0;
			  for (j = 0; j < cnt; j++)
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				hdr_segment_lengths[j];
			      /*  fprintf(fperasures, "header_segment_length[%d] = %d \n", j, bytecnt);  */
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.header[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).header[j][k];
                                byteptr++;
                              }
			      fullshadowMOT.hdrcnt += bytecnt;
			    }
			  cnt = (shdwMOTobjs[shadowMOTindx]).body_highest_segm;
                          printf("debug body_highest_segm %d \n", cnt); 
			  fprintf(fperasures,"%d \n", cnt);
                          byteptr=0;
			  /* determine segment length Dec 17th 2011 */
			  incompl_seg_length = 0;
			  for (j =0 ; j < cnt ; j++)
			  {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[j];
			      if ( bytecnt > incompl_seg_length )
				      incompl_seg_length = bytecnt;
			  }
			  /* end detmn seg length */
			 fprintf(fperasures," %d \n", incompl_seg_length); 
                         fprintf(fpbsrbin,"%d\n", incompl_seg_length);
			  for (j = 0; j < cnt  ; j++)        /* was j < cnt  May 18th 2012 july 21st 2012 weer terug van <= naar < */    
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[j];
			        /* fprintf(fperasures, "body segment_length[%d] is %d \n", j, bytecnt);  */
			      if (bytecnt != 0)
                              {
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.body[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).body[j][k];
                                byteptr++;
                              }
			      }
			      else      /* zero fill missed segments  Dec 17th 2011 */
			      {
                                fprintf(fperasures, " %d \n", j );
                                fprintf(fpbsrbin, "%d\n", j);
				for (k=0; k < incompl_seg_length ; k++)
				{
				  fullshadowMOT.body[byteptr] = 0;
                                   byteptr++;
				}
				bytecnt= incompl_seg_length ;
                              }
			      fullshadowMOT.bodycnt += bytecnt;
                            }
                              fclose(fperasures);
                              fprintf(fpbsrbin,"-99\n");
                              fclose(fpbsrbin);
			      {
				motobjparse (&fullshadowMOT);
				sprintf (service_name, "%s%d", "Service",
					 fullshadowMOT.ID);
				snprintf(filename, sizeof(filename), "incomplete/received-%lu.bin", ++bqe_file_id);
                                snprintf(metadata_filename, sizeof(metadata_filename), "%s.name", filename);

				/*
				   uncompress if compressed data 
				 */
				if ((fullshadowMOT.compression_type == 1) && (fullshadowMOT.body[0] == 31) && (fullshadowMOT.body[1] == 139) && (fullshadowMOT.body[2] == 8))	/* gzip deflate compression */

				  {
				    printf ("gzip uncompress needed \n");
				    (void) deflate_uncompress (fullshadowMOT.
							       body,
							       fullshadowMOT.
							       bodycnt,
							       altname, lname,
							       uncompressed_data,
							       &ldata_out,
							       &lhs2);
				    printf ("altname is %s\n", altname);
				    if (altname[0] != '\0')
				      strcpy (filename, altname);
				    if (ldata_out == 1)

				      {
					for (i = 0; i < fullshadowMOT.bodycnt;
					     i++)

					  {
					    uncompressed_data[i] =
					      fullshadowMOT.body[i];
					  }
					ldata_out = fullshadowMOT.bodycnt;
				      }
				  }

				else
				  /*
				     no uncompress needed 
				   */
				  {
				    for (i = 0; i < fullshadowMOT.bodycnt;
					 i++)

				      {
					uncompressed_data[i] =
					  fullshadowMOT.body[i];
				      }
				    ldata_out = fullshadowMOT.bodycnt;
				  }

				/*
				   expand code to manipulate filename later pa0mbo 
				 */
				/*
				   now only print it  Apr 24th 2012 change content file
                                   to remove spaces
				 
                                removespaces(filename); */
				if ((fpout = fopen (filename, "wb")) == NULL)

				  {
				    printf ("Cannot open file %s for write opening dum1.dat instead\n",
					    filename);
				    /* exit (EXIT_FAILURE); */
                                    fpout = fopen("dum1.dat", "w");
				  }
				for (i = 0; i < ldata_out; i++)
				  fprintf (fpout, "%c", uncompressed_data[i]);

				/*
				   fwrite(uncompressed_data+i, 1, 1, fpout);  
				 */
				 fclose (fpout);
                                printf("Na fclose van filename = %s\n", filename);
				printf ("FileclosedINCBDY %s\n", filename);
			/*	printf ("FileclosedINCBDY  ./incomplete/%s\n",
					removespaces(fullshadowMOT.content_name)); */
                                /* measures to reset and free object */
                                /* shadowMOTindx=-1;
                                 cntshadowMOTs=0;       not for incomplete ? 15-7-2012  */
			      }
			 }
			   /*
			     end extra code 2-7-2012
			   */



labuitnohdr:  
			      shdwMOTobjs[cntshadowMOTs] = defaultshadowMOT;
			      shadowMOTindx = cntshadowMOTs;
			      cntshadowMOTs++;
                              /* limit cntshadowMOTs to 10  Dec 7 2013 */
                              if (cntshadowMOTs > 99 )
                              {
                                   shadowMOTindx = -1;
                                   cntshadowMOTs =0 ;
                              }
			      (shdwMOTobjs[shadowMOTindx]).transport_ID =
				transport_ID;
	    }
			  /*
			     end of insert pa0mbo 26 feb 2009 
			   */

			  if (MOT_objects_assembly_information
			      [corresponding_service].cntID == 0)

			    {
			      MOT_object_index = -2;	/* corresp to empty */
			    }

			  else

			    {
			      MOT_object_index = -2;
			      for (j = 0;
				   j <
				   MOT_objects_assembly_information
				   [corresponding_service].cntID; j++)

				{
				  if (transport_ID ==
				      MOT_objects_assembly_information
				      [corresponding_service].transport_ID[j])

				    {
				      MOT_object_index = j;
				    }
				}
			    }

			  if (MOT_object_index == -2)	/* empty ? */

			    {
			      if (segment_number == 0)

				{
				  number_of_MOT_objects =
				    MOT_objects_assembly_information
				    [corresponding_service].cntID + 1;
				  MOT_object_index =
				    number_of_MOT_objects - 1;
				  MOT_objects_assembly_information
				    [corresponding_service].
				    transport_ID[MOT_object_index] =
				    transport_ID;
				  MOT_objects_assembly_information
				    [corresponding_service].cntID++;
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index] =
				    default_MOT_object_assembly;
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index].transport_ID =
				    transport_ID;
				}

			      else

				{
				  MOT_object_index = -1;
				}
			    }

			  else

			    {
			    }
			}
		      if (data_group_type == 3)	/* MOT header data group */

			{
			  /*
			     here extra code for handling shadowMOT 
			   */
			  if ((shdwMOTobjs[shadowMOTindx]).
			      hdr_segment_lengths[segment_number] == 0)
			    {
			      cnt = data_units_cnts[corresponding_service];
			      cnt =
				cnt -
				2 * ((MSC_data_group_header[0] & 0x40) >> 6) -
				next_data - 2;
			      for (j = 0; j < cnt; j++)
				{
				  (shdwMOTobjs[shadowMOTindx]).
				    header[segment_number][j] =
				    data_units[data_unit_index][next_data +
								2 + j];
				}
			      (shdwMOTobjs[shadowMOTindx]).
				hdr_segment_lengths[segment_number] = cnt;
			      (shdwMOTobjs[shadowMOTindx]).
				hdr_segment_count++;
			      if (last_segment == 1)
				{
				  (shdwMOTobjs[shadowMOTindx]).
				    hdr_highest_segm = segment_number;
				}
			      if (((shdwMOTobjs[shadowMOTindx]).
				   hdr_segment_count - 1) ==
				  (shdwMOTobjs[shadowMOTindx]).
				  hdr_highest_segm)
				{
				  (shdwMOTobjs[shadowMOTindx]).
				    header_complete = 1;
				}

			    }
			  if ((shdwMOTobjs[shadowMOTindx]).header_complete &&
			      (shdwMOTobjs[shadowMOTindx]).body_complete)
			    {
			      /*
			         different parts together now formed complete MOTobject 
			       */
			      /*
			         go and assemble it and write file to disk 
			       */
			      fullshadowMOT = default_MOT_object;
			      fullshadowMOT.hdrcnt = 0;
			      fullshadowMOT.bodycnt = 0;
			      cnt =
				(shdwMOTobjs[shadowMOTindx]).
				hdr_segment_count;
                              byteptr=0;
			      for (j = 0; j < cnt; j++)
				{
				  bytecnt =
				    (shdwMOTobjs[shadowMOTindx]).
				    hdr_segment_lengths[j];
				  for (k = 0; k < bytecnt; k++)
                                  {
				    fullshadowMOT.header[byteptr] =
				      (shdwMOTobjs[shadowMOTindx]).
				      header[j][k];
                                      byteptr++;
                                  }
				  fullshadowMOT.hdrcnt += bytecnt;
				}
			      cnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_count;
                              byteptr=0;
			      for (j = 0; j < cnt; j++)
				{
				  bytecnt =
				    (shdwMOTobjs[shadowMOTindx]).
				    body_segment_lengths[j];
				  for (k = 0; k < bytecnt; k++)
                                  { 
				    fullshadowMOT.body[byteptr] =
				      (shdwMOTobjs[shadowMOTindx]).body[j][k];
                                    byteptr++ ;
                                  }
				  fullshadowMOT.bodycnt += bytecnt;
				}

			      {
				motobjparse (&fullshadowMOT);
				sprintf (service_name, "%s%d", "Service",
					 fullshadowMOT.ID);
				snprintf(filename, sizeof(filename), "picsrx/received-%lu.bin", ++bqe_file_id);
                                snprintf(metadata_filename, sizeof(metadata_filename), "%s.name", filename);

				/*
				   uncompress if compressed data 
				 */
				if ((fullshadowMOT.compression_type == 1) && (fullshadowMOT.body[0] == 31) && (fullshadowMOT.body[1] == 139) && (fullshadowMOT.body[2] == 8))	/* gzip deflate compression */

				  {
				    printf ("gzip uncompress needed \n");
				    (void) deflate_uncompress (fullshadowMOT.
							       body,
							       fullshadowMOT.
							       bodycnt,
							       altname, lname,
							       uncompressed_data,
							       &ldata_out,
							       &lhs2);
				    printf ("altname is %s\n", altname);
				    if (altname[0] != '\0')
				      strcpy (filename, altname);
				    if (ldata_out == 1)

				      {
					for (i = 0; i < fullshadowMOT.bodycnt;
					     i++)

					  {
					    uncompressed_data[i] =
					      fullshadowMOT.body[i];
					  }
					ldata_out = fullshadowMOT.bodycnt;
				      }
				  }

				else
				  /*
				     no uncompress needed 
				   */
				  {
				    for (i = 0; i < fullshadowMOT.bodycnt;
					 i++)

				      {
					uncompressed_data[i] =
					  fullshadowMOT.body[i];
				      }
				    ldata_out = fullshadowMOT.bodycnt;
				  }

				/*
				   expand code to manipulate filename later pa0mbo 
				 */
				/*
				   now only print it 
				 */
                                /* removespaces(filename); */
                                if ((fpout = fopen(metadata_filename, "wb")) == NULL)
                                {
                                   printf("cannot open lastorgfname.txt\n");
                                }
                                else
                                {
                                  lengthorgfname = strlen( fullshadowMOT.content_name);
                                  if (fwrite(fullshadowMOT.content_name, lengthorgfname, 1, fpout) != 1)
                                   {
                                      printf("Could not write org filename to disk \n");
                                   }
                                   fclose(fpout);
                                } 
                                
                                asciitoutf8(filename, newfilename);
				if ((fpout = fopen (newfilename, "wb")) == NULL)

				  {
				    printf ("Cannot open file %s for write opening dum2.dat instead\n",
					    newfilename);
				    /* exit (EXIT_FAILURE); */
                                    fpout= fopen ("dum2.dat", "w");
				  }
				for (i = 0; i < ldata_out; i++)
				  fprintf (fpout, "%c", uncompressed_data[i]);

				/*
				   fwrite(uncompressed_data+i, 1, 1, fpout);  
				 */
				(void) fclose (fpout);
				printf ("FileclosedOKHDR %s\n",
					newfilename);
                               /* shadowMOTindx=-1;
                                cntshadowMOTs=0; */
			      }
			    }
			   /*
			     end extra code 26 feb 2009 
			   */

			  if (MOT_object_index >= 0)

			    {
			      MOT_object_assembly =
				MOT_objects_assembly[corresponding_service]
				[MOT_object_index];
			      if (segment_number == 0)

				{
				  MOT_object_assembly.
				    header_continuity_index =
				    continuity_index;
				  MOT_object_assembly.
				    current_header_segment_no = 0;
				  segmentation_header[0] =
				    data_units[data_unit_index][next_data];
				  segmentation_header[1] =
				    data_units[data_unit_index][next_data +
								1];
				  /* printf ("segmentation_header : %x %x \n",
					  segmentation_header[0],
					  segmentation_header[1]); */

				  cnt =
				    data_units_cnts[corresponding_service];
				  for (j = 0;
				       j <
				       cnt -
				       2 *
				       ((MSC_data_group_header[0] & 0x40) >>
					6) - next_data - 2; j++)

				    {
				      MOT_object_assembly.header[j] =
					data_units[data_unit_index][next_data
								    + 2 + j];
				      MOT_object_assembly.hdrcnt++;
				    }
				  if (last_segment == 1)

				    {
				      MOT_object_assembly.header_complete = 1;
				    }
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index] = MOT_object_assembly;
				  /*
				     printf
				     (" data_group3 cont_indx = %d cnt %d next_data %d hdrcnt %d \n",
				     continuity_index, cnt, next_data,
				     MOT_object_assembly.hdrcnt);  
				   */

				  /*
				     printf("Content of MOT_object_assembly header\n");
				     for (j=0; j < MOT_object_assembly.hdrcnt ; j++)
				     putchar(MOT_object_assembly.header[j]);
				     printf("\n");
				   */
				}

			      else
				if ((MOT_object_assembly.
				     current_header_segment_no + 1 ==
				     segment_number)
				    &&
				    (((MOT_object_assembly.
				       header_continuity_index + 1) % 16) ==
				     continuity_index))

				{
				  MOT_object_assembly.
				    header_continuity_index++;
				  MOT_object_assembly.
				    current_header_segment_no =
				    segment_number;
				  segmentation_header[0] =
				    data_units[data_unit_index][next_data];
				  segmentation_header[1] =
				    data_units[data_unit_index][next_data +
								1];
				  cnt =
				    data_units_cnts[corresponding_service];
				  for (j = 0;
				       j <
				       cnt -
				       2 *
				       ((MSC_data_group_header[0] & 0x40) >>
					6) - next_data - 2; j++)

				    {
				      MOT_object_assembly.
					header[MOT_object_assembly.hdrcnt++] =
					data_units[data_unit_index][next_data
								    + 2 + j];
				    }
				  MOT_object_assembly.
				    header[MOT_object_assembly.hdrcnt] = '\0';
				  if (last_segment == 1)

				    {
				      MOT_object_assembly.header_complete = 1;
				      printf ("dgroup3 header complete b\n");
				    }
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index] = MOT_object_assembly;
				  /*
				   */ printf
				    ("data_group3 segmn > 0 segmentnr %d contindx %d cnt %d last_segmnt %d\n", segment_number, MOT_object_assembly.header_continuity_index, cnt, last_segment);	/* */
				}
			    }	/* end if MOT_object_index >= 0 */
			}	/* end data_group_type == 3 */

		      else if (data_group_type == 4)	/* MOT body data group */

			{
			  /*
			     here extra code for handling shadowMOT 
			   */
			  if ((shdwMOTobjs[shadowMOTindx]).
			      body_segment_lengths[segment_number] == 0)
			    {
                              printf("new segment %d\n", segment_number);
			      cnt = data_units_cnts[corresponding_service];
			      cnt =
				cnt -
				2 * ((MSC_data_group_header[0] & 0x40) >> 6) -
				next_data - 2;
			      /*  printf("segment_number %d length %d  bdy segment cnt %d\n",
					      segment_number, cnt, (shdwMOTobjs[shadowMOTindx]).body_segment_count );  */
			      for (j = 0; j < cnt; j++)
				{
				  (shdwMOTobjs[shadowMOTindx]).
				    body[segment_number][j] =
				    data_units[data_unit_index][next_data +
								2 + j];
				}
			      (shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[segment_number] = cnt;
			      (shdwMOTobjs[shadowMOTindx]).
				body_segment_count++;
			      if (last_segment == 1)
				{
				  (shdwMOTobjs[shadowMOTindx]).
				    body_highest_segm = segment_number;
				}
                         /*     printf("net voor test of alle segs binnen bdy cnt %d highest %d\n",
                                    (shdwMOTobjs[shadowMOTindx]).body_segment_count, (shdwMOTobjs[shadowMOTindx]).body_highest_segm ); */
			      if (((shdwMOTobjs[shadowMOTindx]).
				   body_segment_count -1 ) ==     /*  d.d. 27 Apr 2012 was -1 */
				  (shdwMOTobjs[shadowMOTindx]).
				  body_highest_segm)
				{
				  (shdwMOTobjs[shadowMOTindx]).body_complete =
				    1;
				}
                             }

			  if ((shdwMOTobjs[shadowMOTindx]).header_complete &&
			      (shdwMOTobjs[shadowMOTindx]).body_complete)
                          {
			  fullshadowMOT = default_MOT_object;
			  fullshadowMOT.hdrcnt = 0;
			  fullshadowMOT.bodycnt = 0;
			  cnt =
			    (shdwMOTobjs[shadowMOTindx]).hdr_segment_count;
                          byteptr=0;
			  for (j = 0; j < cnt; j++)
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				hdr_segment_lengths[j];
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.header[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).header[j][k];
                                byteptr++;
                              }
			      fullshadowMOT.hdrcnt += bytecnt;
			    }
			  cnt =
			    (shdwMOTobjs[shadowMOTindx]).body_segment_count;
                          byteptr=0;
			  for (j = 0; j < cnt; j++)
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[j];
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.body[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).body[j][k];
                                byteptr++;
                              }
			      fullshadowMOT.bodycnt += bytecnt;
                            }
			    /* printf("shadowMOT bodycnt %d segmen cnt is %d\n",
					     fullshadowMOT.bodycnt, cnt);  */
			      {
				motobjparse (&fullshadowMOT);
				sprintf (service_name, "%s%d", "Service",
					 fullshadowMOT.ID);
				snprintf(filename, sizeof(filename), "picsrx/received-%lu.bin", ++bqe_file_id);
                                snprintf(metadata_filename, sizeof(metadata_filename), "%s.name", filename);

				/*
				   uncompress if compressed data 
				 */
				if ((fullshadowMOT.compression_type == 1) && (fullshadowMOT.body[0] == 31) && (fullshadowMOT.body[1] == 139) && (fullshadowMOT.body[2] == 8))	/* gzip deflate compression */

				  {
				    printf ("gzip uncompress needed \n");
				    (void) deflate_uncompress (fullshadowMOT.
							       body,
							       fullshadowMOT.
							       bodycnt,
							       altname, lname,
							       uncompressed_data,
							       &ldata_out,
							       &lhs2);
				    printf ("altname is %s\n", altname);
				    if (altname[0] != '\0')
				      strcpy (filename, altname);
				    if (ldata_out == 1)

				      {
					for (i = 0; i < fullshadowMOT.bodycnt;
					     i++)

					  {
					    uncompressed_data[i] =
					      fullshadowMOT.body[i];
					  }
					ldata_out = fullshadowMOT.bodycnt;
				      }
				  }

				else
				  /*
				     no uncompress needed 
				   */
				  {
				    for (i = 0; i < fullshadowMOT.bodycnt;
					 i++)

				      {
					uncompressed_data[i] =
					  fullshadowMOT.body[i];
				      }
				    ldata_out = fullshadowMOT.bodycnt;
				  }

				/*
				   expand code to manipulate filename later pa0mbo 
				 */
				/*
				   now only print it 
				 */
                                /* removespaces(filename); */
                                if ((fpout = fopen(metadata_filename, "wb")) == NULL)
                                {
                                   printf("cannot open lastorgfname.txt\n");
                                }
                                else
                                {
                                  lengthorgfname = strlen( fullshadowMOT.content_name);
                                  if (fwrite(fullshadowMOT.content_name, lengthorgfname, 1, fpout) != 1)
                                   {
                                      printf("Could not write org filename to disk \n");
                                   }
                                   fclose(fpout);
                                } 
                                asciitoutf8(filename, newfilename);
				if ((fpout = fopen (newfilename, "wb")) == NULL)

				  {
				    printf ("Cannot open file %s for write opening dum3.dat instead\n",
					    newfilename);
				    /* exit (EXIT_FAILURE); */
                                    fpout = fopen( "dum3.dat", "w");
				  }
				for (i = 0; i < ldata_out; i++)
				  fprintf (fpout, "%c", uncompressed_data[i]);

				/*
				   fwrite(uncompressed_data+i, 1, 1, fpout);  
				 */
				(void) fclose (fpout);
				printf ("FileclosedOKBDY %s\n",
					newfilename);
                                /* measures to reset and free object */
                               /*  shadowMOTindx--;
                                 cntshadowMOTs--; */

			      }
			    }
			  /*
			     end extra code 26 feb 2009 
			   */


         /* here code to handle incomplete shadowMOTobject  21 Nov 2011 
            added code to write erasures file    May 14th 2012 */
			  if ((shdwMOTobjs[shadowMOTindx]).header_complete &&
			      !(shdwMOTobjs[shadowMOTindx]).body_complete && ((last_segment==1))
				  || (( segment_number + 3 ) ==  (shdwMOTobjs[shadowMOTindx]).body_highest_segm )
				  || (( segment_number + 1 ) ==  (shdwMOTobjs[shadowMOTindx]).body_highest_segm ))   /* digtrx error + bsr */
                          {
                            if ( (fperasures = fopen("erasures.dat", "w"))==NULL)
                            {
                                printf("Cannot open erasures.dat for write \n");
                                exit(1);
                            }
                            if ( (fpbsrbin = fopen("./bsr.bin", "w"))==NULL)
                            {
                                printf("Cannot open ./bsr.bin for write \n");
                                exit(1);
                            }
                            fprintf(fpbsrbin,"%d\n",transport_ID);
                            fprintf(fpbsrbin,"H_OK\n");
			  fullshadowMOT = default_MOT_object;
			  fullshadowMOT.hdrcnt = 0;
			  fullshadowMOT.bodycnt = 0;
			  cnt =
			    (shdwMOTobjs[shadowMOTindx]).hdr_segment_count;
			  /* fprintf(fperasures, "hdr_segment_count is %d \n", cnt); */
                          byteptr=0;
			  for (j = 0; j < cnt; j++)
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				hdr_segment_lengths[j];
			      /*  fprintf(fperasures, "header_segment_length[%d] = %d \n", j, bytecnt); */
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.header[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).header[j][k];
                                byteptr++;
                              }
			      fullshadowMOT.hdrcnt += bytecnt;
			    }
			  cnt = (shdwMOTobjs[shadowMOTindx]).body_highest_segm;
			  fprintf(fperasures,"%d \n", cnt);
                          byteptr=0;
			  /* determine segment length Dec 17th 2011 */
			  incompl_seg_length = 0;
			  for (j =0 ; j < cnt ; j++)
			  {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[j];
			      if ( bytecnt > incompl_seg_length )
				      incompl_seg_length = bytecnt;
			  }
			  /* end detmn seg length */
			 fprintf(fperasures," %d \n", incompl_seg_length); 
                         fprintf(fpbsrbin, "%d\n",incompl_seg_length);

			  for (j = 0; j <= cnt  ; j++)        /* was j < cnt  May 18th 2012 */    
			    {
			      bytecnt =
				(shdwMOTobjs[shadowMOTindx]).
				body_segment_lengths[j];
			        /* fprintf(fperasures, "body segment_length[%d] is %d \n", j, bytecnt);  */
			      if (bytecnt != 0)
                              {
			      for (k = 0; k < bytecnt; k++)
                              {
				fullshadowMOT.body[byteptr] =
				  (shdwMOTobjs[shadowMOTindx]).body[j][k];
                                byteptr++;
                              }
			      }
			      else      /* zero fill missed segments  Dec 17th 2011 */
			      {
                                fprintf(fperasures, " %d \n", j );
                                fprintf(fpbsrbin,"%d\n",j);
				for (k=0; k < incompl_seg_length ; k++)
				{
				  fullshadowMOT.body[byteptr] = 0;
                                   byteptr++;
				}
				bytecnt= incompl_seg_length ;
                              }
			      fullshadowMOT.bodycnt += bytecnt;
                            }
                              fclose(fperasures);
                               fprintf(fpbsrbin,"-99\n");
                               fclose(fpbsrbin);
			      {
				motobjparse (&fullshadowMOT);
				sprintf (service_name, "%s%d", "Service",
					 fullshadowMOT.ID);
				snprintf(filename, sizeof(filename), "incomplete/received-%lu.bin", ++bqe_file_id);
                                snprintf(metadata_filename, sizeof(metadata_filename), "%s.name", filename);

				/*
				   uncompress if compressed data 
				 */
				if ((fullshadowMOT.compression_type == 1) && (fullshadowMOT.body[0] == 31) && (fullshadowMOT.body[1] == 139) && (fullshadowMOT.body[2] == 8))	/* gzip deflate compression */

				  {
				    printf ("gzip uncompress needed \n");
				    (void) deflate_uncompress (fullshadowMOT.
							       body,
							       fullshadowMOT.
							       bodycnt,
							       altname, lname,
							       uncompressed_data,
							       &ldata_out,
							       &lhs2);
				    printf ("altname is %s\n", altname);
				    if (altname[0] != '\0')
				      strcpy (filename, altname);
				    if (ldata_out == 1)

				      {
					for (i = 0; i < fullshadowMOT.bodycnt;
					     i++)

					  {
					    uncompressed_data[i] =
					      fullshadowMOT.body[i];
					  }
					ldata_out = fullshadowMOT.bodycnt;
				      }
				  }

				else
				  /*
				     no uncompress needed 
				   */
				  {
				    for (i = 0; i < fullshadowMOT.bodycnt;
					 i++)

				      {
					uncompressed_data[i] =
					  fullshadowMOT.body[i];
				      }
				    ldata_out = fullshadowMOT.bodycnt;
				  }

				/*
				   expand code to manipulate filename later pa0mbo 
				 */
				/*
				   now only print it  Apr 24th 2012 change content file
                                   to remove spaces
				 
                                removespaces(filename); */
                                if ((fpout = fopen(metadata_filename, "wb")) == NULL)
                                {
                                   printf("cannot open lastorgfname.txt\n");
                                }
                                else
                                {
                                  lengthorgfname = strlen( fullshadowMOT.content_name);
                                  if (fwrite(fullshadowMOT.content_name, lengthorgfname, 1, fpout) != 1)
                                   {
                                      printf("Could not write org filename to disk \n");
                                   }
                                   fclose(fpout);
                                } 
                                asciitoutf8(filename, newfilename);
				if ((fpout = fopen (newfilename, "wb")) == NULL)

				  {
				    printf ("Cannot open file %s for write opening dum4.dat instead\n",
					    newfilename);
				    /* exit (EXIT_FAILURE); */
                                    fpout = fopen("dum4.dat", "w");
				  }
				for (i = 0; i < ldata_out; i++)
				  fprintf (fpout, "%c", uncompressed_data[i]);

				/*
				   fwrite(uncompressed_data+i, 1, 1, fpout);  
				 */
				 fclose (fpout);
				printf ("FileclosedINCBDY %s\n", newfilename);
			/*	printf ("FileclosedINCBDY  ./incomplete/%s\n",
					fullshadowMOT.content_name); */
                                /* measures to reset and free object */
                               /*  shadowMOTindx=-1;
                                 cntshadowMOTs=0;  not for incomplete ? */

			      }
			 }
			   /*
			     end extra code 21 Nov 2011
			   */



			  if (MOT_object_index >= 0)

			  { 
		             /* printf("MOT_object_index is >= 0 \n"); */
			      MOT_object_assembly =
				MOT_objects_assembly[corresponding_service]
				[MOT_object_index];
			      /*
			         printf("oOo bodycnt %d last_segment %d\n",
			         MOT_object_assembly.bodycnt,
			         last_segment);  
			       */
			      if (segment_number == 0)

				{
				  MOT_object_assembly.body_continuity_index =
				    continuity_index;
				  MOT_object_assembly.
				    current_body_segment_no = 0;
				  segmentation_header[0] =
				    data_units[data_unit_index][next_data];
				  segmentation_header[1] =
				    data_units[data_unit_index][next_data +
								1];
				  cnt =
				    data_units_cnts[corresponding_service];
				  /*
				     printf
				     ("dgrp 4 s = 0 cnt = %d hdr[0]= %x next_data = %d bodycnt = %d\n",
				     cnt, MSC_data_group_header[0], next_data,
				     MOT_object_assembly.bodycnt);  
				   */
				  hulp = cnt - 2 *
				    ((MSC_data_group_header[0] & 0x40) >> 6) -
				    next_data - 2;
				  printf ("hulp1 = %d\n", hulp);
				  for (j = 0; j < cnt -
				       2 *
				       ((MSC_data_group_header[0] & 0x40) >>
					6) - next_data - 2; j++)

				    {
				      MOT_object_assembly.body[j] =
					data_units[data_unit_index][next_data
								    + 2 + j];
				      MOT_object_assembly.bodycnt++;
				    }
				  if (last_segment == 1)

				    {
				      MOT_object_assembly.body_complete = 1;
				    }
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index] = MOT_object_assembly;
				}	/* segment_number == 0 */

			      else if (((MOT_object_assembly.current_body_segment_no + 1) == segment_number))	/*    &&
														   ( ((MOT_object_assembly.body_continuity_index + 1)%16)== continuity_index))   */

				{
				  MOT_object_assembly.body_continuity_index++;
				  MOT_object_assembly.
				    current_body_segment_no = segment_number;
				  segmentation_header[0] =
				    data_units[data_unit_index][next_data];
				  segmentation_header[1] =
				    data_units[data_unit_index][next_data +
								1];
				  cnt =
				    data_units_cnts[corresponding_service];
				  /*
				     printf
				     ("dgrp 4 s != 0 cnt = %d hdr[0]= %x next_data = %d bodycnt %d\n",
				     cnt, MSC_data_group_header[0], next_data,
				     MOT_object_assembly.bodycnt);  
				   */
				  hulp =
				    cnt -
				    2 *
				    ((MSC_data_group_header[0] & 0x40) >> 6) -
				    next_data - 2;
				  for (j = 0;
				       j <
				       cnt -
				       2 *
				       ((MSC_data_group_header[0] & 0x40) >>
					6) - next_data - 2; j++)

				    {
				      MOT_object_assembly.
					body[MOT_object_assembly.bodycnt++] =
					data_units[data_unit_index][next_data
								    + 2 + j];
				    }
				  if (last_segment == 1)

				    {
				      MOT_object_assembly.body_complete = 1;
				    }
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index] = MOT_object_assembly;
				}	/* end clause currbodysegmentno +1 == segment_number */
			      else
				{
				  /* printf ("segment gemist currbody %d segment_number %d\n",
				     MOT_object_assembly.  current_body_segment_no, segment_number); */
				  if (last_segment == 1)

				    {
				      MOT_object_assembly.body_complete = 1;
				    }
				}
			    }	/* end clause if MOT_object_index >= 0 */
			}

		      else if (data_group_type == 5)	/* MOT body data group, CA parameters */

			{

			  /*
			     not implemented 
			   */
			}

		      else if (data_group_type == 6)	/* MOT directory */

			{
			  printf ("in data_group_type 6\n");

			  /*
			     only 1 directory/caroussel per service 
			   */
			  MOT_directory_assembly =
			    MOT_directories_assembly[corresponding_service];
			  printf
			    ("dgroup6 transport_ID = %d MOT_transp %d segment_nr %d\n",
			     transport_ID,
			     MOT_directory_assembly.transport_ID,
			     segment_number);
			  if (MOT_directory_assembly.transport_ID !=
			      transport_ID)

			    {
			      MOT_directory_assembly =
				default_MOT_directory_assembly;
			    }
			  if (segment_number == 0)

			    {
			      MOT_directory_assembly.transport_ID =
				transport_ID;
			      MOT_directory_assembly.continuity_index =
				continuity_index;
			      MOT_directory_assembly.current_segment_no = 0;
			      segmentation_header[0] =
				data_units[data_unit_index][next_data];
			      segmentation_header[1] =
				data_units[data_unit_index][next_data + 1];
			      cnt = data_units_cnts[corresponding_service];
			      printf
				("dgroup6 transport_ID %d cont index %d cnt %d next_data %d MSChdr  %x\n",
				 transport_ID, continuity_index, cnt,
				 next_data, MSC_data_group_header[0]);
			      MOT_directory_assembly.bodycnt = 0;
			      for (j = 0;
				   j <
				   cnt -
				   2 *
				   ((MSC_data_group_header[0] & 0x40) >> 6) -
				   next_data - 2; j++)

				{
				  MOT_directory_assembly.
				    body[MOT_directory_assembly.bodycnt++] =
				    data_units[data_unit_index][next_data +
								2 + j];
				}
			      printf ("bodycnt = %d \n",
				      MOT_directory_assembly.bodycnt);
			      if (last_segment == 1)

				{
				  MOT_directory_assembly.body_complete = 1;
				}
			      MOT_directories_assembly[corresponding_service]
				= MOT_directory_assembly;
			    }

			  else
			    if (((MOT_directory_assembly.current_segment_no +
				  1) == segment_number)
				&&
				(((MOT_directory_assembly.continuity_index +
				   1) % 16) == continuity_index))

			    {
			      printf
				("start else if clause segmn != 0 dus \n");
			      MOT_directory_assembly.continuity_index++;
			      MOT_directory_assembly.current_segment_no =
				segment_number;
			      segmentation_header[0] =
				data_units[data_unit_index][next_data];
			      segmentation_header[1] =
				data_units[data_unit_index][next_data + 1];
			      cnt = data_units_cnts[corresponding_service];
			      for (j = 0;
				   j <
				   cnt -
				   2 *
				   ((MSC_data_group_header[0] & 0x40) >> 6) -
				   next_data - 2; j++)

				{
				  MOT_directory_assembly.
				    body[MOT_directory_assembly.bodycnt++] =
				    data_units[data_unit_index][next_data +
								1 + j];
				}
			      if (last_segment == 1)

				{
				  MOT_directory_assembly.body_complete = 1;
				}
			      MOT_directories_assembly[corresponding_service]
				= MOT_directory_assembly;
			      printf ("else clause Mot Dir cnt= %d\n", cnt);
			    }
			  if (MOT_directory_assembly.body_complete == 1)

			    {
			      for (j = 0; j < 13; j++)

				{
				  directory_header[j] =
				    MOT_directory_assembly.body[j];
				  printf ("dir_hdr[%d] = %x \n", j,
					  directory_header[j]);
				}
			      directory_size =
				(directory_header[0] & 0x3F) * 16777216 +
				directory_header[1] * 65536 +
				directory_header[2] * 256 +
				directory_header[3];
			      number_of_objects =
				directory_header[4] * 256 +
				directory_header[5];
			      carousel_period =
				directory_header[6] * 65536 +
				directory_header[7] * 256 +
				directory_header[8];
			      segment_size =
				256 * (directory_header[9] & 0x0F) +
				directory_header[10];
			      directory_extension_length =
				directory_header[11] * 256 +
				directory_header[12];
			      printf
				("MOT dir dirsize %ld numberbytes %d carper %d segsiz %d dirextl %d\n",
				 directory_size, number_of_objects,
				 carousel_period, segment_size,
				 directory_extension_length);
			      for (j = 0; j < directory_extension_length; j++)

				{
				  directory_extension[j] =
				    MOT_directory_assembly.body[13 + j];
				}
			      directory_entry_index =
				13 + directory_extension_length;
			      cntIDs = 0;	/* equivalent if entry_transport_IDs = [] */
			      entry_number = 0;
			      while ((long int) (directory_entry_index + 9 +
						 1) <= directory_size)

				{
				  entry_transport_IDs[entry_number] =
				    MOT_directory_assembly.
				    body[directory_entry_index] * 256 +
				    MOT_directory_assembly.
				    body[directory_entry_index + 1];
				  MOT_object_index = -2;
				  printf
				    (" grp 6 entry_transport_IDs[%d] = %d cntID = %d  direntrindx %d dir_size %ld\n",
				     entry_number,
				     entry_transport_IDs[entry_number],
				     MOT_objects_assembly_information
				     [corresponding_service].cntID,
				     directory_entry_index, directory_size);
				  for (j = 0;
				       j <
				       MOT_objects_assembly_information
				       [corresponding_service].cntID; j++)

				    {
				      if (MOT_objects_assembly_information
					  [corresponding_service].
					  transport_ID[j] ==
					  entry_transport_IDs[entry_number])

					{
					  MOT_object_index = j;
					}
				    }
				  printf ("MOT_object_index = %d\n",
					  MOT_object_index);
				  if (MOT_object_index == -2)

				    {
				      number_of_MOT_objects =
					MOT_objects_assembly_information
					[corresponding_service].cntID;
				      MOT_object_index =
					number_of_MOT_objects - 1;
				      MOT_objects_assembly_information
					[corresponding_service].
					transport_ID[MOT_object_index] =
					entry_transport_IDs[entry_number];
				      MOT_objects_assembly
					[corresponding_service]
					[MOT_object_index] =
					default_MOT_object_assembly;
				      MOT_objects_assembly
					[corresponding_service]
					[MOT_object_index].transport_ID =
					entry_transport_IDs[entry_number];
				      printf
					("grp 6 MOT_object_index = %d entry_number = %d directory_entry_index %d dirsize %ld\n",
					 MOT_object_index, entry_number,
					 directory_entry_index,
					 directory_size);
				    }
				  for (j = 0; j < 6; j++)

				    {
				      MOT_header_part[j] =
					MOT_directory_assembly.
					body[directory_entry_index + 2 + j];

				      /*
				         printf("grp 6 MOT_header_part[%d] = %d \n", j, MOT_header_part[j]); 
				       */
				    }
				  header_size =
				    (MOT_header_part[3] & 0x0F) * 512 +
				    MOT_header_part[4] * 2 +
				    ((MOT_header_part[5] & 0x80) >> 7);
				  printf ("grp 6 header_size = %d \n",
					  header_size);
				  for (j = 0; j < header_size; j++)

				    {
				      MOT_objects_assembly
					[corresponding_service]
					[MOT_object_index].header[j] =
					MOT_directory_assembly.
					body[directory_entry_index + 2 + j];
				    }
				  for (j = header_size;
				       j <
				       header_size +
				       directory_extension_length; j++)

				    {
				      MOT_objects_assembly
					[corresponding_service]
					[MOT_object_index].header[j] =
					directory_extension[j - header_size];
				    }
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index].header_complete = 1;
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index].hdrcnt =
				    header_size + directory_extension_length;
				  directory_entry_index += 2 + header_size;
				  entry_number++;
				  printf
				    ("direntryindx = %d entry_number %d hdrcnt %d \n",
				     directory_entry_index, entry_number,
				     MOT_objects_assembly
				     [corresponding_service]
				     [MOT_object_index].hdrcnt);
				}

			      /*
			         delete objects not contained in this directory 
			       */
			      cnt_deprecated_objects = 0;
			      for (j = 0;
				   j <
				   MOT_objects_assembly_information
				   [corresponding_service].cntID; j++)

				{
				  is_in = 0;
				  for (k = 0; k < entry_number; k++)

				    {
				      if (MOT_objects_assembly_information
					  [corresponding_service].
					  transport_ID[j] ==
					  entry_transport_IDs[k])

					{
					  is_in = 1;
					}
				    }
				  if (is_in == 0)

				    {
				      deprecated_objects
					[cnt_deprecated_objects++] =
					MOT_objects_assembly_information
					[corresponding_service].
					transport_ID[j];
				    }
				}
			      printf ("cnt_deprecated_objects %d\n",
				      cnt_deprecated_objects);
			      for (MOT_object_index = 0;
				   MOT_object_index < cnt_deprecated_objects;
				   MOT_object_index++)

				{
				  MOT_objects_assembly[corresponding_service]
				    [MOT_object_index].delete = 1;
				}
			      MOT_directories_assembly[corresponding_service].
				body_complete = 0;
			    }
			}	/* end data_group_type = 6 */

		      /*
		         sort completed or for deletion marked MOT objects  
		       */
		      MOT_object_index = 0;
		      while ((MOT_object_index + 1) <=
			     MOT_objects_assembly_information
			     [corresponding_service].cntID)

			{
			  last_object =
			    MOT_objects_assembly_information
			    [corresponding_service].cntID;
			  /*
			     printf
			     ("in  sort MOTobjindx %d last_object %d Id %d\n",
			     MOT_object_index, last_object,
			     MOT_objects_assembly_information
			     [corresponding_service].cntID); 
			   */
			  MOT_object_assembly =
			    MOT_objects_assembly[corresponding_service]
			    [MOT_object_index];
			  if ((MOT_object_assembly.header_complete == 1)
			      && (MOT_object_assembly.body_complete == 1))

			    {
			      /*
			         printf
			         ("MOT_object_assembly header en body complete\n"); 
			       */
			      if (MOT_object_assembly.delete != 1)

				{
				  /*
				     printf
				     ("in delete van while lus MOT_object_counter is %d\n",
				     MOT_object_counter); 
				   */
				  MOT_object_counter++;
				  /*
				     printf
				     ("MOT_object_counter = %d na ++  hdrcnt %d data_unit_index %d\n",
				     MOT_object_counter,
				     MOT_object_assembly.hdrcnt,
				     data_unit_index); 
				   */
				  MOT_objects[MOT_object_counter - 1] =
				    default_MOT_object;
				  for (j = 0; j < MOT_object_assembly.hdrcnt;
				       j++)

				    {
				      MOT_objects[MOT_object_counter -
						  1].header[j] =
					MOT_object_assembly.header[j];
				      /*
				         printf("%d toegevoegd hdrbyte %x \n", j,
				         MOT_object_assembly.header[j]); 
				       */
				    }
				  MOT_objects[MOT_object_counter - 1].hdrcnt =
				    MOT_object_assembly.hdrcnt;
				  for (j = 0; j < MOT_object_assembly.bodycnt;
				       j++)

				    {
				      MOT_objects[MOT_object_counter -
						  1].body[j] =
					MOT_object_assembly.body[j];
				    }
				  MOT_objects[MOT_object_counter -
					      1].bodycnt =
				    MOT_object_assembly.bodycnt;
				  MOT_objects[MOT_object_counter - 1].ID =
				    corresponding_service;
				  /*
				     printf("MOT_objs[..].ID = %d \n",
				     MOT_objects[MOT_object_counter -
				     1].ID); 
				   */
				}
			      tmpindx = 0;
			      for (j = 0; j < last_object; j++)

				{
				  if (j != (MOT_object_index))
				    MOT_objects_assembly
				      [corresponding_service][tmpindx++] =
				      MOT_objects_assembly
				      [corresponding_service][j];
				}
			      tmpindx = 0;
			      MOT_objects_assembly_information
				[corresponding_service].cntID = 0;
			      for (j = 0; j < last_object; j++)

				{
				  if (j != MOT_object_index)

				    {
				      MOT_objects_assembly_information
					[corresponding_service].
					transport_ID[tmpindx++] =
					MOT_objects_assembly_information
					[corresponding_service].
					transport_ID[j];
				      MOT_objects_assembly_information
					[corresponding_service].cntID++;
				    }
				}
			    }

			  else

			    {
			      MOT_object_index++;
			    } 
			}	/* end while MOT_object_index < .. */
		    }		/* end if CRC OK */

		  /*
		     MOT object assembly finished 
		   */
		}		/* end if user_application_type 2 or 3 */

	      else if (user_application_type == 1098)	/* journaline news */

		{

		  /*
		     DRM data unit corresponds to a DAB MSC data group 
		     application_data = application_information.application_data[corresponding_service]; 
		   */
		}
	    }

	  else

	    {

	      /*
	         reserved for future use 
	       */
	    }
	}			/* end for data_unit_index loop */

      /*
         saving complete MOT objects 
       */
      for (MOT_object_index = 0; MOT_object_index < MOT_object_counter;
	   MOT_object_index++)
	{
	  motobjparse (&MOT_objects[MOT_object_index]);
	  MOT_object = MOT_objects[MOT_object_index];
	  sprintf (service_name, "%s%d", "Service", MOT_object.ID);
	  snprintf(filename, sizeof(filename), "picsrx/received-%lu.bin", ++bqe_file_id);
                                snprintf(metadata_filename, sizeof(metadata_filename), "%s.name", filename);
	  /*
	     printf("MOT_obj_index %d service_name = %s filename = %s\n",
	     MOT_object_index, service_name, filename);
	     printf("MOT_object_index %d, MOT_object_counter %d \n",
	     MOT_object_index, MOT_object_counter);  
	   */
	  /*
	     uncompress if compressed data 
	   */
	  if ((MOT_object.compression_type == 1) && (MOT_object.body[0] == 31) && (MOT_object.body[1] == 139) && (MOT_object.body[2] == 8))	/* gzip deflate compression */

	    {
	      printf ("gzip uncompress needed \n");
	      (void) deflate_uncompress (MOT_object.body, MOT_object.bodycnt,
					 altname, lname, uncompressed_data,
					 &ldata_out, &lhs2);
	      printf ("altname is %s\n", altname);
	      if (altname[0] != '\0')
		strcpy (filename, altname);
	      if (ldata_out == 1)

		{
		  for (i = 0; i < MOT_object.bodycnt; i++)

		    {
		      uncompressed_data[i] = MOT_object.body[i];
		    }
		  ldata_out = MOT_object.bodycnt;
		}
	    }

	  else
	    /*
	       no uncompress needed 
	     */
	    {
	      for (i = 0; i < MOT_object.bodycnt; i++)

		{
		  uncompressed_data[i] = MOT_object.body[i];
		}
	      ldata_out = MOT_object.bodycnt;
	    }
	  /*
	     expand code to manipulate filename later pa0mbo 
	   */
	  /*
	     now only print it 
	   */
          /* removespaces(filename); */
           if ((fpout = fopen(metadata_filename, "wb")) == NULL)
           {
               printf("cannot open lastorgfname.txt\n");
           }
           else
           {
              lengthorgfname = strlen( MOT_object.content_name);
              if (fwrite(MOT_object.content_name, lengthorgfname, 1, fpout) != 1)
              {
                printf("Could not write org filename to disk \n");
              }
              fclose(fpout);
           } 
          /* convert ifilename to to utf8   */
          asciitoutf8(filename,newfilename);
	  if ((fpout = fopen (newfilename, "wb")) == NULL)

	    {
	      printf ("Cannot open file %s for write opening dum5.dat instead\n", filename);
	      /* exit (EXIT_FAILURE); */
              fpout = fopen("dum5.dat" , "w");
	    }
	  for (i = 0; i < ldata_out; i++)
	    fprintf (fpout, "%c", uncompressed_data[i]);

	  /*
	     fwrite(uncompressed_data+i, 1, 1, fpout);  
	   */
	  (void) fclose (fpout);
	  printf ("FileclosedOKALL %s\n", newfilename);
	}
    }				/* end else clause data decoding */

  /*
     decoding error visualization 
   */
  if (CRC_error_counter == 0)

    {
      /*
         printf("no crc errors in source decode\n"); 
       */
    }

  else if (CRC_OK_counter == 0)

    {
      printf ("crc errors in source decode CRC_OK counter =0 \n");
    }

  else

    {
      printf ("crc errors but CRC_OK counter > 0\n");
    }
}
