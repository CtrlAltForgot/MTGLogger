import {describe,expect,it} from 'vitest'
import {artworkUrl} from './artwork'

const id='63e08eb1-de6e-4f31-a523-86b1e013b1ca'

describe('card artwork delivery',()=>{
  it('serves Scryfall artwork from the app origin',()=>{
    expect(artworkUrl(`https://cards.scryfall.io/normal/front/6/3/${id}.jpg?123`)).toBe(`/api/artwork/normal/front/${id}.jpg`)
  })
  it('preserves the selected face and image size',()=>{
    expect(artworkUrl(`https://cards.scryfall.io/large/back/6/3/${id}.jpg`)).toBe(`/api/artwork/large/back/${id}.jpg`)
  })
  it.each(['/api/decks/a/image','blob:http://localhost/example','https://example.com/card.jpg','https://cards.scryfall.io.example.com/card.jpg'])("preserves other image sources: %s",source=>{
    expect(artworkUrl(source)).toBe(source)
  })
  it('handles missing artwork',()=>{
    expect(artworkUrl(undefined)).toBe('')
    expect(artworkUrl(null)).toBe('')
  })
})
