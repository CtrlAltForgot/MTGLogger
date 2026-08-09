import { describe,expect,it } from 'vitest'
import { gameOutcomeCopy } from './gameOutcome'

describe('gameOutcomeCopy',()=>{
  it('renders a simultaneous loss as a draw without inventing a winner',()=>expect(gameOutcomeCopy({winner_id:null,result_reason:'draw',turn:8},'player')).toEqual({title:'Draw',message:'The game ended in a draw on turn 8.'}))
  it('explains decking victories',()=>expect(gameOutcomeCopy({winner_id:'player',result_reason:'empty_library',turn:12},'player','You')).toEqual({title:'Victory!',message:'You won after an opponent tried to draw from an empty library on turn 12.'}))
})
