/***************************************************************
*
*    File removespaces.c
*
*    removes the spaces contained in the argument string
*
*    Author M.Bos - PA0MBO
*
*    dat Apr 24th 2012
*
***************************************************************/
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

char * removespaces( char *filename)
{
    char *p;
    char *delims = " ";
    int length, i ;
    p= strtok( filename , delims);

    while (p != NULL)
    {
       p = strtok( NULL, delims);
       if ( p != NULL)
            *(p-1) ='_';
    }
    /* now remove umlauts etc */
    length = strlen(filename) ;
    for (i=0; i < length; i++)
    {
      if ( (int)filename[i] < 0 )
      {
           filename[i] = '_' ;
      }    
    }
    return filename ;
}   

