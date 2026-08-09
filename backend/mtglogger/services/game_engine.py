import random
import re
import uuid
from copy import deepcopy


PHASES = ("beginning", "precombat_main", "combat", "postcombat_main", "ending")
BASIC_COLORS = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


class RuleViolation(ValueError):
    pass


def _id() -> str:
    return str(uuid.uuid4())


def _log(state: dict, message: str) -> None:
    state["log"].append({"id": _id(), "turn": state["turn"], "message": message})
    state["log"] = state["log"][-250:]


def _player(state: dict, player_id: str) -> dict:
    return next(player for player in state["players"] if player["id"] == player_id)


def opponent(state: dict, player_id: str) -> dict:
    return next(player for player in state["players"] if player["id"] != player_id)


def _draw(state: dict, player: dict, amount: int = 1) -> None:
    for _ in range(amount):
        if not player["library"]:
            player["lost"] = True
            state["status"] = "complete"
            state["winner_id"] = opponent(state, player["id"])["id"]
            _log(state, f"{player['name']} tried to draw from an empty library and lost.")
            return
        player["hand"].append(player["library"].pop())


def _continuous_stats(state:dict|None,card:dict)->tuple[int,int]:
    if not state or "Creature" not in card.get("type_line",""):return 0,0
    power=toughness=0;controller=card.get("controller_id");type_line=card.get("type_line","").casefold()
    for owner in state["players"]:
        for source in owner["battlefield"]:
            clauses=re.split(r"(?<=[.!])\s+|\n",source.get("oracle_text") or "")
            for clause in clauses:
                lower=clause.casefold()
                if ":" in clause or "until end of turn" in lower or "as long as" in lower or re.match(r"\s*(?:when|whenever|if)\b",lower):continue
                for match in re.finditer(r"\b(other )?((?:[a-z]+ )?creatures|creature tokens) (you|your opponents) control get ([+-]\d+)/([+-]\d+)",clause,re.IGNORECASE):
                    other,group,scope=bool(match.group(1)),match.group(2).casefold(),match.group(3).casefold();source_controller=source.get("controller_id",owner["id"])
                    if other and source["instance_id"]==card.get("instance_id"):continue
                    if (scope=="you" and controller!=source_controller) or (scope=="your opponents" and controller==source_controller):continue
                    if group=="creature tokens" and not card.get("token"):continue
                    qualifier=group.removesuffix(" creatures")
                    if qualifier not in {"creature","creatures"} and group!="creature tokens" and qualifier not in type_line:continue
                    power+=int(match.group(4));toughness+=int(match.group(5))
    return power,toughness


def _parse_stats(card: dict,state:dict|None=None) -> tuple[int, int]:
    try:
        plus = card.get("counters", {}).get("+1/+1", 0); minus = card.get("counters", {}).get("-1/-1", 0)
        static_power,static_toughness=_continuous_stats(state,card)
        return int(card.get("power") or 0) + plus - minus + card.get("temporary_power", 0)+static_power, int(card.get("toughness") or 0) + plus - minus + card.get("temporary_toughness", 0)+static_toughness
    except ValueError:
        return 0, 0


def _mana_symbols(card: dict) -> list[str]:
    return re.findall(r"\{([^}]+)\}", card.get("mana_cost") or "")


def _land_colors(card: dict) -> set[str]:
    text = f"{card.get('name', '')} {card.get('type_line', '')} {card.get('oracle_text', '')}"
    colors = {color for land, color in BASIC_COLORS.items() if land.casefold() in text.casefold()}
    colors.update(re.findall(r"Add \{([WUBRGC])\}", text, re.IGNORECASE))
    if "mana of any color" in text.casefold():
        colors.update("WUBRG")
    return {color.upper() for color in colors}


def _mana_source(card: dict) -> bool:
    return "Land" in card.get("type_line", "") or re.search(r"\{T\}:\s*Add ", card.get("oracle_text") or "", re.IGNORECASE) is not None


def _mana_requirements(card: dict, extra_generic: int = 0) -> tuple[list[set[str]], int]:
    colored=[];generic=extra_generic
    for symbol in _mana_symbols(card):
        if symbol.isdigit():generic+=int(symbol);continue
        choices={part for part in symbol.upper().split("/") if part in "WUBRGC"}
        if choices:colored.append(choices)
    return colored,generic


def _has_keyword(card: dict, keyword: str) -> bool:
    printed={value.casefold() for value in card.get("keywords", [])};temporary={value.casefold() for value in card.get("temporary_keywords", [])}
    return keyword.casefold() in printed|temporary or re.search(rf"\b{re.escape(keyword.casefold())}\b", (card.get("oracle_text") or "").casefold()) is not None


