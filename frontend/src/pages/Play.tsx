import { useEffect, useMemo, useState } from 'react'
import { AutoAwesome, Bolt, ContentCopy, Delete, Favorite, History, People, PlayArrow, Refresh, Replay, Shield, SmartToy, SportsEsports } from '@mui/icons-material'
import { Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, Grid, IconButton, MenuItem, Paper, Stack, Switch, TextField, Tooltip, Typography } from '@mui/material'
import { request } from '../api'
import type { Deck, Game, GameCard, GamePlayer, GameTarget } from '../types'

const phases=[['beginning','Untap & draw'],['precombat_main','Main 1'],['combat','Combat'],['postcombat_main','Main 2'],['ending','End turn']]

function ZoneCard({card,legal,onClick,attacking=false,blocked=false}:{card:GameCard;legal?:boolean;onClick?:()=>void;attacking?:boolean;blocked?:boolean}){
  return <Tooltip title={<><b>{card.name}</b><br/>{card.type_line}<br/>{card.oracle_text}</>} placement="top" arrow>
    <Box className={`play-card ${card.tapped?'is-tapped':''} ${legal?'is-legal':''} ${attacking?'is-attacking':''}`} onClick={onClick} role={legal?'button':undefined} aria-label={card.name}>
      <Box component="img" src={card.image_url||''} alt={card.name}/>
      {card.damage>0&&<Chip className="play-counter" size="small" color="error" label={`${card.damage} dmg`}/>} {blocked&&<Chip className="play-blocked" size="small" label="Blocked"/>}
    </Box>
  </Tooltip>
}

function HiddenHand({count=0}:{count?:number}){return <Stack direction="row" justifyContent="center" className="hidden-hand">{Array.from({length:Math.min(count,12)},(_,index)=><Box key={index} className="card-back" sx={{ml:index?-4:0}}/>)}</Stack>}

function Battlefield({player,game}:{player:GamePlayer;game:Game}){
  const attackers=new Set(game.state.combat.attackers),blocked=new Set(Object.values(game.state.combat.blocks))
  const creatures=player.battlefield.filter(card=>!card.type_line.includes('Land')),lands=player.battlefield.filter(card=>card.type_line.includes('Land'))
  return <Box className="battlefield-zone">
    <Stack direction="row" className="permanent-row" justifyContent="center">{creatures.map(card=><ZoneCard key={card.instance_id} card={card} attacking={attackers.has(card.instance_id)} blocked={blocked.has(card.instance_id)}/>)}</Stack>
    <Stack direction="row" className="land-row" justifyContent="center">{lands.map(card=><ZoneCard key={card.instance_id} card={card}/>)}</Stack>
  </Box>
}

