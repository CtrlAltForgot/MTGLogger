import { useEffect, useState } from 'react'
import { Add, Delete, Edit } from '@mui/icons-material'
import {
  Alert, Button, Card, CardContent, Dialog, DialogActions, DialogContent,
  DialogTitle, Grid, IconButton, MenuItem, Stack, TextField, Tooltip, Typography,
} from '@mui/material'
import { request } from '../api'

type Product={id:string;name:string;product_type:string;set_code:string|null;quantity:number;purchase_price:number|null;market_price:number|null;storage_location:string;notes:string|null}
type Form=Omit<Product,'id'>
const empty:Form={name:'',product_type:'booster_box',set_code:null,quantity:1,purchase_price:null,market_price:null,storage_location:'Unsorted',notes:null}
const types=['booster_box','bundle','collector_box','commander_deck','prerelease_kit','booster_pack']

export default function Sealed(){
  const [items,setItems]=useState<Product[]>([]),[form,setForm]=useState<Form|null>(null)
  const [editingId,setEditingId]=useState<string|null>(null),[deleting,setDeleting]=useState<Product|null>(null)
  const [busy,setBusy]=useState(false),[error,setError]=useState<string>()
  const load=()=>request<Product[]>('/sealed').then(setItems)
  useEffect(()=>{void load()},[])
  const edit=(item:Product)=>{const {id,...values}=item;setEditingId(id);setForm(values)}
  const close=()=>{if(!busy){setForm(null);setEditingId(null)}}
  const save=async()=>{if(!form)return;setBusy(true);setError(undefined);try{await request(editingId?`/sealed/${editingId}`:'/sealed',{method:editingId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(form)});close();setForm(null);setEditingId(null);await load()}catch(e){setError(e instanceof Error?e.message:'Could not save product')}finally{setBusy(false)}}
  const remove=async()=>{if(!deleting)return;setBusy(true);try{await request(`/sealed/${deleting.id}`,{method:'DELETE'});setDeleting(null);await load()}catch(e){setError(e instanceof Error?e.message:'Could not delete product')}finally{setBusy(false)}}
  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2}>
      <div><Typography variant="h4">Sealed inventory</Typography><Typography color="text.secondary">Track boxes, bundles, decks, kits, and packs separately.</Typography></div>
      <Button variant="contained" startIcon={<Add/>} onClick={()=>{setEditingId(null);setForm({...empty})}}>Add product</Button>
    </Stack>
    {error&&<Alert severity="error" onClose={()=>setError(undefined)} sx={{mt:2}}>{error}</Alert>}
    <Grid container spacing={2} mt={1}>{items.map(item=><Grid size={{xs:12,sm:6,lg:4}} key={item.id}><Card><CardContent sx={{position:'relative'}}>
      <Stack direction="row" sx={{position:'absolute',right:8,top:8}}><Tooltip title="Edit product"><IconButton onClick={()=>edit(item)}><Edit/></IconButton></Tooltip><Tooltip title="Delete product"><IconButton color="error" onClick={()=>setDeleting(item)}><Delete/></IconButton></Tooltip></Stack>
      <Typography variant="h6" pr={10}>{item.name}</Typography><Typography color="text.secondary">{item.product_type.replaceAll('_',' ')} · ×{item.quantity}{item.set_code?` · ${item.set_code.toUpperCase()}`:''}</Typography>
      <Typography variant="h5" color="primary.main" mt={2}>${Number(item.market_price||0).toFixed(2)} <Typography component="span" variant="caption" color="text.secondary">each</Typography></Typography>
      <Typography variant="body2" fontWeight={700}>Total ${(Number(item.market_price||0)*item.quantity).toFixed(2)}</Typography><Typography variant="caption" color="text.secondary">Storage · {item.storage_location}</Typography>
      {item.notes&&<Typography variant="body2" color="text.secondary" mt={1}>{item.notes}</Typography>}
    </CardContent></Card></Grid>)}</Grid>
    {!items.length&&<Typography textAlign="center" color="text.secondary" mt={8}>No sealed products logged yet.</Typography>}
    <Dialog open={!!form} onClose={close} fullWidth maxWidth="sm"><DialogTitle>{editingId?'Edit sealed product':'Add sealed product'}</DialogTitle>{form&&<DialogContent><Stack spacing={2} mt={1}>
      <TextField label="Product name" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/>
      <TextField select label="Type" value={form.product_type} onChange={e=>setForm({...form,product_type:e.target.value})}>{types.map(type=><MenuItem key={type} value={type}>{type.replaceAll('_',' ')}</MenuItem>)}</TextField>
      <Stack direction={{xs:'column',sm:'row'}} spacing={2}><TextField fullWidth label="Set code" value={form.set_code||''} onChange={e=>setForm({...form,set_code:e.target.value||null})}/><TextField fullWidth label="Quantity" type="number" value={form.quantity} onChange={e=>setForm({...form,quantity:Math.max(1,Number(e.target.value))})}/></Stack>
      <Stack direction={{xs:'column',sm:'row'}} spacing={2}><TextField fullWidth label="Purchase price each" type="number" value={form.purchase_price??''} onChange={e=>setForm({...form,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth label="Market price each" type="number" value={form.market_price??''} onChange={e=>setForm({...form,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack>
      <TextField label="Storage location" value={form.storage_location} onChange={e=>setForm({...form,storage_location:e.target.value})}/><TextField label="Notes" multiline minRows={2} value={form.notes||''} onChange={e=>setForm({...form,notes:e.target.value||null})}/>
    </Stack></DialogContent>}<DialogActions><Button disabled={busy} onClick={close}>Cancel</Button><Button disabled={busy||!form?.name.trim()} variant="contained" onClick={save}>{editingId?'Save changes':'Add product'}</Button></DialogActions></Dialog>
    <Dialog open={!!deleting} onClose={()=>!busy&&setDeleting(null)}><DialogTitle>Delete sealed product?</DialogTitle><DialogContent>This removes all {deleting?.quantity} logged units of <strong>{deleting?.name}</strong>.</DialogContent><DialogActions><Button disabled={busy} onClick={()=>setDeleting(null)}>Cancel</Button><Button disabled={busy} color="error" variant="contained" onClick={remove}>Delete</Button></DialogActions></Dialog>
  </>
}
