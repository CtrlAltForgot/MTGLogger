import random
import re
from itertools import combinations,product

from .game_engine import _can_block_pair, _effective_rules_text, _has_keyword, _parse_stats, legal_actions, perform_action


def _card(state: dict, player_id: str, instance_id: str) -> dict:
    player = next(player for player in state["players"] if player["id"] == player_id)
    return next(card for zone in (player["hand"],player["battlefield"],player["graveyard"],player.get("exile",[]),player.get("command",[])) for card in zone if card["instance_id"] == instance_id)


def _stats(state:dict,card:dict)->tuple[int,int]:
    return _parse_stats(card,state)


def _threat_score(state:dict,card:dict)->float:
    power,toughness=_stats(state,card);loyalty=card.get("counters",{}).get("loyalty",0);keywords=len(card.get("keywords",[]));text=card.get("oracle_text") or "";shield=card.get("counters",{}).get("shield",0)
    resilience=shield*2.5+(2.5 if _has_keyword(card,"Indestructible") else 0)
    return float(card.get("mana_value") or 0)*2+power*1.4+toughness+loyalty*1.2+keywords*1.5+resilience+min(5,len(text)/80)


def _target_card(state:dict,target_id:str)->dict|None:
    for owner in state["players"]:
        for zone in ("battlefield","graveyard"):
            card=next((card for card in owner[zone] if card["instance_id"]==target_id),None)
            if card:return card
    stack_item=next((item for item in state["stack"] if item["id"]==target_id),None)
    return stack_item["card"] if stack_item else None


def _choose_target(state:dict,action:dict)->str:
    text=" ".join(filter(None,(action.get("label") or "",_card(state,"bot",action["card_id"]).get("oracle_text","") if action.get("card_id") else ""))).casefold()
    harmful=any(word in text for word in ("damage","destroy","exile","tap target","gets -","loses","discards","counter target","gain control of target","control enchanted"));targets=action["targets"]
    preferred=[target for target in targets if (target["controller_id"]!="bot")==harmful] or targets
    damage=re.search(r"deals (\d+) damage",text)
    if harmful and damage:
        lethal=next((target for target in preferred if target["kind"]=="player" and next(player for player in state["players"] if player["id"]==target["id"])["life"]<=int(damage.group(1))),None)
        if lethal:return lethal["id"]
    if "destroy" in text:
        vulnerable=[target for target in preferred if not _has_keyword(_target_card(state,target["id"]) or {},"Indestructible")]
        unshielded=[target for target in vulnerable if (_target_card(state,target["id"]) or {}).get("counters",{}).get("shield",0)==0]
        preferred=unshielded or vulnerable or preferred
    return max(preferred,key=lambda target:_threat_score(state,_target_card(state,target["id"]) or {}))["id"]


def _choose_fight_targets(state:dict,action:dict)->list[str]:
    steps=action.get("target_steps") or [];choices=[]
    for targets in product(*(step.get("targets",[]) for step in steps)):
        ids=[target["id"] for target in targets]
        if any(step.get("distinct") and ids[index] in ids[:index] for index,step in enumerate(steps)):continue
        fighters=[_target_card(state,target_id) for target_id in ids]
        if len(fighters)==1 and action.get("card_id"):
            source=_card(state,"bot",action["card_id"])
            if source in next(player for player in state["players"] if player["id"]=="bot")["battlefield"]:fighters.insert(0,source)
        if len(fighters)>=2 and all(fighters):
            first,second=fighters[:2];first_power,first_toughness=_stats(state,first);second_power,second_toughness=_stats(state,second);enemy=second.get("controller_id")!="bot";score=(_threat_score(state,second) if enemy else -_threat_score(state,second))+(8 if first_power>=second_toughness else -5)-(7 if second_power>=first_toughness else 0)
        else:score=_threat_score(state,fighters[-1] or {}) if fighters else 0
        choices.append((score,ids))
    return max(choices,key=lambda choice:choice[0])[1] if choices else []


def _choose_channel_targets(state:dict,action:dict)->list[str]:
    selected=[]
    for step in action.get("target_steps",[]):
        targets=[target for target in step.get("targets",[]) if not step.get("distinct") or target["id"] not in selected]
        if targets:selected.append(_choose_target(state,{**action,"targets":targets}))
    return selected


def _choose_crew_cost(state:dict,action:dict)->list[str]:
    cards=[_card(state,"bot",card_id) for card_id in action.get("cost_options",[])];required=int(action.get("cost_required_power") or 0);choices=[]
    for amount in range(1,len(cards)+1):
        for group in combinations(cards,amount):
            power=sum(max(0,_stats(state,card)[0]) for card in group)
            if power>=required:choices.append((power-required,sum(_threat_score(state,card) for card in group),len(group),[card["instance_id"] for card in group]))
    return min(choices,key=lambda choice:choice[:3])[3] if choices else []


def _choose_convoke_cost(state:dict,action:dict)->list[str]:
    combinations=(action.get("cost_combinations_by_x") or {}).get(action.get("x_value",0),action.get("cost_combinations") or [])
    if not combinations:return []
    return min(combinations,key=lambda group:(sum(_threat_score(state,_card(state,"bot",card_id)) for card_id in group),len(group)))


def _can_block(state:dict,attacker:dict,blocker:dict)->bool:
    return _can_block_pair(state,attacker,blocker)


