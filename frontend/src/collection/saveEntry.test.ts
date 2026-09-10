import {describe,expect,it,vi} from 'vitest'
import {saveEntry} from './saveEntry'
import type {Inventory} from '../types'
import type {request} from '../api'

const entry={id:'source',foil:false,quantity:3,condition:'near_mint',language:'en',storage_location:'Unsorted',market_price:1,purchase_price:null,notes:null,updated_at:'2026-09-09T00:00:00Z'} as Inventory

describe('saving collection edits',()=>{
  it('sends one request for a foil change and includes the version being edited',async()=>{
    const send=vi.fn().mockResolvedValue({...entry,id:'foil',foil:true})
    await saveEntry(entry,{...entry},2,true,'near_mint',send as typeof request)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][0]).toBe('/inventory/source/move-copies')
    expect(JSON.parse(send.mock.calls[0][1].body)).toEqual({quantity:2,foil:true,condition:'near_mint',expected_updated_at:entry.updated_at})
  })

  it('does not submit a stale price when only notes changed',async()=>{
    const send=vi.fn().mockResolvedValue({...entry,notes:'Box 2'})
    await saveEntry(entry,{...entry,notes:'Box 2'},0,false,'near_mint',send as typeof request)
    expect(JSON.parse(send.mock.calls[0][1].body)).toEqual({notes:'Box 2',expected_updated_at:entry.updated_at})
  })

  it('uses the committed metadata version for the following copy move',async()=>{
    const updated={...entry,notes:'New note',updated_at:'2026-09-09T00:00:01Z'}
    const send=vi.fn().mockResolvedValueOnce(updated).mockResolvedValueOnce({...updated,id:'foil'})
    await saveEntry(entry,{...entry,notes:'New note'},1,true,'near_mint',send as typeof request)
    expect(send).toHaveBeenCalledTimes(2)
    expect(JSON.parse(send.mock.calls[1][1].body).expected_updated_at).toBe(updated.updated_at)
  })

  it('does not move copies if the metadata save failed',async()=>{
    const send=vi.fn().mockRejectedValue(new Error('This entry changed'))
    await expect(saveEntry(entry,{...entry,notes:'New note'},1,true,'near_mint',send as typeof request)).rejects.toThrow('This entry changed')
    expect(send).toHaveBeenCalledTimes(1)
  })
})
