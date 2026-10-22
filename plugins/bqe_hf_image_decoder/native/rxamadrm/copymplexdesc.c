
/*
*     file copymplexdesc.c
*
*     copies multiplex_description structures
*
*     Author M.Bos - PA0MBO
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


extern struct mplex_desc
{
  int stream_lengths[2][6];
  int PL_PartA;
  int PL_PartB;
  int HM_length;
  int PL_HM;
} multiplex_description;
void copy_mplex_desc(struct mplex_desc *src, struct mplex_desc *dest)
{
  int i, j;

  for (j = 0; j < 2; j++)

    {
      for (i = 0; i < 6; i++)
	dest->stream_lengths[j][i] = src->stream_lengths[j][i];
    }
  dest->PL_PartA = src->PL_PartA;
  dest->PL_PartB = src->PL_PartB;
  dest->HM_length = src->HM_length;
  dest->PL_HM = src->PL_HM;
}
