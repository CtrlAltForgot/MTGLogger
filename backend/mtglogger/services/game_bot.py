import random
import re

from .game_engine import _has_keyword, _parse_stats, _protected_from, legal_actions, perform_action


def _card(state: dict, player_id: str, instance_id: str) -> dict:
    player = next(player for player in state["players"] if player["id"] == player_id)
    return next(card for zone in (player["hand"], player["battlefield"]) for card in zone if card["instance_id"] == instance_id)


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
    text=(action.get("label") or _card(state,"bot",action.get("card_id","" )).get("oracle_text","") if action.get("card_id") else action.get("label") or "").casefold()
    harmful=any(word in text for word in ("damage","destroy","exile","tap target","gets -","loses","counter target"));targets=action["targets"]
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


def _can_block(attacker:dict,blocker:dict)->bool:
    text=(attacker.get("oracle_text") or "").casefold()
    return "can't be blocked" not in text and "unblockable" not in text and (not _has_keyword(attacker,"Flying") or _has_keyword(blocker,"Flying") or _has_keyword(blocker,"Reach")) and not _protected_from(attacker,blocker)


def _choose_attackers(state:dict,ids:list[str],difficulty:str)->list[str]:
    bot=next(player for player in state["players"] if player["id"]=="bot");enemy=next(player for player in state["players"] if player["id"]!="bot");attackers={card["instance_id"]:card for card in bot["battlefield"] if card["instance_id"] in ids};blockers=[card for card in enemy["battlefield"] if "Creature" in card.get("type_line","") and not card.get("tapped")]
    if difficulty=="beginner":return ids[:max(1,len(ids)//2)]
    selected=[]
    for card_id in ids:
        attacker=attackers[card_id];power,toughness=_stats(state,attacker);legal=[blocker for blocker in blockers if _can_block(attacker,blocker)]
        if power>=enemy["life"] or not legal or _has_keyword(attacker,"Vigilance"):selected.append(card_id);continue
        favorable=all((power>=_stats(state,blocker)[1] or _has_keyword(attacker,"Deathtouch")) and (toughness>_stats(state,blocker)[0] or _has_keyword(attacker,"Indestructible") or attacker.get("counters",{}).get("shield",0)) for blocker in legal)
        if favorable or (difficulty=="standard" and power>=max(_stats(state,blocker)[1] for blocker in legal)):selected.append(card_id)
    if difficulty=="expert" and len(ids)>len(blockers):
        pressure=sorted((card_id for card_id in ids if card_id not in selected),key=lambda card_id:_stats(state,attackers[card_id])[0],reverse=True)
        selected.extend(pressure[:max(0,len(ids)-len(blockers)-len(selected))])
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
    return result


def _ability_score(state:dict,action:dict)->float:
    text=(action.get("label") or "").casefold();source=_card(state,"bot",action["card_id"]);score=0.5
    draw=re.search(r"draw (?:a|one|two|three|four|five|\d+) cards?",text)
    if draw:
        word=draw.group(0).split()[1];score+={"a":2,"one":2,"two":4,"three":6,"four":8,"five":10}.get(word,int(word)*2 if word.isdigit() else 2)
    if any(term in text for term in ("destroy target","exile target","counter target")):score+=7
    if "create " in text and " token" in text:score+=4
    if "deals " in text and " damage" in text:score+=4
    if "you gain " in text and " life" in text:score+=2
    if action.get("self_sacrifice"):score-=_threat_score(state,source)*.65
    score-=float(action.get("life_cost") or 0)*1.25
    counter_cost=action.get("counter_cost") or {};score-=float(counter_cost.get("amount") or 0)*.75
    bot=next(player for player in state["players"] if player["id"]=="bot")
    if action.get("life_cost",0)>=bot["life"]:score-=100
    return score


def choose_bot_action(state: dict, difficulty: str = "standard") -> dict | None:
    actions = legal_actions(state, "bot")
    if not actions:
        return None
    by_type = {kind: [action for action in actions if action["type"] == kind] for kind in {action["type"] for action in actions}}
    if "keep" in by_type:
        return by_type.get("mulligan",by_type["keep"])[0] if _should_mulligan(state,difficulty) else by_type["keep"][0]
    if "pay_ward" in by_type or "decline_ward" in by_type:
        if "pay_ward" in by_type and difficulty!="beginner":
            action=by_type["pay_ward"][0]
            if action.get("cost_type")=="discard":
                amount=action.get("amount",1);ranked=sorted(action["card_ids"],key=lambda card_id:(_card(state,"bot",card_id).get("mana_value") or 0,"Land" not in _card(state,"bot",card_id).get("type_line","")));return {**action,"card_ids":ranked[:amount]}
            return action
        return by_type["decline_ward"][0]
    if "choose_trigger_target" in by_type:
        action=by_type["choose_trigger_target"][0]
        return {"type":"choose_trigger_target","target_id":_choose_target(state,action)}
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
    if "sacrifice_permanents" in by_type:
        action=by_type["sacrifice_permanents"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:((_card(state,"bot",card_id).get("mana_value") or 0),sum(_stats(state,_card(state,"bot",card_id)))))
        return {"type":"sacrifice_permanents","card_ids":ranked[:amount]}
    if "choose_legendary" in by_type:
        action=by_type["choose_legendary"][0];choice=max(action["card_ids"],key=lambda card_id:((_card(state,"bot",card_id).get("mana_value") or 0),sum(_stats(state,_card(state,"bot",card_id)))))
        return {"type":"choose_legendary","card_ids":[choice]}
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
    if "cast" in by_type:
        spells = by_type["cast"]
        if difficulty == "beginner":
            choice = random.choice(spells)
        else:
            choice = max(spells, key=lambda action: (_card(state, "bot", action["card_id"]).get("mana_value") or 0, len(_card(state, "bot", action["card_id"]).get("oracle_text") or "")))
        if choice.get("targets"):
            choice = {**choice, "target_id":_choose_target(state,choice)}
        return choice
    if "activate" in by_type:
        choices=by_type["activate"];choice=max(choices,key=lambda action:_ability_score(state,action))
        if difficulty=="beginner" or _ability_score(state,choice)>0:
            if choice.get("targets"):
                choice={**choice,"target_id":_choose_target(state,choice)}
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
    return by_type.get("advance_phase", [None])[0]


def run_bot(state: dict, difficulty: str = "standard", limit: int = 80) -> dict:
    steps = 0
    while steps < limit and state["status"] != "complete":
        action = choose_bot_action(state, difficulty)
        if not action:
            break
        state = perform_action(state, "bot", action)
        steps += 1
        if state["status"] == "active" and state["active_player_id"] != "bot" and not state["stack"]:
            break
    return state