def _toxic_value(card: dict) -> int:
    match=re.search(r"\btoxic (\d+)\b",card.get("oracle_text") or "",re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _card_colors(card: dict) -> set[str]:
    return {part for symbol in _mana_symbols(card) for part in symbol.upper().split("/") if part in "WUBRG"}


def _protected_from(card: dict, source: dict) -> bool:
    text = (card.get("oracle_text") or "").casefold()
    if "protection from everything" in text:return True
    colors = _card_colors(source)
    names = {"W":"white","U":"blue","B":"black","R":"red","G":"green"}
    if any(f"protection from {names[color]}" in text for color in colors):return True
    source_types=source.get("type_line","").casefold()
    for kind in ("artifact","creature","enchantment","instant","land","planeswalker","sorcery"):
        if kind in source_types and f"protection from {kind}s" in text:return True
    return len(colors)>1 and "protection from multicolored" in text


def _consume_shield(state:dict,card:dict,reason:str)->bool:
    shields=card.get("counters",{}).get("shield",0)
    if shields<=0:return False
    card["counters"]["shield"]=shields-1
    _log(state,f"A shield counter protected {card['name']} from {reason}.")
    return True


def _damage_permanent(state:dict,target:dict,amount:int,source:dict)->int:
    if amount<=0:return 0
    if _protected_from(target,source):
        _log(state,f"Protection prevented {amount} damage to {target['name']}.");return 0
    if _consume_shield(state,target,"damage"):return 0
    if _has_keyword(source,"Infect") or _has_keyword(source,"Wither"):
        target["counters"]["-1/-1"]=target["counters"].get("-1/-1",0)+amount
    elif "Planeswalker" in target.get("type_line",""):
        target["counters"]["loyalty"]=max(0,target["counters"].get("loyalty",0)-amount)
    else:target["damage"]+=amount
    return amount


def _destroy_permanent(state:dict,owner:dict,card:dict)->bool:
    if _has_keyword(card,"Indestructible"):return False
    if _consume_shield(state,card,"destruction"):return False
    _leave_battlefield(state,owner,card,"graveyard");return True


def _ward_details(card:dict)->dict|None:
    text=card.get("oracle_text") or "";mana=re.search(r"\bward\s*[—-]?\s*((?:\{[^}]+\})+)",text,re.IGNORECASE)
    if mana:return {"cost_type":"mana","mana_cost":mana.group(1).upper(),"amount":0,"label":mana.group(1).upper()}
    life=re.search(r"\bward\s*[—-]?\s*pay (\d+) life\b",text,re.IGNORECASE)
    if life:return {"cost_type":"life","mana_cost":"","amount":int(life.group(1)),"label":f"Pay {life.group(1)} life"}
    discard=re.search(r"\bward\s*[—-]?\s*discard (a|one|two|three|four|five|\d+) cards?\b",text,re.IGNORECASE)
    if discard:
        value=discard.group(1).casefold();amount={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(value,int(value) if value.isdigit() else 1)
        return {"cost_type":"discard","mana_cost":"","amount":amount,"label":f"Discard {amount} card{'s' if amount!=1 else ''}"}
    return None


def _ward_cost(card:dict)->str:
    details=_ward_details(card)
    return details["label"] if details else ""


def _activated_abilities(card: dict) -> list[dict]:
    abilities = []
    for line in (card.get("oracle_text") or "").splitlines():
        match = re.match(r"^([^:]+):\s*(.+)$", line.strip())
        if not match: continue
        cost,effect = match.group(1).strip(),match.group(2).strip()
        mana_cost="".join(re.findall(r"\{[^}]+\}",cost,re.IGNORECASE)).upper().replace("{T}","").replace("{Q}","")
        taps="{T}" in cost.upper()
        source_name=re.escape(card.get("name", ""));self_reference=rf"(?:~|this (?:artifact|creature|permanent)|{source_name})"
        self_sacrifice=re.search(rf"\bsacrifice {self_reference}\b",cost,re.IGNORECASE) is not None
        life_match=re.search(r"\bpay (\d+) life\b",cost,re.IGNORECASE);life_cost=int(life_match.group(1)) if life_match else 0
        counter_match=re.search(rf"\bremove (a|one|two|three|four|five|\d+) ([\w+/-]+) counters? from {self_reference}\b",cost,re.IGNORECASE)
        counter_cost=None
        if counter_match:
            word=counter_match.group(1).casefold();amount={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(word,int(word) if word.isdigit() else 1);counter_cost={"name":counter_match.group(2).replace("−","-"),"amount":amount}
        words={"a":1,"an":1,"one":1,"two":2,"three":3,"four":4,"five":5};selection_cost=None
        discard_match=re.search(r"\bdiscard (a|one|two|three|four|five|\d+) cards?\b",cost,re.IGNORECASE)
        if discard_match:
            word=discard_match.group(1).casefold();selection_cost={"kind":"discard","filter":"card","amount":words.get(word,int(word) if word.isdigit() else 1),"exclude_source":False}
        sacrifice_match=None if self_sacrifice else re.search(r"\bsacrifice (another |a |an |one |two |three )?(creature|artifact|permanent)s?\b",cost,re.IGNORECASE)
        if sacrifice_match:
            count_word=(sacrifice_match.group(1) or "a").strip().casefold();selection_cost={"kind":"sacrifice","filter":sacrifice_match.group(2).casefold(),"amount":words.get(count_word,1),"exclude_source":count_word=="another"}
        unsupported=("discard" in cost.casefold() and not selection_cost) or ("sacrifice" in cost.casefold() and not self_sacrifice and not selection_cost) or ("remove" in cost.casefold() and "counter" in cost.casefold() and not counter_cost)
        if unsupported or (not taps and not mana_cost and not self_sacrifice and not life_cost and not counter_cost and not selection_cost):continue
        if re.match(r"add (?:\{|one mana)", effect, re.IGNORECASE): continue
        ability_card = {**card, "name": f"{card['name']} ability", "oracle_text": effect, "type_line": "Ability", "mana_cost": ""}
        abilities.append({"cost":cost,"mana_cost":mana_cost,"taps":taps,"self_sacrifice":self_sacrifice,"life_cost":life_cost,"counter_cost":counter_cost,"selection_cost":selection_cost,"effect":effect,"card":ability_card})
    return abilities


def _loyalty_abilities(card:dict)->list[dict]:
    abilities=[]
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^([+−-]?\d+):\s*(.+)$",line.strip())
        if not match:continue
        cost=int(match.group(1).replace("−","-"));effect=match.group(2).strip();ability_card={**card,"name":f"{card['name']} loyalty ability","oracle_text":effect,"type_line":"Ability","mana_cost":""};abilities.append({"cost":cost,"effect":effect,"card":ability_card})
    return abilities


def _activated_cost_options(player:dict,source:dict,selection_cost:dict|None)->list[dict]:
    if not selection_cost:return []
    if selection_cost["kind"]=="discard":return list(player["hand"])
    kind=selection_cost["filter"].casefold()
    return [card for card in player["battlefield"] if (not selection_cost.get("exclude_source") or card["instance_id"]!=source["instance_id"]) and (kind=="permanent" or kind in card.get("type_line","").casefold())]


def _can_pay(player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None) -> bool:
    available = []
    for permanent in player["battlefield"]:
        if permanent.get("instance_id")!=excluded_id and not permanent.get("tapped") and _mana_source(permanent) and not ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste")):
            available.append(_land_colors(permanent) or {"C"})
    colored,generic=_mana_requirements(card,extra_generic)
    for choices in colored:
        match = next((colors for colors in available if colors & choices), None)
        if not match:
            return False
        available.remove(match)
    return len(available) >= generic


def _pay_mana(player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None) -> None:
    colored,generic=_mana_requirements(card,extra_generic)
    lands = [permanent for permanent in player["battlefield"] if permanent.get("instance_id")!=excluded_id and not permanent.get("tapped") and _mana_source(permanent) and not ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))]
    chosen = []
    for choices in colored:
        land = next((item for item in lands if (_land_colors(item) or {"C"}) & choices), None)
        if not land:
            raise RuleViolation("Not enough colored mana")
        lands.remove(land)
        chosen.append(land)
    chosen.extend(lands[:generic])
    if len(chosen) < len(colored) + generic:
        raise RuleViolation("Not enough mana")
    for land in chosen:
        land["tapped"] = True


def _new_player(player_id: str, name: str, deck: list[dict], is_bot: bool, format_name: str = "") -> dict:
    library = []
    for source in deck:
        for _ in range(source.get("quantity", 1)):
            card = deepcopy(source)
            card["instance_id"] = _id()
            card["owner_id"] = player_id
            card["controller_id"] = player_id
            card["tapped"] = False
            card["damage"] = 0
            card["counters"] = {}
            if "Planeswalker" in card.get("type_line",""):
                try:card["counters"]["loyalty"]=int(card.get("loyalty") or 0)
                except ValueError:card["counters"]["loyalty"]=0
            card["summoning_sick"] = False
            card.pop("quantity", None)
            library.append(card)
    command = []
    is_commander = "commander" in (format_name or "").casefold()
    if is_commander:
        commander = next((card for card in library if "Legendary" in card.get("type_line", "") and "Creature" in card.get("type_line", "")), None)
        if commander:
            library.remove(commander); commander["commander"] = True; command.append(commander)
    random.SystemRandom().shuffle(library)
    return {"id": player_id, "name": name, "is_bot": is_bot, "format": format_name, "life": 40 if is_commander else 20, "poison": 0, "library": library, "hand": [], "battlefield": [], "graveyard": [], "exile": [], "command": command, "commander_casts": 0, "commander_damage": {}, "land_plays_remaining": 1, "kept_hand": False, "mulligans": 0, "lost": False}


def new_game(player_deck: list[dict], opponent_deck: list[dict], play_first: bool = True, opponent_is_bot: bool = True, player_format: str = "", opponent_format: str = "") -> dict:
    human_id, bot_id = "player", "bot"
    players = [_new_player(human_id, "You", player_deck, False, player_format), _new_player(bot_id, "Bot" if opponent_is_bot else "Guest", opponent_deck, opponent_is_bot, opponent_format)]
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id, "players": players, "stack": [], "combat": {"attackers": [], "blocks": {}, "attack_targets": {},"block_orders":{},"damage_pending":False}, "consecutive_passes": 0, "pending_phase_advance": False, "pending_discard": None, "pending_mulligan_bottom": None, "pending_sacrifice": None, "pending_legendary": None,"pending_scry":None,"pending_damage_order":None,"pending_ward":None,"pending_trigger_targets":[], "log": []}
    for player in players:
        _draw(state, player, 7)
    _log(state, "Opening hands drawn. Choose whether to keep or mulligan.")
    return state


def public_state(state: dict, viewer_id: str = "player") -> dict:
    visible = deepcopy(state)
    originals={card["instance_id"]:card for owner in state["players"] for card in owner["battlefield"]}
    for owner in visible["players"]:
        for card in owner["battlefield"]:
            card["effective_power"],card["effective_toughness"]=_parse_stats(originals[card["instance_id"]],state)
    for player in visible["players"]:
        player["library_count"] = len(player.pop("library"))
        if player["id"] != viewer_id:
            player["hand_count"] = len(player["hand"])
            player["hand"] = []
    return visible


