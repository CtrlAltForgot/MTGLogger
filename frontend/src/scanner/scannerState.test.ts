import {describe,expect,it} from 'vitest'
import {advanceRemovalGate,initialRemovalGate} from './removalGate'
import {
  cardsPerMinute,initialSessionStats,recordSuccessfulAddition,reviewPercentage,
} from './sessionStats'

describe('physical-card removal gate',()=>{
  it('never rearms while the same card remains visible',()=>{
    let gate={...initialRemovalGate(),latched:true}
    for(let frame=0;frame<100;frame++){
      const update=advanceRemovalGate(gate,true)
      gate=update.gate
      expect(update.rearmed).toBe(false)
    }
    expect(gate.latched).toBe(true)
  })

  it('requires three consecutive empty frames before another capture',()=>{
    let gate={...initialRemovalGate(),latched:true}
    let update=advanceRemovalGate(gate,false);gate=update.gate
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,false);gate=update.gate
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,true);gate=update.gate
    expect(gate.emptyFrames).toBe(0)
    for(let frame=0;frame<2;frame++){update=advanceRemovalGate(gate,false);gate=update.gate}
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,false)
    expect(update.rearmed).toBe(true)
    expect(update.gate).toEqual(initialRemovalGate())
  })

  it('rearms after a direct physical swap without requiring an empty table',()=>{
    let gate={...initialRemovalGate(),latched:true}
    let update=advanceRemovalGate(gate,true,true,true);gate=update.gate
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,true,true,true);gate=update.gate
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,true,false,false,true);gate=update.gate
    expect(update.rearmed).toBe(false)
    update=advanceRemovalGate(gate,true,false,false,true)
    expect(update.rearmed).toBe(true)
  })

  it('rearms identical cards only after slide motion and a stable replacement',()=>{
    let gate={...initialRemovalGate(),latched:true}
    gate=advanceRemovalGate(gate,true,false,true).gate
    gate=advanceRemovalGate(gate,true,false,true).gate
    gate=advanceRemovalGate(gate,true,false,false,true).gate
    const update=advanceRemovalGate(gate,true,false,false,true)
    expect(update.rearmed).toBe(true)
  })

  it('ignores a one-frame visual disturbance around an unchanged card',()=>{
    let gate={...initialRemovalGate(),latched:true}
    gate=advanceRemovalGate(gate,true,true,true).gate
    const update=advanceRemovalGate(gate,true,false,false,true)
    expect(update.rearmed).toBe(false)
    expect(update.gate.replacementFrames).toBe(0)
  })
})

describe('scanner session throughput',()=>{
  it('starts only after a following success and excludes 30-second gaps',()=>{
    let stats=recordSuccessfulAddition(initialSessionStats,1_000)
    expect(cardsPerMinute(stats)).toBeNull()
    stats=recordSuccessfulAddition(stats,4_000)
    expect(cardsPerMinute(stats)).toBe(20)
    stats=recordSuccessfulAddition(stats,34_000)
    expect(stats.paceIntervals).toBe(1)
    expect(cardsPerMinute(stats)).toBe(20)
    stats=recordSuccessfulAddition(stats,36_000)
    expect(stats.paceIntervals).toBe(2)
    expect(cardsPerMinute(stats)).toBe(24)
  })

  it('reports the completed-scan percentage sent to Review',()=>{
    expect(reviewPercentage({...initialSessionStats,scans:8,review:2})).toBe(25)
    expect(reviewPercentage(initialSessionStats)).toBe(0)
  })
})
