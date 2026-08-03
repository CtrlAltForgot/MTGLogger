import { useEffect, useState } from 'react'
import { DeleteOutline, Search, VisibilityOff } from '@mui/icons-material'
import { Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogContent, DialogTitle, Grid, InputAdornment, Stack, TextField, Typography } from '@mui/material'
import { API, request } from '../api'
import type { Candidate } from '../types'

type Review={id:string;confidence:number;ocr_text:string|null;created_at:string;candidates:Candidate[]}

export default function ReviewQueue(){
  const [items,setItems]=useState<Review[]>([]),[manual,setManual]=useState<Review|null>(null),[query,setQuery]=useState(''),[matches,setMatches]=useState<Candidate[]>([]),[error,setError]=useState<string>()
  const load=()=>request<Review[]>('/reviews').then(setItems)
  useEffect(()=>{void load()},[])
  const ignore=async(id:string)=>{await request(`/reviews/${id}/ignore`,{method:'POST'});load()}
  const remove=async(id:string)=>{await request(`/reviews/${id}`,{method:'DELETE'});load()}
  const resolve=async(item:Review,candidate:Candidate)=>{await request(`/reviews/${item.id}/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate,defaults:{}})});setManual(null);setMatches([]);load()}
  const search=async()=>{setError(undefined);try{setMatches(await request<Candidate[]>(`/reviews/search?q=${encodeURIComponent(query)}`))}catch(e){setError(e instanceof Error?e.message:'Search failed')}}
  return <>
    <Typography variant="h4">Review queue</Typography><Typography color="text.secondary" mb={3}>Hard scans wait here without interrupting a batch.</Typography>
    <Grid container spacing={2}>{items.map(item=><Grid size={{xs:12,lg:6}} key={item.id}><Card><CardContent>
      <Stack direction="row" justifyContent="space-between"><Chip color={item.confidence>=70?'warning':'error'} label={`${item.confidence.toFixed(1)}% confidence`}/><Stack direction="row"><Button size="small" startIcon={<VisibilityOff/>} onClick={()=>ignore(item.id)}>Ignore</Button><Button size="small" color="error" startIcon={<DeleteOutline/>} onClick={()=>remove(item.id)}>Delete</Button></Stack></Stack>
      <Stack direction={{xs:'column',sm:'row'}} spacing={2} mt={2}><Box component="img" src={`${API}/api/reviews/${item.id}/image`} sx={{width:{xs:'100%',sm:180},maxHeight:260,objectFit:'contain',bgcolor:'#050807',borderRadius:1}}/><Box flex={1} minWidth={0}><Typography variant="caption" color="text.secondary" sx={{whiteSpace:'pre-line'}}>OCR: {item.ocr_text||'No readable text'}</Typography>{item.candidates.map(c=><Stack key={c.scryfall_id} direction="row" spacing={1} alignItems="center" mt={1}><Box component="img" src={c.image_url||''} sx={{width:42}}/><Box flex={1}><Typography fontWeight={700}>{c.name}</Typography><Typography variant="body2">{c.set_name} #{c.collector_number}</Typography></Box><Button variant="contained" size="small" onClick={()=>resolve(item,c)}>Choose</Button></Stack>)}<Button startIcon={<Search/>} sx={{mt:2}} onClick={()=>{setManual(item);setQuery('');setMatches([])}}>Search manually</Button></Box></Stack>
    </CardContent></Card></Grid>)}</Grid>
    {!items.length&&<Typography color="text.secondary" textAlign="center" mt={8}>Nothing needs review.</Typography>}
    <Dialog open={!!manual} onClose={()=>setManual(null)} fullWidth maxWidth="md"><DialogTitle>Find the exact printing</DialogTitle><DialogContent><Stack direction="row" spacing={1} mt={1}><TextField autoFocus fullWidth placeholder="Card name" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&query.length>=2)void search()}} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/><Button variant="contained" disabled={query.length<2} onClick={search}>Search</Button></Stack>{error&&<Alert severity="error" sx={{mt:2}}>{error}</Alert>}<Grid container spacing={2} mt={1}>{matches.map(c=><Grid size={{xs:12,sm:6}} key={c.scryfall_id}><Card variant="outlined"><CardContent><Stack direction="row" spacing={2}><Box component="img" src={c.image_url||''} sx={{width:75}}/><Box flex={1}><Typography fontWeight={800}>{c.name}</Typography><Typography variant="body2">{c.set_name} #{c.collector_number}</Typography><Typography color="primary.main">${Number(c.market_price||0).toFixed(2)}</Typography><Button size="small" variant="contained" sx={{mt:1}} onClick={()=>manual&&resolve(manual,c)}>Choose printing</Button></Box></Stack></CardContent></Card></Grid>)}</Grid></DialogContent></Dialog>
  </>
}
