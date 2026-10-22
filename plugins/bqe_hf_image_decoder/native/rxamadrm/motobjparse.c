/*
*
*     file motobjparse.c
*
*     C-version of MOT_object_parsing.m
*     MATLAB code by Torsten Schorr 2005
*
*     Author M.Bos - PA0MBO
*
*     Date Feb 21st 2009
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
#include <string.h>
#include <math.h>
#include "structtemplates.h"
#include "drmproto.h"
void motobjparse(struct dfltMOTobj *MOT_object)
{
  int header_size, body_size;
  int extension_index;
  int PLI, Param_ID;
  unsigned char data_field[100];
  int data_field_length;
  int i;
  int character_set_indicator;
  unsigned char character_field[100];
  int character_flag_field;
  unsigned char header_extension[900];
  int extension_length;


  /* header parsing */
  /*   for (i = 0; i < 7; i++)
    printf("header[%d] = %x \n", i, MOT_object->header[i]);  */
  body_size =
    (int) (((MOT_object->header[3]) & 0xF0) >> 4) +
    (int) (MOT_object->header[2]) * 16 +
    (int) (MOT_object->header[1]) * 4096 +
    (int) (MOT_object->header[0]) * 1048576;
  header_size =
    ((int) (MOT_object->header[3] & 0x0F) * 512) +
    (int) (MOT_object->header[4]) * 2 +
    (((int) (MOT_object->header[5]) & 0x80) >> 7);
  MOT_object->content_type = (int) ((MOT_object->header[5] & 0x7E) >> 1);
  MOT_object->content_subtype =
    (((int) (MOT_object->header[5]) & 0x1) * 256) +
    (int) (MOT_object->header[6]);
    /* printf("bodsiz %d hdrsiz %d contenttype %d cntsubstype %d\n", body_size,
	 header_size, MOT_object->content_type, MOT_object->content_subtype);   */

  /* header extension parsing */
  extension_index = 0;
  extension_length = MOT_object->hdrcnt - 7;
  for (i = 0; i < extension_length; i++)
    header_extension[i] = MOT_object->header[i + 7];
  while ((extension_index + 7) < MOT_object->hdrcnt)

    {
      PLI = (int) ((MOT_object->header[7 + extension_index] & 0xC0) >> 6);
      Param_ID = (int) (MOT_object->header[7 + extension_index] & 0x3F);
      if (PLI == 0)		/* reserved for future use */

	{
	  extension_index++;
	  goto label1;
	}

      else if (PLI == 1)

	{
	  data_field[0] = header_extension[extension_index + 1];
	  data_field_length = 1;
	  extension_index += 2;
	}

      else if (PLI == 2)

	{
	  data_field[0] = header_extension[extension_index + 1];
	  data_field[1] = header_extension[extension_index + 2];
	  data_field[2] = header_extension[extension_index + 3];
	  data_field[3] = header_extension[extension_index + 4];
	  data_field_length = 4;
	  extension_index += 5;
	}

      else
	/* PLI == 3 */
	{
	  if ((header_extension[extension_index + 1] & 0x80) ==
	      (unsigned char) 0)

	    {
	      data_field_length =
		(int) (header_extension[extension_index + 1] & 0x7F);
	      for (i = 0; i < data_field_length; i++)

		{
		  data_field[i] = header_extension[extension_index + 2 + i];
		}
	      extension_index += 2 + data_field_length;
	    }

	  else

	    {
	      data_field_length =
		(int) (header_extension[extension_index + 1] & 0x7F) * 256 +
		(int) header_extension[extension_index + 2];
	      for (i = 0; i < data_field_length; i++)

		{
		  data_field[i] = header_extension[extension_index + 3 + i];
		}
	      extension_index += 3 + data_field_length;
	    }
	}
      if (Param_ID == 2)	/* Creation Time */

	{
	  MOT_object->creation_time = (int) data_field[0];	/* pa0mbo check if 1 byte ? */
	}

      else if (Param_ID == 3)	/* Start validity */

	{
	  MOT_object->start_validity = (int) data_field[0];
	}

      else if (Param_ID == 4)	/* expire time */

	{
	  MOT_object->expire_time = (int) data_field[0];
	}

      else if (Param_ID == 5)	/* Trigger Time */

	{
	  MOT_object->trigger_time = (int) data_field[0];
	}

      else if (Param_ID == 6)	/*  Version Number */

	{
	  MOT_object->version_number = (int) data_field[0];
	}

      else if (Param_ID == 7)	/* Re-transmission Distance */

	{
	  MOT_object->repetition_distance[0] = data_field[1];
	  MOT_object->repetition_distance[1] = data_field[2];
	  MOT_object->repetition_distance[2] = data_field[3];
	}

      else if (Param_ID == 8)	/* Group Reference */

	{
	  MOT_object->group_reference[0] = data_field[0];
	}

      else if (Param_ID == 10)	/* Priority */

	{
	  MOT_object->priority = (int) data_field[0];
	}

      else if (Param_ID == 11)	/* label */

	{
	  character_set_indicator = (int) (data_field[0] & 0xF);
	  for (i = 0; i < 16; i++)

	    {
	      character_field[i] = data_field[1 + i];
	      MOT_object->label[i] = (char) data_field[1 + i];
	    } character_flag_field =
	    (int) data_field[16] + 256 * (int) data_field[17];
	}

      else if (Param_ID == 12)	/* content name */

	{
	  character_set_indicator = (int) (data_field[0] & 0xF);
	  for (i = 0; i < data_field_length - 1; i++)

	    {
	      character_field[i] = data_field[1 + i];
	      MOT_object->content_name[i] = (char) data_field[1 + i];
	    } MOT_object->content_name[data_field_length - 1] = '\0';	/* end of string needed in C? pa0mbo */
	}

      else if (Param_ID == 15)	/* content description */

	{
	  character_set_indicator = (int) (data_field[0] & 0xF);
          printf("content description of Mot_object\n");
	  for (i = 0; i < data_field_length - 1; i++)

	    {
	      character_field[i] = data_field[1 + i];
	      MOT_object->content_description[i] = (char) data_field[1 + i];
              putchar(character_field[i]);
	}}

      else if (Param_ID == 16)	/* Mime type */

	{
	  for (i = 0; i < data_field_length; i++)

	    {
	      MOT_object->mime_type[i] = data_field[i];
	    }
	}

      else if (Param_ID == 17)	/* compression type */

	{
	  MOT_object->compression_type = data_field[0];
	}

      else if (Param_ID == 63)

	{

	  /* Application specific */
	}

      else

	{

	  /* reserved for future use */
	}
    }				/* end while */
label1:;
}
