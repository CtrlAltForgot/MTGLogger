import { describe,expect,it } from 'vitest'
import type { LegalGameAction } from '../types'
import { automaticGameAction,isMeaningfulGameChoice } from './automaticAction'

const action=(type:string,extra:Partial<LegalGameAction>={}):LegalGameAction=>({type,...extra})

describe('automatic game actions',()=>{
  it('advances empty phases and empty combat without prompting',()=>{
    expect(automaticGameAction([action('declare_attackers',{card_ids:[]}),action('advance_phase'),action('concede')])?.type).toBe('advance_phase')
    expect(isMeaningfulGameChoice(action('declare_blockers',{card_ids:[]}))).toBe(false)
  })

  it('resolves the stack when the player has no response',()=>{
    expect(automaticGameAction([action('resolve'),action('concede')])?.type).toBe('resolve')
  })

  it('waits whenever the player has a real choice',()=>{
    expect(automaticGameAction([action('resolve'),action('cast',{card_id:'response'})])).toBeUndefined()
    expect(automaticGameAction([action('advance_phase'),action('declare_attackers',{card_ids:['attacker']})])).toBeUndefined()
  })
})
