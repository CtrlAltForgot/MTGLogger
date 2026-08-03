import { useEffect, useState } from 'react'
import { Box, Card, CardContent, Grid, MenuItem, Select, Skeleton, Stack, Typography } from '@mui/material'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { request } from '../api'
import type { Inventory } from '../types'
type Group={label:string;count:number}
type Summary={total_value:number;total_cards:number;unique_printings:number;review_count:number;by_set:Group[];by_color:Group[];by_rarity:Group[];by_type:Group[];most_valuable:Inventory[];newest:Inventory[];duplicate_cards:Inventory[]}

export default function Dashboard(){
  const [data,setData]=useState<Summary>(),[breakdown,setBreakdown]=useState<'by_set'|'by_color'|'by_rarity'|'by_type'>('by_set')
  useEffect(()=>{request<Summary>('/dashboard/summary').then(setData)},[])
  if(!data)return <Skeleton height={500}/>
  return <><Typography variant="h4" mb={3}>Collection overview</Typography><Grid container spacing={2}>{[['Total value',`$${Number(data.total_value).toLocaleString(undefined,{minimumFractionDigits:2})}`],['Cards',data.total_cards],['Unique printings',data.unique_printings],['Needs review',data.review_count]].map(([label,value])=><Grid size={{xs:6,md:3}} key={label}><Card><CardContent><Typography color="text.secondary">{label}</Typography><Typography variant="h4">{value}</Typography></CardContent></Card></Grid>)}</Grid>
    <Grid container spacing={3} mt={.5}><Grid size={{xs:12,md:7}}><Card><CardContent><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="h6">Collection breakdown</Typography><Select size="small" value={breakdown} onChange={e=>setBreakdown(e.target.value as typeof breakdown)}><MenuItem value="by_set">By set</MenuItem><MenuItem value="by_color">By color</MenuItem><MenuItem value="by_rarity">By rarity</MenuItem><MenuItem value="by_type">By type</MenuItem></Select></Stack><Box height={320} mt={2}><ResponsiveContainer><BarChart data={data[breakdown].slice(0,10)}><XAxis dataKey="label" tick={{fontSize:11}}/><YAxis/><Tooltip/><Bar dataKey="count" fill="#55d68b" radius={[5,5,0,0]}/></BarChart></ResponsiveContainer></Box></CardContent></Card></Grid>
      <Grid size={{xs:12,md:5}}><Card><CardContent><Typography variant="h6">Most valuable</Typography>{data.most_valuable.map(x=><Stack direction="row" justifyContent="space-between" mt={2} key={x.id}><Box><Typography fontWeight={700}>{x.card_name}</Typography><Typography variant="caption" color="text.secondary">{x.set_code.toUpperCase()} #{x.collector_number} · ×{x.quantity}</Typography></Box><Typography color="primary.main" fontWeight={800}>${Number(x.market_price).toFixed(2)}</Typography></Stack>)}</CardContent></Card></Grid>
      <Grid size={{xs:12,md:6}}><Card><CardContent><Typography variant="h6">Newest cards</Typography>{data.newest.map(x=><Stack direction="row" justifyContent="space-between" mt={1.5} key={x.id}><Typography fontWeight={700}>{x.card_name}</Typography><Typography variant="caption" color="text.secondary">{x.set_code.toUpperCase()} #{x.collector_number}</Typography></Stack>)}</CardContent></Card></Grid>
      <Grid size={{xs:12,md:6}}><Card><CardContent><Typography variant="h6">Duplicate printings</Typography>{data.duplicate_cards.map(x=><Stack direction="row" justifyContent="space-between" mt={1.5} key={x.id}><Typography fontWeight={700}>{x.card_name}</Typography><Typography color="primary.main" fontWeight={800}>×{x.quantity}</Typography></Stack>)}{!data.duplicate_cards.length&&<Typography color="text.secondary" mt={2}>No duplicate printings yet.</Typography>}</CardContent></Card></Grid>
    </Grid></>
}
