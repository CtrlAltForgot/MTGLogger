import { describe,expect,it } from 'vitest'
import type { Game } from '../types'
import { acceptMonotonicGame } from './multiplayerState'

const game=(id:string,version:number)=>({id,state:{version}}) as Game

describe('acceptMonotonicGame',()=>{
  it('rejects a stale poll that arrives after a newer action response',()=>{
    const current=game('match',12),stale=game('match',11)
    expect(acceptMonotonicGame(current,stale)).toBe(current)
  })

  it('accepts equal/newer snapshots and a different selected game',()=>{
    const current=game('match',12),newer=game('match',13),other=game('other',1)
    expect(acceptMonotonicGame(current,newer)).toBe(newer)
    expect(acceptMonotonicGame(current,other)).toBe(other)
  })
})
