import random

from .game_engine import legal_actions, perform_action


def _card(state: dict, player_id: str, instance_id: str) -> dict:
    player = next(player for player in state["players"] if player["id"] == player_id)
    return next(card for zone in (player["hand"], player["battlefield"]) for card in zone if card["instance_id"] == instance_id)


def _stats(card:dict)->tuple[int,int]:
    try:return int(card.get("power") or 0),int(card.get("toughness") or 0)
    except ValueError:return 0,0


def _should_mulligan(state:dict,difficulty:str)->bool:
    bot=next(player for player in state["players"] if player["id"]=="bot");lands=sum("Land" in card.get("type_line","") for card in bot["hand"]);mulligans=bot.get("mulligans",0)
    if difficulty=="beginner" or mulligans>=3:return False
    if difficulty=="standard":return lands<2 or lands>5
    castable_curve=sum(1 for card in bot["hand"] if "Land" not in card.get("type_line","") and (card.get("mana_value") or 0)<=3)
    return lands<2 or lands>4 or castable_curve<2


def _choose_blocks(state:dict,action:dict,difficulty:str)->dict[str,str]:
    bot=next(player for player in state["players"] if player["id"]=="bot");enemy=next(player for player in state["players"] if player["id"]!="bot")
    blockers={card["instance_id"]:card for card in bot["battlefield"]};attackers={card["instance_id"]:card for card in enemy["battlefield"] if card["instance_id"] in state["combat"]["attackers"]};available=set(action["card_ids"]);result={}
    ordered=sorted(attackers,key=lambda card_id:_stats(attackers[card_id])[0],reverse=True)
    for attacker_id in ordered:
        legal=[blocker_id for blocker_id in available if attacker_id in action.get("legal_blocks",{}).get(blocker_id,[])]
        menace=any(keyword.casefold()=="menace" for keyword in attackers[attacker_id].get("keywords",[])) or "menace" in (attackers[attacker_id].get("oracle_text") or "").casefold();needed=2 if menace else 1
        if len(legal)<needed:continue
        if difficulty=="beginner":
            if random.random()<.45:continue
            chosen=random.sample(legal,needed)
        else:
            power,toughness=_stats(attackers[attacker_id]);legal.sort(key=lambda blocker_id:(_stats(blockers[blocker_id])[0]>=toughness,_stats(blockers[blocker_id])[1]>power,_stats(blockers[blocker_id])[0]),reverse=True)
            chosen=legal[:needed]
            if difficulty=="standard" and sum(_stats(blockers[blocker_id])[0] for blocker_id in chosen)<toughness and power<4:continue
        for blocker_id in chosen:result[blocker_id]=attacker_id;available.remove(blocker_id)
    return result


def choose_bot_action(state: dict, difficulty: str = "standard") -> dict | None:
    actions = legal_actions(state, "bot")
    if not actions:
        return None
    by_type = {kind: [action for action in actions if action["type"] == kind] for kind in {action["type"] for action in actions}}
    if "keep" in by_type:
        return by_type.get("mulligan",by_type["keep"])[0] if _should_mulligan(state,difficulty) else by_type["keep"][0]
    if "bottom_mulligan_cards" in by_type:
        action=by_type["bottom_mulligan_cards"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:("Land" in _card(state,"bot",card_id).get("type_line",""),-(_card(state,"bot",card_id).get("mana_value") or 0)))
        return {"type":"bottom_mulligan_cards","card_ids":ranked[:amount]}
    if "resolve" in by_type:
        return by_type["resolve"][0]
    if "discard_to_hand_size" in by_type:
        action=by_type["discard_to_hand_size"][0];amount=action["amount"]
        ranked=sorted(action["card_ids"],key=lambda card_id:(_card(state,"bot",card_id).get("mana_value") or 0,"Land" not in _card(state,"bot",card_id).get("type_line","")))
        return {"type":"discard_to_hand_size","card_ids":ranked[:amount]}
    if "play_land" in by_type:
        return by_type["play_land"][0]
    if "cast" in by_type:
        spells = by_type["cast"]
        if difficulty == "beginner":
            choice = random.choice(spells)
        else:
            choice = max(spells, key=lambda action: (_card(state, "bot", action["card_id"]).get("mana_value") or 0, len(_card(state, "bot", action["card_id"]).get("oracle_text") or "")))
        if choice.get("targets"):
            opposing = [target for target in choice["targets"] if target["controller_id"] != "bot"]
            choice = {**choice, "target_id": (opposing or choice["targets"])[0]["id"]}
        return choice
    if "activate" in by_type:
        choices=by_type["activate"];choice=max(choices,key=lambda action:len(action.get("label", "")))
        if choice.get("targets"):
            opposing=[target for target in choice["targets"] if target["controller_id"]!="bot"];choice={**choice,"target_id":(opposing or choice["targets"])[0]["id"]}
        return choice
    if "declare_attackers" in by_type:
        action = by_type["declare_attackers"][0]
        if difficulty == "beginner":
            ids = action["card_ids"][: max(1, len(action["card_ids"]) // 2)]
        else:
            ids = action["card_ids"]
        return {"type": "declare_attackers", "attacker_ids": ids}
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
