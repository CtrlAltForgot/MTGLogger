import {describe,expect,it} from 'vitest'
import {analyze,paddedCaptureBounds,parseCameraRotation,pipelineHasCapacity,plausibleCardBounds,preferredVideoConstraints,sourceAreaForRotation} from './useAutoScanner'

const width=160,height=120
const frame=(value=20)=>new Uint8ClampedArray(width*height*4).map((_,index)=>index%4===3?255:value)

function changedRegion(x1:number,x2:number,y1:number,y2:number){
  const pixels=frame()
  for(let y=y1;y<y2;y++)for(let x=x1;x<x2;x++){
    const index=(y*width+x)*4
    pixels[index]=pixels[index+1]=pixels[index+2]=180
  }
  return pixels
}

function texturedTable(offset=0){
  const pixels=frame()
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const value=Math.max(0,Math.min(255,28+(x%17)*2+(y%11)+offset)),index=(y*width+x)*4
    pixels[index]=pixels[index+1]=pixels[index+2]=value
  }
  return pixels
}

describe('wide scanner presence analysis',()=>{
  it('detects cards near either side of the scan zone',()=>{
    const baseline=frame()
    expect(analyze(changedRegion(10,50,20,105),undefined,baseline).sceneDifference).toBeGreaterThan(12)
    expect(analyze(changedRegion(110,150,20,105),undefined,baseline).sceneDifference).toBeGreaterThan(12)
  })

  it('detects cards touching the edge of the visible scan zone',()=>{
    expect(analyze(changedRegion(0,45,4,116),undefined,frame()).sceneDifference).toBeGreaterThan(12)
  })

  it('ignores a coherent camera exposure change across an empty table',()=>{
    const result=analyze(texturedTable(18),undefined,texturedTable())
    expect(result.sceneDifference).toBeLessThan(1)
    expect(result.bounds).toBeUndefined()
  })

  it('still detects a card after compensating for changed room lighting',()=>{
    const baseline=texturedTable(),pixels=texturedTable(16)
    for(let y=12;y<112;y++)for(let x=16;x<68;x++){
      const index=(y*width+x)*4,value=(x+y)%18<9?190:55
      pixels[index]=pixels[index+1]=pixels[index+2]=value
    }
    const result=analyze(pixels,undefined,baseline)
    expect(result.sceneDifference).toBeGreaterThan(12)
    expect(result.bounds).toBeDefined()
  })

  it('reports a visible bounding box around the changed card region',()=>{
    const result=analyze(changedRegion(30,90,18,108),undefined,frame())
    expect(result.bounds).toBeDefined()
    expect(result.bounds!.left).toBeGreaterThan(15)
    expect(result.bounds!.width).toBeGreaterThan(30)
    expect(result.bounds!.height).toBeGreaterThan(65)
  })

  it('keeps a distant lighting change out of the card outline',()=>{
    const pixels=changedRegion(12,58,15,108)
    for(let y=40;y<82;y++)for(let x=92;x<154;x++){
      const index=(y*width+x)*4
      pixels[index]=pixels[index+1]=pixels[index+2]=70
    }
    const bounds=analyze(pixels,undefined,frame()).bounds
    expect(bounds).toBeDefined()
    expect(bounds!.left).toBeLessThan(15)
    expect(bounds!.width).toBeLessThan(45)
  })

  it('does not let connected table noise stretch the card outline',()=>{
    const pixels=changedRegion(8,54,8,116)
    // Join a broad table change to the card with a narrow noisy bridge.
    for(let y=54;y<64;y++)for(let x=54;x<142;x++){
      const index=(y*width+x)*4
      pixels[index]=pixels[index+1]=pixels[index+2]=180
    }
    const bounds=analyze(pixels,undefined,frame()).bounds
    expect(bounds).toBeDefined()
    expect(bounds!.left).toBeLessThan(12)
    expect(bounds!.width).toBeLessThan(40)
    expect(bounds!.height).toBeGreaterThan(70)
  })

  it('rejects a small portrait-shaped lighting patch as card presence',()=>{
    expect(plausibleCardBounds({left:2,top:4,width:15,height:38})).toBe(false)
    expect(plausibleCardBounds({left:18,top:8,width:34,height:82})).toBe(true)
  })
})

describe('bounded recognition pipeline',()=>{
  it('requests a high-resolution continuously focused camera feed',()=>{
    const constraints=preferredVideoConstraints('camera-id')
    expect(constraints.width).toEqual({ideal:1920})
    expect(constraints.height).toEqual({ideal:1080})
    expect(constraints.deviceId).toEqual({exact:'camera-id'})
    expect(constraints.advanced).toContainEqual({focusMode:'continuous'})
  })

  it('crops a detected portrait card with a small margin',()=>{
    const crop=paddedCaptureBounds({left:18,top:8,width:34,height:82},1280,720)
    expect(crop).toBeDefined()
    expect(crop!.width).toBeLessThan(1280)
    expect(crop!.height).toBeLessThan(720)
    expect(crop!.x).toBeGreaterThan(0)
  })

  it('uses the fitted card rectangle for an edge-touching capture',()=>{
    const result=analyze(changedRegion(0,52,5,115),undefined,frame())
    const crop=paddedCaptureBounds(result.bounds!,1280,720)
    expect(crop).toBeDefined()
    expect(crop!.x).toBe(0)
    expect(crop!.width/crop!.height).toBeCloseTo(63/88,1)
  })

  it('does not crop a wide table-region false positive',()=>{
    expect(paddedCaptureBounds({left:5,top:30,width:85,height:30},1280,720)).toBeUndefined()
  })

  it('allows one queued automatic capture but caps pipeline backpressure',()=>{
    expect(pipelineHasCapacity(0,2)).toBe(true)
    expect(pipelineHasCapacity(1,2)).toBe(true)
    expect(pipelineHasCapacity(2,2)).toBe(false)
    expect(pipelineHasCapacity(1,1)).toBe(false)
  })
})

describe('camera orientation mapping',()=>{
  const area={left:10,top:20,width:30,height:40}

  it('keeps a full-feed crop full-feed at every orientation',()=>{
    for(const rotation of [0,90,180,270] as const)expect(sourceAreaForRotation({left:0,top:0,width:100,height:100},rotation)).toEqual({left:0,top:0,width:1,height:1})
  })

  it('maps an upright crop back onto the raw camera pixels',()=>{
    expect(sourceAreaForRotation(area,90)).toMatchObject({left:.2,top:expect.closeTo(.6),width:.4,height:.3})
    expect(sourceAreaForRotation(area,180)).toMatchObject({left:expect.closeTo(.6),top:expect.closeTo(.4),width:.3,height:.4})
    expect(sourceAreaForRotation(area,270)).toMatchObject({left:expect.closeTo(.4),top:.1,width:.4,height:.3})
  })

  it('restores only supported saved camera rotations',()=>{
    expect(parseCameraRotation('90')).toBe(90)
    expect(parseCameraRotation('180')).toBe(180)
    expect(parseCameraRotation('270')).toBe(270)
    expect(parseCameraRotation('360')).toBe(0)
    expect(parseCameraRotation('sideways')).toBe(0)
    expect(parseCameraRotation(null)).toBe(0)
  })
})
