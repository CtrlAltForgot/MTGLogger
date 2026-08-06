import type {DeckFormatSuggestions} from '../types'

const CASUAL_FORMAT='Casual / Kitchen Table'

export function recommendedDeckFormat(result:DeckFormatSuggestions){
  return result.suggestions.find(item=>item.confidence==='high')?.format
    ??result.suggestions.find(item=>item.format!==CASUAL_FORMAT)?.format
    ??result.suggestions[0]?.format
}
