import { useCallback, useEffect, useRef, useState } from 'react'
import {advanceRemovalGate,initialRemovalGate,type RemovalGate} from './removalGate'

type State='idle'|'calibrating'|'waiting'|'stabilizing'|'capturing'|'processing'|'remove'
export type ScannerTuning={entryDifference:number;stableMotion:number;stableFrames:number;slingerMode:boolean}
export type DetectionBounds={left:number;top:number;width:number;height:number}
export type ScanArea=DetectionBounds
export type ScannerMetrics={brightness:number;contrast:number;motion:number;sceneDifference:number;bounds?:DetectionBounds}
export type CameraRotation=0|90|180|270
export const defaultTuning:ScannerTuning={entryDifference:12,stableMotion:4,stableFrames:5,slingerMode:false}
const CAMERA_ROTATION_STORAGE_KEY='mtglogger-scanner-camera-rotation'
export const parseCameraRotation=(value:string|null):CameraRotation=>{
  const rotation=Number(value)
  return rotation===90||rotation===180||rotation===270?rotation:0
}
const savedCameraRotation=():CameraRotation=>{
  try{return parseCameraRotation(globalThis.localStorage?.getItem(CAMERA_ROTATION_STORAGE_KEY)??null)}
  catch{return 0}
}
const saveCameraRotation=(rotation:CameraRotation)=>{
  try{globalThis.localStorage?.setItem(CAMERA_ROTATION_STORAGE_KEY,String(rotation))}catch{/* Storage may be disabled. */}
}
export const pipelineHasCapacity=(inFlight:number,maxInFlight:number)=>inFlight<Math.max(1,maxInFlight)
export const plausibleCardBounds=(bounds?:DetectionBounds)=>Boolean(
  bounds&&bounds.height>=50&&bounds.width>=15&&bounds.width<=65&&bounds.height<=100,
)
export function preferredVideoConstraints(deviceId?:string):MediaTrackConstraints{
  return {
    width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:30},
    ...(deviceId?{deviceId:{exact:deviceId}}:{facingMode:'environment'}),
    advanced:[{focusMode:'continuous'} as MediaTrackConstraintSet],
  }
}
export function paddedCaptureBounds(bounds:DetectionBounds,videoWidth:number,videoHeight:number){
  // The motion mask is a presence detector, not a trustworthy card-edge
  // detector. On real captures the high-contrast artwork or rules box can be
  // denser than the physical border and therefore win the fitted window. A
  // small margin then permanently discards the title, footer, or both before
  // recognition sees the frame. Keep a generous safety region; if expanding
  // it no longer resembles a portrait card, return undefined so the caller
  // submits the complete scan area and backend perspective correction finds
  // the physical edge from the high-resolution image.
  const padding=12
  const left=Math.max(0,bounds.left-padding),top=Math.max(0,bounds.top-padding)
  const right=Math.min(100,bounds.left+bounds.width+padding),bottom=Math.min(100,bounds.top+bounds.height+padding)
  const width=right-left,height=bottom-top,aspect=(width*videoWidth)/(height*videoHeight)
  if(aspect<.48||aspect>.92)return undefined
  return {x:Math.round(left/100*videoWidth),y:Math.round(top/100*videoHeight),width:Math.round(width/100*videoWidth),height:Math.round(height/100*videoHeight)}
}

const FRAME_WIDTH=160,FRAME_HEIGHT=120,CALIBRATION_FRAMES=12

function median(values:number[]){
  if(!values.length)return 0
  const sorted=[...values].sort((a,b)=>a-b),middle=Math.floor(sorted.length/2)
  return sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2
}

export function sourceAreaForRotation(area:ScanArea,rotation:CameraRotation){
  const left=area.left/100,top=area.top/100,width=area.width/100,height=area.height/100
  if(rotation===90)return {left:top,top:1-left-width,width:height,height:width}
  if(rotation===180)return {left:1-left-width,top:1-top-height,width,height}
  if(rotation===270)return {left:1-top-height,top:left,width:height,height:width}
  return {left,top,width,height}
}

