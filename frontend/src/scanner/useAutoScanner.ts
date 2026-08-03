import { useCallback, useEffect, useRef, useState } from 'react'

type State='idle'|'waiting'|'stabilizing'|'capturing'|'processing'|'remove'
export type ScannerTuning={presenceContrast:number;stableMotion:number;leaveMotion:number;stableFrames:number}
export type ScannerMetrics={brightness:number;contrast:number;motion:number}
export const defaultTuning:ScannerTuning={presenceContrast:18,stableMotion:2.2,leaveMotion:8,stableFrames:5}

export function useAutoScanner(onCapture:(blob:Blob)=>Promise<void>,tuning:ScannerTuning=defaultTuning) {
  const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null)
  const previous=useRef<Uint8ClampedArray|undefined>(undefined),stable=useRef(0),latched=useRef(false),busy=useRef(false)
  const [state,setState]=useState<State>('idle'),[error,setError]=useState<string>(),[metrics,setMetrics]=useState<ScannerMetrics>({brightness:0,contrast:0,motion:0})
  const start=useCallback(async()=>{try{const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720},facingMode:'environment'},audio:false});if(video.current){video.current.srcObject=stream;await video.current.play();setState('waiting')}}catch(e){setError(e instanceof Error?e.message:'Camera unavailable')}},[])
  const stop=useCallback(()=>{(video.current?.srcObject as MediaStream|null)?.getTracks().forEach(track=>track.stop());previous.current=undefined;setState('idle')},[])
  useEffect(()=>{
    if(state==='idle'||error)return
    const timer=setInterval(()=>{
      const element=video.current,preview=canvas.current
      if(!element||!preview||element.readyState<2||busy.current)return
      const context=preview.getContext('2d',{willReadFrequently:true});if(!context)return
      preview.width=160;preview.height=120;context.drawImage(element,0,0,160,120)
      const pixels=context.getImageData(0,0,160,120).data
      let brightness=0,variance=0,difference=0
      for(let index=0;index<pixels.length;index+=16){const luminance=(pixels[index]+pixels[index+1]+pixels[index+2])/3;brightness+=luminance;variance+=luminance*luminance;if(previous.current)difference+=Math.abs(pixels[index]-previous.current[index])}
      const samples=pixels.length/16,motion=previous.current?difference/samples:99,mean=brightness/samples,contrast=Math.sqrt(Math.max(0,variance/samples-mean*mean))
      previous.current=new Uint8ClampedArray(pixels);setMetrics({brightness:mean,contrast,motion})
      if(latched.current){if(motion>tuning.leaveMotion){stable.current++;if(stable.current>=3){latched.current=false;stable.current=0;setState('waiting')}}else stable.current=0;return}
      if(mean<=25||contrast<tuning.presenceContrast){setState('waiting');stable.current=0;return}
      if(motion<tuning.stableMotion){stable.current++;setState('stabilizing')}else{stable.current=0;setState('waiting')}
      if(stable.current<tuning.stableFrames)return
      busy.current=true;setState('capturing')
      const full=document.createElement('canvas');full.width=element.videoWidth;full.height=element.videoHeight;full.getContext('2d')!.drawImage(element,0,0)
      full.toBlob(async blob=>{if(blob){setState('processing');try{await onCapture(blob)}catch(e){setError(e instanceof Error?e.message:'Scan failed')}}latched.current=true;busy.current=false;stable.current=0;setState('remove')},'image/jpeg',.9)
    },180)
    return()=>clearInterval(timer)
  },[state,error,onCapture,tuning])
  useEffect(()=>stop,[stop])
  return {video,canvas,state,error,metrics,start,stop,setError}
}
