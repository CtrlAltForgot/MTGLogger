import { useEffect, useState } from 'react'
import { Delete, Download, Edit, Search } from '@mui/icons-material'
import {
  Box, Button, Card, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Alert, FormControlLabel, IconButton, InputAdornment, MenuItem, Select, Stack, Switch,
  TextField, Tooltip, Typography,
} from '@mui/material'
import { API, request } from '../api'
import type { Inventory } from '../types'

const conditions=[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']]
const sorts={newest:'sort=date_added&descending=true',name:'sort=card_name&descending=false',value:'sort=market_price&descending=true'}

export default function Collection(){
  const [items,setItems]=useState<Inventory[]>([]),[total,setTotal]=useState(0)
  const [query,setQuery]=useState(''),[sort,setSort]=useState<keyof typeof sorts>('newest'),[reload,setReload]=useState(0)
  const [location,setLocation]=useState(''),[facets,setFacets]=useState<{collections:string[];storage_locations:string[]}>({collections:[],storage_locations:[]})
  const [editing,setEditing]=useState<Inventory|null>(null),[deleting,setDeleting]=useState<Inventory|null>(null),[busy,setBusy]=useState(false)
  const [error,setError]=useState<string>()

  useEffect(()=>{void request<{collections:string[];storage_locations:string[]}>('/inventory/facets').then(setFacets)},[reload])
  useEffect(()=>{const params=new URLSearchParams({q:query,...Object.fromEntries(new URLSearchParams(sorts[sort]))});if(location)params.set('storage_location',location);const timer=setTimeout(()=>request<{items:Inventory[],total:number}>(`/inventory?${params}`).then(x=>{setItems(x.items);setTotal(x.total)}),200);return()=>clearTimeout(timer)},[query,sort,location,reload])
  const save=async()=>{if(!editing)return;setBusy(true);setError(undefined);try{await request(`/inventory/${editing.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:editing.quantity,foil:editing.foil,condition:editing.condition,language:editing.language,storage_location:editing.storage_location,market_price:editing.market_price,purchase_price:editing.purchase_price,notes:editing.notes})});setEditing(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not save entry')}finally{setBusy(false)}}
  const remove=async()=>{if(!deleting)return;setBusy(true);setError(undefined);try{await request(`/inventory/${deleting.id}`,{method:'DELETE'});setDeleting(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not delete entry')}finally{setBusy(false)}}

  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2} mb={3}>
      <Box><Typography variant="h4">Collection</Typography><Typography color="text.secondary">{total} unique inventory entries</Typography></Box>
      <Stack direction="row" spacing={1}><Button startIcon={<Download/>} href={`${API}/api/inventory/export/csv`}>CSV</Button><Button startIcon={<Download/>} href={`${API}/api/inventory/export/json`}>JSON</Button></Stack>
    </Stack>
    {error&&<Alert severity="error" onClose={()=>setError(undefined)} sx={{mb:2}}>{error}</Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
      <TextField fullWidth placeholder="Search name or collector number" value={query} onChange={e=>setQuery(e.target.value)} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/>
      <Select value={sort} onChange={e=>setSort(e.target.value as keyof typeof sorts)} sx={{minWidth:190}}><MenuItem value="newest">Recently added</MenuItem><MenuItem value="name">Name A–Z</MenuItem><MenuItem value="value">Most valuable</MenuItem></Select>
    </Stack>
    {facets.storage_locations.length>1&&<Stack direction="row" spacing={1} mt={1.5}><Select size="small" displayEmpty value={location} onChange={e=>setLocation(e.target.value)} sx={{minWidth:190}}><MenuItem value="">All storage locations</MenuItem>{facets.storage_locations.map(value=><MenuItem key={value} value={value}>{value}</MenuItem>)}</Select></Stack>}
    <Box className="collection-grid" mt={3}>{items.map(item=><Card key={item.id} sx={{display:'flex',overflow:'hidden',position:'relative'}}>
      {item.image_url?<Box component="img" src={item.image_url} loading="lazy" sx={{width:105,objectFit:'cover',objectPosition:'top'}}/>:<Box sx={{width:105,bgcolor:'action.hover'}}/>}
      <Box p={2} pr={7} minWidth={0}><Typography fontWeight={800} noWrap>{item.card_name}</Typography><Typography color="text.secondary" variant="body2">{item.set_name} #{item.collector_number}</Typography><Typography variant="h6" color="primary.main" mt={1}>${Number(item.market_price||0).toFixed(2)}</Typography><Stack direction="row" spacing={.5} mt={1} flexWrap="wrap"><Chip size="small" label={`×${item.quantity}`}/><Chip size="small" variant="outlined" label={item.condition.replaceAll('_',' ')}/>{item.foil&&<Chip size="small" color="warning" label="Foil"/>}</Stack><Typography display="block" variant="caption" color="text.secondary">Deck · {item.deck_assignments.length?item.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'}</Typography><Typography variant="caption" color="text.secondary">Storage · {item.storage_location}</Typography></Box>
      <Stack sx={{position:'absolute',right:4,top:4}}><Tooltip title="Edit entry"><IconButton aria-label={`Edit ${item.card_name}`} onClick={()=>setEditing({...item})}><Edit/></IconButton></Tooltip><Tooltip title="Delete entry"><IconButton color="error" aria-label={`Delete ${item.card_name}`} onClick={()=>setDeleting(item)}><Delete/></IconButton></Tooltip></Stack>
    </Card>)}</Box>
    {!items.length&&<Typography textAlign="center" color="text.secondary" mt={8}>No cards found. The scanner is ready when you are.</Typography>}

    <Dialog open={!!editing} onClose={()=>!busy&&setEditing(null)} fullWidth maxWidth="sm"><DialogTitle>Edit collection entry</DialogTitle>{editing&&<DialogContent><Stack spacing={2} mt={1}>
      <Typography variant="h6">{editing.card_name} · {editing.set_code.toUpperCase()} #{editing.collector_number}</Typography>
      <TextField label="Quantity" type="number" value={editing.quantity} onChange={e=>setEditing({...editing,quantity:Math.max(1,Number(e.target.value))})}/>
      <Select value={editing.condition} onChange={e=>setEditing({...editing,condition:e.target.value})}>{conditions.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</Select>
      <FormControlLabel control={<Switch checked={editing.foil} onChange={e=>setEditing({...editing,foil:e.target.checked})}/>} label="Foil"/>
      <TextField label="Language" value={editing.language} onChange={e=>setEditing({...editing,language:e.target.value})}/>
      <TextField label="Deck" value={editing.deck_assignments.length?editing.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'} helperText="Deck quantities are managed in the Decks tab." slotProps={{input:{readOnly:true}}}/>
      <TextField label="Storage location" value={editing.storage_location} onChange={e=>setEditing({...editing,storage_location:e.target.value})}/>
      <Stack direction="row" spacing={2}><TextField fullWidth label="Purchase price" type="number" value={editing.purchase_price??''} onChange={e=>setEditing({...editing,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth label="Market price" type="number" value={editing.market_price??''} onChange={e=>setEditing({...editing,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack>
      <TextField label="Notes" multiline minRows={2} value={editing.notes??''} onChange={e=>setEditing({...editing,notes:e.target.value})}/>
    </Stack></DialogContent>}<DialogActions><Button disabled={busy} onClick={()=>setEditing(null)}>Cancel</Button><Button disabled={busy} variant="contained" onClick={save}>Save changes</Button></DialogActions></Dialog>

    <Dialog open={!!deleting} onClose={()=>!busy&&setDeleting(null)}><DialogTitle>Delete this entry?</DialogTitle><DialogContent><Typography>This permanently removes all {deleting?.quantity} logged copies of <strong>{deleting?.card_name}</strong> ({deleting?.set_code.toUpperCase()} #{deleting?.collector_number}) from this collection.</Typography></DialogContent><DialogActions><Button disabled={busy} onClick={()=>setDeleting(null)}>Cancel</Button><Button disabled={busy} color="error" variant="contained" onClick={remove}>Delete entry</Button></DialogActions></Dialog>
  </>
}
