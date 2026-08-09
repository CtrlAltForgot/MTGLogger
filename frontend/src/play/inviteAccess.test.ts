import { describe,expect,it } from 'vitest'
import { buildInviteUrl,normalizeInviteUrl,resolveInviteAccess,scrubbedInvitePath } from './inviteAccess'

describe('private game invite access',()=>{
  it('reads a fragment secret without putting it in the server-visible query',()=>{
    expect(resolveInviteAccess('?page=play&invite=room','#token=fragment-secret','stored')).toEqual({code:'room',token:'fragment-secret',shouldScrub:true})
    expect(buildInviteUrl('https://cards.test','/app','room','fragment-secret')).toBe('https://cards.test/app?page=play&invite=room#token=fragment-secret')
  })

  it('migrates legacy query secrets and removes them from the visible location',()=>{
    expect(resolveInviteAccess('?page=play&invite=room&token=legacy-secret','','')).toEqual({code:'room',token:'legacy-secret',shouldScrub:true})
    expect(scrubbedInvitePath('/app','?page=play&invite=room&token=legacy-secret')).toBe('/app?page=play&invite=room')
  })

  it('resumes from tab storage after the address has been scrubbed',()=>{
    expect(resolveInviteAccess('?page=play&invite=room','','stored-secret')).toEqual({code:'room',token:'stored-secret',shouldScrub:false})
  })

  it('normalizes previously saved query-token links before they are copied',()=>{
    expect(normalizeInviteUrl('https://cards.test/app?page=play&invite=room&token=old')).toBe('https://cards.test/app?page=play&invite=room#token=old')
  })
})
