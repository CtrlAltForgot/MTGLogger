import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle, Search } from '@mui/icons-material'
import {
  Alert, Box, Card, CardContent, Chip, Grid, InputAdornment,
  LinearProgress, List, ListItemButton, ListItemText, Pagination, Skeleton, Stack,
  TextField, Typography,
} from '@mui/material'
import { request } from '../api'
import { CardName } from '../components/CardDetails'
import type { ReferenceStatus } from '../types'

type IndexedSet={set_code:string;set_name:string;indexed_printings:number;ready_printings:number;released_at:string|null;updated_at:string|null}
type IndexedCard={scryfall_id:string;name:string;set_code:string;set_name:string;collector_number:string;image_url:string;language:string;layout:string;updated_at:string}
type CardPage={items:IndexedCard[];total:number;page:number;page_size:number}

const duration=(seconds:number)=>{const hours=Math.floor(seconds/3600),minutes=Math.max(1,Math.round(seconds%3600/60));return hours?`${hours}h ${minutes}m`:`${minutes}m`}
const setSummary=(set:IndexedSet)=>`${set.set_code.toUpperCase()}${set.released_at?` · ${set.released_at.slice(0,4)}`:''} · ${set.ready_printings.toLocaleString()} ready`
const setSymbol=(setCode:string)=>`https://svgs.scryfall.io/sets/${encodeURIComponent(setCode.toLowerCase())}.svg`
const symbolAliases:Record<string,string>={ '4bb':'4ed' }
const symbolFallbacks=new Set(['plst'])
const fallbackMark=(code:string)=>/^[fg]\d{2}$/i.test(code)?'DCI':code.toUpperCase()
const SetSymbol=({code,size=20}:{code:string;size?:number})=>{const normalized=code.toLowerCase(),symbolCode=symbolAliases[normalized]||normalized;const [failed,setFailed]=useState(symbolFallbacks.has(normalized));return failed?<Box title={`${code.toUpperCase()} set mark`} sx={{width:Math.max(size,30),height:size,display:'grid',placeItems:'center',flex:'0 0 auto',border:'1px solid',borderColor:'divider',borderRadius:99,fontSize:fallbackMark(code)==='DCI'?7.5:8,fontWeight:900,fontStyle:fallbackMark(code)==='DCI'?'italic':'normal',letterSpacing:'-.03em',color:'text.secondary'}}>{fallbackMark(code)}</Box>:<Box component="img" src={setSymbol(symbolCode)} alt={`${code.toUpperCase()} set symbol`} loading="lazy" onError={()=>setFailed(true)} sx={{width:size,height:size,objectFit:'contain',flex:'0 0 auto',filter:'invert(1)',opacity:.86}}/>}

