import { useEffect, useState } from 'react'
import { Backspace, KeyboardArrowLeft, KeyboardArrowRight, KeyboardReturn } from '@mui/icons-material'
import { Box, Button, Chip, Dialog, DialogActions, DialogContent, IconButton, Stack, Typography } from '@mui/material'
import type { Candidate } from '../types'

type Props={candidates:Candidate[];confidence:number;foil:boolean;onAccept:(candidate:Candidate)=>void;onDecline:()=>void;busy:boolean}

export default function ScanConfirmation({candidates,confidence,foil,onAccept,onDecline,busy}:Props){
  const [selected,setSelected]=useState(0)
  const candidate=candidates[selected]
  const effectiveFoil=foil||(!candidate.finishes.includes('nonfoil')&&candidate.finishes.some(finish=>finish==='foil'||finish==='etched'))
  useEffect(()=>{
    const handle=(event:KeyboardEvent)=>{
      if(busy)return
      if(event.key==='Enter'){event.preventDefault();onAccept(candidate)}
      if(event.key==='Backspace'){event.preventDefault();onDecline()}
      if(event.key==='ArrowLeft'){event.preventDefault();setSelected(index=>(index-1+candidates.length)%candidates.length)}
      if(event.key==='ArrowRight'){event.preventDefault();setSelected(index=>(index+1)%candidates.length)}
      if(/^[1-5]$/.test(event.key)&&Number(event.key)<=candidates.length)setSelected(Number(event.key)-1)
    }
    window.addEventListener('keydown',handle)
    return()=>window.removeEventListener('keydown',handle)
  },[busy,candidate,candidates.length,onAccept,onDecline])
  return <Dialog open fullWidth maxWidth="md" disableEscapeKeyDown>
    <DialogContent>
      <Stack direction={{xs:'column',sm:'row'}} spacing={3} alignItems="center">
        <Box component="img" src={candidate.image_url||''} alt={candidate.name} sx={{width:{xs:180,sm:220},borderRadius:2}}/>
        <Box flex={1}>
          <Chip color={confidence>95?'success':'warning'} label={`${candidate.confidence.toFixed(1)}% confidence`} sx={{mb:2}}/>
          <Typography variant="h4">{candidate.name}</Typography>
          <Typography variant="h6" color="text.secondary">{candidate.set_name} #{candidate.collector_number} · {candidate.language.toUpperCase()}</Typography>
          <Typography variant="h4" color="primary.main" mt={2}>${Number((effectiveFoil?(candidate.foil_market_price||candidate.market_price):candidate.market_price)||0).toFixed(2)}{effectiveFoil&&<Typography component="span" variant="body2" color="text.secondary"> · foil{!foil?' only':''}</Typography>}</Typography>
          <Typography color="text.secondary" mt={2}>Is this the exact printing?</Typography>
        </Box>
      </Stack>
      {candidates.length>1&&<Box mt={3}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <IconButton onClick={()=>setSelected(index=>(index-1+candidates.length)%candidates.length)}><KeyboardArrowLeft/></IconButton>
          <Stack direction="row" spacing={1} sx={{overflowX:'auto',flex:1,pb:1}}>
            {candidates.map((match,index)=><Button key={match.scryfall_id} variant={index===selected?'contained':'outlined'} onClick={()=>setSelected(index)} sx={{minWidth:150,justifyContent:'flex-start'}}>
              <Stack textAlign="left"><span>{index+1}. {match.name}</span><small>{match.set_code.toUpperCase()} #{match.collector_number} · {match.language.toUpperCase()} · {match.confidence}%</small></Stack>
            </Button>)}
          </Stack>
          <IconButton onClick={()=>setSelected(index=>(index+1)%candidates.length)}><KeyboardArrowRight/></IconButton>
        </Stack>
        <Typography variant="caption" color="text.secondary">Use ←/→ or number keys to select another printing.</Typography>
      </Box>}
    </DialogContent>
    <DialogActions sx={{p:3,pt:0}}>
      <Button size="large" color="error" startIcon={<Backspace/>} disabled={busy} onClick={onDecline}>Decline · Backspace</Button>
      <Button size="large" variant="contained" startIcon={<KeyboardReturn/>} disabled={busy} onClick={()=>onAccept(candidate)}>Accept · Enter</Button>
    </DialogActions>
  </Dialog>
}
