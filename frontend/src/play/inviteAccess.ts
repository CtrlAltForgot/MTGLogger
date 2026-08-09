export type InviteAccess={code:string;token:string;shouldScrub:boolean}

export function resolveInviteAccess(search:string,hash:string,storedToken=''):InviteAccess{
  const query=new URLSearchParams(search),fragment=new URLSearchParams(hash.replace(/^#/,'')),code=query.get('invite')||''
  const fragmentToken=fragment.get('token')||'',queryToken=query.get('token')||''
  return {code,token:fragmentToken||queryToken||storedToken,shouldScrub:!!code&&(!!fragmentToken||!!queryToken)}
}

export function guestTokenKey(code:string){return `mtglogger-play-guest-${code}`}

export function scrubbedInvitePath(pathname:string,search:string):string{
  const query=new URLSearchParams(search);query.delete('token');const value=query.toString()
  return `${pathname}${value?`?${value}`:''}`
}

export function buildInviteUrl(origin:string,pathname:string,code:string,token:string):string{
  const query=new URLSearchParams({page:'play',invite:code}),fragment=new URLSearchParams({token})
  return `${origin}${pathname}?${query}#${fragment}`
}

export function normalizeInviteUrl(value:string):string{
  if(!value)return value
  try{
    const url=new URL(value),access=resolveInviteAccess(url.search,url.hash)
    return access.code&&access.token?buildInviteUrl(url.origin,url.pathname,access.code,access.token):value
  }catch{return value}
}
