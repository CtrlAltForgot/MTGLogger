import {describe,expect,it} from 'vitest'
import type {Candidate} from '../types'
import {candidateMarketPrice,candidatePriceColor,displayCandidatePrice} from './ReviewQueue'

const candidate=(prices:Partial<Pick<Candidate,'market_price'|'foil_market_price'|'finishes'>>={}):Candidate=>({
  scryfall_id:'printing',name:'Test Card',set_code:'tst',set_name:'Test Set',collector_number:'1',
  image_url:null,market_price:2.5,foil_market_price:25,finishes:['nonfoil','foil'],language:'en',confidence:90,
  ...prices,
})

describe('review candidate prices',()=>{
  it('uses the finish selected for the pending review',()=>{
    expect(displayCandidatePrice(candidate(),false)).toBe('$2.50')
    expect(displayCandidatePrice(candidate(),true)).toBe('$25.00')
  })

  it('uses foil pricing for foil-only printings and falls back to nonfoil pricing',()=>{
    expect(candidateMarketPrice(candidate({finishes:['foil']}),false)).toBe(25)
    expect(candidateMarketPrice(candidate({finishes:['foil'],foil_market_price:null}),false)).toBe(2.5)
  })

  it('does not present missing prices as free cards',()=>{
    expect(displayCandidatePrice(candidate({market_price:null}),false)).toBe('Price unavailable')
  })

  it('makes valuable candidates increasingly prominent',()=>{
    expect(candidatePriceColor(candidate({market_price:19.99}),false)).toBe('primary.main')
    expect(candidatePriceColor(candidate({market_price:20}),false)).toBe('warning.main')
    expect(candidatePriceColor(candidate({market_price:100}),false)).toBe('error.main')
  })
})
