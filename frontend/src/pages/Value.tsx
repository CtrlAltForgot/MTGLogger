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
const intervalMilliseconds:Record<Window,number>={'1d':3600000,'1w':86400000,'1m':259200000,'6m':1209600000,'1y':2592000000}
const tooltipStyle={background:'rgba(18,12,13,.96)',border:'1px solid rgba(255,255,255,.14)',borderRadius:10,color:'#f8f3f4',boxShadow:'0 14px 38px rgba(0,0,0,.4)'}

function timeline(points:Point[],range:Window,end:number){
  const start=end-windowMilliseconds[range]
  const observations=points.map(point=>({value:Number(point.total_value),timestamp:new Date(point.recorded_at).getTime()})).filter(point=>Number.isFinite(point.timestamp)&&point.timestamp<=end).sort((a,b)=>a.timestamp-b.timestamp)
  const firstInWindow=observations.findIndex(point=>point.timestamp>=start)
  const earlier=observations.filter(point=>point.timestamp<start).at(-1)
  const visible=firstInWindow<0?[]:observations.slice(firstInWindow)
  // If the database has no observation before this window, extend the first
  // known value back to the left boundary. A carried-value chart should fill
  // its entire time domain rather than appearing to begin partway across it.
  let value=earlier?.value??visible[0]?.value
  let cursor=0
  const interval=intervalMilliseconds[range]
  const result:{value:number;timestamp:number}[]=[]
  for(let timestamp=start;timestamp<=end;timestamp+=interval){
    while(cursor<visible.length&&visible[cursor].timestamp<=timestamp)value=visible[cursor++].value
    if(value!=null)result.push({timestamp,value})
  }
  while(cursor<visible.length&&visible[cursor].timestamp<=end)value=visible[cursor++].value
  if(value!=null&&result.at(-1)?.timestamp!==end)result.push({timestamp:end,value})
  const ticks=Array.from({length:Math.floor((end-start)/interval)+1},(_,index)=>start+index*interval)
  const labelTicks=range==='1d'?ticks.filter((_,index)=>index%2===0):ticks
  return {start,result,ticks:labelTicks}
}

export default function Value(){
  const [data,setData]=useState<History>(),[error,setError]=useState<string>(),[window,setWindow]=useState<Window>('1d')
  useEffect(()=>{setData(undefined);void request<History>(`/prices/history?range=${window}`).then(setData).catch(e=>setError(e instanceof Error?e.message:'Could not load value history'))},[window])
  if(error)return <Alert severity="error">{error}</Alert>
  if(!data)return <Box minHeight="45vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>
  const change=data.change_percentage,up=change!=null&&change>0,down=change!=null&&change<0
  const labels:Record<Window,string>={'1d':'Past day','1w':'Past week','1m':'Past month','6m':'Past 6 months','1y':'Past year'}
  const end=Date.now(),{start,result:chart,ticks}=timeline(data.history,window,end)
  const values=chart.map(point=>point.value),minimum=Math.min(...values),maximum=Math.max(...values),padding=Math.max((maximum-minimum)*.18,maximum*.04,.05)
  const axisLabel=(timestamp:number)=>{const date=new Date(timestamp);return window==='1d'?date.toLocaleTimeString([],{hour:'numeric'}):date.toLocaleDateString([],{month:'short',day:'numeric',year:window==='1y'?'2-digit':undefined})}
  const tooltipLabel=(timestamp:number)=>{const quarterHour=15*60*1000,rounded=Math.round(timestamp/quarterHour)*quarterHour;return new Date(rounded).toLocaleString([],{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'})}
  return <>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'end'}} spacing={2} mb={3}><Box><Typography variant="h4">Collection value</Typography><Typography color="text.secondary">Observed market prices, carried forward between hourly updates.</Typography></Box><ToggleButtonGroup exclusive size="small" value={window} onChange={(_,value:Window|null)=>value&&setWindow(value)}>{(['1d','1w','1m','6m','1y'] as Window[]).map(value=><ToggleButton value={value} key={value}>{value.toUpperCase()}</ToggleButton>)}</ToggleButtonGroup></Stack>
    <Stack direction={{xs:'column',md:'row'}} spacing={2} mb={3}>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">Current value</Typography><Typography variant="h3" mt={1}>{money(Number(data.current_value))}</Typography></Card>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">{labels[window]}</Typography>{change==null?<Typography variant="h5" mt={1}>Waiting for another observation</Typography>:<Stack direction="row" alignItems="center" spacing={1} mt={1} color={up?'success.main':down?'error.main':'text.secondary'}>{up?<TrendingUp fontSize="large"/>:down?<TrendingDown fontSize="large"/>:null}<Typography variant="h4">{up?'+':''}{Number(change).toFixed(1)}%</Typography><Typography>{data.change==null?'':`${Number(data.change)>=0?'+':''}${money(Number(data.change))}`}</Typography></Stack>}</Card>
    </Stack>
    <Card sx={{p:{xs:2,md:3},height:430}}><Typography variant="h6" mb={2}>Value history</Typography>{!chart.length?<Box height="85%" display="grid" sx={{placeItems:'center'}}><Typography color="text.secondary" textAlign="center">No observations have been recorded in this period yet.</Typography></Box>:<ResponsiveContainer width="100%" height="88%"><AreaChart data={chart} margin={{top:8,right:12,left:8,bottom:8}}><defs><linearGradient id="valueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#ff5d6c" stopOpacity={.32}/><stop offset="95%" stopColor="#ff5d6c" stopOpacity={0}/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" opacity={.2}/><XAxis dataKey="timestamp" type="number" scale="time" domain={[start,end]} ticks={ticks} tickFormatter={axisLabel} interval={0}/><YAxis domain={[Math.max(0,minimum-padding),maximum+padding]} tickFormatter={value=>money(value)} width={72}/><Tooltip contentStyle={tooltipStyle} labelStyle={{color:'#b9adb0',marginBottom:5}} itemStyle={{color:'#ff6877'}} labelFormatter={label=>tooltipLabel(Number(label))} formatter={value=>[money(Number(value)),'Collection value']}/><Area type="stepAfter" dataKey="value" stroke="#ff5d6c" strokeWidth={3} fill="url(#valueFill)" dot={false} activeDot={{r:5,fill:'#ff6877',stroke:'#f8f3f4',strokeWidth:2}}/></AreaChart></ResponsiveContainer>}</Card>
  </>
}
