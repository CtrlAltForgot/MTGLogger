import type { GameState } from '../types'

export type NoticeTone='phase'|'good'|'danger'|'stack'
export type TableNotice={id:string;text:string;tone:NoticeTone}

export function presentationNoticeDuration(notice:TableNotice){
  const readingTime=notice.text.trim().split(/\s+/).length*180
  return Math.min(5000,Math.max(notice.tone==='phase'?1800:2400,readingTime))
}

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

export function presentationNotices(previous:GameState,current:GameState,phaseLabel:string):TableNotice[]{
  const seen=new Set(previous.log.map(entry=>entry.id))
  const notices=current.log.filter(entry=>!seen.has(entry.id)).map(entry=>({id:`${current.version}-${entry.id}`,text:entry.message,tone:classifyNoticeTone(entry.message,false)}))
  if(previous.phase!==current.phase||previous.turn!==current.turn){
    notices.push({id:`${current.version}-phase-${current.turn}-${current.phase}`,text:`Turn ${current.turn} · ${phaseLabel}`,tone:'phase'})
  }
  return notices
}
