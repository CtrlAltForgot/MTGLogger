import type { GamePlayer } from '../types'

export type CommanderDamageTotal={id:string;name:string;amount:number}

export const commanderDamageTotals=(player?:Pick<GamePlayer,'commander_damage'|'commander_damage_names'>):CommanderDamageTotal[]=>
  Object.entries(player?.commander_damage||{})
    .filter(([,amount])=>amount>0)
    .map(([id,amount])=>({id,name:player?.commander_damage_names?.[id]||id,amount}))
    .sort((left,right)=>right.amount-left.amount||left.name.localeCompare(right.name))
