import type { Game } from '../types'

export const acceptMonotonicGame=(current:Game|undefined,incoming:Game):Game=>
  !current||current.id!==incoming.id||incoming.state.version>=current.state.version?incoming:current
