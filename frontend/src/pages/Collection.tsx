import { useEffect, useState } from 'react'
import { Add, AddToPhotos, CheckBox, CheckBoxOutlineBlank, Close, Delete, Download, Edit, Search, TrendingDown, TrendingUp } from '@mui/icons-material'
import {
  Box, Button, Card, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Alert, Checkbox, Divider, Drawer, IconButton, InputAdornment, List, ListItemButton, ListItemText, Menu, MenuItem, Select, Stack,
  TablePagination, TextField, Tooltip, Typography,
} from '@mui/material'
import { API, request } from '../api'
import FoilArtwork from '../components/FoilArtwork'
import { CardName } from '../components/CardDetails'
import OverflowMarquee from '../components/OverflowMarquee'
import ManualAddDialog from '../components/ManualAddDialog'
import type { Deck, Inventory } from '../types'

const conditions=[['near_mint','Near Mint'],['lightly_played','Lightly Played'],['moderately_played','Moderately Played'],['heavily_played','Heavily Played'],['damaged','Damaged']]
const conditionAbbreviation:Record<string,string>={near_mint:'NM',lightly_played:'LP',moderately_played:'MP',heavily_played:'HP',damaged:'DMG'}
const sorts={newest:'sort=updated_at&descending=true',name:'sort=card_name&descending=false',value:'sort=market_price&descending=true'}

