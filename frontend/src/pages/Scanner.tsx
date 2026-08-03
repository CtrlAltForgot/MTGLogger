import { useCallback, useEffect, useRef, useState } from 'react'
import { CameraAlt, CloudDownload, RestartAlt, Stop } from '@mui/icons-material'
import {
  Alert, Box, Button, Card, CardContent, Chip, FormControlLabel, Grid,
  LinearProgress, MenuItem, Select, Slider, Snackbar, Stack, Switch, TextField, Typography,
} from '@mui/material'
import { request, submitScan } from '../api'
import ScanConfirmation from '../components/ScanConfirmation'
import { defaultTuning, useAutoScanner, type ScannerTuning } from '../scanner/useAutoScanner'
import type { Candidate, Deck, Defaults, Inventory, ScanResult } from '../types'

const initial:Defaults={condition:'near_mint',foil:false,language:'en',storage_location:'Unsorted',collection_name:'Main',status:'owned',box_set_code:null,auto_add:true,deck_id:null}
type ReferenceStatus={state:string;set_code:string|null;completed:number;total:number;indexed_cards:number;error:string|null}

export default function Scanner(){
  const [defaults,setDefaults]=useState(initial)
  const [result,setResult]=useState<ScanResult|null>(null)
  const [tuning,setTuning]=useState<ScannerTuning>(defaultTuning)
  const [references,setReferences]=useState<ReferenceStatus>()
  const [decks,setDecks]=useState<Deck[]>([])
  const [decisionBusy,setDecisionBusy]=useState(false)
  const [success,setSuccess]=useState<Inventory|null>(null)
  const [reviewNotice,setReviewNotice]=useState<ScanResult|null>(null)
  const decisionComplete=useRef<(()=>void)|null>(null)

  const capture=useCallback(async(blob:Blob)=>{
    const next=await submitScan(blob,defaults) as ScanResult
    setResult(next)
    if(next.disposition==='added'&&next.inventory)setSuccess(next.inventory)
    if(next.disposition!=='added'&&defaults.auto_add)setReviewNotice(next)
    if(!defaults.auto_add&&(next.disposition==='confirmation'||next.disposition==='suggestions')&&next.candidates.length){
      await new Promise<void>(resolve=>{decisionComplete.current=resolve})
    }
  },[defaults])
  const scan=useAutoScanner(capture,tuning)

  const refresh=useCallback(()=>request<ReferenceStatus>('/references/status').then(setReferences),[])
  useEffect(()=>{void refresh();const timer=setInterval(refresh,2500);return()=>clearInterval(timer)},[refresh])
  useEffect(()=>{void request<Deck[]>('/decks').then(setDecks)},[])

  const finishDecision=()=>{decisionComplete.current?.();decisionComplete.current=null;setDecisionBusy(false)}
  const accept=useCallback(async(candidate:Candidate)=>{
    if(!result?.review_id)return
    setDecisionBusy(true)
    try{
      const inventory=await request<Inventory>(`/reviews/${result.review_id}/resolve`,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({candidate,defaults}),
      })
      setResult({...result,disposition:'added',inventory,message:`Added ${inventory.card_name}`})
      setSuccess(inventory)
      finishDecision()
    }catch(error){setDecisionBusy(false);scan.setError(error instanceof Error?error.message:'Could not accept card')}
  },[defaults,result,scan])
  const decline=useCallback(async()=>{
    if(!result?.review_id)return
    setDecisionBusy(true)
    try{await request(`/reviews/${result.review_id}`,{method:'DELETE'});setResult(null);finishDecision()}
    catch(error){setDecisionBusy(false);scan.setError(error instanceof Error?error.message:'Could not decline card')}
  },[result,scan])

  const indexSet=async()=>{if(defaults.box_set_code){await request(`/references/sync/${defaults.box_set_code}`,{method:'POST'});refresh()}}
  const candidate:Candidate|undefined=!defaults.auto_add&&(result?.disposition==='confirmation'||result?.disposition==='suggestions')?result.candidates[0]:undefined
  const tone=result?.disposition==='added'?'success':result?.disposition==='suggestions'||result?.disposition==='confirmation'?'warning':'error'
  const stateLabel=scan.state==='remove'?'Remove card':scan.state==='processing'?'Identifying…':scan.state==='stabilizing'?'Hold still…':scan.state==='calibrating'?'Keep guide empty…':scan.state==='waiting'?'Ready for card':'Camera stopped'

  return <Grid container spacing={3}>
    <Grid size={{xs:12,lg:8}}>
      <Card sx={{overflow:'hidden',position:'relative',bgcolor:'#050807'}}>
        <Box component="video" ref={scan.video} muted playsInline sx={{width:'100%',aspectRatio:'16/9',display:'block',objectFit:'cover'}}/>
        <canvas ref={scan.canvas} hidden/><Box className="card-guide"/>
        <Chip label={stateLabel} color={scan.state==='remove'?'success':scan.state==='processing'?'warning':'default'} sx={{position:'absolute',top:16,left:16,fontWeight:700}}/>
        {scan.state==='processing'&&<LinearProgress sx={{position:'absolute',bottom:0,left:0,right:0}}/>}
      </Card>
      <Stack direction="row" spacing={2} mt={2} alignItems="center">
        {scan.state==='idle'
          ?<Button size="large" variant="contained" startIcon={<CameraAlt/>} onClick={scan.start}>Start scanning</Button>
          :<><Button size="large" variant="outlined" startIcon={<Stop/>} onClick={scan.stop}>Stop</Button><Button startIcon={<RestartAlt/>} onClick={scan.calibrate}>Reset background</Button></>}
        <Typography color="text.secondary">Keep the guide empty during calibration, then place and hold a card.</Typography>
      </Stack>
      <Stack direction="row" spacing={2} mt={1}>
        <Typography variant="caption">Scene change {scan.metrics.sceneDifference.toFixed(1)}</Typography>
        <Typography variant="caption">Contrast {scan.metrics.contrast.toFixed(1)}</Typography>
        <Typography variant="caption">Motion {scan.metrics.motion.toFixed(1)}</Typography>
      </Stack>
      {scan.error&&<Alert severity="error" sx={{mt:2}} onClose={()=>scan.setError(undefined)}>{scan.error}</Alert>}
      {result&&!candidate&&<Alert severity={tone} variant="filled" sx={{mt:2,fontSize:'1.05rem'}}>
        <strong>{result.message}</strong> · {result.confidence.toFixed(1)}%
        {result.inventory&&<> · {result.inventory.set_name} #{result.inventory.collector_number} · ${Number(result.inventory.market_price||0).toFixed(2)} · Quantity {result.inventory.quantity}</>}
      </Alert>}
    </Grid>

    <Grid size={{xs:12,lg:4}}>
      <Card><CardContent><Typography variant="h6" gutterBottom>Batch defaults</Typography><Stack spacing={2}>
        <Select value={defaults.condition} onChange={event=>setDefaults({...defaults,condition:event.target.value})}>
          {[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']].map(([value,label])=><MenuItem value={value} key={value}>{label}</MenuItem>)}
        </Select>
        <Select displayEmpty value={defaults.deck_id||''} onChange={event=>setDefaults({...defaults,deck_id:event.target.value||null})}><MenuItem value="">Deck · None</MenuItem>{decks.map(deck=><MenuItem value={deck.id} key={deck.id}>Deck · {deck.name}</MenuItem>)}</Select>
        <TextField label="Storage location" placeholder="e.g. Box 4 / Row B" value={defaults.storage_location} onChange={event=>setDefaults({...defaults,storage_location:event.target.value||'Unsorted'})}/>
        <TextField label="Box Mode set code" placeholder="e.g. FDN" value={defaults.box_set_code||''} onChange={event=>setDefaults({...defaults,box_set_code:event.target.value.trim()||null})}/>
        <Button startIcon={<CloudDownload/>} disabled={!defaults.box_set_code||references?.state==='running'} onClick={indexSet}>
          {references?.state==='running'?`Indexing ${references.completed}/${references.total}`:`Index set artwork (${references?.indexed_cards||0} cached)`}
        </Button>
        {references?.error&&<Alert severity="error">{references.error}</Alert>}
        <FormControlLabel control={<Switch checked={defaults.foil} onChange={event=>setDefaults({...defaults,foil:event.target.checked})}/>} label="Foil"/>
        <FormControlLabel control={<Switch checked={defaults.auto_add} onChange={event=>setDefaults({...defaults,auto_add:event.target.checked})}/>} label="Auto-add near-certain matches (98.5%+)"/>
      </Stack></CardContent></Card>

      <Card sx={{mt:2}}><CardContent><Typography variant="h6">Camera calibration</Typography>
        <Typography variant="caption">Card entry difference: {tuning.entryDifference}</Typography>
        <Slider min={5} max={35} value={tuning.entryDifference} onChange={(_,value)=>setTuning({...tuning,entryDifference:value as number})}/>
        <Typography variant="caption">Minimum stable motion: {tuning.stableMotion.toFixed(1)}</Typography>
        <Slider min={.5} max={8} step={.1} value={tuning.stableMotion} onChange={(_,value)=>setTuning({...tuning,stableMotion:value as number})}/>
        <Typography variant="caption">Frames to capture: {tuning.stableFrames}</Typography>
        <Slider min={3} max={12} value={tuning.stableFrames} onChange={(_,value)=>setTuning({...tuning,stableFrames:value as number})}/>
      </CardContent></Card>
    </Grid>
    {candidate&&<ScanConfirmation candidates={result!.candidates} confidence={result!.confidence} onAccept={accept} onDecline={decline} busy={decisionBusy}/>}
    <Snackbar key={success?`${success.id}-${success.quantity}`:'empty'} open={!!success} autoHideDuration={1800} onClose={()=>setSuccess(null)} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
      <Card elevation={12} sx={{display:'flex',alignItems:'center',minWidth:{xs:320,sm:460},border:'2px solid',borderColor:'success.main',overflow:'hidden'}}>
        {success?.image_url&&<Box component="img" src={success.image_url} alt={success.card_name} sx={{width:82,height:114,objectFit:'cover',objectPosition:'top'}}/>}
        <Box px={2} py={1}><Typography color="success.main" fontWeight={900}>ADDED · {success?.quantity} OWNED</Typography><Typography variant="h6" fontWeight={900}>{success?.card_name}</Typography><Typography color="text.secondary">{success?.set_name} #{success?.collector_number} · ${Number(success?.market_price||0).toFixed(2)}</Typography></Box>
      </Card>
    </Snackbar>
    <Snackbar key={reviewNotice?.review_id||'no-review'} open={!!reviewNotice} autoHideDuration={1800} onClose={()=>setReviewNotice(null)} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
      <Alert severity="warning" variant="filled" sx={{minWidth:{xs:320,sm:460}}}><Typography fontWeight={900}>SAVED FOR REVIEW · {reviewNotice?.confidence.toFixed(1)}%</Typography><Typography>{reviewNotice?.candidates[0]?.name||'Printing uncertain'} · Keep scanning</Typography></Alert>
    </Snackbar>
  </Grid>
}
