
/******************************************************************************/

/*                                                                            */

/*  University of Kaiserslautern, Institute of Communications Engineering     */

/*  Copyright (C) 2004 Andreas Dittrich, Torsten Schorr                       */

/*                                                                            */

/*  Author(s)    : Andreas Dittrich (dittrich@eit.uni-kl.de),                 */

/*                 Torsten Schorr (schorr@eit.uni-kl.de)                      */

/*  Project start: 14.04.2004                                                 */

/*  Last change  : 14.04.2004                                                 */

/*                                                                            */

/******************************************************************************/

/*                                                                            */

/*  This program is free software; you can redistribute it and/or modify      */

/*  it under the terms of the GNU General Public License as published by      */

/*  the Free Software Foundation; either version 2 of the License, or         */

/*  (at your option) any later version.                                       */

/*                                                                            */

/*  This program is distributed in the hope that it will be useful,           */

/*  but WITHOUT ANY WARRANTY; without even the implied warranty of            */

/*  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the             */

/*  GNU General Public License for more details.                              */

/*                                                                            */

/*  You should have received a copy of the GNU General Public License         */

/*  along with this program; if not, write to the Free Software               */

/*  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA */

/*                                                                            */

/******************************************************************************/


/******************************************************************************/

/*                                                                            */

/*  crc16_c.c                                                                   */

/*                                                                            */

/******************************************************************************/

/*                                                                            */

/*  CRC-16 checksum calculation of a bit stream                               */

/*                                                                            */

/*  calculates double checksum of double bits                                 */

/*  From diorama-1.1.1 by Andreas Dittrich and Torsten Schorr                 */

/*                                                                            */

/******************************************************************************/

#include <stdio.h>
#include <math.h>
#include <stdlib.h>
void crc16_c(double out[], double in[], int N);



/******************************************************************************/

/* function                                                                   */

/******************************************************************************/
void crc16_c(double checksum[], double in[], int N)
{
  int i;
  unsigned int b = 0xFFFF;
  unsigned int x = 0x1021;	/* (1) 0001000000100001 */
  unsigned int y;

  for (i = 0; i < N; i++)

    {
      y = ((b >> 15) + (int) floor(in[i] + 0.5)) & 0x01;
      if (y == 1)
	b = ((b << 1) ^ x);

      else
	b = (b << 1);
    }
  *checksum = (double) (b & 0xFFFF);
}