function drawOriented(
  context:CanvasRenderingContext2D,
  video:HTMLVideoElement,
  source:{x:number;y:number;width:number;height:number},
  rotation:CameraRotation,
  width:number,
  height:number,
){
  context.save()
  if(rotation===90){context.translate(width,0);context.rotate(Math.PI/2);context.drawImage(video,source.x,source.y,source.width,source.height,0,0,height,width)}
  else if(rotation===180){context.translate(width,height);context.rotate(Math.PI);context.drawImage(video,source.x,source.y,source.width,source.height,0,0,width,height)}
  else if(rotation===270){context.translate(0,height);context.rotate(-Math.PI/2);context.drawImage(video,source.x,source.y,source.width,source.height,0,0,height,width)}
  else context.drawImage(video,source.x,source.y,source.width,source.height,0,0,width,height)
  context.restore()
}

export function cardShapedBounds(changed:Uint8Array){
  const columns=FRAME_WIDTH/2,rows=FRAME_HEIGHT/2
  // Search inside the motion mask for the densest MTG-shaped window. A global
  // exposure shift can connect the card to a large patch of table, so the
  // connected component's outer bounds are not necessarily the card's edges.
  // The integral image lets us score every plausible portrait window cheaply.
  const integral=new Uint16Array((columns+1)*(rows+1))
  for(let row=0;row<rows;row++)for(let column=0;column<columns;column++){
    const at=(row+1)*(columns+1)+column+1
    integral[at]=changed[row*columns+column]+integral[at-1]+integral[at-(columns+1)]-integral[at-(columns+2)]
  }
  const countIn=(left:number,top:number,width:number,height:number)=>{
    const stride=columns+1,right=left+width,bottom=top+height
    return integral[bottom*stride+right]-integral[top*stride+right]-integral[bottom*stride+left]+integral[top*stride+left]
  }
  // In the 4:3 analysis bitmap, a portrait card viewed through a normal 16:9
  // feed is about 0.54 as wide as it is tall.
  const targetAspect=(63/88)*(FRAME_WIDTH/FRAME_HEIGHT)/(16/9)
  let windowBest:DetectionBounds|undefined,windowScore=0
  for(let height=Math.ceil(rows*.3);height<=rows;height++){
    const width=Math.max(6,Math.round(height*targetAspect))
    if(width>columns)continue
    for(let top=0;top<=rows-height;top++)for(let left=0;left<=columns-width;left++){
      const count=countIn(left,top,width,height),density=count/(width*height)
      if(count<24||density<.22)continue
      // Density is deliberately squared: a correctly fitted card beats a
      // larger rectangle padded with table pixels.
      const score=density*density*Math.sqrt(width*height)*(height/rows)
      if(score<=windowScore)continue
      windowScore=score
      windowBest={left:left/columns*100,top:top/rows*100,width:width/columns*100,height:height/rows*100}
    }
  }
  if(windowBest)return windowBest

  const seen=new Uint8Array(changed.length)
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
  const baselineDeltas:number[]=[],changed=new Uint8Array(FRAME_WIDTH/2*FRAME_HEIGHT/2)
  // Every visible pixel is a valid scan position. Cards may touch an edge when
  // fed by a Card Slinger, so there are deliberately no invisible gutters.
  for(let y=0;y<FRAME_HEIGHT;y+=2)for(let x=0;x<FRAME_WIDTH;x+=2){
    const index=(y*FRAME_WIDTH+x)*4,luminance=(pixels[index]+pixels[index+1]+pixels[index+2])/3
    brightness+=luminance;variance+=luminance*luminance;samples++
    if(previous)motion+=Math.abs(luminance-(previous[index]+previous[index+1]+previous[index+2])/3)
    if(baseline)baselineDeltas.push(luminance-(baseline[index]+baseline[index+1]+baseline[index+2])/3)
  }
  const sceneChanges:number[]=[]
  if(baselineDeltas.length){
    // Camera auto-exposure changes every table pixel together. Treat that
    // coherent luminance offset as illumination, not as a newly arrived card.
    // A physical card has varied artwork, border and text deltas, producing a
    // much wider residual distribution and therefore receives no correction.
    const exposureOffset=median(baselineDeltas)
    const deviation=median(baselineDeltas.map(delta=>Math.abs(delta-exposureOffset)))
    const correction=deviation<=8?exposureOffset:0
    for(let sample=0;sample<baselineDeltas.length;sample++){
      const difference=Math.abs(baselineDeltas[sample]-correction)
      sceneChanges.push(difference)
      if(difference>=24)changed[sample]=1
    }
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
  const previous=useRef<Uint8ClampedArray|undefined>(undefined),baseline=useRef<Uint8ClampedArray|undefined>(undefined),capturedFrame=useRef<Uint8ClampedArray|undefined>(undefined),calibrationCount=useRef(0),noiseMotion=useRef(0),stable=useRef(0),removalGate=useRef<RemovalGate>(initialRemovalGate()),capturing=useRef(false),inFlight=useRef(0),sessionGeneration=useRef(0)
  const [state,setState]=useState<State>('idle'),[error,setError]=useState<string>(),[metrics,setMetrics]=useState<ScannerMetrics>({brightness:0,contrast:0,motion:0,sceneDifference:0})
  const [pendingCaptures,setPendingCaptures]=useState(0)
  const [cameras,setCameras]=useState<MediaDeviceInfo[]>([]),[selectedCamera,setSelectedCamera]=useState('')
  const [rotation,setRotationState]=useState<CameraRotation>(savedCameraRotation)
  const [scanArea,setScanAreaState]=useState<ScanArea>({left:0,top:0,width:100,height:100})
  const setScanArea=useCallback((area:ScanArea)=>setScanAreaState({left:Math.max(0,Math.min(100,area.left)),top:Math.max(0,Math.min(100,area.top)),width:Math.max(1,Math.min(100-area.left,area.width)),height:Math.max(1,Math.min(100-area.top,area.height))}),[])
  const rotateCamera=useCallback(()=>{setRotationState(current=>{
    const next=((current+90)%360) as CameraRotation
    saveCameraRotation(next)
    return next
  });calibrationCount.current=0;baseline.current=undefined;previous.current=undefined;setState('calibrating')},[])

  const calibrate=useCallback(()=>{baseline.current=undefined;previous.current=undefined;capturedFrame.current=undefined;calibrationCount.current=0;noiseMotion.current=0;stable.current=0;removalGate.current=initialRemovalGate();setState('calibrating')},[])
  const start=useCallback(async(deviceId?:string)=>{try{
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw new Error('Camera access requires HTTPS or localhost. Use an HTTPS reverse proxy when opening MTGLogger from another computer.')
    setError(undefined)
    const stream=await navigator.mediaDevices.getUserMedia({video:preferredVideoConstraints(deviceId),audio:false})
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
      const sourceArea=sourceAreaForRotation(scanArea,rotation)
      const areaX=sourceArea.left*element.videoWidth,areaY=sourceArea.top*element.videoHeight
      const areaWidth=sourceArea.width*element.videoWidth,areaHeight=sourceArea.height*element.videoHeight
      preview.width=FRAME_WIDTH;preview.height=FRAME_HEIGHT;drawOriented(context,element,{x:areaX,y:areaY,width:areaWidth,height:areaHeight},rotation,FRAME_WIDTH,FRAME_HEIGHT)
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
      const calibratedNoise=noiseMotion.current/Math.max(1,calibrationCount.current)
      const stableLimit=Math.max(tuning.stableMotion,calibratedNoise*1.6)
      // A loaded Card Slinger never exposes an empty background. In that mode
      // the user-defined scan area itself is the card slot, so a detailed,
      // stable image is a valid first card even if calibration saw the stack.
      // Scene difference alone is not card presence. As daylight changes, a
      // textured strip of carpet can drift differently from the wooden mat and
      // form a small portrait-shaped mask. Requiring the normal tabletop card
      // to occupy a plausible part of the feed lets that stable local lighting
      // change fall through to baseline adaptation instead of latching forever.
      // Slinger mode uses a user-defined card slot and intentionally retains
      // its contrast-based presence rule.
      const cardPresent=(
        next.sceneDifference>=tuning.entryDifference&&plausibleCardBounds(next.bounds)
      )||(tuning.slingerMode&&next.contrast>=10)
      if(removalGate.current.latched){
        const replacementDifference=capturedFrame.current?analyze(pixels,undefined,capturedFrame.current).sceneDifference:0
        // A direct swap produces consecutive frames that differ substantially
        // from the captured card (usually a hand, then the replacement). Minor
        // exposure drift or an unmoved card remains latched.
        const substantiallyChanged=replacementDifference>=Math.max(24,tuning.entryDifference*1.75)
        // Identical copies have the same final pixels. The physical slide is
        // therefore the proof of replacement: observe multiple motion frames,
        // then require the newly exposed card to settle before rearming.
        const transitionMotion=next.motion>=Math.max(8,stableLimit*2.25)
        const update=advanceRemovalGate(removalGate.current,cardPresent,substantiallyChanged,transitionMotion,next.motion<stableLimit)
        removalGate.current=update.gate
        if(update.rearmed){stable.current=0;setState('waiting')}
        return
      }
      if(!cardPresent){
        // Follow stable changes in room light so a fixed calibration frame
        // cannot slowly drift into a false card detection.
        if(next.motion<stableLimit)baseline.current=new Uint8ClampedArray(pixels)
        setState('waiting');stable.current=0;return
      }
      if(!pipelineHasCapacity(inFlight.current,maxInFlight)){
        stable.current=0;setState('processing');return
      }
      setState('stabilizing')
      if(next.motion<stableLimit)stable.current++;else stable.current=0
      if(stable.current<tuning.stableFrames)return
      capturing.current=true;setState('capturing')
      capturedFrame.current=new Uint8ClampedArray(pixels)
      const oriented=document.createElement('canvas'),orientedWidth=rotation%180?areaHeight:areaWidth,orientedHeight=rotation%180?areaWidth:areaHeight
      oriented.width=Math.round(orientedWidth);oriented.height=Math.round(orientedHeight)
      drawOriented(oriented.getContext('2d')!,element,{x:areaX,y:areaY,width:areaWidth,height:areaHeight},rotation,oriented.width,oriented.height)
      const full=document.createElement('canvas'),localCrop=next.bounds?paddedCaptureBounds(next.bounds,oriented.width,oriented.height):undefined
      const crop=localCrop||{x:0,y:0,width:oriented.width,height:oriented.height}
      full.width=crop.width;full.height=crop.height
      const fullContext=full.getContext('2d')!
      fullContext.drawImage(oriented,crop.x,crop.y,crop.width,crop.height,0,0,crop.width,crop.height)
      full.toBlob(blob=>{
        capturing.current=false;stable.current=0
        if(!blob){setState('waiting');return}
        // Latch before network work begins. The video loop can observe removal
        // and prepare another physical card while this request is identifying.
        removalGate.current={...initialRemovalGate(),latched:true};setState('remove')
        const generation=sessionGeneration.current
        inFlight.current++;setPendingCaptures(inFlight.current)
        void onCapture(blob).catch(e=>{if(generation===sessionGeneration.current)setError(e instanceof Error?e.message:'Scan failed')}).finally(()=>{
          if(generation!==sessionGeneration.current)return
          inFlight.current=Math.max(0,inFlight.current-1);setPendingCaptures(inFlight.current)
        })
      },'image/jpeg',.96)
    },tuning.slingerMode?100:180)
    return()=>clearInterval(timer)
  },[state,error,maxInFlight,onCapture,rotation,scanArea,tuning])
  useEffect(()=>stop,[stop])
  return {video,canvas,state,error,metrics,cameras,selectedCamera,pendingCaptures,rotation,rotateCamera,scanArea,setScanArea,start,stop,switchCamera,calibrate,setError}
}
