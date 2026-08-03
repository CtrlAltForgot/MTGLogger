export type RemovalGate={latched:boolean;emptyFrames:number}
export type RemovalUpdate={gate:RemovalGate;rearmed:boolean}

export function advanceRemovalGate(
  gate:RemovalGate,
  cardPresent:boolean,
  requiredEmptyFrames=3,
):RemovalUpdate{
  if(!gate.latched)return {gate,rearmed:false}
  if(cardPresent)return {gate:{latched:true,emptyFrames:0},rearmed:false}
  const emptyFrames=gate.emptyFrames+1
  if(emptyFrames>=requiredEmptyFrames){
    return {gate:{latched:false,emptyFrames:0},rearmed:true}
  }
  return {gate:{latched:true,emptyFrames},rearmed:false}
}