export default function Database(){
  const [status,setStatus]=useState<ReferenceStatus>()
  const [sets,setSets]=useState<IndexedSet[]>([])
  const [selected,setSelected]=useState('')
  const [cards,setCards]=useState<CardPage>()
  const [search,setSearch]=useState('')
  const [page,setPage]=useState(1)
  const [error,setError]=useState('')
  const activeCode=status?.set_code?.replace('priority:','').toLowerCase()
  const selectedSet=sets.find(item=>item.set_code===selected)

  const refreshOverview=useCallback(async()=>{
    try{
      const [nextStatus,nextSets]=await Promise.all([
        request<ReferenceStatus>('/references/status'),request<IndexedSet[]>('/references/sets'),
      ])
      setStatus(nextStatus);setSets(nextSets);setError('')
      setSelected(current=>current||(nextSets.some(item=>item.set_code===nextStatus.set_code?.replace('priority:','').toLowerCase())?nextStatus.set_code!.replace('priority:','').toLowerCase():nextSets[0]?.set_code||''))
    }catch(reason){setError(reason instanceof Error?reason.message:'Could not load MTG Database')}
  },[])
  const refreshCards=useCallback(async()=>{
    if(!selected)return
    try{
      const params=new URLSearchParams({set_code:selected,page:String(page),page_size:'40'})
      if(search.trim())params.set('search',search.trim())
      setCards(await request<CardPage>(`/references/cards?${params}`));setError('')
    }catch(reason){setError(reason instanceof Error?reason.message:'Could not load indexed cards')}
  },[page,search,selected])

  useEffect(()=>{void refreshOverview();const timer=setInterval(refreshOverview,2500);return()=>clearInterval(timer)},[refreshOverview])
  useEffect(()=>{setCards(undefined);void refreshCards();const timer=setInterval(refreshCards,5000);return()=>clearInterval(timer)},[refreshCards])
  const pages=Math.max(1,Math.ceil((cards?.total||0)/40))
  const coverage=useMemo(()=>status?.coverage_percent||0,[status?.coverage_percent])

  return <>
    <Stack direction={{xs:'column',md:'row'}} justifyContent="space-between" spacing={2} mb={3}>
      <Box><Typography variant="h4">MTG Database</Typography><Typography color="text.secondary">Server-side copy of the entire MTG card database to heavily increase recognition accuracy and speed.</Typography></Box>
      <Stack direction="row" spacing={1} alignItems="center"><Chip color={status?.state==='running'?'primary':status?.state==='failed'?'error':status?.coverage_percent===100?'success':'warning'} label={status?.state==='running'?'Syncing…':status?.state==='failed'?'Update interrupted':status?.coverage_percent===100?'Up to date':'Resuming…'}/><Chip variant="outlined" label="All printings"/></Stack>
    </Stack>
    {(error||status?.state==='failed')&&<Alert severity="error" sx={{mb:2}}>{error||status?.error||'Catalog update was interrupted and will retry automatically.'}</Alert>}
    <Card><CardContent>
      <Grid container spacing={3}><Grid size={{xs:6,md:3}}><Typography variant="h4">{(status?.fingerprinted_cards||0).toLocaleString()}</Typography><Typography color="text.secondary">ready printings</Typography></Grid><Grid size={{xs:6,md:3}}><Typography variant="h4">{status?.catalog_total?.toLocaleString()||'—'}</Typography><Typography color="text.secondary">paper printings needed</Typography></Grid><Grid size={{xs:6,md:3}}><Typography variant="h4">{status?.indexed_sets||0}</Typography><Typography color="text.secondary">sets represented</Typography></Grid><Grid size={{xs:6,md:3}}><Typography variant="h4">{status?.errors||0}</Typography><Typography color="text.secondary">sync errors</Typography></Grid></Grid>
      <LinearProgress variant={status?.catalog_total?'determinate':'indeterminate'} value={coverage} sx={{height:11,borderRadius:99,mt:2.5}}/>
      <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" mt={1} spacing={.5}><Box><Typography fontWeight={800}>{status?.coverage_percent==null?'Discovering catalog size':`${coverage.toFixed(2)}% of the complete queue ready`}</Typography>{status?.catalog_total!=null&&<Typography variant="caption" color="text.secondary">{Math.max(0,status.catalog_total-status.fingerprinted_cards).toLocaleString()} exact-print profiles remaining overall{activeCode?` · currently processing ${activeCode.toUpperCase()}`:''}</Typography>}</Box>{status?.state==='running'&&status.estimated_seconds_remaining!=null?<Typography color="primary.main" fontWeight={800}>ETA · about {duration(status.estimated_seconds_remaining)} · {status.estimated_completion_at&&new Date(status.estimated_completion_at).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})} <Typography component="span" color="text.secondary">({status.indexing_rate_per_second?.toFixed(1)}/sec)</Typography></Typography>:<Typography color="text.secondary">{status?.updated_at?`Updated ${new Date(status.updated_at).toLocaleString()}`:''}</Typography>}</Stack>
    </CardContent></Card>

    <Grid container spacing={2.5} mt={.5}>
      <Grid size={{xs:12,md:4,lg:3}}><Card sx={{height:{md:720},overflow:'hidden'}}><CardContent sx={{pb:1}}><Typography variant="h6">Indexed sets</Typography><Typography variant="caption" color="text.secondary">Choose a set to see cards ready for scanning.</Typography></CardContent><List dense sx={{overflowY:'auto',height:{md:640},pt:0}}>{sets.map(item=><ListItemButton selected={selected===item.set_code} key={item.set_code} onClick={()=>{setSelected(item.set_code);setPage(1);setSearch('')}}><SetSymbol code={item.set_code} size={22}/><ListItemText sx={{ml:1.25}} primary={<Stack direction="row" justifyContent="space-between" gap={1}><Typography fontWeight={800}>{item.set_name}</Typography>{item.set_code===activeCode&&<Chip label="Adding" color="primary" size="small"/>}</Stack>} secondary={setSummary(item)}/></ListItemButton>)}{!sets.length&&<Box px={2}><Skeleton height={50}/><Skeleton height={50}/><Skeleton height={50}/></Box>}</List></Card></Grid>
      <Grid size={{xs:12,md:8,lg:9}}><Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} spacing={2} mb={2}><Box><Typography variant="h5">{selectedSet?.set_name||'Choose a set'}</Typography>{selectedSet&&<Typography color="text.secondary">{selectedSet.set_code.toUpperCase()}{selectedSet.released_at?` · ${selectedSet.released_at.slice(0,4)}`:''} · {selectedSet.ready_printings.toLocaleString()} visual printings ready to scan</Typography>}</Box><TextField size="small" placeholder="Search card or collector number" value={search} onChange={event=>{setSearch(event.target.value);setPage(1)}} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}} sx={{minWidth:{sm:320}}}/></Stack>
        {!cards?<Grid container spacing={1.5}>{Array.from({length:8},(_,index)=><Grid size={{xs:6,sm:4,lg:3}} key={index}><Skeleton variant="rounded" height={300}/></Grid>)}</Grid>:<><Grid container spacing={1.5}>{cards.items.map(card=><Grid size={{xs:6,sm:4,lg:3}} key={card.scryfall_id}><Card sx={{height:'100%'}}><Box component="img" src={card.image_url} alt={card.name} loading="lazy" sx={{display:'block',width:'100%',aspectRatio:'745/1040',objectFit:'contain',bgcolor:'rgba(0,0,0,.2)',borderRadius:'0 0 12px 12px'}}/><CardContent sx={{width:'100%',p:1.5}}><Stack direction="row" spacing={.5} alignItems="center"><CheckCircle color="success" sx={{fontSize:16}}/><Typography variant="caption" color="success.main" fontWeight={900}>READY TO SCAN</Typography></Stack><Stack direction="row" spacing={.75} alignItems="center" minWidth={0}><SetSymbol code={card.set_code}/><Typography className="card-title" fontWeight={900} noWrap title={card.name} minWidth={0}><CardName scryfallId={card.scryfall_id}>{card.name}</CardName></Typography></Stack><Typography className="card-printing" variant="caption" color="text.secondary">{card.set_code.toUpperCase()} #{card.collector_number} · {card.language.toUpperCase()}</Typography></CardContent></Card></Grid>)}{!cards.items.length&&<Grid size={{xs:12}}><Alert severity="info">No ready printings match this search yet.</Alert></Grid>}</Grid>{pages>1&&<Stack alignItems="center" mt={3}><Pagination count={pages} page={page} onChange={(_,value)=>setPage(value)} color="primary"/></Stack>}</>}
      </Grid>
    </Grid>
  </>
}
