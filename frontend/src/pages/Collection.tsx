import { useEffect, useState } from 'react'
import { Delete, Download, Edit, Search } from '@mui/icons-material'
import {
  Box, Button, Card, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, IconButton, InputAdornment, MenuItem, Select, Stack, Switch,
  TextField, Tooltip, Typography,
} from '@mui/material'
import { API, request } from '../api'
import type { Inventory } from '../types'

const conditions=[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']]
const statuses=['owned','wishlist','for_trade','for_sale','loaned']
const sorts={newest:'sort=date_added&descending=true',name:'sort=card_name&descending=false',value:'sort=market_price&descending=true'}

export default function Collection(){
  const [items,setItems]=useState<Inventory[]>([]),[total,setTotal]=useState(0)
  const [query,setQuery]=useState(''),[sort,setSort]=useState<keyof typeof sorts>('newest'),[reload,setReload]=useState(0)
  const [editing,setEditing]=useState<Inventory|null>(null),[deleting,setDeleting]=useState<Inventory|null>(null),[busy,setBusy]=useState(false)

  useEffect(()=>{const timer=setTimeout(()=>request<{items:Inventory[],total:number}>(`/inventory?q=${encodeURIComponent(query)}&${sorts[sort]}`).then(x=>{setItems(x.items);setTotal(x.total)}),200);return()=>clearTimeout(timer)},[query,sort,reload])
  const save=async()=>{if(!editing)return;setBusy(true);try{await request(`/inventory/${editing.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:editing.quantity,foil:editing.foil,condition:editing.condition,language:editing.language,storage_location:editing.storage_location,collection_name:editing.collection_name,status:editing.status,market_price:editing.market_price,purchase_price:editing.purchase_price,notes:editing.notes})});setEditing(null);setReload(x=>x+1)}finally{setBusy(false)}}
  const remove=async()=>{if(!deleting)return;setBusy(true);try{await request(`/inventory/${deleting.id}`,{method:'DELETE'});setDeleting(null);setReload(x=>x+1)}finally{setBusy(false)}}

  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2} mb={3}>
      <Box><Typography variant="h4">Collection</Typography><Typography color="text.secondary">{total} unique inventory entries</Typography></Box>
      <Stack direction="row" spacing={1}><Button startIcon={<Download/>} href={`${API}/api/inventory/export/csv`}>CSV</Button><Button startIcon={<Download/>} href={`${API}/api/inventory/export/json`}>JSON</Button></Stack>
    </Stack>
    <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
      <TextField fullWidth placeholder="Search name or collector number" value={query} onChange={e=>setQuery(e.target.value)} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/>
      <Select value={sort} onChange={e=>setSort(e.target.value as keyof typeof sorts)} sx={{minWidth:190}}><MenuItem value="newest">Recently added</MenuItem><MenuItem value="name">Name A–Z</MenuItem><MenuItem value="value">Most valuable</MenuItem></Select>
    </Stack>
    <Box className="collection-grid" mt={3}>{items.map(item=><Card key={item.id} sx={{display:'flex',overflow:'hidden',position:'relative'}}>
      {item.image_url?<Box component="img" src={item.image_url} loading="lazy" sx={{width:105,objectFit:'cover',objectPosition:'top'}}/>:<Box sx={{width:105,bgcolor:'action.hover'}}/>}
      <Box p={2} pr={7} minWidth={0}><Typography fontWeight={800} noWrap>{item.card_name}</Typography><Typography color="text.secondary" variant="body2">{item.set_name} #{item.collector_number}</Typography><Typography variant="h6" color="primary.main" mt={1}>${Number(item.market_price||0).toFixed(2)}</Typography><Stack direction="row" spacing={.5} mt={1} flexWrap="wrap"><Chip size="small" label={`×${item.quantity}`}/><Chip size="small" variant="outlined" label={item.condition.replaceAll('_',' ')}/>{item.foil&&<Chip size="small" color="warning" label="Foil"/>}</Stack><Typography variant="caption" color="text.secondary">{item.collection_name} · {item.storage_location}</Typography></Box>
      <Stack sx={{position:'absolute',right:4,top:4}}><Tooltip title="Edit entry"><IconButton aria-label={`Edit ${item.card_name}`} onClick={()=>setEditing({...item})}><Edit/></IconButton></Tooltip><Tooltip title="Delete entry"><IconButton color="error" aria-label={`Delete ${item.card_name}`} onClick={()=>setDeleting(item)}><Delete/></IconButton></Tooltip></Stack>
    </Card>)}</Box>
    {!items.length&&<Typography textAlign="center" color="text.secondary" mt={8}>No cards found. The scanner is ready when you are.</Typography>}

    <Dialog open={!!editing} onClose={()=>!busy&&setEditing(null)} fullWidth maxWidth="sm"><DialogTitle>Edit collection entry</DialogTitle>{editing&&<DialogContent><Stack spacing={2} mt={1}>
      <Typography variant="h6">{editing.card_name} · {editing.set_code.toUpperCase()} #{editing.collector_number}</Typography>
      <TextField label="Quantity" type="number" value={editing.quantity} onChange={e=>setEditing({...editing,quantity:Math.max(1,Number(e.target.value))})}/>
      <Select value={editing.condition} onChange={e=>setEditing({...editing,condition:e.target.value})}>{conditions.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</Select>
      <FormControlLabel control={<Switch checked={editing.foil} onChange={e=>setEditing({...editing,foil:e.target.checked})}/>} label="Foil"/>
      <TextField label="Language" value={editing.language} onChange={e=>setEditing({...editing,language:e.target.value})}/>
      <TextField label="Collection" value={editing.collection_name} onChange={e=>setEditing({...editing,collection_name:e.target.value})}/>
      <TextField label="Storage location" value={editing.storage_location} onChange={e=>setEditing({...editing,storage_location:e.target.value})}/>
      <Select value={editing.status} onChange={e=>setEditing({...editing,status:e.target.value})}>{statuses.map(value=><MenuItem key={value} value={value}>{value.replaceAll('_',' ')}</MenuItem>)}</Select>
      <Stack direction="row" spacing={2}><TextField fullWidth label="Purchase price" type="number" value={editing.purchase_price??''} onChange={e=>setEditing({...editing,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth label="Market price" type="number" value={editing.market_price??''} onChange={e=>setEditing({...editing,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack>
      <TextField label="Notes" multiline minRows={2} value={editing.notes??''} onChange={e=>setEditing({...editing,notes:e.target.value})}/>
    </Stack></DialogContent>}<DialogActions><Button disabled={busy} onClick={()=>setEditing(null)}>Cancel</Button><Button disabled={busy} variant="contained" onClick={save}>Save changes</Button></DialogActions></Dialog>

    <Dialog open={!!deleting} onClose={()=>!busy&&setDeleting(null)}><DialogTitle>Delete this entry?</DialogTitle><DialogContent><Typography>This permanently removes all {deleting?.quantity} logged copies of <strong>{deleting?.card_name}</strong> ({deleting?.set_code.toUpperCase()} #{deleting?.collector_number}) from this collection.</Typography></DialogContent><DialogActions><Button disabled={busy} onClick={()=>setDeleting(null)}>Cancel</Button><Button disabled={busy} color="error" variant="contained" onClick={remove}>Delete entry</Button></DialogActions></Dialog>
  </>
}