export default function Collection(){
  const [items,setItems]=useState<Inventory[]>([]),[total,setTotal]=useState(0),[totalCards,setTotalCards]=useState(0),[collectionValue,setCollectionValue]=useState(0)
  const [query,setQuery]=useState(''),[sort,setSort]=useState<keyof typeof sorts>('newest'),[reload,setReload]=useState(0)
  const [page,setPage]=useState(0),[pageSize,setPageSize]=useState(48)
  const [location,setLocation]=useState(''),[facets,setFacets]=useState<{collections:string[];storage_locations:string[]}>({collections:[],storage_locations:[]})
  const [editing,setEditing]=useState<Inventory|null>(null),[deleting,setDeleting]=useState<Inventory|null>(null),[busy,setBusy]=useState(false)
  const [selectedCopies,setSelectedCopies]=useState<Set<number>>(new Set()),[copyFoil,setCopyFoil]=useState(false),[copyCondition,setCopyCondition]=useState('near_mint')
  const [error,setError]=useState<string>()
  const [deckMode,setDeckMode]=useState(false),[decks,setDecks]=useState<Deck[]>([]),[selected,setSelected]=useState<Set<string>>(new Set()),[targetDeck,setTargetDeck]=useState<Deck|null>(null)
  const [selectedQuantities,setSelectedQuantities]=useState<Record<string,number>>({}),[quantityItem,setQuantityItem]=useState<Inventory|null>(null),[quantityDraft,setQuantityDraft]=useState(1)
  const [manualAdd,setManualAdd]=useState(false),[exportAnchor,setExportAnchor]=useState<HTMLElement|null>(null)

  useEffect(()=>{void request<{collections:string[];storage_locations:string[]}>('/inventory/facets').then(setFacets)},[reload])
  useEffect(()=>{setPage(0)},[query,sort,location])
  useEffect(()=>{const params=new URLSearchParams({q:query,page:String(page+1),page_size:String(pageSize),...Object.fromEntries(new URLSearchParams(sorts[sort]))});if(location)params.set('storage_location',location);const timer=setTimeout(()=>request<{items:Inventory[],total:number,total_cards:number,collection_value:number,page_size:number}>(`/inventory?${params}`).then(x=>{setItems(x.items);setTotal(x.total);setTotalCards(x.total_cards);setCollectionValue(Number(x.collection_value));if(x.total>0&&page*x.page_size>=x.total)setPage(Math.max(0,Math.ceil(x.total/x.page_size)-1))}).catch(e=>setError(e instanceof Error?e.message:'Could not load collection')),200);return()=>clearTimeout(timer)},[query,sort,location,page,pageSize,reload])
  const save=async()=>{if(!editing)return;setBusy(true);setError(undefined);try{await request(`/inventory/${editing.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({language:editing.language,storage_location:editing.storage_location,market_price:editing.market_price,purchase_price:editing.purchase_price,notes:editing.notes})});if(selectedCopies.size&&(copyFoil!==editing.foil||copyCondition!==editing.condition))await request(`/inventory/${editing.id}/move-copies`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:selectedCopies.size,foil:copyFoil,condition:copyCondition})});setEditing(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not save entry')}finally{setBusy(false)}}
  const remove=async()=>{if(!deleting)return;setBusy(true);setError(undefined);try{await request(`/inventory/${deleting.id}`,{method:'DELETE'});setDeleting(null);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not delete entry')}finally{setBusy(false)}}
  const available=(item:Inventory)=>Math.max(0,item.quantity-item.deck_assignments.reduce((sum,deck)=>sum+deck.quantity,0))
  const selectedItems=items.filter(item=>selected.has(item.id)&&available(item)>0)
  const selectedCards=selectedItems.reduce((sum,item)=>sum+(selectedQuantities[item.id]||1),0)
  const openDeckMode=async()=>{setError(undefined);try{setDecks(await request<Deck[]>('/decks'));setDeckMode(true);setSelected(new Set());setSelectedQuantities({})}catch(e){setError(e instanceof Error?e.message:'Could not load decks')}}
  const closeDeckMode=(force=false)=>{if(busy&&!force)return;setDeckMode(false);setSelected(new Set());setSelectedQuantities({});setQuantityItem(null);setTargetDeck(null)}
  const deselect=(item:Inventory)=>{setSelected(current=>{const next=new Set(current);next.delete(item.id);return next});setSelectedQuantities(current=>{const next={...current};delete next[item.id];return next})}
  const chooseQuantity=(item:Inventory,quantity:number)=>{const safe=Math.max(1,Math.min(available(item),quantity));setSelected(current=>new Set(current).add(item.id));setSelectedQuantities(current=>({...current,[item.id]:safe}));setQuantityItem(null)}
  const toggleSelected=(item:Inventory)=>{if(selected.has(item.id)){deselect(item);return}const copies=available(item);if(copies>1){setQuantityDraft(copies);setQuantityItem(item)}else if(copies===1)chooseQuantity(item,1)}
  const selectableItems=items.filter(item=>available(item)>0)
  const allVisibleSelected=selectableItems.length>0&&selectableItems.every(item=>selected.has(item.id))
  const toggleAllVisible=()=>{if(allVisibleSelected){setSelected(current=>{const next=new Set(current);selectableItems.forEach(item=>next.delete(item.id));return next});setSelectedQuantities(current=>{const next={...current};selectableItems.forEach(item=>delete next[item.id]);return next})}else{setSelected(current=>{const next=new Set(current);selectableItems.forEach(item=>next.add(item.id));return next});setSelectedQuantities(current=>({...current,...Object.fromEntries(selectableItems.map(item=>[item.id,available(item)]))}))}}
  const addToDeck=async()=>{if(!targetDeck||!selectedItems.length)return;setBusy(true);setError(undefined);try{await request(`/decks/${targetDeck.id}/entries`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:selectedItems.map(item=>({inventory_id:item.id,quantity:selectedQuantities[item.id]||1}))})});closeDeckMode(true);setReload(x=>x+1)}catch(e){setError(e instanceof Error?e.message:'Could not add cards to deck')}finally{setBusy(false)}}
  const openEditor=(item:Inventory)=>{setSelectedCopies(new Set());setCopyFoil(item.foil);setCopyCondition(item.condition);setEditing({...item})}
  const toggleCopy=(index:number)=>setSelectedCopies(current=>{const next=new Set(current);next.has(index)?next.delete(index):next.add(index);return next})

  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2} mb={3}>
      <Box><Typography variant="h4">Collection</Typography><Typography color="text.secondary">{totalCards.toLocaleString()} {totalCards===1?'card':'cards'} · Collection value: <Box component="span" color="primary.main" fontWeight={750}>${collectionValue.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</Box></Typography></Box>
      <Stack direction="row" spacing={1}><Button startIcon={<Add/>} variant="contained" onClick={()=>setManualAdd(true)}>Add</Button><Button startIcon={<AddToPhotos/>} variant={deckMode?'contained':'text'} onClick={()=>deckMode?closeDeckMode():void openDeckMode()}>{deckMode?'Cancel':'Add to Deck'}</Button><Button startIcon={<Download/>} onClick={event=>setExportAnchor(event.currentTarget)}>Export</Button><Menu anchorEl={exportAnchor} open={!!exportAnchor} onClose={()=>setExportAnchor(null)}><MenuItem component="a" href={`${API}/api/inventory/export/csv`} onClick={()=>setExportAnchor(null)}>Download CSV</MenuItem><MenuItem component="a" href={`${API}/api/inventory/export/json`} onClick={()=>setExportAnchor(null)}>Download JSON</MenuItem></Menu></Stack>
    </Stack>
    {error&&<Alert severity="error" onClose={()=>setError(undefined)} sx={{mb:2}}>{error}</Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={2}>
      <TextField fullWidth placeholder="Search name or collector number" value={query} onChange={e=>setQuery(e.target.value)} slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>}}}/>
      <Select value={sort} onChange={e=>setSort(e.target.value as keyof typeof sorts)} sx={{minWidth:190}}><MenuItem value="newest">Recently added</MenuItem><MenuItem value="name">Name A–Z</MenuItem><MenuItem value="value">Most valuable</MenuItem></Select>
    </Stack>
    {facets.storage_locations.length>1&&<Stack direction="row" spacing={1} mt={1.5}><Select size="small" displayEmpty value={location} onChange={e=>setLocation(e.target.value)} sx={{minWidth:190}}><MenuItem value="">All storage locations</MenuItem>{facets.storage_locations.map(value=><MenuItem key={value} value={value}>{value}</MenuItem>)}</Select></Stack>}
    <Box className="collection-grid" mt={3}>{items.map(item=>{const remaining=available(item),checked=selected.has(item.id);return <Card key={item.id} onClick={()=>deckMode&&remaining>0&&toggleSelected(item)} sx={{display:'flex',overflow:'visible',position:'relative',alignItems:'flex-start',minHeight:202,cursor:deckMode&&remaining>0?'pointer':'default',outline:checked?'3px solid':'none',outlineColor:'primary.main',opacity:deckMode&&remaining===0?.48:1}}>
      {item.image_url?<FoilArtwork src={item.image_url} alt={item.card_name} foil={item.foil} sx={{width:{xs:116,sm:128},flexShrink:0,aspectRatio:'63 / 88',m:1,borderRadius:1.5,border:'1px solid',borderColor:'divider',bgcolor:'#080506'}} imageSx={{objectFit:'contain',objectPosition:'center'}}/>:<Box sx={{width:{xs:116,sm:128},flexShrink:0,aspectRatio:'63 / 88',m:1,bgcolor:'action.hover',border:'1px solid',borderColor:'divider',borderRadius:1.5}}/>}
      {item.quantity>1&&<Chip size="small" color="primary" label={`×${item.quantity}`} sx={{position:'absolute',left:-10,top:-10,zIndex:4,fontWeight:900,boxShadow:'0 3px 12px rgba(0,0,0,.58)'}}/>}
      {deckMode&&<Checkbox checked={checked} disabled={remaining===0} icon={<CheckBoxOutlineBlank/>} checkedIcon={<CheckBox/>} inputProps={{'aria-label':`Select ${item.card_name}`}} onClick={event=>event.stopPropagation()} onChange={()=>toggleSelected(item)} sx={{position:'absolute',right:7,top:7,zIndex:3,bgcolor:'rgba(18,12,13,.94)',border:'1px solid',borderColor:'divider','&:hover':{bgcolor:'background.paper'}}}/>}
      <Box py={1.5} pl={.5} pr={1} pb={6} minWidth={0} flex={1}><Stack spacing={.75}><Box><Typography component="div" fontWeight={800}><OverflowMarquee className="card-title" title={item.card_name}><CardName scryfallId={item.scryfall_id}>{item.card_name}</CardName></OverflowMarquee></Typography><Typography component="div" color="text.secondary" variant="body2"><OverflowMarquee className="card-printing" title={`${item.set_name} #${item.collector_number}`}>{item.set_name} #{item.collector_number}</OverflowMarquee></Typography></Box><PriceMovement item={item}/><Stack direction="row" spacing={.5} flexWrap="wrap"><Chip size="small" variant="outlined" label={conditionAbbreviation[item.condition]||item.condition.toUpperCase()}/>{item.foil&&<Chip size="small" color="warning" label="Foil"/>}</Stack><Typography display="block" variant="caption" color="text.secondary">Deck · {item.deck_assignments.length?item.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'}</Typography><Typography variant="caption" color="text.secondary">Storage · {item.storage_location}</Typography></Stack></Box>
      {!deckMode&&<Stack direction="row" sx={{position:'absolute',right:4,bottom:4}}><Tooltip title="Edit entry"><IconButton aria-label={`Edit ${item.card_name}`} onClick={()=>openEditor(item)}><Edit/></IconButton></Tooltip><Tooltip title="Delete entry"><IconButton color="error" aria-label={`Delete ${item.card_name}`} onClick={()=>setDeleting(item)}><Delete/></IconButton></Tooltip></Stack>}
    </Card>})}</Box>
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
      <Typography className="card-title" variant="h6">{editing.card_name} · {editing.set_code.toUpperCase()} #{editing.collector_number}</Typography>
      <CopySelector item={editing} selected={selectedCopies} onToggle={toggleCopy}/>
      {!!selectedCopies.size&&<Box sx={{p:1.5,border:'1px solid',borderColor:'primary.main',borderRadius:2}}><Typography fontWeight={800}>{selectedCopies.size} {selectedCopies.size===1?'copy':'copies'} selected</Typography><Stack direction={{xs:'column',sm:'row'}} spacing={1} mt={1.25}><Button variant={copyFoil?'outlined':'contained'} onClick={()=>setCopyFoil(false)}>Make Nonfoil</Button><Button variant={copyFoil?'contained':'outlined'} onClick={()=>setCopyFoil(true)}>Make Foil</Button><TextField select size="small" label="Condition" value={copyCondition} onChange={e=>setCopyCondition(e.target.value)} sx={{minWidth:170}}>{conditions.map(([value,label])=><MenuItem key={value} value={value}>{label}</MenuItem>)}</TextField></Stack><Typography variant="caption" color="text.secondary" display="block" mt={1}>Saving separates changed copies into their own collection entry.</Typography></Box>}
      <TextField label="Language" value={editing.language} onChange={e=>setEditing({...editing,language:e.target.value})}/>
      <TextField label="Deck" value={editing.deck_assignments.length?editing.deck_assignments.map(deck=>`${deck.deck_name} ×${deck.quantity}`).join(', '):'None'} helperText="Deck quantities are managed in the Decks tab." slotProps={{input:{readOnly:true}}}/>
      <TextField label="Storage location" value={editing.storage_location} onChange={e=>setEditing({...editing,storage_location:e.target.value})}/>
      <Stack direction="row" spacing={2}><TextField fullWidth label="Purchase price" type="number" value={editing.purchase_price??''} onChange={e=>setEditing({...editing,purchase_price:e.target.value===''?null:Number(e.target.value)})}/><TextField fullWidth label="Market price" type="number" value={editing.market_price??''} onChange={e=>setEditing({...editing,market_price:e.target.value===''?null:Number(e.target.value)})}/></Stack>
      <TextField label="Notes" multiline minRows={2} value={editing.notes??''} onChange={e=>setEditing({...editing,notes:e.target.value})}/>
    </Stack></DialogContent>}<DialogActions><Button disabled={busy} onClick={()=>setEditing(null)}>Cancel</Button><Button disabled={busy} variant="contained" onClick={save}>Save changes</Button></DialogActions></Dialog>

    <Dialog open={!!deleting} onClose={()=>!busy&&setDeleting(null)}><DialogTitle>Delete this entry?</DialogTitle><DialogContent><Typography>This permanently removes all {deleting?.quantity} logged copies of <strong>{deleting?.card_name}</strong> ({deleting?.set_code.toUpperCase()} #{deleting?.collector_number}) from this collection.</Typography></DialogContent><DialogActions><Button disabled={busy} onClick={()=>setDeleting(null)}>Cancel</Button><Button disabled={busy} color="error" variant="contained" onClick={remove}>Delete entry</Button></DialogActions></Dialog>

    <Drawer anchor="left" open={deckMode} onClose={()=>closeDeckMode()} slotProps={{backdrop:{sx:{backdropFilter:'none',backgroundColor:'rgba(0,0,0,.28)'}}}}><Box sx={{width:{xs:290,sm:350},p:2.5,pt:3}}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h5">Add to Deck</Typography><IconButton onClick={()=>closeDeckMode()}><Close/></IconButton></Stack><Typography color="text.secondary" mt={1}>{selectedCards} {selectedCards===1?'card':'cards'} selected from {selectedItems.length} {selectedItems.length===1?'printing':'printings'}.</Typography><Button startIcon={allVisibleSelected?<CheckBox/>:<CheckBoxOutlineBlank/>} disabled={!selectableItems.length||busy} onClick={toggleAllVisible} sx={{mt:1.25}}>{allVisibleSelected?'Clear visible cards':'Select all visible cards'}</Button><Divider sx={{my:2}}/><Typography fontWeight={800} mb={1}>Choose a deck</Typography><List disablePadding>{decks.map(deck=><ListItemButton key={deck.id} disabled={!selectedCards} onClick={()=>setTargetDeck(deck)} sx={{border:'1px solid',borderColor:'divider',borderRadius:2,mb:1}}><ListItemText primary={deck.name} secondary={`${deck.total_cards} cards`}/></ListItemButton>)}</List>{!decks.length&&<Typography color="text.secondary">Create a deck in the Decks tab first.</Typography>}<Typography variant="caption" color="text.secondary" display="block" mt={2}>Select cards individually in the collection. Multi-copy printings ask how many copies to include.</Typography></Box></Drawer>
    <Dialog open={!!quantityItem} onClose={()=>setQuantityItem(null)} maxWidth="xs" fullWidth><DialogTitle>How many copies?</DialogTitle><DialogContent>{quantityItem&&<Stack spacing={1.5} mt={1}><Typography><strong>{quantityItem.card_name}</strong> has {available(quantityItem)} unassigned copies.</Typography><TextField autoFocus fullWidth type="number" label="Copies to add" value={quantityDraft} onChange={event=>setQuantityDraft(Math.max(1,Math.min(available(quantityItem),Number(event.target.value)||1)))} slotProps={{htmlInput:{min:1,max:available(quantityItem)}}}/></Stack>}</DialogContent><DialogActions><Button onClick={()=>setQuantityItem(null)}>Cancel</Button><Button variant="contained" disabled={!quantityItem} onClick={()=>quantityItem&&chooseQuantity(quantityItem,quantityDraft)}>Select copies</Button></DialogActions></Dialog>
    <Dialog open={!!targetDeck} onClose={()=>!busy&&setTargetDeck(null)}><DialogTitle>Add cards to {targetDeck?.name}?</DialogTitle><DialogContent><Typography>Are you sure you want to add <strong>{selectedCards} {selectedCards===1?'card':'cards'}</strong> across {selectedItems.length} {selectedItems.length===1?'printing':'printings'} to <strong>{targetDeck?.name}</strong>?</Typography></DialogContent><DialogActions><Button disabled={busy} onClick={()=>setTargetDeck(null)}>No</Button><Button disabled={busy||!selectedCards} variant="contained" onClick={()=>void addToDeck()}>Yes, add cards</Button></DialogActions></Dialog>
    <ManualAddDialog open={manualAdd} onClose={()=>setManualAdd(false)} onAdded={()=>setReload(value=>value+1)}/>
  </>
}

