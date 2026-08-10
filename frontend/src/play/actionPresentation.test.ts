import { describe,expect,it } from 'vitest'
import type { LegalGameAction } from '../types'
import { actionPrompt } from './actionPresentation'

const actions=(...types:string[])=>types.map(type=>({type}) as LegalGameAction)

describe('actionPrompt',()=>{
  it('foregrounds combat choices over passing',()=>expect(actionPrompt(actions('pass_priority','declare_attackers'),true,false)).toEqual({title:'Your choice',detail:'Choose attackers, then confirm combat',tone:'choice'}))
  it('explains automatic progression',()=>expect(actionPrompt(actions('advance_phase'),true,true)).toEqual({title:'Game in motion',detail:'Ready to move to the next phase',tone:'automatic'}))
  it('makes opponent priority explicit',()=>expect(actionPrompt(actions(),false,false).title).toBe('Opponent’s priority'))
})
