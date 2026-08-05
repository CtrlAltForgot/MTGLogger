export type RemovalGate={
  latched:boolean
  emptyFrames:number
  replacementFrames:number
  transitionFrames:number
  settledFrames:number
}
export type RemovalUpdate={gate:RemovalGate;rearmed:boolean}

export function advanceRemovalGate(
  gate:RemovalGate,
  cardPresent:boolean,
  substantiallyChanged=false,
  transitionMotion=false,
  settled=false,
  requiredEmptyFrames=3,
  requiredReplacementFrames=2,
):RemovalUpdate{
  if(!gate.latched)return {gate,rearmed:false}
  if(cardPresent){
    const replacementFrames=substantiallyChanged?gate.replacementFrames+1:0
    const transitionFrames=transitionMotion?gate.transitionFrames+1:gate.transitionFrames
    const transitionObserved=Math.max(replacementFrames,transitionFrames)>=requiredReplacementFrames
    const settledFrames=transitionObserved&&settled?gate.settledFrames+1:0
    if(settledFrames>=requiredReplacementFrames){
      return {gate:{latched:false,emptyFrames:0,replacementFrames:0,transitionFrames:0,settledFrames:0},rearmed:true}
    }
    return {gate:{latched:true,emptyFrames:0,replacementFrames,transitionFrames,settledFrames},rearmed:false}
  }
  const emptyFrames=gate.emptyFrames+1
  if(emptyFrames>=requiredEmptyFrames){
    return {gate:{latched:false,emptyFrames:0,replacementFrames:0,transitionFrames:0,settledFrames:0},rearmed:true}
  }
  return {gate:{...gate,emptyFrames},rearmed:false}
}

export const initialRemovalGate=():RemovalGate=>({latched:false,emptyFrames:0,replacementFrames:0,transitionFrames:0,settledFrames:0})
