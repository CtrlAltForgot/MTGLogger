import type { LegalGameAction } from '../types'

const automaticTypes=new Set(['advance_phase','pass_priority','resolve','resolve_combat_damage'])
const utilityTypes=new Set(['adjust_life','add_counter','move_zone','create_token','concede'])

export const isMeaningfulGameChoice=(action:LegalGameAction)=>{
  if(automaticTypes.has(action.type)||utilityTypes.has(action.type))return false
  if(['declare_attackers','declare_blockers'].includes(action.type))return !!action.card_ids?.length
  return true
}

export const automaticGameAction=(actions:LegalGameAction[])=>{
  if(actions.some(isMeaningfulGameChoice))return undefined
  return actions.find(action=>action.type==='resolve_combat_damage')
    ||actions.find(action=>action.type==='resolve')
    ||actions.find(action=>action.type==='advance_phase')
    ||actions.find(action=>action.type==='pass_priority')
}

export const automaticActionDelay=(action:LegalGameAction)=>({
  resolve_combat_damage:3400,
  resolve:2800,
  advance_phase:2200,
  pass_priority:1600,
}[action.type]||2200)
