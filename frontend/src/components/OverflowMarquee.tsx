import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Box } from '@mui/material'

export default function OverflowMarquee({children,className,title}:{children:ReactNode;className?:string;title?:string}){
  const viewport=useRef<HTMLDivElement>(null),content=useRef<HTMLSpanElement>(null)
  const [overflow,setOverflow]=useState(false),[distance,setDistance]=useState(0)
  useEffect(()=>{
    const measure=()=>{const extra=(content.current?.scrollWidth||0)-(viewport.current?.clientWidth||0);setOverflow(extra>2);setDistance(Math.max(0,extra))}
    measure()
    const observer=new ResizeObserver(measure)
    if(viewport.current)observer.observe(viewport.current)
    if(content.current)observer.observe(content.current)
    return()=>observer.disconnect()
  },[children])
  return <Box ref={viewport} className={`overflow-marquee${overflow?' is-overflowing':''}`} title={title}>
    <Box ref={content} component="span" className={className} sx={{'--marquee-distance':`-${distance}px`} as never}>{children}</Box>
  </Box>
}
