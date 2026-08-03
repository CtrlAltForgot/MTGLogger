import { useEffect, useState } from 'react'
import { TrendingDown, TrendingUp } from '@mui/icons-material'
import { Alert, Box, Card, CircularProgress, Stack, Typography } from '@mui/material'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { request } from '../api'

type Point={recorded_at:string;total_value:number}
type History={current_value:number;previous_value:number|null;change:number|null;change_percentage:number|null;history:Point[]}

const money=(value:number)=>value.toLocaleString(undefined,{style:'currency',currency:'USD'})

export default function Value(){
  const [data,setData]=useState<History>(),[error,setError]=useState<string>()
  useEffect(()=>{void request<History>('/prices/history').then(setData).catch(e=>setError(e instanceof Error?e.message:'Could not load value history'))},[])
  if(error)return <Alert severity="error">{error}</Alert>
  if(!data)return <Box minHeight="45vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>
  const change=data.change_percentage,up=change!=null&&change>0
  const multipleDays=new Set(data.history.map(point=>new Date(point.recorded_at).toLocaleDateString())).size>1
  const chart=data.history.map(point=>{const date=new Date(point.recorded_at);return {...point,value:Number(point.total_value),label:multipleDays?date.toLocaleDateString():date.toLocaleTimeString([],{hour:'numeric',minute:'2-digit',second:'2-digit'})}})
  const values=chart.map(point=>point.value),minimum=Math.min(...values),maximum=Math.max(...values),padding=Math.max((maximum-minimum)*.18,maximum*.04,.05)
  return <>
    <Typography variant="h4">Collection value</Typography>
    <Typography color="text.secondary" mb={3}>A real history of observed market prices—never estimated or backfilled.</Typography>
    <Stack direction={{xs:'column',md:'row'}} spacing={2} mb={3}>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">Current value</Typography><Typography variant="h3" mt={1}>{money(Number(data.current_value))}</Typography></Card>
      <Card sx={{p:3,flex:1}}><Typography color="text.secondary">Since last price update</Typography>{change==null?<Typography variant="h5" mt={1}>Waiting for another observation</Typography>:<Stack direction="row" alignItems="center" spacing={1} mt={1} color={up?'success.main':'error.main'}>{up?<TrendingUp fontSize="large"/>:<TrendingDown fontSize="large"/>}<Typography variant="h4">{up?'+':''}{Number(change).toFixed(1)}%</Typography><Typography>{data.change==null?'':`${Number(data.change)>=0?'+':''}${money(Number(data.change))}`}</Typography></Stack>}</Card>
    </Stack>
    <Card sx={{p:{xs:2,md:3},height:430}}><Typography variant="h6" mb={2}>Value history</Typography>{chart.length<2?<Box height="85%" display="grid" sx={{placeItems:'center'}}><Typography color="text.secondary" textAlign="center">History starts here. The first movement will appear after a genuine price change is observed.</Typography></Box>:<ResponsiveContainer width="100%" height="88%"><AreaChart data={chart} margin={{top:8,right:12,left:8,bottom:8}}><defs><linearGradient id="valueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#ff5d6c" stopOpacity={.45}/><stop offset="95%" stopColor="#ff5d6c" stopOpacity={0}/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" opacity={.2}/><XAxis dataKey="label" minTickGap={28}/><YAxis domain={[Math.max(0,minimum-padding),maximum+padding]} tickFormatter={value=>money(value)} width={72}/><Tooltip labelFormatter={(_label,payload)=>payload?.[0]?.payload?.recorded_at?new Date(payload[0].payload.recorded_at).toLocaleString():_label} formatter={value=>[money(Number(value)),'Collection value']}/><Area type="monotone" dataKey="value" stroke="#ff5d6c" strokeWidth={3} fill="url(#valueFill)" dot={{r:4}} activeDot={{r:6}}/></AreaChart></ResponsiveContainer>}</Card>
  </>
}
