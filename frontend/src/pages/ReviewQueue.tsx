import { useEffect, useState } from 'react'
import { AutoAwesome, DeleteOutline, Search, VisibilityOff } from '@mui/icons-material'
import { Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid, LinearProgress, Stack, Typography } from '@mui/material'
import { API, request } from '../api'
import type { Candidate, Deck, Defaults } from '../types'
import { CardName } from '../components/CardDetails'
import CardSearch from '../components/CardSearch'

type Review={id:string;confidence:number;ocr_text:string|null;created_at:string;candidates:Candidate[];defaults:Defaults}
type Metrics={top1:number;top5:number;mean_margin:number}
type TrainingStatus={state:string;phase:string;progress:number;correction_count?:number;batch_correction_count?:number;corrections?:number;labels?:number;epoch?:number;epochs?:number;started_at?:string;completed_at?:string;duration_seconds?:number;result?:{state:string;training_examples?:number;validation_examples?:number;baseline?:Metrics;candidate?:Metrics}}
const foilOnly=(candidate:Candidate)=>!candidate.finishes.includes('nonfoil')&&candidate.finishes.some(finish=>finish==='foil'||finish==='etched')
export const candidateMarketPrice=(candidate:Candidate,foil:boolean)=>{
  const price=(foil||foilOnly(candidate))?(candidate.foil_market_price??candidate.market_price):candidate.market_price
  return price==null?null:Number(price)
}
export const displayCandidatePrice=(candidate:Candidate,foil:boolean)=>{
  const price=candidateMarketPrice(candidate,foil)
  return price==null?'Price unavailable':`$${price.toFixed(2)}`
}
export const candidatePriceColor=(candidate:Candidate,foil:boolean)=>{
  const price=candidateMarketPrice(candidate,foil)
  return price!=null&&price>=100?'error.main':price!=null&&price>=20?'warning.main':'primary.main'
}

