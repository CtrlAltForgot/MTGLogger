import { useCallback, useEffect, useRef, useState } from 'react'
import {advanceRemovalGate,type RemovalGate} from './removalGate'

type State='idle'|'calibrating'|'waiting'|'stabilizing'|'capturing'|'processing'|'remove'
export type ScannerTuning={entryDifference:number;stableMotion:number;stableFrames:number}
export type DetectionBounds={left:number;top:number;width:number;height:number}
export type ScannerMetrics={brightness:number;contrast:number;motion:number;sceneDifference:number;bounds?:DetectionBounds}
export const defaultTuning:ScannerTuning={entryDifference:12,stableMotion:4,stableFrames:5}
export const pipelineHasCapacity=(inFlight:number,maxInFlight:number)=>inFlight<Math.max(1,maxInFlight)

const FRAME_WIDTH=160,FRAME_HEIGHT=120,CALIBRATION_FRAMES=12

export function cardShapedBounds(changed:Uint8Array){
  const columns=FRAME_WIDTH/2,rows=FRAME_HEIGHT/2,seen=new Uint8Array(changed.length)
  let best:DetectionBounds|undefined,bestScore=0
  for(let origin=0;origin<changed.length;origin++){
    if(!changed[origin]||seen[origin])continue
    const queue=[origin];seen[origin]=1
    let count=0,minColumn=columns,minRow=rows,maxColumn=0,maxRow=0
    for(let cursor=0;cursor<queue.length;cursor++){
      const point=queue[cursor],column=point%columns,row=Math.floor(point/columns)
      count++;minColumn=Math.min(minColumn,column);maxColumn=Math.max(maxColumn,column);minRow=Math.min(minRow,row);maxRow=Math.max(maxRow,row)
      for(const neighbor of [point-1,point+1,point-columns,point+columns]){
        if(neighbor<0||neighbor>=changed.length||seen[neighbor]||!changed[neighbor])continue
        const neighborColumn=neighbor%columns
        if(Math.abs(neighborColumn-column)>1)continue
        seen[neighbor]=1;queue.push(neighbor)
      }
    }
    const width=(maxColumn-minColumn+1)*2,height=(maxRow-minRow+1)*2,aspect=width/Math.max(1,height)
    // MTG cards remain portrait-shaped even under ordinary perspective. Wide
    // components are table/exposure changes and should never stretch the box.
    if(count<20||height<FRAME_HEIGHT*.28||width<FRAME_WIDTH*.08||aspect<.32||aspect>1.02)continue
    const shapeFit=Math.exp(-Math.abs(Math.log(aspect/.716))*1.8)
    const score=Math.sqrt(count)*shapeFit*(height/FRAME_HEIGHT)
    if(score<=bestScore)continue
    bestScore=score
    best={left:minColumn*2/FRAME_WIDTH*100,top:minRow*2/FRAME_HEIGHT*100,width:width/FRAME_WIDTH*100,height:height/FRAME_HEIGHT*100}
  }
  return best
}

export function analyze(pixels:Uint8ClampedArray,previous?:Uint8ClampedArray,baseline?:Uint8ClampedArray){
  let brightness=0,variance=0,motion=0,sceneDifference=0,samples=0
  const sceneChanges:number[]=[],changed=new Uint8Array(FRAME_WIDTH/2*FRAME_HEIGHT/2)
  // Monitor nearly the full scan zone so the card does not need meticulous
  // centering. A narrow outer gutter ignores preview borders and OBS overlays.
  for(let y=4;y<116;y+=2)for(let x=6;x<154;x+=2){
    const index=(y*FRAME_WIDTH+x)*4,luminance=(pixels[index]+pixels[index+1]+pixels[index+2])/3
    brightness+=luminance;variance+=luminance*luminance;samples++
    if(previous)motion+=Math.abs(luminance-(previous[index]+previous[index+1]+previous[index+2])/3)
    if(baseline){const difference=Math.abs(luminance-(baseline[index]+baseline[index+1]+baseline[index+2])/3);sceneChanges.push(difference);if(difference>=24)changed[y/2*(FRAME_WIDTH/2)+x/2]=1}
  }
  // A card may occupy only a quarter of a widescreen frame. Average the most
  // changed third so a clearly visible off-center card is not diluted by table.
  if(sceneChanges.length){sceneChanges.sort((a,b)=>b-a);const changed=Math.max(1,Math.ceil(sceneChanges.length/3));for(let i=0;i<changed;i++)sceneDifference+=sceneChanges[i];sceneDifference/=changed}
  const mean=brightness/samples
  const bounds=baseline?cardShapedBounds(changed):undefined
  return {brightness:mean,contrast:Math.sqrt(Math.max(0,variance/samples-mean*mean)),motion:previous?motion/samples:99,sceneDifference:baseline?sceneDifference:0,bounds}
}

