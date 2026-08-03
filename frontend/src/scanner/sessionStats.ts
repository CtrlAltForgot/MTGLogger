export type SessionStats={
  scans:number
  added:number
  review:number
  totalMs:number
  lastMs:number
  serverMs:number
  paceIntervals:number
  paceMs:number
  lastAddedAt:number|null
}

export const initialSessionStats:SessionStats={
  scans:0,added:0,review:0,totalMs:0,lastMs:0,serverMs:0,
  paceIntervals:0,paceMs:0,lastAddedAt:null,
}

export function recordSuccessfulAddition(current:SessionStats,now:number):SessionStats{
  const interval=current.lastAddedAt===null?null:now-current.lastAddedAt
  const countsTowardPace=interval!==null&&interval<30_000
  return {
    ...current,
    added:current.added+1,
    lastAddedAt:now,
    paceIntervals:current.paceIntervals+(countsTowardPace?1:0),
    paceMs:current.paceMs+(countsTowardPace?interval:0),
  }
}

export function cardsPerMinute(stats:SessionStats):number|null{
  return stats.paceIntervals>0?60_000*stats.paceIntervals/stats.paceMs:null
}

export function reviewPercentage(stats:SessionStats):number{
  return stats.scans>0?100*stats.review/stats.scans:0
}
