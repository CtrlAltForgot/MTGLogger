import {artworkUrl} from '../artwork'
import { useState } from 'react'
import { Box, Button, Typography } from '@mui/material'

type Props={src:string;alt?:string;foil?:boolean;sx?:Record<string,unknown>;imageSx?:Record<string,unknown>}

export default function FoilArtwork({src,alt='',foil=false,sx,imageSx}:Props){
  const [failedSource,setFailedSource]=useState<string>()
  const failed=failedSource===src
  return <Box className={foil?'foil-artwork':undefined} sx={{position:'relative',overflow:'hidden',flexShrink:0,bgcolor:'action.hover',...sx}}>
    {failed?<Box sx={{height:'100%',minHeight:100,display:'flex',flexDirection:'column',justifyContent:'center',textAlign:'center',p:1,position:'relative',zIndex:1}}>
      <Typography variant="caption" color="text.secondary">Image unavailable</Typography>
      <Button size="small" onClick={event=>{event.stopPropagation();setFailedSource(undefined)}} aria-label={`Retry image for ${alt||'card'}`}>Retry</Button>
    </Box>:<Box component="img" key={src} src={artworkUrl(src)} alt={alt} onError={()=>setFailedSource(src)} sx={{width:'100%',height:'100%',display:'block',...imageSx}}/>}
  </Box>
}
