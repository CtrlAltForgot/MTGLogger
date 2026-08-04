import { useEffect, useState } from 'react'
import { TrendingDown, TrendingUp } from '@mui/icons-material'
import { Alert, Box, Card, CircularProgress, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { request } from '../api'

type Point={recorded_at:string;total_value:number}
type Window='1d'|'1w'|'1m'|'6m'|'1y'
type History={current_value:number;previous_value:number|null;change:number|null;change_percentage:number|null;range:Window;window_start:string;window_end:string;history:Point[]}

const money=(value:number)=>value.toLocaleString(undefined,{style:'currency',currency:'USD'})
const windowMilliseconds:Record<Window,number>={'1d':86400000,'1w':604800000,'1m':2592000000,'6m':15811200000,'1y':31536000000}

export default function Value(){
  const [data,setData]=useState<History>(),[error,setError]=useState<string>(),[window,setWindow]=useState<Window>('1d')
  useEffect(()=>{setData(undefined);void request<History>(`/prices/history?range=${window}`).then(setData).catch(e=>setError(e instanceof Error?e.message:'Could not load value history'))},[window])
  if(error)return <Alert severity="error">{error}</Alert>
  if(!data)return <Box minHeight="45vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>
  const change=data.change_percentage,up=change!=null&&change>0,down=change!=null&&change<0
  const labels:Record<Window,string>={'1d':'Past day','1w':'Past week','1m':'Past month','6m':'Past 6 months','1y':'Past year'}
  const chart=data.history.map(point=>({...point,value:Number(point.total_value),timestamp:new Date(point.recorded_at).getTime()}))
  const values=chart.map(point=>point.value),minimum=Math.min(...values),maximum=Math.max(...values),padding=Math.max((maximum-minimum)*.18,maximum*.04,.05)
  const end=data.window_end?new Date(data.window_end).getTime():Date.now(),start=data.window_start?new Date(data.window_start).getTime():end-windowMilliseconds[window]
  const axisLabel=(timestamp:number)=>{const date=new Date(timestamp);return window==='1d'?date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):date.toLocaleDateString([],{month:'short',day:'numeric',year:window==='1y'?'2-digit':undefined})}
  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'end'}} spacing={2} mb={3}><Box><Typography variant="h4">Collection value</Typography><Typography color="text.secondary">Hourly observed market prices—never estimated or backfilled.</Typography></Box><ToggleButtonGroup exclusive size="small" value={window} onChange={(_,value:Window|null)=>value&&setWindow(value)}>{(['1d','1w','1m','6m','1y'] as Window[]).map(value=><ToggleButton value={value} key={value}>{value.toUpperCase()}</ToggleButton>)}</ToggleButtonGroup></Stack>
    <Stack direction={{xs:'column',md:'row'}} spacing={2} mb={3}>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">Current value</Typography><Typography variant="h3" mt={1}>{money(Number(data.current_value))}</Typography></Card>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">{labels[window]}</Typography>{change==null?<Typography variant="h5" mt={1}>Waiting for another observation</Typography>:<Stack direction="row" alignItems="center" spacing={1} mt={1} color={up?'success.main':down?'error.main':'text.secondary'}>{up?<TrendingUp fontSize="large"/>:down?<TrendingDown fontSize="large"/>:null}<Typography variant="h4">{up?'+':''}{Number(change).toFixed(1)}%</Typography><Typography>{data.change==null?'':`${Number(data.change)>=0?'+':''}${money(Number(data.change))}`}</Typography></Stack>}</Card>
    </Stack>
    <Card sx={{p:{xs:2,md:3},height:430}}><Typography variant="h6" mb={2}>Value history</Typography>{!chart.length?<Box height="85%" display="grid" sx={{placeItems:'center'}}><Typography color="text.secondary" textAlign="center">No observations have been recorded in this period yet.</Typography></Box>:<ResponsiveContainer width="100%" height="88%"><AreaChart data={chart} margin={{top:8,right:12,left:8,bottom:8}}><defs><linearGradient id="valueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#ff5d6c" stopOpacity={.45}/><stop offset="95%" stopColor="#ff5d6c" stopOpacity={0}/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" opacity={.2}/><XAxis dataKey="timestamp" type="number" scale="time" domain={[start,end]} tickFormatter={axisLabel} minTickGap={28}/><YAxis domain={[Math.max(0,minimum-padding),maximum+padding]} tickFormatter={value=>money(value)} width={72}/><Tooltip labelFormatter={label=>new Date(Number(label)).toLocaleString()} formatter={value=>[money(Number(value)),'Collection value']}/><Area type="monotone" dataKey="value" stroke="#ff5d6c" strokeWidth={3} fill="url(#valueFill)" dot={{r:4}} activeDot={{r:6}}/></AreaChart></ResponsiveContainer>}</Card>
  </>
}
