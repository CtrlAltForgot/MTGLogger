import { describe, expect, it } from 'vitest'
import { formatScannedAt } from './Collection'

describe('formatScannedAt',()=>{
  it('includes both the local date and time',()=>{
    const formatted=formatScannedAt('2026-08-07T18:54:48.355152+00:00')
    expect(formatted).toMatch(/2026/)
    expect(formatted).toMatch(/\d{1,2}:\d{2}/)
  })
})
