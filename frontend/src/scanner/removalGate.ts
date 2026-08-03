export type RemovalGate={latched:boolean;emptyFrames:number;replacementFrames:number}
export type RemovalUpdate={gate:RemovalGate;rearmed:boolean}

export function advanceRemovalGate(
  gate:RemovalGate,
  cardPresent:boolean,
  substantiallyChanged=false,
  requiredEmptyFrames=3,
  requiredReplacementFrames=2,
):RemovalUpdate{
  if(!gate.latched)return {gate,rearmed:false}
  if(cardPresent){
    const replacementFrames=substantiallyChanged?gate.replacementFrames+1:0
    if(replacementFrames>=requiredReplacementFrames){
      return {gate:{latched:false,emptyFrames:0,replacementFrames:0},rearmed:true}
    }
    return {gate:{latched:true,emptyFrames:0,replacementFrames},rearmed:false}
  }
  const emptyFrames=gate.emptyFrames+1
  if(emptyFrames>=requiredEmptyFrames){
    return {gate:{latched:false,emptyFrames:0,replacementFrames:0},rearmed:true}
  }
  return {gate:{latched:true,emptyFrames,replacementFrames:gate.replacementFrames},rearmed:false}
}