def _choose_attackers(state:dict,ids:list[str],difficulty:str)->list[str]:
    bot=next(player for player in state["players"] if player["id"]=="bot");enemy=next(player for player in state["players"] if player["id"]!="bot");attackers={card["instance_id"]:card for card in bot["battlefield"] if card["instance_id"] in ids};blockers=[card for card in enemy["battlefield"] if "Creature" in card.get("type_line","") and not card.get("tapped")]
    if difficulty=="beginner":return ids[:max(1,len(ids)//2)]
    selected=[]
    for card_id in ids:
        attacker=attackers[card_id];power,toughness=_stats(state,attacker);legal=[blocker for blocker in blockers if _can_block(state,attacker,blocker)]
        if power>=enemy["life"] or not legal or _has_keyword(attacker,"Vigilance"):selected.append(card_id);continue
        favorable=all((power>=_stats(state,blocker)[1] or _has_keyword(attacker,"Deathtouch")) and (toughness>_stats(state,blocker)[0] or _has_keyword(attacker,"Indestructible") or attacker.get("counters",{}).get("shield",0)) for blocker in legal)
        if favorable or (difficulty=="standard" and power>=max(_stats(state,blocker)[1] for blocker in legal)):selected.append(card_id)
    if difficulty=="expert" and len(ids)>len(blockers):
        pressure=sorted((card_id for card_id in ids if card_id not in selected),key=lambda card_id:_stats(state,attackers[card_id])[0],reverse=True)
        selected.extend(pressure[:max(0,len(ids)-len(blockers)-len(selected))])
    if len(selected)==1 and "can't attack or block alone" in _effective_rules_text(state,attackers[selected[0]]):
        companion=next((card_id for card_id in ids if card_id not in selected),None)
        if companion:selected.append(companion)
        else:selected=[]
    return selected


def _should_mulligan(state:dict,difficulty:str)->bool:
    bot=next(player for player in state["players"] if player["id"]=="bot");lands=sum("Land" in card.get("type_line","") for card in bot["hand"]);mulligans=bot.get("mulligans",0)
    if difficulty=="beginner" or mulligans>=3:return False
    if difficulty=="standard":return lands<2 or lands>5
    castable_curve=sum(1 for card in bot["hand"] if "Land" not in card.get("type_line","") and (card.get("mana_value") or 0)<=3)
    return lands<2 or lands>4 or castable_curve<2


def _choose_blocks(state:dict,action:dict,difficulty:str)->dict[str,str]:
    bot=next(player for player in state["players"] if player["id"]=="bot");enemy=next(player for player in state["players"] if player["id"]!="bot")
    blockers={card["instance_id"]:card for card in bot["battlefield"]};attackers={card["instance_id"]:card for card in enemy["battlefield"] if card["instance_id"] in state["combat"]["attackers"]};available=set(action["card_ids"]);result={}
    ordered=sorted(attackers,key=lambda card_id:_stats(state,attackers[card_id])[0],reverse=True)
    for attacker_id in ordered:
        legal=[blocker_id for blocker_id in available if attacker_id in action.get("legal_blocks",{}).get(blocker_id,[])]
        menace=any(keyword.casefold()=="menace" for keyword in attackers[attacker_id].get("keywords",[])) or "menace" in (attackers[attacker_id].get("oracle_text") or "").casefold();needed=2 if menace else 1
        if len(legal)<needed:continue
        if difficulty=="beginner":
            if random.random()<.45:continue
            chosen=random.sample(legal,needed)
        else:
            power,toughness=_stats(state,attackers[attacker_id]);legal.sort(key=lambda blocker_id:(_stats(state,blockers[blocker_id])[0]>=toughness,_stats(state,blockers[blocker_id])[1]>power,blockers[blocker_id].get("counters",{}).get("shield",0)>0,_stats(state,blockers[blocker_id])[0]),reverse=True)
            chosen=legal[:needed]
            if difficulty=="standard" and sum(_stats(state,blockers[blocker_id])[0] for blocker_id in chosen)<toughness and power<4:continue
        for blocker_id in chosen:result[blocker_id]=attacker_id;available.remove(blocker_id)
    if len(result)==1:
        blocker_id=next(iter(result))
        if "can't attack or block alone" in _effective_rules_text(state,blockers[blocker_id]):return {}
    return result


def _ability_score(state:dict,action:dict)->float:
    text=(action.get("label") or "").casefold();source=_card(state,"bot",action["card_id"]);score=0.5
    draw=re.search(r"draw (?:a|one|two|three|four|five|\d+) cards?",text)
    if draw:
        word=draw.group(0).split()[1];score+={"a":2,"one":2,"two":4,"three":6,"four":8,"five":10}.get(word,int(word)*2 if word.isdigit() else 2)
    if any(term in text for term in ("destroy target","exile target","counter target","gain control of target")):score+=7
    if "create " in text and " token" in text:score+=4
    if "deals " in text and " damage" in text:score+=4
    if "you gain " in text and " life" in text:score+=2
    if "regenerate" in text:
        threatened=bool(state["stack"] and state["stack"][-1].get("target_id")==source["instance_id"] and "destroy" in (state["stack"][-1]["card"].get("oracle_text") or "").casefold())
        combat=state.get("combat",{});in_combat=source["instance_id"] in combat.get("attackers",[]) or source["instance_id"] in combat.get("blocks",{}) or source["instance_id"] in combat.get("blocks",{}).values()
        score+=9 if threatened else 4 if combat.get("damage_pending") and in_combat else -6
    if action.get("target_steps"):
        target_ids=_choose_fight_targets(state,action);opponent_card=_target_card(state,target_ids[-1]) if target_ids else None
        if opponent_card:
            source_power,source_toughness=_stats(state,source);enemy_power,enemy_toughness=_stats(state,opponent_card);score+=(8 if source_power>=enemy_toughness else -5)-(9 if enemy_power>=source_toughness else 0)
    if action.get("self_sacrifice"):score-=_threat_score(state,source)*.65
    score-=float(action.get("life_cost") or 0)*1.25
    if action.get("energy_cost")!="X":score-=float(action.get("energy_cost") or 0)*.35
    counter_cost=action.get("counter_cost") or {};score-=float(counter_cost.get("amount") or 0)*.75
    cost_cards=[_card(state,"bot",card_id) for card_id in action.get("cost_options",[])]
    if cost_cards:
        ranked=sorted(cost_cards,key=lambda card:_threat_score(state,card) if action.get("cost_kind")=="sacrifice" else float(card.get("mana_value") or 0)+(2 if "Land" in card.get("type_line","") else 0));score-=sum((_threat_score(state,card)*.55 if action.get("cost_kind")=="sacrifice" else float(card.get("mana_value") or 0)+.5) for card in ranked[:action.get("cost_amount",1)])
    bot=next(player for player in state["players"] if player["id"]=="bot")
    if action.get("life_cost",0)>=bot["life"]:score-=100
    return score


def _choose_ability_cost(state:dict,action:dict)->list[str]:
    amount=action.get("cost_amount",0)
    if not amount and not action.get("selection_x"):return []
    if action.get("cost_kind")=="compound" or action.get("selection_x"):
        bot=next(player for player in state["players"] if player["id"]=="bot");battlefield_ids={card["instance_id"] for card in bot["battlefield"]}
        def payment_cost(group:list[str])->float:
            return sum(_threat_score(state,_card(state,"bot",card_id)) if card_id in battlefield_ids else float(_card(state,"bot",card_id).get("mana_value") or 0)+(.75 if "Land" in _card(state,"bot",card_id).get("type_line","") else 0) for card_id in group)
        groups=(action.get("cost_combinations_by_x") or {}).get(action.get("x_value",0),action.get("cost_combinations",[]))
        return min(groups,key=payment_cost,default=[])
    cards=[_card(state,"bot",card_id) for card_id in action.get("cost_options",[])]
    if action.get("cost_kind")=="sacrifice":cards.sort(key=lambda card:_threat_score(state,card))
    else:cards.sort(key=lambda card:("Land" in card.get("type_line",""),card.get("mana_value") or 0))
    return [card["instance_id"] for card in cards[:amount]]


def _mode_score(state:dict,mode:dict)->float:
    text=(mode.get("label") or "").casefold();score=0.2
    draw=re.search(r"draw (?:a|one|two|three|four|five|\d+) cards?",text)
    if draw:
        word=draw.group(0).split()[1];score+={"a":2,"one":2,"two":4,"three":6,"four":8,"five":10}.get(word,int(word)*2 if word.isdigit() else 2)
    targets=mode.get("targets",[])
    if any(term in text for term in ("destroy target","exile target","return target","gain control of target")) and targets:
        score+=max((_threat_score(state,_target_card(state,target["id"]) or {}) for target in targets if target.get("controller_id")!="bot"),default=1)
    damage=re.search(r"deals (\d+) damage",text)
    if damage:
        amount=int(damage.group(1));score+=amount
        if any(target["kind"]=="player" and target["controller_id"]!="bot" and next(player for player in state["players"] if player["id"]==target["id"])["life"]<=amount for target in targets):score+=100
    if "you gain " in text and " life" in text:score+=2
    if "create " in text and " token" in text:score+=4
    return score


def _choose_x(state:dict,action:dict)->int:
    maximum=int(action.get("x_max") or 0);text=" ".join(filter(None,(action.get("label") or "",_card(state,"bot",action["card_id"]).get("oracle_text","") if action.get("card_id") else ""))).casefold();enemy=next(player for player in state["players"] if player["id"]!="bot")
    if re.search(r"deals? x damage",text):return min(maximum,max(0,enemy["life"]))
    if re.search(r"draw x cards?",text):
        bot=next(player for player in state["players"] if player["id"]=="bot");return min(maximum,max(0,len(bot["library"])-1))
    return maximum


def choose_bot_action(state: dict, difficulty: str = "standard", use_priority_protocol:bool=False) -> dict | None:
    actions = legal_actions(state, "bot",allow_direct_resolution=not use_priority_protocol)
    if not actions:
        return None
    by_type = {kind: [action for action in actions if action["type"] == kind] for kind in {action["type"] for action in actions}}
    if "keep" in by_type:
        return by_type.get("mulligan",by_type["keep"])[0] if _should_mulligan(state,difficulty) else by_type["keep"][0]
    if "take_top_card" in by_type or "mill_top_card" in by_type or "keep_top_card" in by_type:
        if "take_top_card" in by_type:return by_type["take_top_card"][0]
        return by_type["keep_top_card"][0] if difficulty=="beginner" else by_type.get("mill_top_card",by_type["keep_top_card"])[0]
    if "choose_revealed_discard" in by_type:
        return max(by_type["choose_revealed_discard"],key=lambda action:float((action.get("card") or {}).get("mana_value") or 0))
    if "discard_optional_card" in by_type or "decline_optional_discard" in by_type:
        if difficulty=="beginner" or "discard_optional_card" not in by_type:return by_type["decline_optional_discard"][0]
        return min(by_type["discard_optional_card"],key=lambda action:float(_card(state,"bot",action["card_id"]).get("mana_value") or 0))
    if "accept_rad_counters" in by_type or "decline_rad_counters" in by_type:return by_type["decline_rad_counters"][0]
    if "pay_cumulative_upkeep" in by_type or "sacrifice_cumulative_upkeep" in by_type:
        if "pay_cumulative_upkeep" not in by_type or difficulty=="beginner" and random.random()<.2:return by_type["sacrifice_cumulative_upkeep"][0]
        action=by_type["pay_cumulative_upkeep"][0];options=action.get("cost_options",[]);amount=action.get("cost_amount",0)
        ranked=sorted(options,key=lambda card_id:_threat_score(state,_target_card(state,card_id) or _card(state,"bot",card_id)))
        return {**action,"cost_card_ids":ranked[:amount]}
    if "pay_echo" in by_type or "sacrifice_echo" in by_type:
        if "pay_echo" not in by_type or difficulty=="beginner" and random.random()<.2:return by_type["sacrifice_echo"][0]
        action=by_type["pay_echo"][0];return {**action,"cost_card_ids":_choose_ability_cost(state,action)}
    if "pay_ward" in by_type or "decline_ward" in by_type:
        if "pay_ward" in by_type and difficulty!="beginner":
            action=by_type["pay_ward"][0]
            if action.get("cost_type")=="discard":
                amount=action.get("amount",1);ranked=sorted(action["card_ids"],key=lambda card_id:(_card(state,"bot",card_id).get("mana_value") or 0,"Land" not in _card(state,"bot",card_id).get("type_line","")));return {**action,"card_ids":ranked[:amount]}
            return action
        return by_type["decline_ward"][0]
    if "pay_blight" in by_type or "decline_blight" in by_type:
        if "pay_blight" in by_type and difficulty!="beginner":
            action=by_type["pay_blight"][0];chosen=_choose_ability_cost(state,action);return {"type":"pay_blight","cost_card_ids":chosen}
        return by_type["decline_blight"][0]
    if "choose_proliferate" in by_type:
        action=by_type["choose_proliferate"][0];chosen=[]
        for target in action.get("targets",[]):
            counters=target.get("counters",{});own=target.get("controller_id")=="bot"
            beneficial=sum(amount for name,amount in counters.items() if name not in {"-1/-1","stun","poison"});harmful=sum(amount for name,amount in counters.items() if name in {"-1/-1","stun","poison"})
            if (own and beneficial>harmful) or (not own and harmful>=beneficial):chosen.append(target["id"])
        return {"type":"choose_proliferate","target_ids":chosen}
    if "choose_amass_army" in by_type:
        action=by_type["choose_amass_army"][0];choice=max(action.get("targets",[]),key=lambda target:_threat_score(state,_target_card(state,target["id"]) or {}),default=None)
        return {"type":"choose_amass_army","target_id":choice["id"]} if choice else by_type.get("concede",[None])[0]
    if "choose_populate_token" in by_type:
        action=by_type["choose_populate_token"][0];choice=max(action.get("targets",[]),key=lambda target:_threat_score(state,_target_card(state,target["id"]) or {}),default=None)
        return {"type":"choose_populate_token","target_id":choice["id"]} if choice else by_type.get("skip_populate",by_type.get("concede",[None]))[0]
    if "skip_populate" in by_type:return by_type["skip_populate"][0]
    if "choose_bolster_creature" in by_type:
        action=by_type["choose_bolster_creature"][0];choice=max(action.get("targets",[]),key=lambda target:_threat_score(state,_target_card(state,target["id"]) or {}),default=None)
        return {"type":"choose_bolster_creature","target_id":choice["id"]} if choice else by_type.get("concede",[None])[0]
    if "keep_explored" in by_type:
        action=by_type["keep_explored"][0];revealed=action.get("card") or {};bot=next(player for player in state["players"] if player["id"]=="bot");lands=sum("Land" in card.get("type_line","") for card in bot["battlefield"]);value=float(revealed.get("mana_value") or 0)
        keep=difficulty=="beginner" or value<=lands+2 or (_threat_score(state,revealed)>=8 and value<=lands+4)
        return by_type["keep_explored"][0] if keep else by_type["graveyard_explored"][0]
    if "cast_madness" in by_type or "decline_madness" in by_type:
        cast=by_type.get("cast_madness",[None])[0]
        if cast and (difficulty!="beginner" or random.random()<.5):
            if cast.get("x_max") is not None:cast={**cast,"x_value":_choose_x(state,cast)}
            if cast.get("targets"):cast={**cast,"target_id":_choose_target(state,cast)}
            return cast
        return by_type.get("decline_madness",[None])[0]
    if "decline_rebound" in by_type and "cast" not in by_type:return by_type["decline_rebound"][0]
    if "cast_discovered" in by_type or "hand_discovered" in by_type or "decline_discovery" in by_type:
        cast=by_type.get("cast_discovered",[None])[0]
        if cast:
            if cast.get("targets"):cast={**cast,"target_id":_choose_target(state,cast)}
            return cast
        return by_type.get("hand_discovered",by_type.get("decline_discovery",[None]))[0]
    if "cast_zethi_copy" in by_type or "decline_zethi_copy" in by_type:
        cast=by_type.get("cast_zethi_copy",[None])[0]
        if cast and difficulty!="beginner":
            if cast.get("targets"):cast={**cast,"target_id":_choose_target(state,cast)}
            return cast
        return by_type.get("decline_zethi_copy",[None])[0]
    if "pay_counter_payment" in by_type or "decline_counter_payment" in by_type:
        return by_type.get("pay_counter_payment",by_type.get("decline_counter_payment",[None]))[0] if difficulty!="beginner" else by_type.get("decline_counter_payment",by_type.get("pay_counter_payment",[None]))[0]
    if "choose_manifest_dread" in by_type:
        action=by_type["choose_manifest_dread"][0];choice=max(action.get("cards",[]),key=lambda card:("Creature" in card.get("type_line",""),_threat_score(state,card)),default=None)
        return {"type":"choose_manifest_dread","card_id":choice["instance_id"]} if choice else by_type.get("concede",[None])[0]
    if "accept_transform" in by_type or "decline_transform" in by_type:
        return by_type["decline_transform"][0] if difficulty=="beginner" else by_type["accept_transform"][0]
    if "choose_creature_type" in by_type:
        action=by_type["choose_creature_type"][0];bot=next(player for player in state["players"] if player["id"]=="bot");suggestions=action.get("suggested_types") or ["Human"]
        choice=max(suggestions,key=lambda subtype:sum(re.search(rf"\b{re.escape(subtype)}\b",card.get("type_line",""),re.IGNORECASE) is not None for zone in (bot["hand"],bot["battlefield"],bot["graveyard"],bot.get("exile",[]),bot.get("command",[]),bot.get("library",[])) for card in zone));return {"type":"choose_creature_type","creature_type":choice}
    if "pay_tilonalli" in by_type or "decline_tilonalli" in by_type:
        payment=by_type.get("pay_tilonalli",[None])[0]
        return {"type":"pay_tilonalli","x_value":payment["x_max"]} if payment and payment.get("x_max",0)>0 and difficulty!="beginner" else by_type["decline_tilonalli"][0]
    if "pay_optional_mana" in by_type or "decline_optional_mana" in by_type:
        return by_type["pay_optional_mana"][0] if "pay_optional_mana" in by_type and difficulty!="beginner" else by_type["decline_optional_mana"][0]
    if "choose_dungeon_room" in by_type:
        rooms={action.get("room"):action for action in by_type["choose_dungeon_room"]};bot=next(player for player in state["players"] if player["id"]=="bot")
        preferred="Lost Well" if len(bot["hand"])<4 else "Forge" if any("Creature" in card.get("type_line","") for card in bot["battlefield"]) else "Lost Well"
        if "Throne of the Dead Three" in rooms:preferred="Throne of the Dead Three" if difficulty=="expert" else "Catacombs"
        return rooms.get(preferred,by_type["choose_dungeon_room"][0])
    if "choose_dungeon_target" in by_type:
        action=by_type["choose_dungeon_target"][0];own=[target for target in action.get("targets",[]) if target.get("controller_id")=="bot"]
        candidates=own if action.get("room")=="Forge" and own else [target for target in action.get("targets",[]) if target.get("controller_id")!="bot"] or action.get("targets",[])
        choice=max(candidates,key=lambda target:_threat_score(state,_target_card(state,target["id"]) or {}),default=None);return {"type":"choose_dungeon_target","target_id":choice["id"]} if choice else by_type.get("concede",[None])[0]
    if "choose_dungeon_card" in by_type or "skip_dungeon_card" in by_type:
        action=by_type.get("choose_dungeon_card",[None])[0];choice=max((action or {}).get("cards",[]),key=lambda card:_threat_score(state,card),default=None)
        return {"type":"choose_dungeon_card","card_id":choice["instance_id"]} if choice else by_type["skip_dungeon_card"][0]
    if "choose_trigger_target" in by_type:
        action=by_type["choose_trigger_target"][0]
        return {"type":"choose_trigger_target","target_id":_choose_target(state,action)}
    if "choose_trigger_mode" in by_type:
        action=by_type["choose_trigger_mode"][0];modes=action.get("modes") or []
        return {"type":"choose_trigger_mode","mode_index":modes[0]["index"]}
    if "choose_trigger_targets" in by_type:
        action=by_type["choose_trigger_targets"][0];return {"type":"choose_trigger_targets","target_ids":_choose_fight_targets(state,action)}
    if "skip_trigger" in by_type:return by_type["skip_trigger"][0]
    if "bottom_mulligan_cards" in by_type:
        action=by_type["bottom_mulligan_cards"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:("Land" in _card(state,"bot",card_id).get("type_line",""),-(_card(state,"bot",card_id).get("mana_value") or 0)))
        return {"type":"bottom_mulligan_cards","card_ids":ranked[:amount]}
    if "resolve" in by_type:
        return by_type["resolve"][0]
    if "resolve_combat_damage" in by_type:
        return by_type["resolve_combat_damage"][0]
    if "discard_cards" in by_type:
        action=by_type["discard_cards"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:(_card(state,"bot",card_id).get("mana_value") or 0,"Land" not in _card(state,"bot",card_id).get("type_line","")))
        return {"type":"discard_cards","card_ids":ranked[:amount]}
    if "discard_connive" in by_type:
        action=by_type["discard_connive"][0];amount=action["amount"];bot=next(player for player in state["players"] if player["id"]=="bot");lands_in_hand=sum("Land" in card.get("type_line","") for card in bot["hand"])
        def connive_cost(card_id:str)->tuple[float,float]:
            card=_card(state,"bot",card_id);land="Land" in card.get("type_line","");keep_value=float(card.get("mana_value") or 0)+(3 if land and lands_in_hand<=3 else -1 if land and lands_in_hand>=5 else 0);counter_bonus=0 if land else (1.5 if difficulty=="expert" else .5)
            return keep_value-counter_bonus,float(card.get("mana_value") or 0)
        ranked=sorted(action["card_ids"],key=connive_cost);return {"type":"discard_connive","card_ids":ranked[:amount]}
    if "sacrifice_permanents" in by_type:
        action=by_type["sacrifice_permanents"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:((_card(state,"bot",card_id).get("mana_value") or 0),sum(_stats(state,_card(state,"bot",card_id)))))
        return {"type":"sacrifice_permanents","card_ids":ranked[:amount]}
    if "choose_legendary" in by_type:
        action=by_type["choose_legendary"][0];choice=max(action["card_ids"],key=lambda card_id:((_card(state,"bot",card_id).get("mana_value") or 0),sum(_stats(state,_card(state,"bot",card_id)))))
        return {"type":"choose_legendary","card_ids":[choice]}
    if "move_commander" in by_type or "keep_commander" in by_type:
        move=by_type.get("move_commander",[])[0] if by_type.get("move_commander") else None
        keep=by_type.get("keep_commander",[])[0] if by_type.get("keep_commander") else None
        bot=next(player for player in state["players"] if player["id"]=="bot")
        # Preserve graveyard recursion when the commander can immediately be recovered; otherwise avoid losing access to it.
        recursion=any("return target creature card" in (card.get("oracle_text") or "").casefold() and "graveyard" in (card.get("oracle_text") or "").casefold() for card in bot["hand"])
        return keep if difficulty=="expert" and keep and keep.get("zone")=="graveyard" and recursion else move or keep
    if "search_library" in by_type:
        action=by_type["search_library"][0];cards=action.get("cards",[]);amount=action.get("max_amount",0)
        ranked=sorted(cards,key=lambda card:(("Land" not in card.get("type_line","") if action.get("destination")=="battlefield" else True),_threat_score(state,card),-(card.get("mana_value") or 0)),reverse=True)
        chosen=[]
        for card in ranked:
            if action.get("different_names") and any(other["name"].casefold()==card["name"].casefold() for other in chosen):continue
            if action.get("shared_land_type") and chosen:
                current=set.intersection(*(set(re.split(r"\s+",other.get("type_line","").split("—",1)[-1].casefold())) for other in chosen));candidate=set(re.split(r"\s+",card.get("type_line","").split("—",1)[-1].casefold()))
                if not current&candidate:continue
            chosen.append(card)
            if len(chosen)>=amount:break
        return {"type":"search_library","card_ids":[card["instance_id"] for card in chosen]}
    if "scry" in by_type or "surveil" in by_type:
        kind="surveil" if "surveil" in by_type else "scry";action=by_type[kind][0];cards={card["instance_id"]:card for card in action.get("cards",[])};bot=next(player for player in state["players"] if player["id"]=="bot");lands_in_hand=sum("Land" in card.get("type_line","") for card in bot["hand"])
        if difficulty=="beginner":bottom=[]
        else:bottom=[card_id for card_id in action["card_ids"] if ("Land" in cards[card_id].get("type_line","") and lands_in_hand>=4) or ("Land" not in cards[card_id].get("type_line","") and lands_in_hand<2) or (difficulty=="expert" and (cards[card_id].get("mana_value") or 0)>max(3,len([card for card in bot["battlefield"] if "Land" in card.get("type_line","")])+2))]
        top=[card_id for card_id in action["card_ids"] if card_id not in bottom]
        return {"type":kind,"top_ids":top,"graveyard_ids" if kind=="surveil" else "bottom_ids":bottom}
    if "order_blockers" in by_type:
        action=by_type["order_blockers"][0];orders={}
        for group in action["groups"]:
            blockers=sorted(group["blockers"],key=lambda card:(_stats(state,card)[1],_stats(state,card)[0],card.get("mana_value") or 0))
            orders[group["attacker"]["instance_id"]]=[card["instance_id"] for card in blockers]
        return {"type":"order_blockers","block_orders":orders}
    if "play_land" in by_type:
        return by_type["play_land"][0]
    if "suspend" in by_type:
        choice=max(by_type["suspend"],key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])))
        suspended_value=_threat_score(state,_card(state,"bot",choice["card_id"]));best_cast=max((_threat_score(state,_card(state,"bot",action["card_id"])) for action in by_type.get("cast",[])),default=-1)
        if "cast" not in by_type or suspended_value>best_cast+3:
            if choice.get("x_max") is not None:choice={**choice,"x_value":max(choice.get("x_min",1),choice["x_max"])}
            return choice
    if "foretell" in by_type:
        choice=max(by_type["foretell"],key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])))
        candidate=_card(state,"bot",choice["card_id"]);best_cast=max((_threat_score(state,_card(state,"bot",action["card_id"])) for action in by_type.get("cast",[])),default=-1)
        discount=float(candidate.get("mana_value") or 0)-2
        if "cast" not in by_type or discount>=2 or _threat_score(state,candidate)>best_cast+3:return choice
    if "plot" in by_type:
        choice=max(by_type["plot"],key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])));candidate=_card(state,"bot",choice["card_id"]);symbols=re.findall(r"\{([^}]+)\}",choice.get("mana_cost") or "");plot_value=sum(int(symbol) if symbol.isdigit() else 1 for symbol in symbols)-int(choice.get("plot_reduction") or 0);discount=float(candidate.get("mana_value") or 0)-max(0,plot_value)
        if "cast" not in by_type or discount>=2 or difficulty=="beginner" and random.random()<.35:return choice
    if "turn_face_up" in by_type:
        return max(by_type["turn_face_up"],key=lambda action:_threat_score(state,{**_card(state,"bot",action["card_id"]),**(_card(state,"bot",action["card_id"]).get("face_down_values") or {})}))
    if "crew" in by_type:
        usable=[]
        for action in by_type["crew"]:
            vehicle=_card(state,"bot",action["card_id"]);active=state["active_player_id"]=="bot"
            if vehicle.get("crewed_turn")!=state["turn"] and not (active and state["phase"] in {"precombat_main","combat"} and vehicle.get("summoning_sick")):usable.append(action)
        if usable:
            choice=max(usable,key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])));return {**choice,"cost_card_ids":_choose_crew_cost(state,choice)}
    if "cycle" in by_type:
        bot=next(player for player in state["players"] if player["id"]=="bot");lands_in_hand=sum("Land" in card.get("type_line","") for card in bot["hand"]);lands_in_play=sum("Land" in card.get("type_line","") for card in bot["battlefield"])
        choice=min(by_type["cycle"],key=lambda action:(_card(state,"bot",action["card_id"]).get("mana_value") or 0))
        stranded=(_card(state,"bot",choice["card_id"]).get("mana_value") or 0)>lands_in_play+2
        if "cast" not in by_type or lands_in_hand<2 or (difficulty=="expert" and stranded):return choice
    if "activate_speed_graveyard" in by_type:
        return by_type["activate_speed_graveyard"][0]
    if "station" in by_type:
        action=max(by_type["station"],key=lambda candidate:max((_parse_stats(_card(state,"bot",card_id),state)[0] for card_id in candidate.get("cost_options",[])),default=0));crew=max(action["cost_options"],key=lambda card_id:_parse_stats(_card(state,"bot",card_id),state)[0]);return {**action,"cost_card_ids":[crew]}
    if "cast_face_down" in by_type and "cast" not in by_type:
        choices=by_type["cast_face_down"];return random.choice(choices) if difficulty=="beginner" else max(choices,key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])))
    if "unearth" in by_type:
        choice=max(by_type["unearth"],key=lambda action:_threat_score(state,_card(state,"bot",action["card_id"])))
        best_cast=max((_threat_score(state,_card(state,"bot",action["card_id"])) for action in by_type.get("cast",[])),default=-1)
        if difficulty=="beginner" and random.random()<.5 or "cast" not in by_type or _threat_score(state,_card(state,"bot",choice["card_id"]))>=best_cast:return choice
    if "channel" in by_type:
        choice=max(by_type["channel"],key=lambda action:_ability_score(state,action))
        if choice.get("x_max") is not None:
            choice={**choice,"x_value":_choose_x(state,choice)};choice["target_steps"]=(choice.get("target_steps_by_x") or {}).get(choice["x_value"],choice.get("target_steps",[]))
        if choice.get("targets"):choice={**choice,"target_id":_choose_target(state,choice)}
        if choice.get("target_steps"):choice={**choice,"target_ids":_choose_channel_targets(state,choice)}
        if "cast" not in by_type or difficulty=="expert" or _ability_score(state,choice)>2:return choice
    if "cast" in by_type:
        spells = by_type["cast"]
        if state["stack"]:
            top=state["stack"][-1];counterspells=[action for action in spells if "counter target spell" in (_card(state,"bot",action["card_id"]).get("oracle_text") or "").casefold()]
            enemy=next(player for player in state["players"] if player["id"]!="bot")
            def is_lethal(action:dict)->bool:
                match=re.search(r"deals (\d+|x) damage to any target",(_card(state,"bot",action["card_id"]).get("oracle_text") or "").casefold());amount=(action.get("x_max",0) if match and match.group(1)=="x" else int(match.group(1)) if match else 0)
                return amount>=enemy["life"] and any(target.get("id")==enemy["id"] for target in action.get("targets",[]))
            lethal=[action for action in spells if is_lethal(action)]
            spells=(counterspells if top.get("controller_id")!="bot" else []) or lethal
            if not spells:return by_type.get("pass_priority",[None])[0]
        if difficulty == "beginner":
            choice = random.choice(spells)
        else:
            choice = max(spells, key=lambda action: ((_card(state,"bot",action["card_id"]).get("mana_value") or 0)+(2 if action.get("kicked") else 0)+(1 if action.get("buyback") else 0)+(1 if action.get("blessing_top") else 0),len(_card(state,"bot",action["card_id"]).get("oracle_text") or "")))
        if choice.get("x_max") is not None:choice={**choice,"x_value":_choose_x(state,choice)}
        if choice.get("cost_options"):choice={**choice,"cost_card_ids":_choose_convoke_cost(state,choice) if choice.get("cost_kind") in {"convoke","waterbend"} else _choose_ability_cost(state,choice)}
        if choice.get("target_steps"):choice={**choice,"target_ids":_choose_fight_targets(state,choice)}
        if choice.get("modes"):
            maximum=choice.get("mode_max",choice.get("mode_count",1));minimum=choice.get("mode_min",choice.get("mode_count",1));ranked=sorted(choice["modes"],key=lambda candidate:_mode_score(state,candidate),reverse=True)
            chosen=([ranked[0]]*maximum if choice.get("mode_repeatable") and ranked else ranked[:maximum])
            if choice.get("mode_repeatable") and chosen:
                while len(chosen)<minimum:chosen.append(chosen[0])
            chosen=chosen[:max(minimum,maximum)];mode_targets=[]
            for mode in chosen:
                targets=mode.get("targets",[])
                if choice.get("mode_distinct_targets"):targets=[target for target in targets if target["id"] not in mode_targets]
                mode_targets.append(_choose_target(state,{**choice,"label":mode["label"],"targets":targets}) if targets else None)
            choice={**choice,"chosen_modes":[mode["index"] for mode in chosen],"mode_targets":mode_targets,"target_id":mode_targets[0] if len(mode_targets)==1 else None,"modes":None}
        if choice.get("targets"):
            choice = {**choice, "target_id":_choose_target(state,choice)}
        return choice
    if "equip" in by_type:
        candidates=[]
        for action in by_type["equip"]:
            equipment=_card(state,"bot",action["card_id"]);target_id=_choose_target(state,action)
            if equipment.get("attached_to")!=target_id:candidates.append((action,target_id,_threat_score(state,_target_card(state,target_id) or {})))
        if candidates:
            action,target_id,_=max(candidates,key=lambda candidate:candidate[2]);return {**action,"target_id":target_id}
    if "activate" in by_type:
        choices=by_type["activate"];choice=max(choices,key=lambda action:_ability_score(state,action))
        if (difficulty=="beginner" and _ability_score(state,choice)>-.5) or _ability_score(state,choice)>0:
            if choice.get("x_max") is not None:choice={**choice,"x_value":_choose_x(state,choice)}
            choice={**choice,"cost_card_ids":_choose_convoke_cost(state,choice) if choice.get("cost_kind")=="waterbend" else _choose_ability_cost(state,choice)}
            if choice.get("targets"):
                choice={**choice,"target_id":_choose_target(state,choice)}
            if choice.get("target_steps"):choice={**choice,"target_ids":_choose_fight_targets(state,choice)}
            return choice
    if "activate_loyalty" in by_type:
        choices=by_type["activate_loyalty"];choice=max(choices,key=lambda action:len(action.get("label","")))
        if choice.get("targets"):
            choice={**choice,"target_id":_choose_target(state,choice)}
        return choice
    if "declare_attackers" in by_type:
        action = by_type["declare_attackers"][0]
        ids=_choose_attackers(state,action["card_ids"],difficulty)
        defenders=action.get("defenders",[]);planeswalkers=[target for target in defenders if target["kind"]=="permanent"];enemy=next(player for player in state["players"] if player["id"]!="bot");total_power=sum(_stats(state,_card(state,"bot",card_id))[0] for card_id in ids);player_target=next((target for target in defenders if target["kind"]=="player"),None)
        target=(player_target if total_power>=enemy["life"] else max(planeswalkers,key=lambda candidate:_threat_score(state,_target_card(state,candidate["id"]) or {}),default=player_target)) if difficulty=="expert" else (defenders[0] if defenders else None)
        return {"type": "declare_attackers", "attacker_ids": ids,"attack_targets":{card_id:target["id"] for card_id in ids} if target else {}}
    if "declare_blockers" in by_type:
        action = by_type["declare_blockers"][0]
        return {"type": "declare_blockers", "blocks": _choose_blocks(state,action,difficulty)}
    if "ninjutsu" in by_type:
        swaps=[]
        for action in by_type["ninjutsu"]:
            ninja=_card(state,"bot",action["card_id"])
            for target in action["targets"]:
                attacker=_card(state,"bot",target["id"]);gain=_threat_score(state,ninja)-_threat_score(state,attacker)
                swaps.append((gain,action,target["id"]))
        if swaps:
            gain,action,target_id=max(swaps,key=lambda entry:entry[0])
            if gain>0 or difficulty=="beginner" and random.random()<.35:return {**action,"target_id":target_id,"targets":None}
    return by_type.get("advance_phase",by_type.get("pass_priority",[None]))[0]


def run_bot(state: dict, difficulty: str = "standard", limit: int = 80) -> dict:
    steps = 0
    while steps < limit and state["status"] != "complete":
        action = choose_bot_action(state, difficulty,use_priority_protocol=True)
        if not action:
            break
        state = perform_action(state, "bot", action,allow_direct_resolution=False)
        steps += 1
        if state["status"] == "active" and state["active_player_id"] != "bot" and not state["stack"]:
            break
    return state