export function useAutoScanner(
  onCapture:(blob:Blob)=>Promise<boolean>,
  tuning:ScannerTuning=defaultTuning,
  maxInFlight=2,
){
  const video=useRef<HTMLVideoElement>(null),canvas=useRef<HTMLCanvasElement>(null)
  const previous=useRef<Uint8ClampedArray|undefined>(undefined),baseline=useRef<Uint8ClampedArray|undefined>(undefined),capturedFrame=useRef<Uint8ClampedArray|undefined>(undefined),calibrationCount=useRef(0),noiseMotion=useRef(0),stable=useRef(0),removalGate=useRef<RemovalGate>({latched:false,emptyFrames:0,replacementFrames:0}),capturing=useRef(false),inFlight=useRef(0),sessionGeneration=useRef(0)
  const [state,setState]=useState<State>('idle'),[error,setError]=useState<string>(),[metrics,setMetrics]=useState<ScannerMetrics>({brightness:0,contrast:0,motion:0,sceneDifference:0})
  const [pendingCaptures,setPendingCaptures]=useState(0)
  const [cameras,setCameras]=useState<MediaDeviceInfo[]>([]),[selectedCamera,setSelectedCamera]=useState('')

  const calibrate=useCallback(()=>{baseline.current=undefined;previous.current=undefined;capturedFrame.current=undefined;calibrationCount.current=0;noiseMotion.current=0;stable.current=0;removalGate.current={latched:false,emptyFrames:0,replacementFrames:0};setState('calibrating')},[])
  const start=useCallback(async(deviceId?:string)=>{try{
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw new Error('Camera access requires HTTPS or localhost. Use an HTTPS reverse proxy when opening MTGLogger from another computer.')
    setError(undefined)
    const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720},...(deviceId?{deviceId:{exact:deviceId}}:{facingMode:'environment'})},audio:false})
    if(video.current){video.current.srcObject=stream;await video.current.play();const active=stream.getVideoTracks()[0]?.getSettings().deviceId||deviceId||'';setSelectedCamera(active);setCameras((await navigator.mediaDevices.enumerateDevices()).filter(device=>device.kind==='videoinput'));calibrate()}
  }catch(e){setError(e instanceof Error?e.message:'Camera unavailable')}},[calibrate])
  const stop=useCallback(()=>{sessionGeneration.current++;(video.current?.srcObject as MediaStream|null)?.getTracks().forEach(track=>track.stop());baseline.current=undefined;previous.current=undefined;capturing.current=false;inFlight.current=0;setPendingCaptures(0);setState('idle')},[])
  const switchCamera=useCallback(async(deviceId:string)=>{stop();setSelectedCamera(deviceId);await start(deviceId)},[start,stop])

  useEffect(()=>{
    if(state==='idle'||error)return
    const timer=setInterval(()=>{
      const element=video.current,preview=canvas.current
      if(!element||!preview||element.readyState<2||capturing.current)return
      const context=preview.getContext('2d',{willReadFrequently:true});if(!context)return
      preview.width=FRAME_WIDTH;preview.height=FRAME_HEIGHT;context.drawImage(element,0,0,FRAME_WIDTH,FRAME_HEIGHT)
      const pixels=context.getImageData(0,0,FRAME_WIDTH,FRAME_HEIGHT).data
      if(!baseline.current){
        if(!previous.current){previous.current=new Uint8ClampedArray(pixels);return}
        // Let exposure settle, then retain the last empty frame as the baseline.
        const calibrationMetrics=analyze(pixels,previous.current)
        calibrationCount.current++
        noiseMotion.current+=calibrationMetrics.motion
        previous.current=new Uint8ClampedArray(pixels)
        if(calibrationCount.current>=CALIBRATION_FRAMES){baseline.current=new Uint8ClampedArray(pixels);setState('waiting')}
        return
      }
      const next=analyze(pixels,previous.current,baseline.current);previous.current=new Uint8ClampedArray(pixels);setMetrics(next)
      const cardPresent=next.sceneDifference>=tuning.entryDifference
      if(removalGate.current.latched){
        const replacementDifference=capturedFrame.current?analyze(pixels,undefined,capturedFrame.current).sceneDifference:0
        // A direct swap produces consecutive frames that differ substantially
        // from the captured card (usually a hand, then the replacement). Minor
        // exposure drift or an unmoved card remains latched.
        const substantiallyChanged=replacementDifference>=Math.max(24,tuning.entryDifference*1.75)
        const update=advanceRemovalGate(removalGate.current,cardPresent,substantiallyChanged)
        removalGate.current=update.gate
        if(update.rearmed){stable.current=0;setState('waiting')}
        return
      }
      if(!cardPresent){setState('waiting');stable.current=0;return}
      if(!pipelineHasCapacity(inFlight.current,maxInFlight)){
        stable.current=0;setState('processing');return
      }
      const calibratedNoise=noiseMotion.current/Math.max(1,calibrationCount.current)
      const stableLimit=Math.max(tuning.stableMotion,calibratedNoise*1.6)
      setState('stabilizing')
      if(next.motion<stableLimit)stable.current++;else stable.current=0
      if(stable.current<tuning.stableFrames)return
      capturing.current=true;setState('capturing')
      capturedFrame.current=new Uint8ClampedArray(pixels)
      const full=document.createElement('canvas');full.width=element.videoWidth;full.height=element.videoHeight;full.getContext('2d')!.drawImage(element,0,0)
      full.toBlob(blob=>{
        capturing.current=false;stable.current=0
        if(!blob){calibrate();return}
        // Latch before network work begins. The video loop can observe removal
        // and prepare another physical card while this request is identifying.
        removalGate.current={latched:true,emptyFrames:0,replacementFrames:0};setState('remove')
        const generation=sessionGeneration.current
        inFlight.current++;setPendingCaptures(inFlight.current)
        void onCapture(blob).then(cardCaptured=>{
          if(generation===sessionGeneration.current&&!cardCaptured)calibrate()
        }).catch(e=>{if(generation===sessionGeneration.current)setError(e instanceof Error?e.message:'Scan failed')}).finally(()=>{
          if(generation!==sessionGeneration.current)return
          inFlight.current=Math.max(0,inFlight.current-1);setPendingCaptures(inFlight.current)
        })
      },'image/jpeg',.9)
    },180)
    return()=>clearInterval(timer)
  },[state,error,maxInFlight,onCapture,tuning,calibrate])
  useEffect(()=>stop,[stop])
  return {video,canvas,state,error,metrics,cameras,selectedCamera,pendingCaptures,start,stop,switchCamera,calibrate,setError}
}