function CopySelector({item,selected,onToggle}:{item:Inventory;selected:Set<number>;onToggle:(index:number)=>void}){
  const assigned=item.deck_assignments.reduce((sum,deck)=>sum+deck.quantity,0)
  return <Box>
    <Stack direction="row" justifyContent="space-between" alignItems="baseline">
      <Typography fontWeight={800}>Physical copies · {item.quantity}</Typography>
      <Typography variant="caption" color="text.secondary">Select copies to change</Typography>
    </Stack>
    <Box sx={{display:'flex',gap:1.25,overflowX:'auto',py:1.25,pb:1.75}}>
      {Array.from({length:item.quantity},(_,index)=>{
        const locked=index<assigned,checked=selected.has(index)
        return <Box key={index} role="button" aria-label={`${locked?'Assigned':'Select'} copy ${index+1}`} onClick={()=>!locked&&onToggle(index)} sx={{position:'relative',flex:'0 0 92px',p:.75,border:'2px solid',borderColor:checked?'primary.main':'divider',borderRadius:2,cursor:locked?'not-allowed':'pointer',opacity:locked ? .48 : 1,bgcolor:checked?'rgba(255,96,112,.10)':'background.default'}}>
          {item.image_url?<FoilArtwork src={item.image_url} alt={`${item.card_name} copy ${index+1}`} foil={item.foil} sx={{width:'100%',aspectRatio:'63 / 88',borderRadius:1,overflow:'hidden'}} imageSx={{objectFit:'contain'}}/>:<Box sx={{width:'100%',aspectRatio:'63 / 88',bgcolor:'action.hover',borderRadius:1}}/>}
          <Checkbox checked={checked} disabled={locked} size="small" sx={{position:'absolute',right:0,top:0,bgcolor:'background.paper',p:.25}}/>
          <Typography variant="caption" fontWeight={800} display="block" textAlign="center" mt={.5}>{locked?'In deck':`Copy ${index+1}`}</Typography>
        </Box>
      })}
    </Box>
    {assigned>0&&<Typography variant="caption" color="text.secondary">{assigned} {assigned===1?'copy is':'copies are'} assigned to decks and cannot be changed here.</Typography>}
  </Box>
}

function PriceMovement({item}:{item:Inventory}){
  const current=Number(item.market_price||0),previous=item.previous_market_price==null?null:Number(item.previous_market_price)
  const change=previous&&current!==previous?(current-previous)/previous*100:null
  const up=change!=null&&change>0
  return <Stack direction="row" alignItems="center" spacing={.75}>
    <Typography className="card-price" variant="h6" color={change==null?'primary.main':up?'success.main':'error.main'}>${current.toFixed(2)}</Typography>
    {change!=null&&<Stack direction="row" alignItems="center" color={up?'success.main':'error.main'}>{up?<TrendingUp fontSize="small"/>:<TrendingDown fontSize="small"/>}<Typography variant="caption" fontWeight={800}>{up?'+':''}{change.toFixed(1)}%</Typography></Stack>}
  </Stack>
}
