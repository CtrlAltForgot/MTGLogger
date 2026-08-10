import type { LegalGameAction } from '../types'

const prompts:Record<string,string>={
  keep:'Keep this opening hand or take a mulligan',
  bottom_mulligan_cards:'Choose the cards to put on the bottom',
  declare_attackers:'Choose attackers, then confirm combat',
  declare_blockers:'Assign blockers, then confirm combat',
  order_blockers:'Set the order combat damage will be assigned',
  discard_cards:'Choose the required cards from your hand',
  discard_connive:'Choose which card to discard',
  sacrifice_permanents:'Choose the permanents to sacrifice',
  choose_legendary:'Choose the legendary permanent to keep',
  resolve_combat_damage:'Combat damage is being assigned',
  pass_priority:'You have priority',
  advance_phase:'Ready to move to the next phase',
  resolve:'The top item on the stack is resolving',
}

export function actionPrompt(actions:LegalGameAction[],viewerHasPriority:boolean,automatic:boolean){
  if(!viewerHasPriority)return{title:'Opponent’s priority',detail:'The game will continue when they finish their choice',tone:'waiting' as const}
  const choice=actions.find(action=>prompts[action.type]&&!['pass_priority','advance_phase','resolve','resolve_combat_damage'].includes(action.type))
  if(choice)return{title:'Your choice',detail:prompts[choice.type],tone:'choice' as const}
  if(automatic){
    const action=actions.find(item=>prompts[item.type])
    return{title:'Game in motion',detail:action?prompts[action.type]:'Advancing automatically',tone:'automatic' as const}
  }
  return{title:'Your priority',detail:actions.some(action=>action.type==='pass_priority')?'Play a card or pass priority':'Choose an available action',tone:'priority' as const}
}
