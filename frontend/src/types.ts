export type DeckAssignment = { deck_id:string; deck_name:string; quantity:number }
export type Inventory = { id:string; card_name:string; set_code:string; set_name:string; collector_number:string; scryfall_id:string; quantity:number; foil:boolean; language:string; condition:string; purchase_price:number|null; market_price:number|null; storage_location:string; collection_name:string; image_url:string|null; notes:string|null; rarity:string|null; type_line:string|null; color_identity:string; status:string; date_added:string; deck_assignments:DeckAssignment[] }
export type Candidate = { scryfall_id:string; name:string; set_code:string; set_name:string; collector_number:string; image_url:string|null; market_price:number|null; foil_market_price:number|null; finishes:string[]; confidence:number }
export type ScanResult = { disposition:'added'|'confirmation'|'suggestions'|'queued'; confidence:number; inventory:Inventory|null; candidates:Candidate[]; review_id:string|null; message:string }
export type Defaults = { condition:string; foil:boolean; language:string; storage_location:string; collection_name:string; status:string; box_set_code:string|null; auto_add:boolean; deck_id:string|null }
export type DeckEntry = { id:string; quantity:number; inventory:Inventory }
export type Deck = { id:string; name:string; format:string|null; description:string|null; total_cards:number; unique_cards:number; total_value:number; created_at:string; updated_at:string; entries:DeckEntry[] }
export type AvailableCard = { inventory:Inventory; assigned_quantity:number; available_quantity:number }
