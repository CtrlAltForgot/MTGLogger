import type { GameState } from '../types'

export type NoticeTone='phase'|'good'|'danger'|'stack'

export function classifyNoticeTone(message:string,phaseChanged:boolean):NoticeTone{
  if(phaseChanged)return'phase'
  const lower=message.toLowerCase()
  if(/(wins|gain|created|resolved|entered)/.test(lower))return'good'
  if(/(damage|destroy|dies|lost|countered|sacrifice|discard)/.test(lower))return'danger'
  return'stack'
}

export function visualStateDiff(previous:GameState,current:GameState){
  const oldBattlefield=new Set(previous.players.flatMap(owner=>owner.battlefield.map(card=>card.instance_id)))
  return{
    enteringIds:new Set(current.players.flatMap(owner=>owner.battlefield.map(card=>card.instance_id)).filter(id=>!oldBattlefield.has(id))),
    impactedPlayerIds:new Set(current.players.filter(owner=>{const before=previous.players.find(item=>item.id===owner.id);return !!before&&(before.life!==owner.life||before.poison!==owner.poison)}).map(owner=>owner.id)),
    phaseChanged:previous.phase!==current.phase||previous.turn!==current.turn,
  }
}