export default function ReviewQueue(){
  const [items,setItems]=useState<Review[]>([]),[decks,setDecks]=useState<Deck[]>([]),[manual,setManual]=useState<Review|null>(null),[error,setError]=useState<string>(),[training,setTraining]=useState<TrainingStatus>()
  const [resolving,setResolving]=useState(false)
  const [preview,setPreview]=useState<Review|null>(null)
  const load=()=>request<Review[]>('/reviews').then(setItems)
  useEffect(()=>{void load();void request<Deck[]>('/decks').then(setDecks)},[])
  useEffect(()=>{const refresh=()=>void request<TrainingStatus>('/reviews/training/status').then(setTraining).catch(()=>undefined);refresh();const timer=window.setInterval(refresh,training?.state==='running'?1000:5000);return()=>window.clearInterval(timer)},[training?.state])
  const ignore=async(id:string)=>{await request(`/reviews/${id}/ignore`,{method:'POST'});load()}
  const remove=async(id:string)=>{await request(`/reviews/${id}`,{method:'DELETE'});load()}
  const resolve=async(item:Review,candidate:Candidate)=>{
    if(resolving)return
    setResolving(true);setError(undefined)
    try{
      await request(`/reviews/${item.id}/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate})})
      setManual(null);setItems(current=>current.filter(review=>review.id!==item.id))
    }catch(e){setError(e instanceof Error?e.message:'Could not save this printing. Please try again.')}
    finally{setResolving(false)}
  }
  return <>
    <Typography variant="h4">Review queue</Typography><Typography color="text.secondary" mb={3}>Hard scans wait here without interrupting a batch.</Typography>
    {error&&!manual&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError(undefined)}>{error}</Alert>}
    <Grid container spacing={2}>{items.map(item=><Grid size={{xs:12,lg:6}} key={item.id}><Card><CardContent>
      <Stack direction="row" justifyContent="space-between"><Chip color={item.confidence>=70?'warning':'error'} label={`${item.confidence.toFixed(1)}% confidence`}/><Stack direction="row"><Button size="small" startIcon={<VisibilityOff/>} onClick={()=>ignore(item.id)}>Ignore</Button><Button size="small" color="error" startIcon={<DeleteOutline/>} onClick={()=>remove(item.id)}>Delete</Button></Stack></Stack>
      <Stack direction={{xs:'column',sm:'row'}} spacing={1.5} mt={2}><Box onClick={()=>setPreview(item)} sx={{width:{xs:'100%',sm:180},height:252,flexShrink:0,display:'grid',placeItems:'center',overflow:'hidden',bgcolor:'#050807',border:'1px solid',borderColor:'divider',borderRadius:1.5,cursor:'zoom-in'}}><Box component="img" src={`${API}/api/reviews/${item.id}/image`} sx={{display:'block',width:'100%',height:'100%',objectFit:'contain'}}/></Box><Box flex={1} minWidth={0}><Stack direction="row" spacing={.75} flexWrap="wrap" mb={1}><Chip size="small" label={item.defaults.condition.replaceAll('_',' ')}/><Chip size="small" label={item.defaults.language.toUpperCase()}/>{item.defaults.foil&&<Chip size="small" color="warning" label="Foil"/>}<Chip size="small" label={`Deck · ${decks.find(deck=>deck.id===item.defaults.deck_id)?.name||'None'}`}/><Chip size="small" label={`Storage · ${item.defaults.storage_location}`}/></Stack><Box sx={{maxHeight:112,overflow:'auto',p:1,bgcolor:'action.hover',borderRadius:1}}><Typography display="block" variant="caption" color="text.secondary" sx={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>OCR: {item.ocr_text||'No readable text'}</Typography></Box>{item.candidates.map(c=><Stack key={c.scryfall_id} direction="row" spacing={1} alignItems="center" mt={1}><Box component="img" src={c.image_url||''} sx={{width:42,flexShrink:0}}/><Box flex={1} minWidth={0}><Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap><Typography fontWeight={700}><CardName scryfallId={c.scryfall_id}>{c.name}</CardName></Typography><Typography className="card-price" color={candidatePriceColor(c,item.defaults.foil)} fontWeight={900}>{displayCandidatePrice(c,item.defaults.foil)}</Typography></Stack><Typography variant="body2">{c.set_name} #{c.collector_number} · {c.language.toUpperCase()}</Typography></Box><Button variant="contained" size="small" disabled={resolving} onClick={()=>void resolve(item,c)}>Choose</Button></Stack>)}<Button startIcon={<Search/>} sx={{mt:2}} disabled={resolving} onClick={()=>{setManual(item);setError(undefined)}}>Find this card</Button></Box></Stack>
    </CardContent></Card></Grid>)}</Grid>
    {!items.length&&<TrainingPanel status={training}/>}
    <Dialog open={!!manual} onClose={()=>{if(!resolving)setManual(null)}} fullWidth maxWidth="md" sx={{'& .MuiDialog-paper':{m:{xs:1,sm:4},width:{xs:'calc(100% - 16px)',sm:'calc(100% - 64px)'},maxHeight:{xs:'calc(100% - 16px)',sm:'calc(100% - 64px)'}}}}>
      <DialogTitle>Find this card</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" mb={1}>Choose a printing to confirm this scan and add it to your collection.</Typography>
        {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}
        {resolving&&<LinearProgress sx={{mb:2}}/>}
        {manual&&<CardSearch key={manual.id} language={manual.defaults.language} disabled={resolving} onChoose={card=>void resolve(manual,card)}/>}
      </DialogContent>
      <DialogActions><Button disabled={resolving} onClick={()=>setManual(null)}>Cancel</Button></DialogActions>
    </Dialog>
    <Dialog open={!!preview} onClose={()=>setPreview(null)} fullWidth maxWidth="md"><DialogTitle>Captured scan</DialogTitle><DialogContent sx={{display:'grid',placeItems:'center',bgcolor:'#050807',p:2}}>{preview&&<Box component="img" src={`${API}/api/reviews/${preview.id}/image`} sx={{display:'block',maxWidth:'100%',maxHeight:'75vh',objectFit:'contain'}}/>}</DialogContent></Dialog>
  </>
}

function TrainingPanel({status}:{status?:TrainingStatus}){
  const running=status?.state==='running', result=status?.result, promoted=result?.state==='promoted'
  const hasBatchCount=status?.batch_correction_count!==undefined
  const batchCount=status?.batch_correction_count??0
  const retainedCount=status?.correction_count??status?.corrections??0
  const phase=({preparing:'Preparing confirmed scans',training:'Training camera-aware adapter',validating:'Validating against held-out cards',refining:'Refining with every correction',finalizing:'Saving validated model',completed:'Training complete'} as Record<string,string>)[status?.phase||'']||'Neural training ready'
  const percent=Math.round((status?.progress||0)*100)
  return <Stack alignItems="center" mt={8} spacing={2}>
    <Typography color="text.secondary">Nothing needs review.</Typography>
    <Card variant="outlined" sx={{width:'100%',maxWidth:760}}><CardContent>
      <Stack direction="row" spacing={1.25} alignItems="center" mb={1}><AutoAwesome color="primary"/><Box flex={1}><Typography fontWeight={900}>Neural learning</Typography><Typography variant="body2" color="text.secondary">{phase}</Typography></Box><Chip size="small" color={running?'warning':promoted?'success':'default'} label={running?`${percent}%`:promoted?'Promoted':result?.state==='rejected'?'Safely rejected':'Ready'}/></Stack>
      <LinearProgress variant="determinate" value={percent} color={running?'primary':promoted?'success':'primary'} sx={{height:10,borderRadius:99,my:1.5}}/>
      <Stack direction={{xs:'column',sm:'row'}} spacing={{xs:.5,sm:3}}>
        <Typography variant="body2"><b>{(hasBatchCount?batchCount:retainedCount).toLocaleString()}</b> {hasBatchCount?'confirmed corrections from this batch':'retained corrections used'}</Typography>
        {hasBatchCount&&retainedCount!==batchCount&&<Typography variant="body2" color="text.secondary"><b>{retainedCount.toLocaleString()}</b> retained total</Typography>}
        {status?.epoch!==undefined&&status?.epochs&&<Typography variant="body2">Epoch <b>{status.epoch}</b> / {status.epochs}</Typography>}
        {status?.duration_seconds!==undefined&&<Typography variant="body2">Finished in <b>{status.duration_seconds.toFixed(1)}s</b></Typography>}
      </Stack>
      {result?.baseline&&result?.candidate&&<Box mt={2} p={1.5} bgcolor="action.hover" borderRadius={1.5}>
        <Typography variant="body2" fontWeight={800}>{promoted?'The validated model improved and is now active.':'The candidate was not activated because every safety metric must hold or improve.'}</Typography>
        <Typography variant="caption" color="text.secondary">Top-1 {(result.baseline.top1*100).toFixed(1)}% → {(result.candidate.top1*100).toFixed(1)}% · Top-5 {(result.baseline.top5*100).toFixed(1)}% → {(result.candidate.top5*100).toFixed(1)}% · {result.training_examples||0} training / {result.validation_examples||0} validation examples</Typography>
      </Box>}
    </CardContent></Card>
  </Stack>
}
