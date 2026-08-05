import { useEffect, useState } from 'react'
import { Box } from '@mui/material'

type Props={src:string;alt?:string;foil?:boolean;sx?:Record<string,unknown>;imageSx?:Record<string,unknown>}

export default function FoilArtwork({src,alt='',foil=false,sx,imageSx}:Props){
  const [loaded,setLoaded]=useState(false)
  useEffect(()=>setLoaded(false),[src])
  return <Box className={foil?'foil-artwork':undefined} sx={{position:'relative',overflow:'hidden',flexShrink:0,bgcolor:'action.hover',...sx}}>
    <Box component="img" src={src} alt={alt} onLoad={()=>setLoaded(true)} sx={{width:'100%',height:'100%',display:'block',opacity:loaded?1:0,transition:'opacity 140ms ease',...imageSx}}/>
  </Box>
}
