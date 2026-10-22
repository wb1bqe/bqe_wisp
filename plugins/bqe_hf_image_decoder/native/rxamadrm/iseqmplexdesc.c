

/*
*     file iseqmplexdesc.c
*
*     compares  multiplex_description structures
*
*     Author M.Bos - PA0MBO
*     Date Feb 21st 2009
*
*     returns zero if the two structures are equal 
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
extern struct mplex_desc
{
  int stream_lengths[2][6];
  int PL_PartA;
  int PL_PartB;
  int HM_length;
  int PL_HM;
} multiplex_description;
int cmp_mplex_desc(struct mplex_desc *src, struct mplex_desc *dest)
{
  int i, j;

  for (i = 0; i < 2; i++)

    {
      for (j = 0; j < 6; j++)

	{
	  if (dest->stream_lengths[i][j] != src->stream_lengths[i][j])

	    {
	      printf("cmp mplex i %d  j %d  %d %d \n", i, j,
		     dest->stream_lengths[i][j], src->stream_lengths[i][j]);
	      return 1;
	    }
	}
    }
  if (dest->PL_PartA != src->PL_PartA)
    return 1;
  if (dest->PL_PartB != src->PL_PartB)
    return 1;
  if (dest->HM_length != src->HM_length)
    return 1;
  if (dest->PL_HM != src->PL_HM)
    return 1;
  return 0;
}
