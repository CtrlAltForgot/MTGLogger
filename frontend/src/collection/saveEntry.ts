import {request} from '../api'
import type {Inventory} from '../types'

const fields=['language','condition','storage_location','market_price','purchase_price','notes'] as const

/** Send only edited fields, and use the returned version for a copy split. */
export async function saveEntry(original:Inventory,edited:Inventory,copies:number,foil:boolean,condition:string,send= request){
  const changes=Object.fromEntries(fields.filter(field=>edited[field]!==original[field]).map(field=>[field,edited[field]]))
  let saved=original
  if(Object.keys(changes).length){
    saved=await send<Inventory>(`/inventory/${original.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...changes,expected_updated_at:original.updated_at})})
  }
  if(copies&&(foil!==saved.foil||condition!==saved.condition)){
    saved=await send<Inventory>(`/inventory/${original.id}/move-copies`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:copies,foil,condition,expected_updated_at:saved.updated_at})})
  }
  return saved
}
