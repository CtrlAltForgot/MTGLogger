import { useEffect, useState } from 'react'
import { Delete, Download, Edit, Search, TrendingDown, TrendingUp } from '@mui/icons-material'
import {
  Box, Button, Card, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Alert, FormControlLabel, IconButton, InputAdornment, MenuItem, Select, Stack, Switch,
  TablePagination, TextField, Tooltip, Typography,
} from '@mui/material'
import { API, request } from '../api'
import FoilArtwork from '../components/FoilArtwork'
import { CardName } from '../components/CardDetails'
import OverflowMarquee from '../components/OverflowMarquee'
import type { Inventory } from '../types'

const conditions=[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']]
const conditionAbbreviation:Record<string,string>={near_mint:'NM',lightly_played:'LP',moderately_played:'MP',heavily_played:'HP',damaged:'DMG'}
const sorts={newest:'sort=updated_at&descending=true',name:'sort=card_name&descending=false',value:'sort=market_price&descending=true'}

export default function Collection(){
  const [items,setItems]=useState<Inventory[]>([]),[total,setTotal]=useState(0),[totalCards,setTotalCards]=useState(0),[collectionValue,setCollectionValue]=useState(0)
  const [query,setQuery]=useState(''),[sort,setSort]=useState<keyof typeof sorts>('newest'),[reload,setReload]=useState(0)
  const [page,setPage]=useState(0),[pageSize,setPageSize]=useState(48)
  const [location,setLocation]=useState(''),[facets,setFacets]=useState<{collections:string[];storage_locations:string[]}>({collections:[],storage_locations:[]})
  const [editing,setEditing]=useState<Inventory|null>(null),[deleting,setDeleting]=useState<Inventory|null>(null),[busy,setBusy]=useState(false),[priceBusy,setPriceBusy]=useState(false)
  const [error,setError]=useState<string>()

  useEffect(()=>{void request<{collections:string[];storage_locations:string[]}>('/inventory/facets').then(setFacets)},[reload])
  useEffect(()=>{setPage(0)},[query,sort,location])
  useEffect(()=>{const params=new URLSearchParams({q:query,page:String(page+1),page_size:String(pageSize),...Object.fromEntries(new URLSearchParams(sorts[sort]))});if(location)params.set('storage_location',location);const timer=setTimeout(()=>request<{items:Inventory[],total:number,total_cards:number,collection_value:number,page_size:number}>(`/inventory?${params}`).then(x=>{setItems(x.items);setTotal(x.total);setTotalCards(x.total_cards);setCollectionValue(Number(x.collection_value));if(x.total>0&&page*x.page_size>=x.total)setPage(Math.max(0,Math.ceil(x.total/x.page_size)-1))}).catch(e=>setError(e instanceof Error?e.message:'Could not load collection')),200);return()=>clearTimeout(timer)},[query,sort,location,page,pageSize,reload])
  const save=async()=>{if(!editing)return;setBusy(true);setError(undefined);try{await request(`/inventory/${editing.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:editing.quantity,foil:editing.foil,condition:editing.condition,language:editing.language,storage_location:editing.storage_location,market_price:editing.market_price,purchase_price:editing.purchase_price,notes:editing.notes})});setEditing(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not save entry')}finally{setBusy(false)}}
  const setFoil=async(foil:boolean)=>{if(!editing)return;const itemId=editing.id;setEditing({...editing,foil});setPriceBusy(true);setError(undefined);try{const price=await request<{market_price:number|null}>(`/inventory/${itemId}/price?foil=${foil}`);setEditing(current=>current?.id===itemId?{...current,foil,market_price:price.market_price}:current)}catch(e){setError(e instanceof Error?e.message:'Could not refresh finish price')}finally{setPriceBusy(false)}}
  const remove=async()=>{if(!deleting)return;setBusy(true);setError(undefined);try{await request(`/inventory/${deleting.id}`,{method:'DELETE'});setDeleting(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not delete entry')}finally{setBusy(false)}}

  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2} mb={3}>
      <Box><Typography variant="h4">Collection</Typography><Typography color="text.secondary">{totalCards.toLocaleString()} {totalCards===1?'card':'cards'} · Collection value: <Box component="span" color="primary.main" fontWeight={750}>${collectionValue.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</Box></Typography></Box>
      <Stack direction="row" spacing={1}><Button startIcon={<Download/>} href={`${API}/api/inventory/export/csv`}>CSV</Button><Button startIcon={<Download/>} href={`${API}/api/inventory/export/json`}>JSON</Button></Stack>
    </Stack>
    {error&&<Alert severity="error" onClose={()=>setError(undefined)} sx={{mb:2}}>{error}</Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
      <TextField fullWidth placeholder="Search name or collector number" value={query} onChange={e=>setQuery(e.target.value)} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/>
      <Select value={sort} onChange={e=>setSort(e.target.value as keyof typeof sorts)} sx={{minWidth:190}}><MenuItem value="newest">Recently added</MenuItem><MenuItem value="name">Name A–Z</MenuItem><MenuItem value="value">Most valuable</MenuItem></Select>
    </Stack>
    {facets.storage_locations.length>1&&<Stack direction="row" spacing={1} mt={1.5}><Select size="small" displayEmpty value={location} onChange={e=>setLocation(e.target.value)} sx={{minWidth:190}}><MenuItem value="">All storage locations</MenuItem>{facets.storage_locations.map(value=><MenuItem key={value} value={value}>{value}</MenuItem>)}</Select></Stack>}
    <Box className="collection-grid" mt={3}>{items.map(item=><Card key={item.id} sx={{display:'flex',overflow:'hidden',position:'relative',alignItems:'flex-start',minHeight:202}}>
      {item.image_url?<FoilArtwork src={item.image_url} alt={item.card_name} foil={item.foil} sx={{width:{xs:116,sm:128},flexShrink:0,aspectRatio:'63 / 88',m:1,borderRadius:1.5,border:'1px solid',borderColor:'divider',bgcolor:'#080506'}} imageSx={{objectFit:'contain',objectPosition:'center'}}/>:<Box sx={{width:{xs:116,sm:128},flexShrink:0,aspectRatio:'63 / 88',m:1,bgcolor:'action.hover',border:'1px solid',borderColor:'divider',borderRadius:1.5}}/>}
      {item.quantity>1&&<Chip size="small" color="primary" label={`×${item.quantity}`} sx={{position:'absolute',left:14,top:14,zIndex:2,fontWeight:900,boxShadow:'0 2px 10px rgba(0,0,0,.55)'}}/>}
      <Box py={1.5} pl={.5} pr={1} pb={6} minWidth={0} flex={1}><Typography component="div" fontWeight={800}><OverflowMarquee className="card-title" title={item.card_name}><CardName scryfallId={item.scryfall_id}>{item.card_name}</CardName></OverflowMarquee></Typography><Typography component="div" color="text.secondary" variant="body2"><OverflowMarquee className="card-printing" title={`${item.set_name} #${item.collector_number}`}>{item.set_name} #{item.collector_number}</OverflowMarquee></Typography><PriceMovement item={item}/><Stack direction="row" spacing={.5} mt={1} flexWrap="wrap"><Chip size="small" variant="outlined" label={conditionAbbreviation[item.condition]||item.condition.toUpperCase()}/>{item.foil&&<Chip size="small" color="warning" label="Foil"/>}</Stack><Typography display="block" variant="caption" color="text.secondary">Deck · {item.deck_assignments.length?item.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'}</Typography><Typography variant="caption" color="text.secondary">Storage · {item.storage_location}</Typography></Box>
      <Stack direction="row" sx={{position:'absolute',right:4,bottom:4}}><Tooltip title="Edit entry"><IconButton aria-label={`Edit ${item.card_name}`} onClick={()=>setEditing({...item})}><Edit/></IconButton></Tooltip><Tooltip title="Delete entry"><IconButton color="error" aria-label={`Delete ${item.card_name}`} onClick={()=>setDeleting(item)}><Delete/></IconButton></Tooltip></Stack>
    </Card>)}</Box>
    {!items.length&&<Typography textAlign="center" color="text.secondary" mt={8}>No cards found. The scanner is ready when you are.</Typography>}
    {total>0&&<TablePagination
      component="div"
      count={total}
      page={page}
      onPageChange={(_,nextPage)=>setPage(nextPage)}
      rowsPerPage={pageSize}
      onRowsPerPageChange={event=>{setPageSize(Number(event.target.value));setPage(0)}}
      rowsPerPageOptions={[24,48,96,192]}
      labelRowsPerPage="Cards per page"
      sx={{mt:2}}
    />}

    <Dialog open={!!editing} onClose={()=>!busy&&setEditing(null)} fullWidth maxWidth="sm"><DialogTitle>Edit collection entry</DialogTitle>{editing&&<DialogContent><Stack spacing={2} mt={1}>
      {editing.image_url&&<FoilArtwork src={editing.image_url} alt={editing.card_name} foil={editing.foil} sx={{width:150,alignSelf:'center',borderRadius:2}}/>}
      <Typography className="card-title" variant="h6">{editing.card_name} · {editing.set_code.toUpperCase()} #{editing.collector_number}</Typography>
      <TextField label="Quantity" type="number" value={editing.quantity} onChange={e=>setEditing({...editing,quantity:Math.max(1,Number(e.target.value))})}/>
      <Select value={editing.condition} onChange={e=>setEditing({...editing,condition:e.target.value})}>{conditions.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</Select>
      <FormControlLabel control={<Switch checked={editing.foil} onChange={e=>void setFoil(e.target.checked)}/>} label="Foil"/>
      <TextField label="Language" value={editing.language} onChange={e=>setEditing({...editing,language:e.target.value})}/>
      <TextField label="Deck" value={editing.deck_assignments.length?editing.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'} helperText="Deck quantities are managed in the Decks tab." slotProps={{input:{readOnly:true}}}/>
      <TextField label="Storage location" value={editing.storage_location} onChange={e=>setEditing({...editing,storage_location:e.target.value})}/>
      <Stack direction="row" spacing={2}><TextField fullWidth label="Purchase price" type="number" value={editing.purchase_price??''} onChange={e=>setEditing({...editing,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth disabled={priceBusy} label={priceBusy?'Market price · refreshing…':'Market price'} type="number" value={editing.market_price??''} onChange={e=>setEditing({...editing,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack>
      <TextField label="Notes" multiline minRows={2} value={editing.notes??''} onChange={e=>setEditing({...editing,notes:e.target.value})}/>
    </Stack></DialogContent>}<DialogActions><Button disabled={busy} onClick={()=>setEditing(null)}>Cancel</Button><Button disabled={busy} variant="contained" onClick={save}>Save changes</Button></DialogActions></Dialog>

    <Dialog open={!!deleting} onClose={()=>!busy&&setDeleting(null)}><DialogTitle>Delete this entry?</DialogTitle><DialogContent><Typography>This permanently removes all {deleting?.quantity} logged copies of <strong>{deleting?.card_name}</strong> ({deleting?.set_code.toUpperCase()} #{deleting?.collector_number}) from this collection.</Typography></DialogContent><DialogActions><Button disabled={busy} onClick={()=>setDeleting(null)}>Cancel</Button><Button disabled={busy} color="error" variant="contained" onClick={remove}>Delete entry</Button></DialogActions></Dialog>
  </>
}

function PriceMovement({item}:{item:Inventory}){
  const current=Number(item.market_price||0),previous=item.previous_market_price==null?null:Number(item.previous_market_price)
  const change=previous&&current!==previous?(current-previous)/previous*100:null
  const up=change!=null&&change>0
  return <Stack direction="row" alignItems="center" spacing={.75} mt={1}>
    <Typography variant="h6" color={change==null?'primary.main':up?'success.main':'error.main'}>${current.toFixed(2)}</Typography>
    {change!=null&&<Stack direction="row" alignItems="center" color={up?'success.main':'error.main'}>{up?<TrendingUp fontSize="small"/>:<TrendingDown fontSize="small"/>}<Typography variant="caption" fontWeight={800}>{up?'+':''}{change.toFixed(1)}%</Typography></Stack>}
  </Stack>
}
