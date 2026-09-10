import {artworkUrl} from '../artwork'
import {useEffect,useState} from 'react'
import {ArrowBack,Search} from '@mui/icons-material'
import {Alert,Box,Button,Chip,InputAdornment,LinearProgress,MenuItem,Pagination,Stack,TextField,Typography} from '@mui/material'
import {request} from '../api'
import type {Candidate} from '../types'

type Hit=Candidate&{oracle_name:string;printing_count:number}
type Results={items:Hit[];total:number;page:number;page_size:number;fuzzy:boolean;mode:'cards'|'printings';sets:{code:string;name:string;count:number}[]}

/** Shared by review and manual add, including the iPhone's website tabs. */
export default function CardSearch({onChoose,language='en',disabled=false}:{onChoose:(card:Candidate)=>void;language?:string;disabled?:boolean}){
  const [query,setQuery]=useState('')
  const [family,setFamily]=useState<Hit|null>(null)
  const [filter,setFilter]=useState('')
  const [setCode,setSetCode]=useState('')
  const [page,setPage]=useState(1)
  const [response,setResponse]=useState<{key:string;data:Results}>()
  const [failure,setFailure]=useState<{key:string;message:string}>()
  const [attempt,setAttempt]=useState(0)
  const params=new URLSearchParams({q:[query,filter].filter(Boolean).join(' ').trim(),lang:language,page:String(page)})
  if(family)params.set('name',family.oracle_name)
  if(setCode)params.set('set_code',setCode)
  const key=params.toString()
  const active=query.trim().length>=2||!!family
  const data=response?.key===key?response.data:undefined
  const error=failure?.key===key?failure.message:undefined
  const loading=active&&!data&&!error
  const sets=data?.sets??response?.data.sets??[]
  const visibleSets=setCode&&!sets.some(set=>set.code===setCode)?[{code:setCode,name:setCode.toUpperCase(),count:0},...sets]:sets

  useEffect(()=>{
    if(!active)return
    const controller=new AbortController()
    let current=true
    const timer=window.setTimeout(()=>{
      void request<Results>(`/reviews/browse?${key}`,{signal:controller.signal}).then(data=>{
        if(current)setResponse({key,data})
      }).catch(error=>{
        if(current&&!controller.signal.aborted)setFailure({key,message:error instanceof Error?error.message:'Search is unavailable. Try again.'})
      })
    },250)
    return()=>{current=false;controller.abort();window.clearTimeout(timer)}
  },[key,active,attempt])

  const changeQuery=(value:string)=>{setQuery(value);setFamily(null);setFilter('');setSetCode('');setPage(1);setFailure(undefined)}
  const back=()=>{setFamily(null);setFilter('');setSetCode('');setPage(1);setFailure(undefined)}
  const choose=(hit:Hit)=>{
    if(data?.mode==='printings'||hit.printing_count===1){onChoose(hit);return}
    setFamily(hit);setPage(1);setFilter('');setSetCode('')
  }
  return <Box>
    <Box sx={{position:'sticky',top:0,zIndex:2,bgcolor:'background.paper',pt:1,pb:1.5}}>
      {!family?<>
        <TextField autoFocus fullWidth label="Find a card" placeholder="Part of a name, set, or collector number" value={query}
          onChange={event=>changeQuery(event.target.value)} disabled={disabled}
          slotProps={{input:{startAdornment:<InputAdornment position="start"><Search/></InputAdornment>},htmlInput:{maxLength:100}}}/>
        <Typography variant="caption" color="text.secondary" display="block" mt={.75}>Results appear as you type. Try “plains sld”, “bolt lightning”, or a misspelled name.</Typography>
      </>:<>
        <Button startIcon={<ArrowBack/>} onClick={back} disabled={disabled} size="small">Back to cards</Button>
        <Typography variant="h6" mt={.5}>{family.name}</Typography>
        <Typography variant="body2" color="text.secondary" mb={1.5}>Choose the picture that matches your card. Set and number are optional.</Typography>
        <Stack direction={{xs:'column',sm:'row'}} spacing={1.5}>
          <TextField select fullWidth size="small" label="Set" value={setCode} disabled={disabled}
            onChange={event=>{setSetCode(event.target.value);setPage(1)}}>
            <MenuItem value="">All matching sets</MenuItem>
            {visibleSets.map(set=><MenuItem key={set.code} value={set.code}>{set.name} ({set.code.toUpperCase()}) · {set.count}</MenuItem>)}
          </TextField>
          <TextField fullWidth size="small" label="Narrow by set or number" placeholder="e.g. secret lair or 1735" value={filter}
            disabled={disabled} slotProps={{htmlInput:{maxLength:80}}} onChange={event=>{setFilter(event.target.value);setPage(1)}}/>
        </Stack>
      </>}
    </Box>
    {!active&&<Stack spacing={1.5} py={2}>
      <Typography color="text.secondary">Glare in the way? Search by what you can read and choose the artwork. No photo needed.</Typography>
      <Stack direction="row" gap={1} flexWrap="wrap">{['Plains','Island','Swamp','Mountain','Forest'].map(name=><Chip key={name} label={name} onClick={()=>changeQuery(name)} disabled={disabled}/>)}</Stack>
    </Stack>}
    {loading&&<Box py={2} role="status"><LinearProgress/><Typography color="text.secondary" variant="body2" mt={1}>Finding cards…</Typography></Box>}
    {error&&<Alert severity="error" action={<Button onClick={()=>{setFailure(undefined);setResponse(undefined);setAttempt(value=>value+1)}}>Retry</Button>}>{error}</Alert>}
    {data&&<>
      {data.fuzzy&&<Alert severity="info" sx={{mb:1.5}}>Showing similar spellings. Choose the card you meant.</Alert>}
      <Typography variant="body2" color="text.secondary" mb={1.5} role="status">{data.total.toLocaleString()} matching {data.mode==='cards'?'cards':'printings'}{data.mode==='cards'?' · choose a card to see its printings':''}</Typography>
      {!data.total&&<Alert severity="info" action={family?<Button onClick={()=>{setFilter('');setSetCode('');setPage(1)}}>Clear filters</Button>:undefined}>
        No matches yet. Try a shorter part of the name or just the set and collector number.
      </Alert>}
      <Box sx={{display:'grid',gridTemplateColumns:{xs:'repeat(2,minmax(0,1fr))',sm:'repeat(3,minmax(0,1fr))',md:'repeat(4,minmax(0,1fr))'},gap:1.5}}>
        {data.items.map(hit=><Box component="button" type="button" key={hit.scryfall_id} disabled={disabled} onClick={()=>choose(hit)}
          aria-label={`${data.mode==='cards'&&hit.printing_count>1?'See printings for':'Choose'} ${hit.name}${data.mode==='printings'?` ${hit.set_code.toUpperCase()} ${hit.collector_number}`:''}`}
          sx={{appearance:'none',font:'inherit',textAlign:'left',p:1,border:'1px solid',borderColor:'divider',borderRadius:2,bgcolor:'background.default',color:'text.primary',cursor:'pointer',minWidth:0,'&:hover,&:focus-visible':{borderColor:'primary.main'},'&:disabled':{opacity:.5,cursor:'wait'}}}>
          <Box component="img" src={artworkUrl(hit.image_url||'')} alt="" loading="lazy" sx={{display:'block',width:'100%',aspectRatio:'63 / 88',objectFit:'contain',borderRadius:1}}/>
          <Typography className="card-title" fontWeight={800} mt={1} sx={{overflowWrap:'anywhere'}}>{hit.name}</Typography>
          {hit.oracle_name!==hit.name&&<Typography variant="caption" color="text.secondary" display="block">{hit.oracle_name}</Typography>}
          <Typography variant="caption" color="text.secondary" display="block" mt={.5}>
            {data.mode==='cards'&&hit.printing_count>1?`${hit.printing_count.toLocaleString()} matching printings`:`${hit.set_name} · ${hit.set_code.toUpperCase()} #${hit.collector_number}`}
          </Typography>
          <Typography color="primary" variant="body2" fontWeight={750} mt={1}>{data.mode==='cards'&&hit.printing_count>1?'See printings':'Choose printing'}</Typography>
        </Box>)}
      </Box>
      {data.total>data.page_size&&<Stack alignItems="center" mt={2}><Pagination page={page} count={Math.ceil(data.total/data.page_size)} onChange={(_,next)=>{setPage(next)}} size="small" siblingCount={0} color="primary" disabled={disabled}/></Stack>}
    </>}
  </Box>
}
