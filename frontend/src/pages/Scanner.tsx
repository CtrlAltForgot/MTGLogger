import { useCallback, useEffect, useState } from 'react'
import { CameraAlt, CropFree, RestartAlt, VideocamOff } from '@mui/icons-material'
import {
  Alert, Box, Button, Card, CardContent, Chip, FormControlLabel, Grid,
  IconButton, LinearProgress, MenuItem, Select, Slider, Snackbar, Stack, Switch, TextField, Tooltip, Typography,
} from '@mui/material'
import { request, submitScan } from '../api'
import FoilArtwork from '../components/FoilArtwork'
import { CardName } from '../components/CardDetails'
import { cardsPerMinute, initialSessionStats, recordSuccessfulAddition, reviewPercentage } from '../scanner/sessionStats'
import { defaultTuning, useAutoScanner, type ScanArea, type ScannerTuning } from '../scanner/useAutoScanner'
import type { Deck, Defaults, Inventory, ScanResult } from '../types'

const initial:Defaults={condition:'near_mint',foil:false,language:'en',storage_location:'Unsorted',collection_name:'Main',status:'owned',box_set_code:null,auto_add:true,deck_id:null}
const languages=[
  ['en','English'],['es','Spanish'],['fr','French'],['de','German'],['it','Italian'],
  ['pt','Portuguese'],['ja','Japanese'],['ko','Korean'],['ru','Russian'],
  ['zhs','Chinese (Simplified)'],['zht','Chinese (Traditional)'],['he','Hebrew'],
  ['la','Latin'],['grc','Ancient Greek'],['ar','Arabic'],['sa','Sanskrit'],['phy','Phyrexian'],
]
export default function Scanner(){
  const [defaults,setDefaults]=useState(initial)
  const [result,setResult]=useState<ScanResult|null>(null)
  const [tuning,setTuning]=useState<ScannerTuning>(defaultTuning)
  const [decks,setDecks]=useState<Deck[]>([])
  const [success,setSuccess]=useState<Inventory|null>(null)
  const [lastIdentified,setLastIdentified]=useState<{inventory:Inventory;confidence:number}|null>(null)
  const [reviewNotice,setReviewNotice]=useState<ScanResult|null>(null)
  const [stats,setStats]=useState(initialSessionStats)
  const [settingScanArea,setSettingScanArea]=useState(false)
  const [cropStart,setCropStart]=useState<{x:number;y:number}|null>(null)
  const [draftArea,setDraftArea]=useState<ScanArea|null>(null)

  const capture=useCallback(async(blob:Blob)=>{
    const started=performance.now()
    const next=await submitScan(blob,defaults) as ScanResult
    const elapsed=performance.now()-started
    // An empty frame is consumed, not a request to replace the camera
    // baseline. Calibration is exclusively controlled by the explicit button.
    if(next.disposition==='empty'){setResult(null);return true}
    setStats(current=>{
      const updated={...current,scans:current.scans+1,review:current.review+(next.disposition==='added'?0:1),totalMs:current.totalMs+elapsed,lastMs:elapsed,serverMs:next.processing_ms}
      return next.disposition==='added'?recordSuccessfulAddition(updated,performance.now()):updated
    })
    setResult(next)
    if(next.disposition==='added'&&next.inventory){
      setSuccess(next.inventory)
      setLastIdentified({inventory:next.inventory,confidence:next.confidence})
    }
    if(next.disposition!=='added')setReviewNotice(next)
    return true
  },[defaults])
  const scan=useAutoScanner(capture,tuning,2)

  useEffect(()=>{void request<Deck[]>('/decks').then(setDecks)},[])
  useEffect(()=>{void scan.start()},[scan.start])

  const tone=result?.disposition==='added'?'success':result?.disposition==='suggestions'||result?.disposition==='confirmation'?'warning':'error'
  const stateLabel=scan.state==='remove'?(tuning.slingerMode?'Slide next card':'Swap to next card'):scan.state==='processing'?'Identifying…':scan.state==='stabilizing'?'Hold still…':scan.state==='calibrating'?'Calibrating…':scan.state==='waiting'?'Ready for card':''
  const sessionCardsPerMinute=cardsPerMinute(stats)
  const sessionReviewPercentage=reviewPercentage(stats)
  const visibleScanArea=draftArea||scan.scanArea
  const areaPoint=(event:React.PointerEvent<HTMLElement>)=>{const rect=event.currentTarget.getBoundingClientRect();return{x:Math.max(0,Math.min(100,(event.clientX-rect.left)/rect.width*100)),y:Math.max(0,Math.min(100,(event.clientY-rect.top)/rect.height*100))}}
  const beginScanArea=(event:React.PointerEvent<HTMLElement>)=>{if(!settingScanArea)return;event.currentTarget.setPointerCapture(event.pointerId);const point=areaPoint(event);setCropStart(point);setDraftArea({left:point.x,top:point.y,width:0,height:0})}
  const moveScanArea=(event:React.PointerEvent<HTMLElement>)=>{if(!settingScanArea||!cropStart)return;const point=areaPoint(event);setDraftArea({left:Math.min(cropStart.x,point.x),top:Math.min(cropStart.y,point.y),width:Math.abs(point.x-cropStart.x),height:Math.abs(point.y-cropStart.y)})}
  const finishScanArea=()=>{if(draftArea&&draftArea.width>=5&&draftArea.height>=5){scan.setScanArea(draftArea);scan.calibrate()}setCropStart(null);setDraftArea(null);setSettingScanArea(false)}

  return <Grid container spacing={3}>
    <Grid size={{xs:12,lg:8}}>
      <Box onPointerDown={beginScanArea} onPointerMove={moveScanArea} onPointerUp={finishScanArea} sx={{overflow:'hidden',position:'relative',bgcolor:'#050807',borderRadius:3,minHeight:320,cursor:settingScanArea?'crosshair':'default',touchAction:settingScanArea?'none':'auto'}}>
        <Box component="video" ref={scan.video} muted playsInline disablePictureInPicture controlsList="nodownload noplaybackrate noremoteplayback" sx={{width:'100%',aspectRatio:'16/9',display:'block',objectFit:'cover'}}/>
        <canvas ref={scan.canvas} hidden/>
        {(settingScanArea||(scan.scanArea.left>0||scan.scanArea.top>0||scan.scanArea.width<100||scan.scanArea.height<100))&&<><Box sx={{position:'absolute',left:0,right:0,top:0,height:`${visibleScanArea.top}%`,bgcolor:'rgba(0,0,0,.56)',pointerEvents:'none'}}/><Box sx={{position:'absolute',left:0,right:0,top:`${visibleScanArea.top+visibleScanArea.height}%`,bottom:0,bgcolor:'rgba(0,0,0,.56)',pointerEvents:'none'}}/><Box sx={{position:'absolute',left:0,top:`${visibleScanArea.top}%`,width:`${visibleScanArea.left}%`,height:`${visibleScanArea.height}%`,bgcolor:'rgba(0,0,0,.56)',pointerEvents:'none'}}/><Box sx={{position:'absolute',left:`${visibleScanArea.left+visibleScanArea.width}%`,right:0,top:`${visibleScanArea.top}%`,height:`${visibleScanArea.height}%`,bgcolor:'rgba(0,0,0,.56)',pointerEvents:'none'}}/><Box sx={{position:'absolute',left:`${visibleScanArea.left}%`,top:`${visibleScanArea.top}%`,width:`${visibleScanArea.width}%`,height:`${visibleScanArea.height}%`,outline:'2px dashed rgba(255,255,255,.9)',pointerEvents:'none'}}>{settingScanArea&&<Typography sx={{position:'absolute',top:8,left:10,color:'common.white',textShadow:'0 1px 4px #000',fontWeight:800}}>Drag around the area to scan</Typography>}</Box></>}
        {scan.metrics.bounds&&scan.state!=='calibrating'&&<Box sx={{position:'absolute',left:`${scan.metrics.bounds.left}%`,top:`${scan.metrics.bounds.top}%`,width:`${scan.metrics.bounds.width}%`,height:`${scan.metrics.bounds.height}%`,border:'3px solid',borderColor:'success.main',borderRadius:2,boxShadow:'0 0 0 1px rgba(0,0,0,.4), 0 0 24px rgba(100,217,151,.28)',transition:'all 120ms linear',pointerEvents:'none'}}/>}
        {tuning.slingerMode&&!scan.metrics.bounds&&scan.state!=='calibrating'&&<Box sx={{position:'absolute',left:`${scan.scanArea.left}%`,top:`${scan.scanArea.top}%`,width:`${scan.scanArea.width}%`,height:`${scan.scanArea.height}%`,border:'3px solid',borderColor:'success.main',borderRadius:2,boxShadow:'0 0 24px rgba(100,217,151,.28)',pointerEvents:'none'}}/>}
        {stateLabel&&<Chip label={stateLabel} color={scan.state==='remove'?'success':scan.state==='processing'?'warning':'default'} sx={{position:'absolute',top:16,left:16,fontWeight:700,backdropFilter:'blur(12px)'}}/>}
        {scan.state!=='idle'&&<Tooltip title="Turn camera off"><IconButton aria-label="Turn camera off" onClick={scan.stop} sx={{position:'absolute',right:16,top:16,color:'common.white',bgcolor:'rgba(10,7,8,.55)',backdropFilter:'blur(12px)','&:hover':{bgcolor:'rgba(10,7,8,.78)'}}}><VideocamOff/></IconButton></Tooltip>}
        {scan.state==='processing'&&<LinearProgress sx={{position:'absolute',bottom:0,left:0,right:0}}/>}
      </Box>
      <Stack direction="row" spacing={2} mt={2} alignItems="center">
        {scan.state==='idle'
          ?<Button variant="contained" startIcon={<CameraAlt/>} onClick={()=>void scan.start()}>Turn camera on</Button>
          :<><Button startIcon={<RestartAlt/>} onClick={scan.calibrate}>Recalibrate empty table</Button><Button startIcon={<CropFree/>} color={settingScanArea?'primary':'inherit'} onClick={()=>{setCropStart(null);setDraftArea(scan.scanArea);setSettingScanArea(current=>!current)}}>Set scan area</Button>{(scan.scanArea.left>0||scan.scanArea.top>0||scan.scanArea.width<100||scan.scanArea.height<100)&&<Button onClick={()=>{scan.setScanArea({left:0,top:0,width:100,height:100});scan.calibrate()}}>Use full feed</Button>}</>}
        <Typography color="text.secondary">{tuning.slingerMode?'Align the scan area with the slinger window. Each physical slide rearms the next capture—even for identical copies.':'Place a card anywhere in view and hold it steady. Swap directly to the next card when identified.'}</Typography>
      </Stack>
      {scan.cameras.length>1&&<Select size="small" value={scan.selectedCamera} onChange={event=>void scan.switchCamera(event.target.value)} sx={{mt:1.5,minWidth:280}}>{scan.cameras.map((camera,index)=><MenuItem value={camera.deviceId} key={camera.deviceId}>{camera.label||`Camera ${index+1}`}</MenuItem>)}</Select>}
      <Grid container spacing={1.5} mt={.75}>
        <Grid size={{xs:12,sm:6}}><ScannerGauge label="Scanning pace" value={sessionCardsPerMinute} max={30} display={sessionCardsPerMinute===null?'Measuring':sessionCardsPerMinute.toFixed(1)} unit="cards/min" tone="primary" helper={stats.paceIntervals?`${stats.paceIntervals} recent interval${stats.paceIntervals===1?'':'s'} measured`:'Scan two cards within 30 seconds'}/></Grid>
        <Grid size={{xs:12,sm:6}}><ScannerGauge label="Review rate" value={sessionReviewPercentage} max={100} display={`${sessionReviewPercentage.toFixed(1)}%`} unit={`${stats.review} of ${stats.scans}`} tone={sessionReviewPercentage>25?'warning':'success'} helper={stats.scans?'Lower is better':'Waiting for the first scan'}/></Grid>
      </Grid>
      <Stack direction="row" mt={1.25} px={.5} spacing={2.25} alignItems="center" flexWrap="wrap" useFlexGap color="text.secondary">
        <Typography variant="caption"><strong>{stats.scans}</strong> scanned</Typography>
        <Typography variant="caption" color="success.main"><strong>{stats.added}</strong> added</Typography>
        {scan.pendingCaptures>0&&<Typography variant="caption" color="info.main"><strong>{scan.pendingCaptures}</strong> queued</Typography>}
        {stats.scans>0&&<><Typography variant="caption">Last <strong>{(stats.lastMs/1000).toFixed(1)}s</strong></Typography><Typography variant="caption">Recognition <strong>{(stats.serverMs/1000).toFixed(1)}s</strong></Typography><Typography variant="caption">Average <strong>{(stats.totalMs/stats.scans/1000).toFixed(1)}s</strong></Typography></>}
      </Stack>
      {scan.error&&<Alert severity="error" sx={{mt:2}} onClose={()=>scan.setError(undefined)}>{scan.error}</Alert>}
      {result&&<Alert severity={tone} variant="filled" sx={{mt:2,fontSize:'1.05rem'}}>
        <strong>{result.message}</strong> · {result.confidence.toFixed(1)}%
        {result.inventory&&<> · {result.inventory.set_name} #{result.inventory.collector_number} · ${Number(result.inventory.market_price||0).toFixed(2)} · Quantity {result.inventory.quantity}</>}
      </Alert>}
    </Grid>

    <Grid size={{xs:12,lg:4}}>
      <Card sx={{height:'100%',minHeight:420,overflow:'hidden'}}>
        <CardContent sx={{height:'100%',display:'flex',flexDirection:'column'}}>
          <Typography variant="overline" color="text.secondary" letterSpacing=".1em">Last identified</Typography>
          {lastIdentified?<LastIdentified inventory={lastIdentified.inventory} confidence={lastIdentified.confidence}/>:<Box sx={{flex:1,minHeight:340,display:'grid',placeItems:'center',textAlign:'center',px:3}}>
            <Box><CameraAlt sx={{fontSize:48,color:'text.disabled',mb:1}}/><Typography variant="h6">Waiting for the first card</Typography><Typography color="text.secondary">The last successful match will stay here so you can verify its artwork while continuing to scan.</Typography></Box>
          </Box>}
        </CardContent>
      </Card>
    </Grid>

    <Grid size={12}>
      <Card><CardContent>
        <Typography variant="h6" mb={2}>Scanner settings</Typography>
        <Grid container spacing={4}>
          <Grid size={{xs:12,md:7}}><Typography variant="subtitle1" mb={1.5}>Batch defaults</Typography><Grid container spacing={2}>
        <Grid size={{xs:12,sm:6,md:4}}><Select fullWidth value={defaults.condition} onChange={event=>setDefaults({...defaults,condition:event.target.value})}>
          {[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']].map(([value,label])=><MenuItem value={value} key={value}>{label}</MenuItem>)}
        </Select></Grid>
        <Grid size={{xs:12,sm:6,md:4}}><TextField fullWidth select label="Language" value={defaults.language} onChange={event=>setDefaults({...defaults,language:event.target.value})}>
          {languages.map(([value,label])=><MenuItem value={value} key={value}>{label}</MenuItem>)}
        </TextField></Grid>
        <Grid size={{xs:12,sm:6,md:4}}><Select fullWidth displayEmpty value={defaults.deck_id||''} onChange={event=>setDefaults({...defaults,deck_id:event.target.value||null})}><MenuItem value="">Deck · None</MenuItem>{decks.map(deck=><MenuItem value={deck.id} key={deck.id}>Deck · {deck.name}</MenuItem>)}</Select></Grid>
        <Grid size={{xs:12,sm:6}}><TextField fullWidth label="Storage location" placeholder="e.g. Box 4 / Row B" value={defaults.storage_location} onChange={event=>setDefaults({...defaults,storage_location:event.target.value||'Unsorted'})}/></Grid>
        <Grid size={{xs:12,sm:6}}><TextField fullWidth label="Box Mode set code" placeholder="e.g. FDN" value={defaults.box_set_code||''} onChange={event=>setDefaults({...defaults,box_set_code:event.target.value.trim()||null})}/></Grid>
        <Grid size={12}><Stack direction={{xs:'column',sm:'row'}} spacing={{xs:0,sm:2}} flexWrap="wrap">
          <FormControlLabel control={<Switch checked={defaults.foil} onChange={event=>setDefaults({...defaults,foil:event.target.checked})}/>} label="Foil"/>
          <FormControlLabel control={<Switch checked={defaults.auto_add} onChange={event=>setDefaults({...defaults,auto_add:event.target.checked})}/>} label="Auto-add near-certain matches (98.5%+)"/>
        </Stack></Grid>
      </Grid></Grid>
          <Grid size={{xs:12,md:5}} sx={{borderLeft:{md:'1px solid'},borderColor:{md:'divider'},pl:{md:4}}}>
            <Typography variant="subtitle1">Camera calibration</Typography>
            <Typography variant="body2" color="text.secondary" mb={1.5}>Tune only if cards capture too early or while still moving.</Typography>
            <FormControlLabel sx={{mb:1}} control={<Switch checked={tuning.slingerMode} onChange={event=>{setTuning({...tuning,slingerMode:event.target.checked});window.setTimeout(scan.calibrate,0)}}/>} label="Card Slinger mode"/>
            {tuning.slingerMode&&<Alert severity="info" sx={{mb:1.5}}>Use “Set scan area” around the slinger window. A card is counted only after slide motion followed by a stable image.</Alert>}
            <Typography variant="caption">Card entry difference: {tuning.entryDifference}</Typography>
            <Slider min={5} max={35} value={tuning.entryDifference} onChange={(_,value)=>setTuning({...tuning,entryDifference:value as number})}/>
            <Typography variant="caption">Minimum stable motion: {tuning.stableMotion.toFixed(1)}</Typography>
            <Slider min={.5} max={8} step={.1} value={tuning.stableMotion} onChange={(_,value)=>setTuning({...tuning,stableMotion:value as number})}/>
            <Typography variant="caption">Frames to capture: {tuning.stableFrames}</Typography>
            <Slider min={3} max={12} value={tuning.stableFrames} onChange={(_,value)=>setTuning({...tuning,stableFrames:value as number})}/>
          </Grid>
        </Grid>
      </CardContent></Card>
    </Grid>
    <Snackbar key={success?`${success.id}-${success.quantity}`:'empty'} open={!!success} autoHideDuration={1800} onClose={()=>setSuccess(null)} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
      <Card elevation={12} sx={{display:'flex',alignItems:'center',minWidth:{xs:320,sm:460},border:'2px solid',borderColor:'success.main',overflow:'hidden'}}>
        {success?.image_url&&<FoilArtwork src={success.image_url} alt={success.card_name} foil={success.foil} sx={{width:82,height:114}} imageSx={{objectFit:'cover',objectPosition:'top'}}/>}
        <Box px={2} py={1}><Typography color="success.main" fontWeight={900}>ADDED · {success?.quantity} OWNED</Typography><Typography className="card-title" variant="h6" fontWeight={900}>{success&&<CardName scryfallId={success.scryfall_id}>{success.card_name}</CardName>}</Typography><Typography className="card-printing" color="text.secondary">{success?.set_name} #{success?.collector_number} · ${Number(success?.market_price||0).toFixed(2)}</Typography></Box>
      </Card>
    </Snackbar>
    <Snackbar key={reviewNotice?.review_id||'no-review'} open={!!reviewNotice} autoHideDuration={1800} onClose={()=>setReviewNotice(null)} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
      <Alert severity="warning" variant="filled" sx={{minWidth:{xs:320,sm:460}}}><Typography fontWeight={900}>CARD REQUIRES REVIEW</Typography><Typography>{reviewNotice?.candidates[0]?.name||'Printing uncertain'} · Saved to Review · Keep scanning</Typography></Alert>
    </Snackbar>
  </Grid>
}

function LastIdentified({inventory,confidence}:{inventory:Inventory;confidence:number}){
  const price=Number(inventory.market_price||0)
  return <Stack sx={{flex:1,minHeight:0}} spacing={1.5}>
    <Box sx={{flex:1,minHeight:260,display:'grid',placeItems:'center',py:1.5}}>
      {inventory.image_url
        ?<FoilArtwork
          src={inventory.image_url}
          alt={inventory.card_name}
          foil={inventory.foil}
          sx={{height:'100%',maxHeight:430,maxWidth:'100%',aspectRatio:'488 / 680',borderRadius:'4.75% / 3.5%',boxShadow:'0 22px 54px rgba(0,0,0,.38)'}}
          imageSx={{objectFit:'contain'}}
        />
        :<Box sx={{width:'70%',aspectRatio:'488 / 680',border:'1px dashed',borderColor:'divider',borderRadius:3,display:'grid',placeItems:'center'}}><Typography color="text.secondary">Artwork unavailable</Typography></Box>}
    </Box>
    <Box sx={{borderTop:'1px solid',borderColor:'divider',pt:1.75}}>
      <Stack direction="row" spacing={1} mb={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" color={confidence>=98.5?'success':'warning'} label={`${confidence.toFixed(1)}% confidence`}/>
        {inventory.foil&&<Chip size="small" color="warning" label="Foil"/>}
        <Chip size="small" variant="outlined" label={`Quantity ${inventory.quantity}`}/>
      </Stack>
      <Typography className="card-title" variant="h5" fontWeight={900} lineHeight={1.05}><CardName scryfallId={inventory.scryfall_id}>{inventory.card_name}</CardName></Typography>
      <Typography className="card-printing" color="text.secondary" mt={.5}>{inventory.set_name} · {inventory.set_code.toUpperCase()} #{inventory.collector_number}</Typography>
      <Typography variant="h5" color="primary.main" mt={1}>${price.toFixed(2)}</Typography>
    </Box>
  </Stack>
}

function ScannerGauge({label,value,max,display,unit,tone,helper}:{label:string,value:number|null,max:number,display:string,unit:string,tone:'primary'|'success'|'warning',helper:string}){
  const progress=Math.max(0,Math.min(1,(value||0)/max))
  return <Box sx={{position:'relative',height:128,borderTop:'1px solid',borderBottom:'1px solid',borderColor:'divider',display:'flex',alignItems:'center',px:2,gap:2,overflow:'hidden'}}>
    <Box sx={{width:118,height:74,position:'relative',flexShrink:0}}>
      <Box component="svg" viewBox="0 0 120 72" aria-hidden sx={{width:120,height:72,overflow:'visible'}}>
        <Box component="path" d="M 12 62 A 48 48 0 0 1 108 62" fill="none" stroke="currentColor" sx={{color:'action.hover'}} strokeWidth="10" strokeLinecap="round"/>
        <Box component="path" d="M 12 62 A 48 48 0 0 1 108 62" fill="none" stroke="currentColor" sx={{color:`${tone}.main`,transition:'stroke-dashoffset 350ms ease'}} strokeWidth="10" strokeLinecap="round" pathLength="100" strokeDasharray="100" strokeDashoffset={100-progress*100}/>
      </Box>
      <Box sx={{position:'absolute',inset:'29px 0 0',textAlign:'center'}}><Typography variant="h5" lineHeight={1}>{display}</Typography><Typography variant="caption" color="text.secondary">{unit}</Typography></Box>
    </Box>
    <Box minWidth={0}><Typography variant="overline" color="text.secondary" letterSpacing=".08em">{label}</Typography><Typography variant="body2">{helper}</Typography></Box>
  </Box>
}
