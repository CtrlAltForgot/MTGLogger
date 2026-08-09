import type { GameState } from '../types'

export const gameOutcomeCopy=(state:Pick<GameState,'winner_id'|'result_reason'|'turn'>,viewerId:string,winnerName?:string)=>{
  if(!state.winner_id)return {title:'Draw',message:`The game ended in a draw on turn ${state.turn}.`}
  const reason=state.result_reason==='empty_library'?' after an opponent tried to draw from an empty library':state.result_reason==='concession'?' by concession':''
  return {title:state.winner_id===viewerId?'Victory!':'Game over',message:`${winnerName||'The winner'} won${reason} on turn ${state.turn}.`}
}
