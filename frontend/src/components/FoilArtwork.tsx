import { Box } from '@mui/material'

type Props={src:string;alt?:string;foil?:boolean;sx?:Record<string,unknown>;imageSx?:Record<string,unknown>}

export default function FoilArtwork({src,alt='',foil=false,sx,imageSx}:Props){
  return <Box className={foil?'foil-artwork':undefined} sx={{position:'relative',overflow:'hidden',flexShrink:0,...sx}}>
    <Box component="img" src={src} alt={alt} sx={{width:'100%',height:'100%',display:'block',...imageSx}}/>
  </Box>
}
