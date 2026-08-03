import { useEffect, useState } from 'react'
import { Backspace, KeyboardArrowLeft, KeyboardArrowRight, KeyboardReturn, Search } from '@mui/icons-material'
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, IconButton, InputAdornment, Stack, TextField, Typography } from '@mui/material'
import { API, request } from '../api'
import type { Candidate } from '../types'
import FoilArtwork from './FoilArtwork'
import { CardName } from './CardDetails'

type Props={reviewId:string;candidates:Candidate[];confidence:number;foil:boolean;language:string;onAccept:(candidate:Candidate)=>void;onSkip:()=>void;busy:boolean}

const foilOnly=(candidate:Candidate)=>!candidate.finishes.includes('nonfoil')&&candidate.finishes.some(finish=>finish==='foil'||finish==='etched')

export default function ScanConfirmation({reviewId,candidates,confidence,foil,language,onAccept,onSkip,busy}:Props){
  const [selected,setSelected]=useState(0)
  const [manual,setManual]=useState(candidates.length===0)
  const [query,setQuery]=useState(candidates[0]?.name||'')
  const [matches,setMatches]=useState<Candidate[]>([])
  const [searching,setSearching]=useState(false)
  const [error,setError]=useState<string>()
  const choices=manual?matches:candidates
  const candidate=choices[selected]
  const effectiveFoil=!!candidate&&(foil||foilOnly(candidate))

  const search=async()=>{
    if(query.trim().length<2)return
    setSearching(true);setError(undefined)
    try{setMatches(await request<Candidate[]>(`/reviews/search?q=${encodeURIComponent(query.trim())}&lang=${language}`));setSelected(0)}
    catch(value){setError(value instanceof Error?value.message:'Search failed')}
    finally{setSearching(false)}
  }

  useEffect(()=>{
    const handle=(event:KeyboardEvent)=>{
      if(busy||searching)return
      const target=event.target as HTMLElement|null
      if(target?.tagName==='INPUT')return
      if(event.key==='Enter'&&candidate){event.preventDefault();onAccept(candidate)}
      if(event.key==='Backspace'){event.preventDefault();onSkip()}
      if(event.key==='ArrowLeft'&&choices.length){event.preventDefault();setSelected(index=>(index-1+choices.length)%choices.length)}
      if(event.key==='ArrowRight'&&choices.length){event.preventDefault();setSelected(index=>(index+1)%choices.length)}
      if(/^[1-5]$/.test(event.key)&&Number(event.key)<=choices.length)setSelected(Number(event.key)-1)
    }
    window.addEventListener('keydown',handle)
    return()=>window.removeEventListener('keydown',handle)
  },[busy,candidate,choices.length,onAccept,onSkip,searching])

  return <Dialog open fullWidth maxWidth="lg" disableEscapeKeyDown>
    <DialogContent>
      <Stack direction={{xs:'column',md:'row'}} spacing={3} alignItems={{md:'flex-start'}}>
        <Box>
          <Typography variant="overline" color="text.secondary">Camera capture</Typography>
          <Box sx={{width:{xs:'100%',md:310},height:{xs:300,md:430},display:'grid',placeItems:'center',bgcolor:'#050807',border:'1px solid',borderColor:'divider',borderRadius:2,overflow:'hidden'}}>
            <Box component="img" src={`${API}/api/reviews/${reviewId}/image`} alt="Captured card" sx={{display:'block',width:'100%',height:'100%',objectFit:'contain'}}/>
          </Box>
        </Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" spacing={1} alignItems="center" mb={2}>
            <Chip color={confidence>95?'success':confidence>=70?'warning':'error'} label={`${confidence.toFixed(1)}% confidence`}/>
            <Typography color="text.secondary">Keep this physical card aside until resolved.</Typography>
          </Stack>
          {!manual&&candidate&&<Stack direction={{xs:'column',sm:'row'}} spacing={2} alignItems="center">
            <FoilArtwork src={candidate.image_url||''} alt={candidate.name} foil={effectiveFoil} sx={{width:170,borderRadius:2,flexShrink:0}}/>
            <Box flex={1}>
              <Typography className="card-title" variant="h4"><CardName scryfallId={candidate.scryfall_id}>{candidate.name}</CardName></Typography>
              <Typography className="card-printing" variant="h6" color="text.secondary">{candidate.set_name} #{candidate.collector_number} · {candidate.language.toUpperCase()}</Typography>
              <Typography variant="h4" color="primary.main" mt={2}>${Number((effectiveFoil?(candidate.foil_market_price||candidate.market_price):candidate.market_price)||0).toFixed(2)}{effectiveFoil&&<Typography component="span" variant="body2" color="text.secondary"> · foil{!foil?' only':''}</Typography>}</Typography>
              <Typography color="text.secondary" mt={2}>Is this the exact physical printing?</Typography>
            </Box>
          </Stack>}
          {manual&&<>
            <Typography variant="h5" mb={1}>Find the exact printing</Typography>
            <Typography color="text.secondary" mb={2}>Search while the captured card remains visible for comparison.</Typography>
            <Stack direction="row" spacing={1}>
              <TextField autoFocus fullWidth placeholder="Card name" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void search()}}} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/>
              <Button variant="contained" disabled={query.trim().length<2||searching} onClick={()=>void search()}>Search</Button>
            </Stack>
            {error&&<Alert severity="error" sx={{mt:2}}>{error}</Alert>}
            {!searching&&matches.length===0&&<Typography color="text.secondary" mt={3}>{query?'Search to see possible printings.':'OCR found no reliable name. Enter the card name.'}</Typography>}
          </>}
          {choices.length>1&&<Box mt={3}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <IconButton onClick={()=>setSelected(index=>(index-1+choices.length)%choices.length)}><KeyboardArrowLeft/></IconButton>
              <Stack direction="row" spacing={1} sx={{overflowX:'auto',flex:1,pb:1}}>
                {choices.map((match,index)=><Button key={match.scryfall_id} variant={index===selected?'contained':'outlined'} onClick={()=>setSelected(index)} sx={{minWidth:165,justifyContent:'flex-start'}}>
                  <Stack textAlign="left"><span className="card-name">{index+1}. {match.name}</span><small>{match.set_code.toUpperCase()} #{match.collector_number} · {match.language.toUpperCase()} · {match.confidence}%</small></Stack>
                </Button>)}
              </Stack>
              <IconButton onClick={()=>setSelected(index=>(index+1)%choices.length)}><KeyboardArrowRight/></IconButton>
            </Stack>
            <Typography variant="caption" color="text.secondary">Use ←/→ or number keys to select another printing.</Typography>
          </Box>}
          {!manual&&<Button startIcon={<Search/>} sx={{mt:3}} onClick={()=>{setManual(true);setMatches([]);setSelected(0)}}>Search another printing</Button>}
        </Box>
      </Stack>
    </DialogContent>
    <DialogActions sx={{p:3,pt:0}}>
      <Button size="large" color="error" startIcon={<Backspace/>} disabled={busy} onClick={onSkip}>Skip card · Backspace</Button>
      {manual&&candidates.length>0&&<Button size="large" onClick={()=>{setManual(false);setSelected(0)}}>Back to suggestions</Button>}
      <Button size="large" variant="contained" startIcon={<KeyboardReturn/>} disabled={busy||!candidate} onClick={()=>candidate&&onAccept(candidate)}>Accept exact printing · Enter</Button>
    </DialogActions>
  </Dialog>
}
