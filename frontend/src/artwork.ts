import {API} from './api'

/** Keep public card images on the app's origin, including the exact back face. */
export function artworkUrl(source:string|null|undefined):string{
  if(!source)return ''
  try{
    const url=new URL(source)
    if(url.protocol!=='https:'||url.hostname!=='cards.scryfall.io'||url.port||url.username||url.password)return source
    const match=url.pathname.match(/^\/(small|normal|large)\/(front|back)\/[0-9a-f]\/[0-9a-f]\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jpg$/i)
    if(match)return `${API}/api/artwork/${match[1].toLowerCase()}/${match[2].toLowerCase()}/${match[3].toLowerCase()}.jpg`
  }catch{/* Local uploads and relative image paths keep their existing URL. */}
  return source
}
