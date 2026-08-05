import { useCallback, useEffect, useRef, useState } from 'react'
import {advanceRemovalGate,type RemovalGate} from './removalGate'

type State='idle'|'calibrating'|'waiting'|'stabilizing'|'capturing'|'processing'|'remove'
export type ScannerTuning={entryDifference:number;stableMotion:number;stableFrames:number}
export type DetectionBounds={left:number;top:number;width:number;height:number}
export type ScanArea=DetectionBounds
export type ScannerMetrics={brightness:number;contrast:number;motion:number;sceneDifference:number;bounds?:DetectionBounds}
export const defaultTuning:ScannerTuning={entryDifference:12,stableMotion:4,stableFrames:5}
export const pipelineHasCapacity=(inFlight:number,maxInFlight:number)=>inFlight<Math.max(1,maxInFlight)
export function paddedCaptureBounds(bounds:DetectionBounds,videoWidth:number,videoHeight:number){
  const padding=1
  const left=Math.max(0,bounds.left-padding),top=Math.max(0,bounds.top-padding)
  const right=Math.min(100,bounds.left+bounds.width+padding),bottom=Math.min(100,bounds.top+bounds.height+padding)
  const width=right-left,height=bottom-top,aspect=(width*videoWidth)/(height*videoHeight)
  if(aspect<.48||aspect>.92)return undefined
  return {x:Math.round(left/100*videoWidth),y:Math.round(top/100*videoHeight),width:Math.round(width/100*videoWidth),height:Math.round(height/100*videoHeight)}
}

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
    // Fit the changed silhouette to a physical card rectangle. Reflections and
    // pale borders often leave holes in the motion mask, so its raw component
    // is not itself a trustworthy rectangle. This fitted rectangle is shared
    // by the visible outline and the submitted recognition crop.
    // The lightweight analysis canvas is 4:3 while the camera feed is normally
    // 16:9, so a physical 63:88 card occupies a ~0.537-wide analysis box.
    const targetAspect=(63/88)*(FRAME_WIDTH/FRAME_HEIGHT)/(16/9)
    let fittedWidth=width,fittedHeight=height
    if(width/height<targetAspect)fittedWidth=height*targetAspect
    else fittedHeight=width/targetAspect
    const centerColumn=(minColumn+maxColumn+1)/2*2,centerRow=(minRow+maxRow+1)/2*2
    const fittedLeft=Math.max(0,Math.min(FRAME_WIDTH-fittedWidth,centerColumn-fittedWidth/2))
    const fittedTop=Math.max(0,Math.min(FRAME_HEIGHT-fittedHeight,centerRow-fittedHeight/2))
    best={left:fittedLeft/FRAME_WIDTH*100,top:fittedTop/FRAME_HEIGHT*100,width:fittedWidth/FRAME_WIDTH*100,height:fittedHeight/FRAME_HEIGHT*100}
  }
  return best
}

export function analyze(pixels:Uint8ClampedArray,previous?:Uint8ClampedArray,baseline?:Uint8ClampedArray){
  let brightness=0,variance=0,motion=0,sceneDifference=0,samples=0
  const sceneChanges:number[]=[],changed=new Uint8Array(FRAME_WIDTH/2*FRAME_HEIGHT/2)
  // Every visible pixel is a valid scan position. Cards may touch an edge when
  // fed by a Card Slinger, so there are deliberately no invisible gutters.
  for(let y=0;y<FRAME_HEIGHT;y+=2)for(let x=0;x<FRAME_WIDTH;x+=2){
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
  const [scanArea,setScanAreaState]=useState<ScanArea>({left:0,top:0,width:100,height:100})
  const setScanArea=useCallback((area:ScanArea)=>setScanAreaState({left:Math.max(0,Math.min(100,area.left)),top:Math.max(0,Math.min(100,area.top)),width:Math.max(1,Math.min(100-area.left,area.width)),height:Math.max(1,Math.min(100-area.top,area.height))}),[])

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
      const areaX=scanArea.left/100*element.videoWidth,areaY=scanArea.top/100*element.videoHeight
      const areaWidth=scanArea.width/100*element.videoWidth,areaHeight=scanArea.height/100*element.videoHeight
      preview.width=FRAME_WIDTH;preview.height=FRAME_HEIGHT;context.drawImage(element,areaX,areaY,areaWidth,areaHeight,0,0,FRAME_WIDTH,FRAME_HEIGHT)
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
      const next=analyze(pixels,previous.current,baseline.current);previous.current=new Uint8ClampedArray(pixels)
      setMetrics({...next,bounds:next.bounds?{left:scanArea.left+next.bounds.left*scanArea.width/100,top:scanArea.top+next.bounds.top*scanArea.height/100,width:next.bounds.width*scanArea.width/100,height:next.bounds.height*scanArea.height/100}:undefined})
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
      const full=document.createElement('canvas'),localCrop=next.bounds?paddedCaptureBounds(next.bounds,areaWidth,areaHeight):undefined
      const crop=localCrop?{x:Math.round(areaX+localCrop.x),y:Math.round(areaY+localCrop.y),width:localCrop.width,height:localCrop.height}:{x:Math.round(areaX),y:Math.round(areaY),width:Math.round(areaWidth),height:Math.round(areaHeight)}
      full.width=crop.width;full.height=crop.height
      const fullContext=full.getContext('2d')!
      fullContext.drawImage(element,crop.x,crop.y,crop.width,crop.height,0,0,crop.width,crop.height)
      full.toBlob(blob=>{
        capturing.current=false;stable.current=0
        if(!blob){setState('waiting');return}
        // Latch before network work begins. The video loop can observe removal
        // and prepare another physical card while this request is identifying.
        removalGate.current={latched:true,emptyFrames:0,replacementFrames:0};setState('remove')
        const generation=sessionGeneration.current
        inFlight.current++;setPendingCaptures(inFlight.current)
        void onCapture(blob).catch(e=>{if(generation===sessionGeneration.current)setError(e instanceof Error?e.message:'Scan failed')}).finally(()=>{
          if(generation!==sessionGeneration.current)return
          inFlight.current=Math.max(0,inFlight.current-1);setPendingCaptures(inFlight.current)
        })
      },'image/jpeg',.9)
    },180)
    return()=>clearInterval(timer)
  },[state,error,maxInFlight,onCapture,scanArea,tuning])
  useEffect(()=>stop,[stop])
  return {video,canvas,state,error,metrics,cameras,selectedCamera,pendingCaptures,scanArea,setScanArea,start,stop,switchCamera,calibrate,setError}
}
