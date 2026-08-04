import { useState } from 'react'
import { Inventory2, Search, Style } from '@mui/icons-material'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  InputAdornment, MenuItem, Stack, TextField, ToggleButton, ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { request } from '../api'
import type { Candidate } from '../types'

type Kind='card'|'sealed'
type SealedForm={name:string;product_type:string;set_code:string;quantity:number;purchase_price:number|null;market_price:number|null;storage_location:string;notes:string}
const emptySealed:SealedForm={name:'',product_type:'booster_box',set_code:'',quantity:1,purchase_price:null,market_price:null,storage_location:'Unsorted',notes:''}
const conditions=[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']]
const productTypes=['booster_box','bundle','collector_box','commander_deck','prerelease_kit','booster_pack']

export default function ManualAddDialog({open,onClose,onAdded}:{open:boolean;onClose:()=>void;onAdded:()=>void}){
  const [kind,setKind]=useState<Kind>('card'),[query,setQuery]=useState(''),[matches,setMatches]=useState<Candidate[]>([]),[selected,setSelected]=useState<Candidate|null>(null)
  const [quantity,setQuantity]=useState(1),[foil,setFoil]=useState(false),[condition,setCondition]=useState('near_mint'),[storage,setStorage]=useState('Unsorted')
  const [sealed,setSealed]=useState<SealedForm>(emptySealed),[busy,setBusy]=useState(false),[error,setError]=useState<string>()
  const close=()=>{if(!busy){onClose();setSelected(null);setMatches([]);setQuery('');setError(undefined)}}
  const search=async()=>{if(query.trim().length<2)return;setBusy(true);setError(undefined);try{setMatches(await request<Candidate[]>(`/reviews/search?q=${encodeURIComponent(query.trim())}&lang=en`))}catch(e){setError(e instanceof Error?e.message:'Could not search cards')}finally{setBusy(false)}}
  const choose=(candidate:Candidate)=>{setSelected(candidate);setFoil(!candidate.finishes.includes('nonfoil')&&candidate.finishes.some(value=>value==='foil'||value==='etched'))}
  const finish=()=>{onAdded();onClose();setSelected(null);setMatches([]);setQuery('');setError(undefined)}
  const addCard=async()=>{if(!selected)return;setBusy(true);setError(undefined);try{await request('/inventory',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card_name:selected.name,set_code:selected.set_code,set_name:selected.set_name,collector_number:selected.collector_number,scryfall_id:selected.scryfall_id,oracle_id:selected.oracle_id,quantity,foil,language:selected.language||'en',condition,market_price:foil?selected.foil_market_price:selected.market_price,storage_location:storage,collection_name:'Main',image_url:selected.image_url,color_identity:selected.color_identity||'',rarity:selected.rarity,type_line:selected.type_line,status:'owned'})});finish()}catch(e){setError(e instanceof Error?e.message:'Could not add card')}finally{setBusy(false)}}
  const addSealed=async()=>{setBusy(true);setError(undefined);try{await request('/sealed',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...sealed,set_code:sealed.set_code||null,notes:sealed.notes||null})});finish();setSealed(emptySealed)}catch(e){setError(e instanceof Error?e.message:'Could not add sealed product')}finally{setBusy(false)}}
  return <Dialog open={open} onClose={close} fullWidth maxWidth="md">
    <DialogTitle>Add to collection</DialogTitle>
    <DialogContent>
      <ToggleButtonGroup exclusive fullWidth value={kind} onChange={(_,value:Kind|null)=>value&&setKind(value)} sx={{mt:1,mb:2}}><ToggleButton value="card"><Style sx={{mr:1}}/>Card printing</ToggleButton><ToggleButton value="sealed"><Inventory2 sx={{mr:1}}/>Sealed product</ToggleButton></ToggleButtonGroup>
      {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}
      {kind==='card'&&<>
        <Stack direction="row" spacing={1}><TextField autoFocus fullWidth placeholder="Search card name" value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void search()}} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/><Button variant="contained" disabled={busy||query.trim().length<2} onClick={()=>void search()}>Search</Button></Stack>
        {!selected&&<Box sx={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:1.5,mt:2,maxHeight:430,overflowY:'auto'}}>{matches.map(candidate=><Box component="button" type="button" key={candidate.scryfall_id} onClick={()=>choose(candidate)} sx={{appearance:'none',textAlign:'left',p:1,border:'1px solid',borderColor:'divider',borderRadius:2,bgcolor:'background.default',color:'text.primary',cursor:'pointer','&:hover':{borderColor:'primary.main'}}}><Box component="img" src={candidate.image_url||''} alt="" sx={{display:'block',width:'100%',aspectRatio:'63 / 88',objectFit:'contain',borderRadius:1}}/><Typography className="card-title" fontWeight={800} mt={1}>{candidate.name}</Typography><Typography variant="caption" color="text.secondary">{candidate.set_name} #{candidate.collector_number}</Typography></Box>)}</Box>}
        {selected&&<Stack direction={{xs:'column',sm:'row'}} spacing={2.5}><Box component="img" src={selected.image_url||''} alt={selected.name} sx={{width:190,alignSelf:'center',borderRadius:2}}/><Stack spacing={1.5} flex={1}><Box><Typography className="card-title" variant="h5">{selected.name}</Typography><Typography color="text.secondary">{selected.set_name} #{selected.collector_number}</Typography></Box><Stack direction="row" spacing={1.5}><TextField fullWidth label="Quantity" type="number" value={quantity} onChange={e=>setQuantity(Math.max(1,Number(e.target.value)))}/><TextField select fullWidth label="Finish" value={foil?'foil':'nonfoil'} onChange={e=>setFoil(e.target.value==='foil')}><MenuItem value="nonfoil" disabled={!selected.finishes.includes('nonfoil')}>Nonfoil</MenuItem><MenuItem value="foil" disabled={!selected.finishes.some(value=>value==='foil'||value==='etched')}>Foil</MenuItem></TextField></Stack><TextField select label="Condition" value={condition} onChange={e=>setCondition(e.target.value)}>{conditions.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField><TextField label="Storage location" value={storage} onChange={e=>setStorage(e.target.value)}/><Button onClick={()=>setSelected(null)}>Choose another printing</Button></Stack></Stack>}
      </>}
      {kind==='sealed'&&<Stack spacing={2}>
        <TextField autoFocus label="Product name" value={sealed.name} onChange={e=>setSealed({...sealed,name:e.target.value})}/><TextField select label="Type" value={sealed.product_type} onChange={e=>setSealed({...sealed,product_type:e.target.value})}>{productTypes.map(type=><MenuItem key={type} value={type}>{type.replaceAll('_',' ')}</MenuItem>)}</TextField><Stack direction={{xs:'column',sm:'row'}} spacing={2}><TextField fullWidth label="Set code (optional)" value={sealed.set_code} onChange={e=>setSealed({...sealed,set_code:e.target.value})}/><TextField fullWidth label="Quantity" type="number" value={sealed.quantity} onChange={e=>setSealed({...sealed,quantity:Math.max(1,Number(e.target.value))})}/></Stack><Stack direction={{xs:'column',sm:'row'}} spacing={2}><TextField fullWidth label="Purchase price each" type="number" value={sealed.purchase_price??''} onChange={e=>setSealed({...sealed,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth label="Market price each" type="number" value={sealed.market_price??''} onChange={e=>setSealed({...sealed,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack><TextField label="Storage location" value={sealed.storage_location} onChange={e=>setSealed({...sealed,storage_location:e.target.value})}/><TextField label="Notes" multiline minRows={2} value={sealed.notes} onChange={e=>setSealed({...sealed,notes:e.target.value})}/>
      </Stack>}
    </DialogContent>
    <DialogActions><Button disabled={busy} onClick={close}>Cancel</Button>{kind==='card'&&<Button variant="contained" disabled={busy||!selected} onClick={()=>void addCard()}>Add {quantity} {quantity===1?'card':'cards'}</Button>}{kind==='sealed'&&<Button variant="contained" disabled={busy||!sealed.name.trim()} onClick={()=>void addSealed()}>Add sealed product</Button>}</DialogActions>
  </Dialog>
}
