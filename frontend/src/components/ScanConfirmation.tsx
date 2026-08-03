import { useEffect } from 'react'
import { Backspace, KeyboardReturn } from '@mui/icons-material'
import { Box, Button, Chip, Dialog, DialogActions, DialogContent, Stack, Typography } from '@mui/material'
import type { Candidate } from '../types'

type Props={candidate:Candidate;confidence:number;onAccept:()=>void;onDecline:()=>void;busy:boolean}

export default function ScanConfirmation({candidate,confidence,onAccept,onDecline,busy}:Props){
  useEffect(()=>{
    const handle=(event:KeyboardEvent)=>{
      if(busy)return
      if(event.key==='Enter'){event.preventDefault();onAccept()}
      if(event.key==='Backspace'){event.preventDefault();onDecline()}
    }
    window.addEventListener('keydown',handle)
    return()=>window.removeEventListener('keydown',handle)
  },[busy,onAccept,onDecline])
  return <Dialog open fullWidth maxWidth="sm" disableEscapeKeyDown>
    <DialogContent>
      <Stack direction={{xs:'column',sm:'row'}} spacing={3} alignItems="center">
        <Box component="img" src={candidate.image_url||''} alt={candidate.name} sx={{width:{xs:180,sm:220},borderRadius:2}}/>
        <Box flex={1}>
          <Chip color="warning" label={`${confidence.toFixed(1)}% confidence`} sx={{mb:2}}/>
          <Typography variant="h4">{candidate.name}</Typography>
          <Typography variant="h6" color="text.secondary">{candidate.set_name} #{candidate.collector_number}</Typography>
          <Typography variant="h4" color="primary.main" mt={2}>${Number(candidate.market_price||0).toFixed(2)}</Typography>
          <Typography color="text.secondary" mt={2}>Is this the exact printing?</Typography>
        </Box>
      </Stack>
    </DialogContent>
    <DialogActions sx={{p:3,pt:0}}>
      <Button size="large" color="error" startIcon={<Backspace/>} disabled={busy} onClick={onDecline}>Decline · Backspace</Button>
      <Button size="large" variant="contained" startIcon={<KeyboardReturn/>} disabled={busy} onClick={onAccept}>Accept · Enter</Button>
    </DialogActions>
  </Dialog>
}