export default function Play(){
  const [decks,setDecks]=useState<Deck[]>([]),[games,setGames]=useState<Game[]>([]),[game,setGame]=useState<Game>(),[busy,setBusy]=useState(false),[error,setError]=useState<string>()
  const [setup,setSetup]=useState({name:'Game vs Bot',player_deck_id:'',opponent_deck_id:'',bot_difficulty:'standard',opponent_type:'bot',play_first:true})
  const [inviteUrl,setInviteUrl]=useState(''),[targetChoice,setTargetChoice]=useState<{cardId:string;targets:GameTarget[]}>()
  const query=new URLSearchParams(location.search),inviteCode=query.get('invite'),inviteToken=query.get('token'),isGuest=!!inviteCode&&!!inviteToken,viewerId=isGuest?'bot':'player',opponentId=isGuest?'player':'bot'
  const load=async()=>{const [deckData,gameData]=await Promise.all([request<Deck[]>('/decks'),request<Game[]>('/play')]);setDecks(deckData.filter(deck=>deck.total_cards>0));setGames(gameData);setSetup(current=>({...current,player_deck_id:current.player_deck_id||deckData[0]?.id||'',opponent_deck_id:current.opponent_deck_id||deckData[1]?.id||deckData[0]?.id||''}))}
  const gamePath=isGuest?`/play/invite/${inviteCode}/state?token=${encodeURIComponent(inviteToken||'')}`:game?`/play/${game.id}`:''
  useEffect(()=>{if(isGuest){request<Game>(`/play/invite/${inviteCode}/state?token=${encodeURIComponent(inviteToken||'')}`).then(setGame).catch(e=>setError(e.message))}else void load().catch(e=>setError(e.message))},[])
  useEffect(()=>{if(!game||game.opponent_type!=='human')return;const timer=setInterval(()=>request<Game>(gamePath).then(setGame).catch(()=>undefined),1800);return()=>clearInterval(timer)},[game?.id,gamePath])
  const act=async(action:Record<string,unknown>)=>{if(!game)return;setBusy(true);setError(undefined);try{const path=isGuest?`/play/invite/${inviteCode}/actions?token=${encodeURIComponent(inviteToken||'')}`:`/play/${game.id}/actions`;setGame(await request<Game>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action)}))}catch(e){setError(e instanceof Error?e.message:'The action could not be completed')}finally{setBusy(false)}}
  const create=async()=>{setBusy(true);setError(undefined);try{const created=await request<Game>('/play',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(setup)});setGame(created);if(created.invite_code&&created.invite_token){const url=`${location.origin}${location.pathname}?page=play&invite=${encodeURIComponent(created.invite_code)}&token=${encodeURIComponent(created.invite_token)}`;localStorage.setItem(`mtglogger-invite-${created.id}`,url);setInviteUrl(url)}}catch(e){setError(e instanceof Error?e.message:'Could not start the game')}finally{setBusy(false)}}
  const undo=async()=>{if(!game||isGuest)return;setBusy(true);try{setGame(await request<Game>(`/play/${game.id}/undo`,{method:'POST'}))}catch(e){setError(e instanceof Error?e.message:'Nothing to undo')}finally{setBusy(false)}}
  const remove=async(id:string)=>{await request(`/play/${id}`,{method:'DELETE'});if(game?.id===id)setGame(undefined);await load()}
  const legal=(type:string,cardId?:string)=>game?.legal_actions.some(action=>action.type===type&&(!cardId||action.card_id===cardId))
  const player=game?.state.players.find(item=>item.id===viewerId),bot=game?.state.players.find(item=>item.id===opponentId)
  const phase=phases.find(([key])=>key===game?.state.phase)?.[1]
  const attackAction=game?.legal_actions.find(action=>action.type==='declare_attackers'),blockAction=game?.legal_actions.find(action=>action.type==='declare_blockers')
  const humanActive=game?.state.active_player_id===viewerId
  const winner=game?.state.players.find(item=>item.id===game.state.winner_id)
  const gameTitle=useMemo(()=>decks.find(deck=>deck.id===game?.player_deck_id)?.name,[decks,game])

  if(!game)return <Box>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" gap={2} mb={3}><Box><Typography variant="h3">Play</Typography><Typography color="text.secondary">Play your physical collection virtually with database artwork and automatic rules assistance.</Typography></Box><Chip icon={<AutoAwesome/>} color="primary" label="Rules engine · Bots · Private invites" sx={{alignSelf:'flex-start'}}/></Stack>
    {error&&<Alert severity="error" sx={{mb:2}}>{error}</Alert>}
    <Grid container spacing={2.5}>
      <Grid size={{xs:12,md:7}}><Card variant="outlined"><CardContent><Stack direction="row" spacing={1} alignItems="center" mb={2}>{setup.opponent_type==='bot'?<SmartToy color="primary"/>:<People color="primary"/>}<Typography variant="h5">New {setup.opponent_type==='bot'?'bot':'private'} game</Typography></Stack><Grid container spacing={2}>
        <Grid size={{xs:12}}><TextField fullWidth label="Game name" value={setup.name} onChange={e=>setSetup({...setup,name:e.target.value})}/></Grid>
        <Grid size={{xs:12,sm:6}}><TextField select fullWidth label="Your deck" value={setup.player_deck_id} onChange={e=>setSetup({...setup,player_deck_id:e.target.value})}>{decks.map(deck=><MenuItem key={deck.id} value={deck.id}>{deck.name} · {deck.total_cards} cards</MenuItem>)}</TextField></Grid>
        <Grid size={{xs:12,sm:6}}><TextField select fullWidth label={setup.opponent_type==='bot'?'Bot deck':'Guest deck'} value={setup.opponent_deck_id} onChange={e=>setSetup({...setup,opponent_deck_id:e.target.value})}>{decks.map(deck=><MenuItem key={deck.id} value={deck.id}>{deck.name} · {deck.total_cards} cards</MenuItem>)}</TextField></Grid>
        <Grid size={{xs:12,sm:6}}><TextField select fullWidth label="Opponent" value={setup.opponent_type} onChange={e=>setSetup({...setup,opponent_type:e.target.value,name:e.target.value==='bot'?'Game vs Bot':'Private game'})}><MenuItem value="bot">Bot opponent</MenuItem><MenuItem value="human">Invite a player</MenuItem></TextField></Grid>
        {setup.opponent_type==='bot'&&<Grid size={{xs:12,sm:6}}><TextField select fullWidth label="Bot difficulty" value={setup.bot_difficulty} onChange={e=>setSetup({...setup,bot_difficulty:e.target.value})}><MenuItem value="beginner">Beginner · forgiving</MenuItem><MenuItem value="standard">Standard · efficient</MenuItem><MenuItem value="expert">Expert · strongest lines</MenuItem></TextField></Grid>}
        <Grid size={{xs:12,sm:6}}><FormControlLabel control={<Switch checked={setup.play_first} onChange={e=>setSetup({...setup,play_first:e.target.checked})}/>} label="I play first"/></Grid>
      </Grid><Button sx={{mt:2}} variant="contained" size="large" startIcon={busy?<CircularProgress size={18}/>:<PlayArrow/>} disabled={busy||!setup.player_deck_id||!setup.opponent_deck_id} onClick={()=>void create()}>Start game</Button></CardContent></Card></Grid>
      <Grid size={{xs:12,md:5}}><Card variant="outlined"><CardContent><Stack direction="row" spacing={1} alignItems="center" mb={2}><History/><Typography variant="h5">Saved games</Typography></Stack>{!games.length?<Typography color="text.secondary">Your games will automatically save here.</Typography>:games.map(saved=><Stack key={saved.id} direction="row" alignItems="center" py={1} borderBottom="1px solid" borderColor="divider"><Box flex={1}><Typography fontWeight={900}>{saved.name}</Typography><Typography variant="caption" color="text.secondary">Turn {saved.state.turn} · {saved.status} · {saved.opponent_type==='bot'?`${saved.bot_difficulty} bot`:'private game'}</Typography></Box><Button onClick={()=>{setGame(saved);setInviteUrl(localStorage.getItem(`mtglogger-invite-${saved.id}`)||'')}}>Resume</Button><IconButton aria-label="Delete game" onClick={()=>void remove(saved.id)}><Delete/></IconButton></Stack>)}</CardContent></Card></Grid>
    </Grid>
  </Box>

  return <Box className="play-shell">
    <Stack direction="row" alignItems="center" gap={1} mb={1}><Button onClick={()=>{if(isGuest)location.href='?page=play';else{setGame(undefined);void load()}}}>← Games</Button><Typography variant="h5" flex={1}>{game.name} · {gameTitle}</Typography><Chip label={`Turn ${game.state.turn}`}/><Chip color={humanActive?'success':'secondary'} label={`${humanActive?'Your':game.opponent_type==='bot'?'Bot':bot?.name} turn · ${phase}`}/>{!isGuest&&<Tooltip title="Undo last action"><span><IconButton disabled={busy} onClick={()=>void undo()}><Replay/></IconButton></span></Tooltip>}<Tooltip title="Refresh saved state"><IconButton onClick={()=>request<Game>(gamePath).then(setGame)}><Refresh/></IconButton></Tooltip></Stack>
    {inviteUrl&&<Alert severity="info" sx={{mb:1}} action={<Button startIcon={<ContentCopy/>} onClick={()=>navigator.clipboard.writeText(inviteUrl)}>Copy invite</Button>}>Private game ready. Send the invite link to the other player; it grants access only to the guest seat.</Alert>}
    {error&&<Alert severity="error" sx={{mb:1}}>{error}</Alert>}
    <Paper className="play-table" elevation={8}>
      <Box className="player-hud opponent-hud"><Favorite color="error"/><Typography variant="h4">{bot?.life}</Typography><Box><Typography fontWeight={900}>{bot?.name}{game.opponent_type==='bot'?` · ${game.bot_difficulty}`:''}</Typography><Typography variant="caption">{bot?.library_count} library · {bot?.graveyard.length} graveyard</Typography></Box></Box>
      <HiddenHand count={bot?.hand_count}/><Battlefield player={bot!} game={game}/>
      <Divider className="table-divider"><Chip icon={<Bolt/>} label={game.state.stack.length?`Stack · ${game.state.stack.at(-1)?.card.name}`:phase}/></Divider>
      <Battlefield player={player!} game={game}/>
      <Stack direction="row" className="player-hand" justifyContent="center">{player?.hand.map(card=>{const action=game.legal_actions.find(item=>item.card_id===card.instance_id);return <ZoneCard key={card.instance_id} card={card} legal={!!action&&!busy} onClick={action?()=>action.targets?.length?setTargetChoice({cardId:card.instance_id,targets:action.targets}):void act({type:action.type,card_id:card.instance_id}):undefined}/>})}</Stack>
      <Box className="player-hud"><Favorite color="error"/><Typography variant="h4">{player?.life}</Typography><Box><Typography fontWeight={900}>You</Typography><Typography variant="caption">{player?.library_count} library · {player?.graveyard.length} graveyard</Typography></Box></Box>
      <Stack className="play-actions" direction="row" gap={1} flexWrap="wrap" useFlexGap>
        {legal('keep')&&<Button variant="contained" color="success" disabled={busy} onClick={()=>void act({type:'keep'})}>Keep hand</Button>}{legal('mulligan')&&<Button variant="outlined" disabled={busy} onClick={()=>void act({type:'mulligan'})}>Mulligan</Button>}
        {legal('resolve')&&<Button variant="contained" disabled={busy} onClick={()=>void act({type:'resolve'})}>Resolve {game.state.stack.at(-1)?.card.name}</Button>}
        {attackAction&&<Button variant="contained" color="error" startIcon={<Bolt/>} disabled={busy} onClick={()=>void act({type:'declare_attackers',attacker_ids:attackAction.card_ids})}>Attack with all</Button>}
        {blockAction&&<Button variant="contained" color="info" startIcon={<Shield/>} disabled={busy} onClick={()=>void act({type:'declare_blockers',blocks:Object.fromEntries((blockAction.card_ids||[]).slice(0,game.state.combat.attackers.length).map((id,index)=>[id,game.state.combat.attackers[index]]))})}>Block automatically</Button>}
        {legal('advance_phase')&&<Button variant="contained" disabled={busy} onClick={()=>void act({type:'advance_phase'})}>Next · {phases[(phases.findIndex(([key])=>key===game.state.phase)+1)%phases.length][1]}</Button>}
        <Button size="small" onClick={()=>void act({type:'adjust_life',target_id:viewerId,amount:-1})}>−1 life</Button><Button size="small" onClick={()=>void act({type:'adjust_life',target_id:viewerId,amount:1})}>+1 life</Button><Button size="small" onClick={()=>void act({type:'create_token',token_name:'1/1 Creature Token',power:1,toughness:1})}>Create 1/1</Button>{legal('concede')&&<Button color="error" disabled={busy} onClick={()=>void act({type:'concede'})}>Concede</Button>}
      </Stack>
      <Paper className="game-log" variant="outlined"><Typography variant="overline">Game log</Typography>{game.state.log.slice(-8).reverse().map(entry=><Typography key={entry.id} variant="caption" display="block"><b>T{entry.turn}</b> · {entry.message}</Typography>)}</Paper>
    </Paper>
    <Dialog open={!!targetChoice} onClose={()=>setTargetChoice(undefined)}><DialogTitle>Choose a target</DialogTitle><DialogContent><Stack spacing={1} minWidth={320}>{targetChoice?.targets.map(target=><Button key={target.id} variant="outlined" onClick={()=>{void act({type:'cast',card_id:targetChoice.cardId,target_id:target.id});setTargetChoice(undefined)}}>{target.name} · {target.controller_id===viewerId?'yours':'opponent'}</Button>)}</Stack></DialogContent><DialogActions><Button onClick={()=>setTargetChoice(undefined)}>Cancel</Button></DialogActions></Dialog>
    <Dialog open={game.state.status==='complete'}><DialogTitle>{winner?.id===viewerId?'Victory!':'Game over'}</DialogTitle><DialogContent><Typography>{winner?.name} won on turn {game.state.turn}.</Typography></DialogContent><DialogActions><Button onClick={()=>{location.href='?page=play'}}>Return to games</Button></DialogActions></Dialog>
  </Box>
}
