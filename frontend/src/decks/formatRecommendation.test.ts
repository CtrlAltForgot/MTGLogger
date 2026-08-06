import {describe,expect,it} from 'vitest'
import {recommendedDeckFormat} from './formatRecommendation'

describe('deck format recommendation',()=>{
  it('selects the first legality and structure match',()=>{
    expect(recommendedDeckFormat({complete_deck:true,card_count:104,suggestions:[
      {format:'Modern',confidence:'high',reasons:[]},
      {format:'Legacy',confidence:'high',reasons:[]},
      {format:'Commander',confidence:'possible',reasons:[]},
    ]})).toBe('Modern')
  })

  it('prefers a supported possibility over the casual fallback',()=>{
    expect(recommendedDeckFormat({complete_deck:false,card_count:42,suggestions:[
      {format:'Commander',confidence:'possible',reasons:[]},
      {format:'Casual / Kitchen Table',confidence:'possible',reasons:[]},
    ]})).toBe('Commander')
  })

  it('leaves an empty result unselected',()=>{
    expect(recommendedDeckFormat({complete_deck:false,card_count:0,suggestions:[]})).toBeUndefined()
  })
})
