import { createContext, type ReactNode, useContext, useEffect, useState } from 'react'
import { Close, OpenInNew } from '@mui/icons-material'
import {
  Box, Button, ButtonBase, Chip, CircularProgress, Dialog, DialogContent,
  DialogTitle, Divider, Grid, IconButton, Stack, Typography,
} from '@mui/material'
import { request } from '../api'

type Details={scryfall_id:string;oracle_id?:string;name:string;set_code:string;set_name:string;collector_number:string;image_url?:string;mana_cost?:string;type_line?:string;oracle_text?:string;flavor_text?:string;power?:string;toughness?:string;loyalty?:string;rarity?:string;artist?:string;language?:string;released_at?:string;finishes?:string[];prices?:Record<string,string|null>;legalities?:Record<string,string>;scryfall_uri?:string}
const Context=createContext<(id:string)=>void>(()=>undefined)

export function CardDetailsProvider({children}:{children:ReactNode}){
  const [id,setId]=useState<string>(),[details,setDetails]=useState<Details>(),[error,setError]=useState('')
  useEffect(()=>{if(!id){setDetails(undefined);setError('');return}setDetails(undefined);setError('');void request<Details>(`/references/card/${id}`).then(setDetails).catch(reason=>setError(reason instanceof Error?reason.message:'Could not load card details'))},[id])
  const legal=details?.legalities?Object.entries(details.legalities).filter(([,status])=>status==='legal').map(([format])=>format):[]
  const price=details?.prices?.usd,foilPrice=details?.prices?.usd_foil
  const rules=details?displayRules(details):''
  return <Context.Provider value={setId}>{children}<Dialog open={!!id} onClose={()=>setId(undefined)} fullWidth maxWidth="md"><DialogTitle className={details?'card-name':undefined} sx={{pr:7}}>{details?.name||'Card details'}<IconButton onClick={()=>setId(undefined)} sx={{position:'absolute',right:12,top:10}}><Close/></IconButton></DialogTitle><DialogContent className="card-detail-body" dividers sx={{minHeight:460}}>{error?<Typography color="error.main">{error}</Typography>:!details?<Box minHeight={400} display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>:<Grid container spacing={3}><Grid size={{xs:12,sm:5}}>{details.image_url&&<Box component="img" src={details.image_url} alt={details.name} sx={{display:'block',width:'100%',maxWidth:360,mx:'auto',borderRadius:3,boxShadow:'0 18px 50px rgba(0,0,0,.35)'}}/>}</Grid><Grid size={{xs:12,sm:7}}><Typography className="card-title" variant="h4">{details.name}</Typography><Typography className="card-printing" color="text.secondary">{details.set_name} ({details.set_code.toUpperCase()}) #{details.collector_number} · {details.language?.toUpperCase()}</Typography><Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap mt={2}>{details.rarity&&<Chip label={details.rarity}/>} {details.finishes?.map(finish=><Chip key={finish} variant="outlined" label={finish}/>)}</Stack><Divider sx={{my:2}}/><Typography fontWeight={800}>{details.type_line}</Typography>{rules&&<Typography whiteSpace="pre-line" mt={2}>{rules}</Typography>}{(details.power||details.toughness)&&<Typography fontWeight={900} mt={2}>{details.power}/{details.toughness}</Typography>}{details.loyalty&&<Typography fontWeight={900} mt={2}>Loyalty {details.loyalty}</Typography>}{details.flavor_text&&<Typography color="text.secondary" fontStyle="italic" mt={2}>{details.flavor_text}</Typography>}<Divider sx={{my:2}}/><Stack direction="row" spacing={3}><Box><Typography variant="caption" color="text.secondary">NONFOIL</Typography><Typography className="card-price" variant="h6">{price?`$${price}`:'—'}</Typography></Box><Box><Typography variant="caption" color="text.secondary">FOIL</Typography><Typography className="card-price" variant="h6">{foilPrice?`$${foilPrice}`:'—'}</Typography></Box></Stack>{details.artist&&<Typography color="text.secondary" mt={2}>Illustrated by {details.artist}</Typography>}{legal.length>0&&<><Typography fontWeight={800} mt={2} mb={1}>Legal in</Typography><Stack direction="row" gap={.75} flexWrap="wrap">{legal.map(format=><Chip key={format} size="small" label={format}/>)}</Stack></>}{details.scryfall_uri&&<Button href={details.scryfall_uri} target="_blank" rel="noreferrer" startIcon={<OpenInNew/>} sx={{mt:2}}>Open exact printing on Scryfall</Button>}</Grid></Grid>}</DialogContent></Dialog></Context.Provider>
}

function displayRules(details:Details){
  const lines=(details.oracle_text||'').split('\n').filter(line=>{
    if(!details.type_line?.includes('Land'))return true
    return !/^\s*\(?\s*\{T\}\s*:\s*Add\s+.*?\.?\s*\)?\s*$/i.test(line)
  })
  return lines.join('\n').trim()
}

export function CardName({scryfallId,children}:{scryfallId:string;children:ReactNode}){
  const open=useContext(Context)
  return <ButtonBase className="card-name" component="span" onClick={event=>{event.stopPropagation();open(scryfallId)}} sx={{fontWeight:'inherit',color:'inherit',textAlign:'left',justifyContent:'flex-start',maxWidth:'100%','&:hover':{color:'primary.main',textDecoration:'underline'}}}>{children}</ButtonBase>
}
