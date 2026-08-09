import { describe,expect,it } from 'vitest'
import { commanderDamageTotals } from './commanderDamage'

describe('commanderDamageTotals',()=>{
  it('keeps each commander separate and orders the greatest threat first',()=>{
    expect(commanderDamageTotals({commander_damage:{a:8,b:19,zero:0},commander_damage_names:{a:'Partner One',b:'Partner Two',zero:'None'}})).toEqual([
      {id:'b',name:'Partner Two',amount:19},
      {id:'a',name:'Partner One',amount:8},
    ])
  })

  it('keeps legacy saved-game totals visible without a name map',()=>{
    expect(commanderDamageTotals({commander_damage:{player:12}})).toEqual([{id:'player',name:'player',amount:12}])
  })
})