def _target_kind(card: dict) -> str | None:
    text = (card.get("oracle_text") or "").casefold()
    if "counter target spell" in text: return "spell"
    if re.search(r"target creature card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_creature"
    if re.search(r"target (?:nonland )?card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_card"
    if re.search(r"target player mills?", text): return "player"
    if re.search(r"target player sacrifices?",text):return "player"
    if re.search(r"(?:destroy|exile) target (?:artifact, creature, enchantment, planeswalker|nonland permanent|permanent)", text): return "permanent"
    if re.search(r"(?:destroy|exile|tap|untap|return) target creature", text) or re.search(r"target creature .*(?:gets [+-]\d+/[+-]\d+|gains? [^.]+ until end of turn)", text) or re.search(r"(?:deals \d+ damage|put .+ counters?) (?:to|on) target creature", text): return "creature"
    if re.search(r"return target (?:nonland )?permanent", text): return "permanent"
    if re.search(r"deals \d+ damage to any target", text): return "any"
    return None


def _spell_targeting_card(card:dict)->dict:
    if not any(kind in card.get("type_line","") for kind in ("Creature","Artifact","Enchantment","Planeswalker","Battle")):return card
    clauses=re.split(r"(?<=[.!])\s+|\n",card.get("oracle_text") or "");spell_text=" ".join(clause for clause in clauses if not re.match(r"\s*(?:when|whenever|at the beginning)\b",clause,re.IGNORECASE))
    return {**card,"oracle_text":spell_text}


def _targets(state: dict, caster_id: str, card: dict) -> list[dict]:
    kind = _target_kind(card)
    if not kind: return []
    text = (card.get("oracle_text") or "").casefold()
    targets = []
    if kind == "spell":
        return [{"id": item["id"], "name": item["card"]["name"], "kind": "spell", "controller_id": item["controller_id"]} for item in state["stack"]]
    if kind in {"graveyard_creature","graveyard_card"}:
        own_only="your graveyard" in text
        return [{"id":graveyard_card["instance_id"],"name":graveyard_card["name"],"kind":"card","controller_id":owner["id"]} for owner in state["players"] if not own_only or owner["id"]==caster_id for graveyard_card in owner["graveyard"] if kind=="graveyard_card" or "Creature" in graveyard_card.get("type_line","")]
    for player in state["players"]:
        if kind in {"any", "player"}: targets.append({"id": player["id"], "name": player["name"], "kind": "player", "controller_id": player["id"]})
        for permanent in player["battlefield"]:
            if kind in {"any", "permanent"} or (kind == "creature" and "Creature" in permanent.get("type_line", "")):
                if "you control" in text and player["id"] != caster_id: continue
                if "an opponent controls" in text and player["id"] == caster_id: continue
                if "nonland permanent" in text and "Land" in permanent.get("type_line", ""): continue
                if _has_keyword(permanent,"Shroud") or (player["id"] != caster_id and _has_keyword(permanent,"Hexproof")): continue
                if _protected_from(permanent,card): continue
                targets.append({"id": permanent["instance_id"], "name": permanent["name"], "kind": "permanent", "controller_id": player["id"]})
    return targets


def _countered_spell_destination(state:dict,controller:dict,card:dict)->None:
    owner=_player(state,card.get("owner_id",controller["id"]));card["controller_id"]=owner["id"]
    if card.get("commander"):owner["command"].append(card)
    else:owner["graveyard"].append(card)


def _multiplayer(state: dict) -> bool:
    return not any(player.get("is_bot") for player in state["players"])


def _pending_decision(state:dict)->bool:
    return bool(state.get("pending_discard") or state.get("pending_sacrifice") or state.get("pending_legendary") or state.get("pending_scry") or state.get("pending_damage_order") or state.get("pending_ward") or state.get("pending_trigger_targets"))


def _queue_ward(state:dict,caster:dict,target_id:str|None,stack_item:dict)->None:
    if not target_id:return
    target_owner=next((owner for owner in state["players"] if any(card["instance_id"]==target_id for card in owner["battlefield"])),None)
    target=next((card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"]==target_id),None);details=_ward_details(target or {})
    if target and target_owner and target_owner["id"]!=caster["id"] and details:
        state["pending_ward"]={"player_id":caster["id"],"stack_id":stack_item["id"],"source_name":target["name"],**details};state["priority_player_id"]=caster["id"];_log(state,f"{target['name']}'s ward requires {details['label']}.")


def _commander_tax(player: dict, card: dict) -> int:
    return player.get("commander_casts", 0) * 2 if card.get("commander") else 0


def _maximum_hand_size(player:dict)->int|None:
    texts=[(card.get("oracle_text") or "").casefold() for card in player["battlefield"]]
    if any("you have no maximum hand size" in text for text in texts):return None
    increases=sum(int(value) for text in texts for value in re.findall(r"maximum hand size is increased by (\d+)",text))
    return 7+increases


def legal_actions(state: dict, player_id: str) -> list[dict]:
    if state["status"] == "complete":
        return []
    player = _player(state, player_id)
    pending_discard=state.get("pending_discard")
    if pending_discard:
        if pending_discard["player_id"] != player_id:return []
        return [{"type":"discard_cards","card_ids":[card["instance_id"] for card in player["hand"]],"amount":pending_discard["amount"],"reason":pending_discard.get("reason","cleanup")},{"type":"concede"}]
    pending_sacrifice=state.get("pending_sacrifice")
    if pending_sacrifice:
        if pending_sacrifice["player_id"]!=player_id:return []
        return [{"type":"sacrifice_permanents","card_ids":pending_sacrifice["card_ids"],"amount":pending_sacrifice["amount"]},{"type":"concede"}]
    pending_legendary=state.get("pending_legendary")
    if pending_legendary:
        if pending_legendary["player_id"]!=player_id:return []
        return [{"type":"choose_legendary","card_ids":pending_legendary["card_ids"],"amount":1},{"type":"concede"}]
    pending_scry=state.get("pending_scry")
    if pending_scry:
        if pending_scry["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["library"]};cards=[cards_by_id[card_id] for card_id in pending_scry["card_ids"] if card_id in cards_by_id]
        return [{"type":pending_scry.get("mode","scry"),"card_ids":pending_scry["card_ids"],"cards":cards,"amount":pending_scry["amount"]},{"type":"concede"}]
    pending_damage=state.get("pending_damage_order")
    if pending_damage:
        if pending_damage["player_id"]!=player_id:return []
        battlefield={card["instance_id"]:card for owner in state["players"] for card in owner["battlefield"]}
        groups=[{"attacker":battlefield[attacker_id],"blockers":[battlefield[blocker_id] for blocker_id in blocker_ids if blocker_id in battlefield]} for attacker_id,blocker_ids in pending_damage["groups"].items() if attacker_id in battlefield]
        return [{"type":"order_blockers","groups":groups},{"type":"concede"}]
    pending_ward=state.get("pending_ward")
    if pending_ward:
        if pending_ward["player_id"]!=player_id:return []
        common={"cost_type":pending_ward.get("cost_type","mana"),"mana_cost":pending_ward.get("mana_cost",""),"amount":pending_ward.get("amount",0),"label":pending_ward.get("label",pending_ward.get("mana_cost","")),"source_name":pending_ward["source_name"]};actions=[{"type":"decline_ward",**common},{"type":"concede"}];kind=common["cost_type"]
        can_pay=(kind=="mana" and _can_pay(player,{"mana_cost":common["mana_cost"]})) or (kind=="life" and player["life"]>=common["amount"]) or (kind=="discard" and len(player["hand"])>=common["amount"])
        if can_pay:actions.insert(0,{"type":"pay_ward",**common,**({"card_ids":[card["instance_id"] for card in player["hand"]]} if kind=="discard" else {})})
        return actions
    pending_triggers=state.get("pending_trigger_targets") or []
    if pending_triggers:
        pending=pending_triggers[0]
        if pending["controller_id"]!=player_id:return []
        targets=_targets(state,player_id,pending["card"])
        return ([{"type":"choose_trigger_target","targets":targets,"label":pending["card"]["oracle_text"],"source_name":pending["source_name"]},{"type":"concede"}] if targets else [{"type":"skip_trigger","source_name":pending["source_name"]},{"type":"concede"}])
    if state["status"] == "mulligan":
        if player["kept_hand"]:
            return []
        if state.get("pending_mulligan_bottom") == player_id:
            return [{"type":"bottom_mulligan_cards","card_ids":[card["instance_id"] for card in player["hand"]],"amount":player.get("mulligans",0)}]
        return [{"type": "keep"}, {"type": "mulligan"}]
    if state["priority_player_id"] != player_id:
        return []
    actions = [{"type": "concede"}]
    active = state["active_player_id"] == player_id
    main = state["phase"] in {"precombat_main", "postcombat_main"}
    if active and main and not state["stack"]:
        if player["land_plays_remaining"]:
            actions.extend({"type": "play_land", "card_id": card["instance_id"]} for card in player["hand"] if "Land" in card.get("type_line", ""))
    castable = [(card, "hand") for card in player["hand"]]
    castable.extend((card, "command") for card in player.get("command", []))
    for card, source in castable:
        instant_speed = "Instant" in card.get("type_line", "") or _has_keyword(card, "Flash")
        if "Land" in card.get("type_line", "") or not ((active and main and not state["stack"]) or instant_speed) or not _can_pay(player, card, _commander_tax(player, card)): continue
        targeting_card=_spell_targeting_card(card);targets = _targets(state, player_id, targeting_card)
        if _target_kind(targeting_card) and not targets: continue
        action = {"type": "cast", "card_id": card["instance_id"], "source": source, "commander_tax": _commander_tax(player, card)}
        if targets: action["targets"] = targets
        actions.append(action)
    for permanent in player["battlefield"]:
        for index, ability in enumerate(_activated_abilities(permanent)):
            if ability["taps"] and (permanent.get("tapped") or ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))):continue
            if ability["mana_cost"] and not _can_pay(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None):continue
            if ability["life_cost"] and player["life"]<ability["life_cost"]:continue
            if ability["counter_cost"] and permanent.get("counters",{}).get(ability["counter_cost"]["name"],0)<ability["counter_cost"]["amount"]:continue
            cost_options=_activated_cost_options(player,permanent,ability["selection_cost"])
            if ability["selection_cost"] and len(cost_options)<ability["selection_cost"]["amount"]:continue
            targets = _targets(state, player_id, ability["card"])
            if _target_kind(ability["card"]) and not targets: continue
            action = {"type": "activate", "card_id": permanent["instance_id"], "ability_index": index, "label": f"{ability['cost']}: {ability['effect']}","life_cost":ability["life_cost"],"self_sacrifice":ability["self_sacrifice"],"counter_cost":ability["counter_cost"],"cost_kind":ability["selection_cost"]["kind"] if ability["selection_cost"] else None,"cost_amount":ability["selection_cost"]["amount"] if ability["selection_cost"] else 0,"cost_options":[card["instance_id"] for card in cost_options]}
            if targets: action["targets"] = targets
            actions.append(action)
    if active and main and not state["stack"]:
        for permanent in player["battlefield"]:
            if "Planeswalker" not in permanent.get("type_line","") or permanent.get("loyalty_activated_turn")==state["turn"]:continue
            current=permanent.get("counters",{}).get("loyalty",0)
            for index,ability in enumerate(_loyalty_abilities(permanent)):
                if current+ability["cost"]<0:continue
                targets=_targets(state,player_id,ability["card"])
                if _target_kind(ability["card"]) and not targets:continue
                action={"type":"activate_loyalty","card_id":permanent["instance_id"],"ability_index":index,"label":f"{ability['cost']:+d}: {ability['effect']}"}
                if targets:action["targets"]=targets
                actions.append(action)
    if _multiplayer(state):
        actions.append({"type": "pass_priority"})
        if active and not state["stack"] and not state.get("pending_phase_advance") and not state["combat"].get("damage_pending"):
            actions.append({"type": "advance_phase"})
    elif state["stack"]:
        actions.append({"type": "resolve"})
    elif state["combat"].get("damage_pending"):
        actions.append({"type":"resolve_combat_damage"})
    else:
        actions.append({"type": "advance_phase"})
    if active and state["phase"] == "combat" and not state["combat"]["attackers"]:
        eligible = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped") and not _has_keyword(card,"Defender") and "can't attack" not in (card.get("oracle_text") or "").casefold() and (not card.get("summoning_sick") or _has_keyword(card, "Haste"))]
        if eligible:
            defending=opponent(state,player_id);defenders=[{"id":defending["id"],"name":defending["name"],"kind":"player","controller_id":defending["id"]}]
            defenders.extend({"id":card["instance_id"],"name":card["name"],"kind":"permanent","controller_id":defending["id"]} for card in defending["battlefield"] if "Planeswalker" in card.get("type_line",""))
            actions.append({"type": "declare_attackers", "card_ids": eligible,"defenders":defenders})
    elif not active and state["phase"] == "combat" and state["combat"]["attackers"] and not state["combat"].get("damage_pending"):
        attackers = [card for card in opponent(state, player_id)["battlefield"] if card["instance_id"] in state["combat"]["attackers"]]
        blockers = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped")]
        if blockers:
            legal_blocks = {blocker["instance_id"]:[attacker["instance_id"] for attacker in attackers if "can't be blocked" not in (attacker.get("oracle_text") or "").casefold() and "unblockable" not in (attacker.get("oracle_text") or "").casefold() and (not _has_keyword(attacker,"Flying") or _has_keyword(blocker,"Flying") or _has_keyword(blocker,"Reach")) and not _protected_from(attacker,blocker)] for blocker in player["battlefield"] if blocker["instance_id"] in blockers}
            if any(legal_blocks.values()): actions.append({"type": "declare_blockers", "card_ids": blockers, "legal_blocks": legal_blocks})
    return actions


def _resolve_spell(state: dict) -> None:
    item = state["stack"].pop()
    card, caster = item["card"], _player(state, item["controller_id"])
    targeting_card=_spell_targeting_card(card) if item.get("kind","spell")=="spell" else card;target_kind=_target_kind(targeting_card);target_id=item.get("target_id")
    if target_kind and target_id not in {target["id"] for target in _targets(state,caster["id"],targeting_card)}:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card)
        _log(state,f"{card['name']} was countered because its target was no longer legal.");return
    text = (card.get("oracle_text") or "").casefold()
    is_permanent_spell = item.get("kind", "spell") == "spell" and any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"))
    effect_text = "" if is_permanent_spell and re.search(r"\b(?:when|whenever|at the beginning)\b", text) else text
    other = opponent(state, caster["id"])
    target_player = next((player for player in state["players"] if player["id"] == target_id), None)
    target_owner = next((player for player in state["players"] if any(permanent["instance_id"] == target_id for permanent in player["battlefield"])), None)
    target = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == target_id), None)
    source_permanent = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == item.get("source_id")), None)
    target_stack_item = next((entry for entry in state["stack"] if entry["id"] == target_id), None)
    graveyard_owner=next((player for player in state["players"] if any(graveyard_card["instance_id"]==target_id for graveyard_card in player["graveyard"])),None)
    graveyard_target=next((graveyard_card for player in state["players"] for graveyard_card in player["graveyard"] if graveyard_card["instance_id"]==target_id),None)
    draw_match = re.search(r"draw (?:a|one|two|three|four) cards?", effect_text)
    if draw_match:
        word = draw_match.group(0).split()[1]
        _draw(state, caster, {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}[word])
    life_match = re.search(r"you gain (\d+) life", effect_text)
    if life_match:
        caster["life"] += int(life_match.group(1))
    damage_match = re.search(r"deals (\d+) damage to (?:target opponent|each opponent)", effect_text)
    if damage_match:
        other["life"] -= int(damage_match.group(1))
    lose_life = re.search(r"(?:target opponent|each opponent) loses (\d+) life", effect_text)
    if lose_life: other["life"] -= int(lose_life.group(1))
    you_lose = re.search(r"you lose (\d+) life", effect_text)
    if you_lose: caster["life"] -= int(you_lose.group(1))
    targeted_damage = re.search(r"deals (\d+) damage to (?:any target|target creature)", effect_text)
    if targeted_damage and (target_player or target):
        amount = int(targeted_damage.group(1))
        if target_player:
            if _has_keyword(card,"Infect"):target_player["poison"]=target_player.get("poison",0)+amount
            else:target_player["life"] -= amount
        elif target:_damage_permanent(state,target,amount,card)
    if target and target_owner and re.search(r"destroy target (?:creature|permanent|nonland permanent)", effect_text):
        if _destroy_permanent(state,target_owner,target):_log(state, f"{target['name']} was destroyed.")
    if target and target_owner and re.search(r"exile target (?:creature|permanent|nonland permanent)", effect_text):
        _leave_battlefield(state, target_owner, target, "exile"); _log(state, f"{target['name']} was exiled.")
    if target and target_owner and re.search(r"return target (?:creature|permanent|nonland permanent).* to (?:its|their) owner'?s hand", effect_text):
        _leave_battlefield(state, target_owner, target, "hand"); _log(state, f"{target['name']} returned to its owner's hand.")
    if target and re.search(r"\btap target creature", effect_text): target["tapped"] = True
    if target and re.search(r"\buntap target creature", effect_text): target["tapped"] = False
    stats_match = re.search(r"target creature[^.]* gets ([+-]\d+)/([+-]\d+) until end of turn", effect_text)
    if target and stats_match:
        target["temporary_power"] = target.get("temporary_power", 0) + int(stats_match.group(1))
        target["temporary_toughness"] = target.get("temporary_toughness", 0) + int(stats_match.group(2))
    if source_permanent:
        source_name=re.escape(source_permanent.get("name","").casefold());self_stats=re.search(rf"(?:this creature|{source_name}) gets ([+-]\d+)/([+-]\d+) until end of turn",effect_text)
        if self_stats:
            source_permanent["temporary_power"]=source_permanent.get("temporary_power",0)+int(self_stats.group(1));source_permanent["temporary_toughness"]=source_permanent.get("temporary_toughness",0)+int(self_stats.group(2))
    counter_match=re.search(r"put (a|one|two|three|four|five|six|seven|eight|nine|ten|\d+) ([+−-]\d+/[+−-]\d+|loyalty|charge|shield|stun) counters? on target (?:creature|permanent|artifact|planeswalker)",effect_text)
    if target and counter_match:
        words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};amount=words.get(counter_match.group(1),int(counter_match.group(1)) if counter_match.group(1).isdigit() else 1);name=counter_match.group(2).replace("−","-")
        target["counters"][name]=target["counters"].get(name,0)+amount;_log(state,f"{target['name']} received {amount} {name} counter(s).")
    keyword_match=re.search(r"target creature gains? ([^.]+?) until end of turn",effect_text)
    if target and keyword_match:
        supported=("flying","first strike","double strike","deathtouch","haste","hexproof","indestructible","lifelink","menace","reach","trample","vigilance")
        gained=[keyword for keyword in supported if re.search(rf"\b{re.escape(keyword)}\b",keyword_match.group(1))]
        target["temporary_keywords"]=sorted(set(target.get("temporary_keywords",[]))|set(gained));_log(state,f"{target['name']} gained {', '.join(gained)} until end of turn.")
    mill_match = re.search(r"target player mills? (\d+|one|two|three|four|five|six|seven|eight|nine|ten) cards?", effect_text)
    if mill_match and target_player:
        words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}; amount=words.get(mill_match.group(1),int(mill_match.group(1)) if mill_match.group(1).isdigit() else 0)
        for _ in range(min(amount,len(target_player["library"]))): target_player["graveyard"].append(target_player["library"].pop())
        _log(state, f"{target_player['name']} milled {amount} card(s).")
    scry_match=re.search(r"\bscry (\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",effect_text)
    surveil_match=re.search(r"\bsurveil (\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",effect_text)
    library_match,mode=(surveil_match,"surveil") if surveil_match else (scry_match,"scry")
    if library_match and not state.get("pending_scry"):
        words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};requested=words.get(library_match.group(1),int(library_match.group(1)) if library_match.group(1).isdigit() else 0);amount=min(requested,len(caster["library"]));ids=[card["instance_id"] for card in reversed(caster["library"][-amount:])] if amount else []
        if ids:state["pending_scry"]={"player_id":caster["id"],"amount":amount,"card_ids":ids,"mode":mode};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} is {mode}ing {amount}.")
    discard_match = re.search(r"(?:(target|each) opponent|you) discards? (a|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", effect_text)
    if discard_match:
        amount_word=discard_match.group(2);words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 0);affected=caster if discard_match.group(0).startswith("you") else other;required=min(amount,len(affected["hand"]))
        if required:state["pending_discard"]={"player_id":affected["id"],"amount":required,"reason":"effect"};state["priority_player_id"]=affected["id"];_log(state,f"{affected['name']} must discard {required} card(s).")
    elif re.search(r"(?:then |you )?discard (?:a|one|two|three|four|\d+) cards?",effect_text):
        match=re.search(r"discard (a|one|two|three|four|\d+) cards?",effect_text);word=match.group(1);words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(word,int(word) if word.isdigit() else 1);required=min(amount,len(caster["hand"]))
        if required:state["pending_discard"]={"player_id":caster["id"],"amount":required,"reason":"effect"};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} must discard {required} card(s).")
    if target_stack_item and "counter target spell" in effect_text:
        state["stack"].remove(target_stack_item); countered=target_stack_item["card"]
        if target_stack_item.get("kind", "spell") == "spell": _countered_spell_destination(state,_player(state,target_stack_item["controller_id"]),countered)
        _log(state, f"{countered['name']} was countered.")
    if graveyard_target and graveyard_owner:
        if re.search(r"(?:return|put) target (?:creature )?card .*graveyard (?:to|into|onto) (?:the battlefield|play)",effect_text):
            graveyard_owner["graveyard"].remove(graveyard_target);graveyard_target["controller_id"]=caster["id"];graveyard_target["summoning_sick"]="Creature" in graveyard_target.get("type_line","");caster["battlefield"].append(graveyard_target);_log(state,f"{graveyard_target['name']} returned to the battlefield under {caster['name']}'s control.")
            _queue_triggers(state,"enters",graveyard_target,caster)
        elif re.search(r"return target (?:creature )?card .*graveyard to (?:your|its owner'?s) hand",effect_text):
            graveyard_owner["graveyard"].remove(graveyard_target);graveyard_target["controller_id"]=graveyard_target.get("owner_id",graveyard_owner["id"]);_player(state,graveyard_target["controller_id"])["hand"].append(graveyard_target);_log(state,f"{graveyard_target['name']} returned to its owner's hand.")
        elif re.search(r"exile target (?:creature )?card .*graveyard",effect_text):
            graveyard_owner["graveyard"].remove(graveyard_target);graveyard_owner["exile"].append(graveyard_target);_log(state,f"{graveyard_target['name']} was exiled from a graveyard.")
    sacrifice_match=re.search(r"(?:target player|each opponent) sacrifices? (a|one|two|three|four|\d+) (creature|permanent)s?",effect_text)
    if sacrifice_match:
        words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(sacrifice_match.group(1),int(sacrifice_match.group(1)) if sacrifice_match.group(1).isdigit() else 1);affected=target_player if "target player" in sacrifice_match.group(0) and target_player else other;kind=sacrifice_match.group(2)
        choices=[permanent["instance_id"] for permanent in affected["battlefield"] if kind=="permanent" or "Creature" in permanent.get("type_line","")];required=min(amount,len(choices))
        if required:state["pending_sacrifice"]={"player_id":affected["id"],"amount":required,"card_ids":choices};state["priority_player_id"]=affected["id"];_log(state,f"{affected['name']} must sacrifice {required} {kind}(s).")
    destroy_all = re.search(r"destroy all (creatures|artifacts|enchantments|nonland permanents)", effect_text)
    exile_all = re.search(r"exile all (creatures|artifacts|enchantments|nonland permanents)", effect_text)
    for match,destination in ((destroy_all,"graveyard"),(exile_all,"exile")):
        if not match: continue
        kind=match.group(1)
        for owner in state["players"]:
            for permanent in list(owner["battlefield"]):
                type_line=permanent.get("type_line","").casefold();matches=(kind=="nonland permanents" and "land" not in type_line) or kind[:-1] in type_line
                if matches:
                    if destination=="graveyard":_destroy_permanent(state,owner,permanent)
                    else:_leave_battlefield(state,owner,permanent,destination)
        _log(state,f"All {kind} were {'destroyed' if destination=='graveyard' else 'exiled'}.")
    global_stats=re.search(r"(?:all|each) creatures?(?: you control| your opponents control)? get ([+-]\d+)/([+-]\d+) until end of turn",effect_text)
    if global_stats:
        own_only="you control" in global_stats.group(0);opponents_only="opponents control" in global_stats.group(0)
        for owner in state["players"]:
            if own_only and owner["id"]!=caster["id"] or opponents_only and owner["id"]==caster["id"]:continue
            for permanent in owner["battlefield"]:
                if "Creature" in permanent.get("type_line",""):permanent["temporary_power"]=permanent.get("temporary_power",0)+int(global_stats.group(1));permanent["temporary_toughness"]=permanent.get("temporary_toughness",0)+int(global_stats.group(2))
    token_match = re.search(r"create (a|one|two|three|four) (\d+)/(\d+) ([^.]*?) creature tokens?", effect_text)
    if token_match:
        amount = {"a":1,"one":1,"two":2,"three":3,"four":4}[token_match.group(1)]
        for _ in range(amount):
            caster["battlefield"].append({"instance_id":_id(),"scryfall_id":"token","name":f"{token_match.group(4).title()} Token","image_url":None,"type_line":f"Token Creature — {token_match.group(4).title()}","oracle_text":"","mana_cost":"","mana_value":0,"power":token_match.group(2),"toughness":token_match.group(3),"owner_id":caster["id"],"controller_id":caster["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True})
        _log(state, f"{caster['name']} created {amount} token(s).")
    entered = False
    if is_permanent_spell:
        card["summoning_sick"] = "Creature" in card.get("type_line", "")
        if re.search(r"\benters (?:the battlefield )?tapped\b",text):card["tapped"]=True
        caster["battlefield"].append(card); entered = True
    elif item.get("kind", "spell") == "spell":
        caster["graveyard"].append(card)
    _log(state, f"{card['name']} resolved.")
    if entered: _queue_triggers(state, "enters", card, caster)


def _leave_battlefield(state: dict, owner: dict, card: dict, destination: str) -> None:
    if card in owner["battlefield"]: owner["battlefield"].remove(card)
    card["damage"] = 0; card["tapped"] = False
    _queue_triggers(state, "dies" if destination == "graveyard" else "leaves", card, owner)
    if card.get("token"): return
    zone_owner=_player(state,card.get("owner_id",owner["id"]));card["controller_id"]=zone_owner["id"]
    if card.get("commander"):
        zone_owner["command"].append(card)
    else:
        zone_owner[destination].append(card)


def _queue_triggers(state: dict, event: str, event_card: dict | None, event_owner: dict) -> None:
    sources = [(owner, permanent) for owner in state["players"] for permanent in owner["battlefield"]]
    if event == "dies" and event_card: sources.append((event_owner, event_card))
    for owner, source in sources:
        text = source.get("oracle_text") or ""
        clauses = re.split(r"(?<=[.!])\s+|\n", text)
        for clause in clauses:
            lower = clause.casefold(); matches = False
            trigger_count = 1
            if event == "enters" and event_card:
                under_control = event_card.get("controller_id") == owner["id"]
                is_creature = "creature" in event_card.get("type_line", "").casefold()
                is_land = "land" in event_card.get("type_line", "").casefold()
                matches = under_control and ((is_creature and (("whenever another creature enters" in lower and source is not event_card) or "whenever a creature enters the battlefield under your control" in lower)) or (is_land and re.search(r"whenever (?:a|another) land enters(?: the battlefield)? under your control", lower) is not None) or (source is event_card and re.search(r"when (?:~|this (?:creature|permanent)|[^,]+) enters", lower) is not None))
            elif event == "dies" and event_card:
                matches = (source is event_card and re.search(r"when (?:~|this creature|[^,]+) dies", lower) is not None) or (source is not event_card and "whenever another creature dies" in lower)
            elif event == "upkeep":
                matches = "at the beginning of each player's upkeep" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your upkeep" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's upkeep" in lower)
            elif event == "end_step":
                matches = "at the beginning of each end step" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your end step" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's end step" in lower)
            elif event == "cast" and event_card:
                cast_by_controller = event_owner["id"] == owner["id"]
                type_line = event_card.get("type_line", "").casefold()
                matches = cast_by_controller and ("whenever you cast a spell" in lower or ("whenever you cast a creature spell" in lower and "creature" in type_line) or ("whenever you cast a noncreature spell" in lower and "creature" not in type_line) or ("whenever you cast an instant or sorcery spell" in lower and any(kind in type_line for kind in ("instant", "sorcery"))))
            elif event == "attackers_declared":
                attacking_ids = set(state.get("combat", {}).get("attackers", []))
                controlled_attackers = [card for card in owner["battlefield"] if card.get("instance_id") in attacking_ids]
                source_attacked = source.get("instance_id") in attacking_ids
                source_name = re.escape(source.get("name", "").casefold())
                if source_attacked and re.search(rf"whenever (?:~|this creature|{source_name}) attacks\b", lower):
                    matches = True
                elif controlled_attackers and "whenever one or more creatures you control attack" in lower:
                    matches = True
                elif controlled_attackers and "whenever a creature you control attacks" in lower:
                    matches = True; trigger_count = len(controlled_attackers)
            elif event == "combat_damage_player" and event_card:
                source_hit = source.get("instance_id") == event_card.get("instance_id")
                source_name = re.escape(source.get("name", "").casefold())
                matches = source_hit and re.search(rf"whenever (?:~|this creature|{source_name}) deals combat damage to (?:a player|an opponent)", lower) is not None
            if not matches or "," not in clause: continue
            effect = clause.split(",", 1)[1].strip(); ability_card = {**source, "name": f"{source['name']} trigger", "oracle_text": effect, "type_line": "Ability", "mana_cost": ""}
            targets = _targets(state, owner["id"], ability_card)
            for _ in range(trigger_count):
                trigger={"id":_id(),"kind":"trigger","card":ability_card,"controller_id":owner["id"],"target_id":None,"source_id":source["instance_id"]}
                if _target_kind(ability_card):
                    if targets:state.setdefault("pending_trigger_targets",[]).append({"controller_id":owner["id"],"source_name":source["name"],"trigger":trigger,"card":ability_card});state["priority_player_id"]=state["pending_trigger_targets"][0]["controller_id"]
                    else:_log(state,f"{source['name']}'s trigger had no legal target and was removed.")
                else:state["stack"].append(trigger)
                _log(state, f"{source['name']} triggered: {effect}")


def _combat_damage(state: dict) -> None:
    attacker = _player(state, state["active_player_id"])
    defender = opponent(state, attacker["id"])
    originally_blocked = set(state["combat"]["blocks"].values())
    def hit_defender(creature:dict, amount:int,target_id:str)->None:
        planeswalker=next((card for card in defender["battlefield"] if card["instance_id"]==target_id and "Planeswalker" in card.get("type_line","")),None)
        if planeswalker:planeswalker["counters"]["loyalty"]=max(0,planeswalker["counters"].get("loyalty",0)-amount)
        elif _has_keyword(creature,"Infect"):defender["poison"]=defender.get("poison",0)+amount
        else:defender["life"] -= amount
        toxic=_toxic_value(creature)
        if not planeswalker and amount>0 and toxic:defender["poison"]=defender.get("poison",0)+toxic
        if _has_keyword(creature,"Lifelink"): attacker["life"] += amount
        if not planeswalker and creature.get("commander"):
            source = creature.get("owner_id", attacker["id"]); defender.setdefault("commander_damage", {})[source] = defender.setdefault("commander_damage", {}).get(source, 0) + amount
        if not planeswalker and amount > 0:
            _queue_triggers(state,"combat_damage_player",creature,attacker)

    def damage_step(first: bool) -> None:
        battlefield = {card["instance_id"]: card for player in state["players"] for card in player["battlefield"]}
        deathtouch_hit:set[str]=set();life_gain={attacker["id"]:0,defender["id"]:0}
        def strikes(card:dict)->bool:
            has_first=_has_keyword(card,"First strike");double=_has_keyword(card,"Double strike")
            return has_first or double if first else not has_first or double
        for attacker_id in state["combat"]["attackers"]:
            creature=battlefield.get(attacker_id)
            if not creature or not strikes(creature):continue
            power=max(0,_parse_stats(creature,state)[0]);assigned_ids=state["combat"].get("block_orders",{}).get(attacker_id) or [blocker_id for blocker_id,target_id in state["combat"]["blocks"].items() if target_id==attacker_id];blockers=[battlefield[blocker_id] for blocker_id in assigned_ids if blocker_id in battlefield]
            attack_target=state["combat"].get("attack_targets",{}).get(attacker_id,defender["id"])
            if attacker_id not in originally_blocked:
                hit_defender(creature,power,attack_target);continue
            remaining=power
            for blocker in blockers:
                _,toughness=_parse_stats(blocker,state);lethal=1 if _has_keyword(creature,"Deathtouch") else max(1,toughness-blocker.get("damage",0));assigned=min(remaining,lethal);dealt=_damage_permanent(state,blocker,assigned,creature);remaining-=assigned
                if dealt and _has_keyword(creature,"Deathtouch"):deathtouch_hit.add(blocker["instance_id"])
                if dealt and _has_keyword(creature,"Lifelink"):life_gain[attacker["id"]]+=dealt
            if remaining and _has_keyword(creature,"Trample"):hit_defender(creature,remaining,attack_target)
        for blocker_id,attacker_id in state["combat"]["blocks"].items():
            blocker,creature=battlefield.get(blocker_id),battlefield.get(attacker_id)
            if not blocker or not creature or not strikes(blocker):continue
            amount=max(0,_parse_stats(blocker,state)[0]);dealt=_damage_permanent(state,creature,amount,blocker)
            if dealt and _has_keyword(blocker,"Deathtouch"):deathtouch_hit.add(creature["instance_id"])
            if dealt and _has_keyword(blocker,"Lifelink"):life_gain[defender["id"]]+=dealt
        attacker["life"]+=life_gain[attacker["id"]];defender["life"]+=life_gain[defender["id"]]
        for owner in (attacker,defender):
            for creature in list(owner["battlefield"]):
                if "Creature" not in creature.get("type_line",""):continue
                _,toughness=_parse_stats(creature,state)
                if creature.get("damage",0)>=toughness or creature["instance_id"] in deathtouch_hit:_destroy_permanent(state,owner,creature)

    participants=[card for owner in (attacker,defender) for card in owner["battlefield"] if card["instance_id"] in state["combat"]["attackers"] or card["instance_id"] in state["combat"]["blocks"]]
    if any(_has_keyword(card,"First strike") or _has_keyword(card,"Double strike") for card in participants):damage_step(True)
    damage_step(False)
    _log(state, "Combat damage resolved.")
    state["combat"] = {"attackers": [], "blocks": {},"attack_targets":{},"block_orders":{},"damage_pending":False}


def _check_winner(state: dict) -> None:
    losers = [player for player in state["players"] if player["life"] <= 0 or player.get("poison",0)>=10 or player.get("lost") or any(amount >= 21 for amount in player.get("commander_damage", {}).values())]
    if losers:
        state["status"] = "complete"
        state["winner_id"] = opponent(state, losers[0]["id"])["id"]
        _log(state, f"{opponent(state, losers[0]['id'])['name']} wins the game.")


def _state_based_actions(state: dict) -> None:
    changed=True
    while changed:
        changed=False
        for owner in state["players"]:
            for permanent in list(owner["battlefield"]):
                _,toughness=_parse_stats(permanent,state)
                if "Creature" in permanent.get("type_line","") and (toughness<=0 or (permanent.get("damage",0)>=toughness and not _has_keyword(permanent,"Indestructible"))):
                    if toughness<=0:_leave_battlefield(state,owner,permanent,"graveyard")
                    else:_destroy_permanent(state,owner,permanent)
                    changed=True
                elif "Planeswalker" in permanent.get("type_line","") and permanent.get("counters",{}).get("loyalty",0)<=0:
                    _leave_battlefield(state,owner,permanent,"graveyard");changed=True
    if _pending_decision(state):return
    for owner in state["players"]:
        if any("legend rule doesn't apply" in (permanent.get("oracle_text") or "").casefold() for permanent in owner["battlefield"]):continue
        groups:dict[str,list[dict]]={}
        for permanent in owner["battlefield"]:
            if "Legendary" in permanent.get("type_line",""):groups.setdefault((permanent.get("rules_name") or permanent.get("name","")).casefold(),[]).append(permanent)
        duplicate=next((cards for cards in groups.values() if len(cards)>1),None)
        if duplicate:
            state["pending_legendary"]={"player_id":owner["id"],"card_ids":[card["instance_id"] for card in duplicate]};state["priority_player_id"]=owner["id"];_log(state,f"{owner['name']} must choose one legendary {duplicate[0].get('rules_name') or duplicate[0]['name']} to keep.");return


def _begin_next_turn(state:dict)->None:
    state["pending_discard"]=None;state["turn"] += 1; state["phase"] = PHASES[0]; state["active_player_id"] = opponent(state, state["active_player_id"])["id"]
    active = _player(state, state["active_player_id"]); active["land_plays_remaining"] = 1
    for owner in state["players"]:
        for permanent in owner["battlefield"]: permanent.pop("temporary_power",None); permanent.pop("temporary_toughness",None);permanent.pop("temporary_keywords",None); permanent["damage"] = 0
    for permanent in active["battlefield"]: permanent["tapped"] = False; permanent["summoning_sick"] = False
    _draw(state, active); _log(state, f"Turn {state['turn']} began for {active['name']}."); _queue_triggers(state,"upkeep",None,active)


def _advance_turn_phase(state: dict) -> None:
    if state["phase"] == "combat" and state["combat"]["attackers"]: _combat_damage(state)
    index = PHASES.index(state["phase"])
    if index == len(PHASES) - 1:
        ending=_player(state,state["active_player_id"]);maximum=_maximum_hand_size(ending);excess=max(0,len(ending["hand"])-maximum) if maximum is not None else 0
        if excess:
            state["pending_discard"]={"player_id":ending["id"],"amount":excess};state["priority_player_id"]=ending["id"];state["pending_phase_advance"]=False;state["consecutive_passes"]=0;_log(state,f"{ending['name']} must discard {excess} card(s) to hand size.");return
        _begin_next_turn(state)
    else:
        state["phase"] = PHASES[index + 1]
        if state["phase"] == "ending":
            _queue_triggers(state,"end_step",None,_player(state,state["active_player_id"]))
    if not state.get("pending_trigger_targets"):
        state["priority_player_id"] = state["active_player_id"]
    state["pending_phase_advance"] = False; state["consecutive_passes"] = 0


def perform_action(state: dict, player_id: str, action: dict) -> dict:
    state = deepcopy(state)
    player = _player(state, player_id)
    action_type = action.get("type")
    allowed = {entry["type"] for entry in legal_actions(state, player_id)}
    manual_actions = {"adjust_life", "add_counter", "create_token", "move_zone"}
    if action_type not in allowed and action_type not in manual_actions:
        raise RuleViolation(f"{action_type} is not legal right now")
    if action_type == "keep":
        if player.get("mulligans",0):state["pending_mulligan_bottom"]=player_id;_log(state,f"{player['name']} kept and must put {player['mulligans']} card(s) on the bottom.")
        else:
            player["kept_hand"] = True; _log(state, f"{player['name']} kept seven cards.")
            if all(item["kept_hand"] for item in state["players"]):state["status"] = "active"; state["priority_player_id"] = state["active_player_id"]
    elif action_type == "mulligan":
        player["mulligans"]=min(7,player.get("mulligans",0)+1);player["library"].extend(player["hand"]);player["hand"]=[]
        random.SystemRandom().shuffle(player["library"]);_draw(state,player,7);_log(state,f"{player['name']} took mulligan {player['mulligans']} and drew seven new cards.")
    elif action_type == "bottom_mulligan_cards":
        required=player.get("mulligans",0);requested=action.get("card_ids") or []
        if state.get("pending_mulligan_bottom")!=player_id or len(requested)!=required or len(set(requested))!=required:raise RuleViolation(f"Choose exactly {required} cards to put on the bottom")
        chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected cards are not in your hand")
        for card in chosen:player["hand"].remove(card);player["library"].insert(0,card)
        state["pending_mulligan_bottom"]=None;player["kept_hand"]=True;_log(state,f"{player['name']} put {required} card(s) on the bottom and kept {len(player['hand'])}.")
        if all(item["kept_hand"] for item in state["players"]):state["status"]="active";state["priority_player_id"]=state["active_player_id"]
    elif action_type == "play_land":
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id") and "Land" in card.get("type_line", "")), None)
        if not card: raise RuleViolation("That land is not in your hand")
        player["hand"].remove(card); player["battlefield"].append(card); player["land_plays_remaining"] -= 1; _log(state, f"{player['name']} played {card['name']}."); _queue_triggers(state,"enters",card,player)
    elif action_type == "cast":
        source = next((zone for zone in ("hand", "command") if any(card["instance_id"] == action.get("card_id") for card in player.get(zone, []))), None)
        card = next((card for card in player.get(source or "hand", []) if card["instance_id"] == action.get("card_id")), None)
        tax = _commander_tax(player, card) if card else 0
        if not card or not _can_pay(player, card, tax): raise RuleViolation("That spell cannot be cast")
        targeting_card=_spell_targeting_card(card);targets = _targets(state, player_id, targeting_card); target_id = action.get("target_id")
        if _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}: raise RuleViolation("Choose a legal target")
        _pay_mana(player, card, tax); player[source].remove(card)
        if card.get("commander"): player["commander_casts"] = player.get("commander_casts", 0) + 1
        stack_item={"id": _id(), "card": card, "controller_id": player_id, "target_id": target_id};state["stack"].append(stack_item); state["consecutive_passes"] = 0; state["pending_phase_advance"] = False
        _queue_triggers(state,"cast",card,player)
        _queue_ward(state,player,target_id,stack_item)
        if _multiplayer(state) and not state.get("pending_ward") and not state.get("pending_trigger_targets"): state["priority_player_id"] = opponent(state, player_id)["id"]
        _log(state, f"{player['name']} cast {card['name']}{f' with {tax} commander tax' if tax else ''}{' targeting '+next((target['name'] for target in targets if target['id']==target_id),'') if target_id else ''}.")
    elif action_type == "activate":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That ability cannot be activated")
        ability=_activated_abilities(permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        if len(selected_cost_ids)!=required_cost or len(set(selected_cost_ids))!=required_cost or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose exactly {required_cost} legal card(s) for the activation cost")
        selected_cost_cards=[card for zone in (player["hand"],player["battlefield"]) for card in zone if card["instance_id"] in set(selected_cost_ids)]
        if len(selected_cost_cards)!=required_cost:raise RuleViolation("One or more activation cost cards are no longer available")
        if ability["mana_cost"]:_pay_mana(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None)
        if ability["life_cost"]:player["life"]-=ability["life_cost"]
        if ability["counter_cost"]:
            name,amount=ability["counter_cost"]["name"],ability["counter_cost"]["amount"];permanent["counters"][name]-=amount
        if ability["taps"]:permanent["tapped"]=True
        stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"source_id":permanent["instance_id"]};state["stack"].append(stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_queue_ward(state,player,target_id,stack_item)
        if ability["self_sacrifice"]:_leave_battlefield(state,player,permanent,"graveyard")
        if available.get("cost_kind")=="discard":
            for card in selected_cost_cards:player["hand"].remove(card);player["graveyard"].append(card)
        elif available.get("cost_kind")=="sacrifice":
            for card in selected_cost_cards:_leave_battlefield(state,player,card,"graveyard")
        if _multiplayer(state) and not state.get("pending_ward") and not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {permanent['name']}: {ability['effect']}")
    elif action_type == "activate_loyalty":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id") and "Planeswalker" in card.get("type_line","")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate_loyalty" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That loyalty ability cannot be activated")
        ability=_loyalty_abilities(permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        permanent["counters"]["loyalty"]=permanent["counters"].get("loyalty",0)+ability["cost"];permanent["loyalty_activated_turn"]=state["turn"];stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"source_id":permanent["instance_id"]};state["stack"].append(stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_queue_ward(state,player,target_id,stack_item)
        if _multiplayer(state) and not state.get("pending_ward"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {permanent['name']} ({ability['cost']:+d}): {ability['effect']}")
    elif action_type == "resolve":
        _resolve_spell(state)
    elif action_type == "pass_priority":
        state["consecutive_passes"] = state.get("consecutive_passes", 0) + 1
        if state["consecutive_passes"] >= 2:
            state["consecutive_passes"] = 0
            if state["stack"]: _resolve_spell(state)
            elif state["combat"].get("damage_pending"):_combat_damage(state)
            elif state["phase"]=="combat" and state["combat"]["attackers"]:state["combat"]["damage_pending"]=True;_log(state,"Blockers were finalized. Players may respond before combat damage.")
            elif state.get("pending_phase_advance"): _advance_turn_phase(state)
            if not _pending_decision(state):state["priority_player_id"] = state["active_player_id"]
        else:
            state["priority_player_id"] = opponent(state, player_id)["id"]
    elif action_type == "declare_attackers":
        requested = set(action.get("attacker_ids") or [])
        eligible = {card_id for entry in legal_actions(state, player_id) if entry["type"] == "declare_attackers" for card_id in entry.get("card_ids", [])}
        if not requested.issubset(eligible): raise RuleViolation("One or more attackers are not eligible")
        attack_action=next(entry for entry in legal_actions(state,player_id) if entry["type"]=="declare_attackers");defender_ids={target["id"] for target in attack_action.get("defenders",[])};requested_targets=action.get("attack_targets") or {};default_target=opponent(state,player_id)["id"]
        if any(requested_targets.get(attacker_id,default_target) not in defender_ids for attacker_id in requested):raise RuleViolation("Choose a legal defender for every attacker")
        state["combat"]["attackers"] = list(requested)
        state["combat"]["attack_targets"]={attacker_id:requested_targets.get(attacker_id,default_target) for attacker_id in requested}
        for card in player["battlefield"]:
            if card["instance_id"] in requested and not _has_keyword(card,"Vigilance"): card["tapped"] = True
        _queue_triggers(state,"attackers_declared",None,player)
        if not state.get("pending_trigger_targets"):state["priority_player_id"] = opponent(state, player_id)["id"]
        _log(state, f"{player['name']} attacked with {len(requested)} creature(s).")
    elif action_type == "declare_blockers":
        block_action=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="declare_blockers"),None);available=set(block_action.get("card_ids",[])) if block_action else set();legal_blocks=block_action.get("legal_blocks",{}) if block_action else {}
        blocks = action.get("blocks") or {}
        if not set(blocks).issubset(available) or any(attacker_id not in legal_blocks.get(blocker_id,[]) for blocker_id,attacker_id in blocks.items()): raise RuleViolation("One or more blocks are illegal")
        attacking_owner=opponent(state,player_id);battlefield={card["instance_id"]:card for card in attacking_owner["battlefield"]}
        for attacker_id in state["combat"]["attackers"]:
            if _has_keyword(battlefield.get(attacker_id,{}),"Menace") and 0<list(blocks.values()).count(attacker_id)<2:raise RuleViolation("A creature with menace must be blocked by at least two creatures")
        state["combat"]["blocks"] = blocks;groups={attacker_id:[blocker_id for blocker_id,target_id in blocks.items() if target_id==attacker_id] for attacker_id in state["combat"]["attackers"]};groups={attacker_id:blocker_ids for attacker_id,blocker_ids in groups.items() if len(blocker_ids)>1}
        if groups:state["pending_damage_order"]={"player_id":state["active_player_id"],"groups":groups};state["priority_player_id"]=state["active_player_id"]
        else:state["combat"]["damage_pending"]=True;state["priority_player_id"] = state["active_player_id"];_log(state,"Blockers were finalized. Players may respond before combat damage.")
    elif action_type == "order_blockers":
        pending=state.get("pending_damage_order") or {};orders=action.get("block_orders") or {};expected=pending.get("groups",{})
        if pending.get("player_id")!=player_id or set(orders)!=set(expected) or any(len(order)!=len(expected[attacker_id]) or len(set(order))!=len(order) or set(order)!=set(expected[attacker_id]) for attacker_id,order in orders.items()):raise RuleViolation("Order every creature blocking each attacker exactly once")
        state["combat"]["block_orders"]=orders;state["pending_damage_order"]=None;state["combat"]["damage_pending"]=True;state["priority_player_id"]=state["active_player_id"];_log(state,"Damage order was chosen. Players may respond before combat damage.")
    elif action_type == "resolve_combat_damage":
        if _multiplayer(state) or not state["combat"].get("damage_pending"):raise RuleViolation("Combat damage is not ready")
        _combat_damage(state)
    elif action_type in {"pay_ward","decline_ward"}:
        pending=state.get("pending_ward") or {}
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no ward cost for this player")
        stack_item=next((item for item in state["stack"] if item["id"]==pending["stack_id"]),None)
        if not stack_item:raise RuleViolation("The warded spell or ability is no longer on the stack")
        if action_type=="pay_ward":
            kind=pending.get("cost_type","mana")
            if kind=="mana":_pay_mana(player,{"mana_cost":pending["mana_cost"]})
            elif kind=="life":
                if player["life"]<pending["amount"]:raise RuleViolation("Not enough life to pay ward")
                player["life"]-=pending["amount"]
            elif kind=="discard":
                requested=action.get("card_ids") or [];amount=pending["amount"]
                if len(requested)!=amount or len(set(requested))!=amount:raise RuleViolation(f"Choose exactly {amount} card(s) to discard for ward")
                chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
                if len(chosen)!=amount:raise RuleViolation("One or more Ward discards are not in your hand")
                for chosen_card in chosen:player["hand"].remove(chosen_card);player["graveyard"].append(chosen_card)
            _log(state,f"{player['name']} paid {pending.get('label',pending.get('mana_cost',''))} for {pending['source_name']}'s ward.")
        else:
            state["stack"].remove(stack_item)
            if stack_item.get("kind","spell")=="spell":_countered_spell_destination(state,player,stack_item["card"])
            _log(state,f"{stack_item['card']['name']} was countered by {pending['source_name']}'s ward.")
        state["pending_ward"]=None;state["priority_player_id"]=opponent(state,player_id)["id"] if _multiplayer(state) else player_id
    elif action_type in {"choose_trigger_target","skip_trigger"}:
        pending_list=state.get("pending_trigger_targets") or []
        if not pending_list or pending_list[0]["controller_id"]!=player_id:raise RuleViolation("There is no triggered target decision for this player")
        pending=pending_list.pop(0);targets=_targets(state,player_id,pending["card"]);target_id=action.get("target_id")
        if action_type=="choose_trigger_target":
            if target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target for the triggered ability")
            trigger=pending["trigger"];trigger["target_id"]=target_id;state["stack"].append(trigger);_log(state,f"{player['name']} chose {next(target['name'] for target in targets if target['id']==target_id)} for {pending['source_name']}'s trigger.")
        elif targets:raise RuleViolation("This triggered ability still has legal targets")
        state["pending_trigger_targets"]=pending_list;state["priority_player_id"]=pending_list[0]["controller_id"] if pending_list else state["active_player_id"]
    elif action_type == "advance_phase":
        if _multiplayer(state):
            state["pending_phase_advance"] = True; state["consecutive_passes"] = 1; state["priority_player_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} is ready to leave {state['phase'].replace('_', ' ')}.")
        elif state["phase"]=="combat" and state["combat"]["attackers"] and player_id!=state["active_player_id"]:
            state["combat"]["damage_pending"]=True;state["priority_player_id"]=state["active_player_id"];_log(state,"No blockers were declared. Players may respond before combat damage.")
        else:
            _advance_turn_phase(state)
    elif action_type == "concede":
        state["status"] = "complete"; state["winner_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} conceded.")
    elif action_type == "discard_cards":
        pending=state.get("pending_discard") or {};requested=action.get("card_ids") or [];required=pending.get("amount",0)
        if pending.get("player_id")!=player_id or len(requested)!=required or len(set(requested))!=required:raise RuleViolation(f"Choose exactly {required} cards to discard")
        chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected cards are not in your hand")
        for card in chosen:player["hand"].remove(card);player["graveyard"].append(card)
        reason=pending.get("reason","cleanup");state["pending_discard"]=None;_log(state,f"{player['name']} discarded {required} card(s){' to maximum hand size' if reason=='cleanup' else ''}.")
        if reason=="cleanup":_begin_next_turn(state)
        else:state["priority_player_id"]=state["active_player_id"]
    elif action_type == "sacrifice_permanents":
        pending=state.get("pending_sacrifice") or {};requested=action.get("card_ids") or [];required=pending.get("amount",0);allowed_ids=set(pending.get("card_ids",[]))
        if pending.get("player_id")!=player_id or len(requested)!=required or len(set(requested))!=required or not set(requested).issubset(allowed_ids):raise RuleViolation(f"Choose exactly {required} legal permanent(s) to sacrifice")
        chosen=[card for card in player["battlefield"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected permanents are no longer available")
        for card in chosen:_leave_battlefield(state,player,card,"graveyard")
        state["pending_sacrifice"]=None;state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} sacrificed {required} permanent(s).")
    elif action_type == "choose_legendary":
        pending=state.get("pending_legendary") or {};requested=action.get("card_ids") or []
        if pending.get("player_id")!=player_id or len(requested)!=1 or requested[0] not in pending.get("card_ids",[]):raise RuleViolation("Choose exactly one legendary permanent to keep")
        keep=requested[0]
        for card in list(player["battlefield"]):
            if card["instance_id"] in pending["card_ids"] and card["instance_id"]!=keep:_leave_battlefield(state,player,card,"graveyard")
        state["pending_legendary"]=None;state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} chose a legendary permanent to keep.")
    elif action_type in {"scry","surveil"}:
        pending=state.get("pending_scry") or {};top_ids=action.get("top_ids") or [];away_ids=(action.get("graveyard_ids") if action_type=="surveil" else action.get("bottom_ids")) or [];expected=pending.get("card_ids",[])
        if pending.get("player_id")!=player_id or pending.get("mode","scry")!=action_type or len(top_ids)+len(away_ids)!=len(expected) or len(set(top_ids+away_ids))!=len(expected) or set(top_ids+away_ids)!=set(expected):raise RuleViolation(f"Choose each {action_type}ed card exactly once")
        cards={card["instance_id"]:card for card in player["library"] if card["instance_id"] in set(expected)}
        if len(cards)!=len(expected):raise RuleViolation(f"The top of the library changed before {action_type} resolved")
        player["library"]=[card for card in player["library"] if card["instance_id"] not in cards]
        if action_type=="scry":player["library"][0:0]=[cards[card_id] for card_id in reversed(away_ids)]
        else:player["graveyard"].extend(cards[card_id] for card_id in away_ids)
        player["library"].extend(cards[card_id] for card_id in reversed(top_ids));state["pending_scry"]=None;state["priority_player_id"]=state["active_player_id"]
        _log(state,f"{player['name']} kept {len(top_ids)} card(s) on top and put {len(away_ids)} in {'the graveyard' if action_type=='surveil' else 'the bottom of the library'}.")
    elif action_type == "adjust_life":
        target_player = _player(state, action.get("target_id") or player_id); amount = max(-100, min(100, int(action.get("amount") or 0))); target_player["life"] += amount; _log(state, f"{target_player['name']}'s life was adjusted by {amount:+d}.")
    elif action_type == "add_counter":
        permanent = next((card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"] == action.get("target_id")), None)
        if not permanent: raise RuleViolation("Choose a permanent")
        name = (action.get("counter_name") or "+1/+1")[:32]; amount = max(-20, min(20, int(action.get("amount") or 1))); permanent["counters"][name] = max(0, permanent["counters"].get(name, 0) + amount); _log(state, f"{permanent['name']} now has {permanent['counters'][name]} {name} counter(s).")
    elif action_type == "create_token":
        token = {"instance_id":_id(),"scryfall_id":"token","name":(action.get("token_name") or "Creature Token")[:80],"image_url":None,"type_line":"Token Creature","oracle_text":"","mana_cost":"","mana_value":0,"power":str(max(0,min(99,int(action.get("power") or 1)))),"toughness":str(max(1,min(99,int(action.get("toughness") or 1)))),"owner_id":player_id,"controller_id":player_id,"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True}; player["battlefield"].append(token); _log(state, f"{player['name']} created {token['name']}.")
    elif action_type == "move_zone":
        source_owner = next((owner for owner in state["players"] if any(card["instance_id"] == action.get("target_id") for zone in ("hand","battlefield","graveyard","exile") for card in owner[zone])), None)
        destination = action.get("destination")
        if not source_owner or destination not in {"hand","battlefield","graveyard","exile"}: raise RuleViolation("Choose a card and destination zone")
        source = next(zone for zone in ("hand","battlefield","graveyard","exile") if any(card["instance_id"] == action.get("target_id") for card in source_owner[zone])); card = next(card for card in source_owner[source] if card["instance_id"] == action.get("target_id"))
        source_owner[source].remove(card); source_owner[destination].append(card); _log(state, f"{card['name']} moved from {source} to {destination}.")
    _state_based_actions(state);_check_winner(state)
    state["version"] += 1
    return state
