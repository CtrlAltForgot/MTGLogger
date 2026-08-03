import {describe,expect,it} from 'vitest'
import {analyze} from './useAutoScanner'

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

describe('wide scanner presence analysis',()=>{
  it('detects cards near either side of the scan zone',()=>{
    const baseline=frame()
    expect(analyze(changedRegion(10,50,20,105),undefined,baseline).sceneDifference).toBeGreaterThan(12)
    expect(analyze(changedRegion(110,150,20,105),undefined,baseline).sceneDifference).toBeGreaterThan(12)
  })

  it('ignores the narrow preview gutter outside the scan zone',()=>{
    expect(analyze(changedRegion(0,5,0,120),undefined,frame()).sceneDifference).toBe(0)
  })
})
