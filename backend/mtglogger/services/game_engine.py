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
            if source.get("attached_to")==card.get("instance_id"):
                attachment_text=(source.get("oracle_text") or "").casefold();attachment_match=re.search(r"(?:equipped|enchanted) creature gets ([+-]\d+)/([+-]\d+)(?! until end of turn)",attachment_text)
                if attachment_match:power+=int(attachment_match.group(1));toughness+=int(attachment_match.group(2))
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
    return "Land" in card.get("type_line", "") or any(kind in card.get("type_line","") for kind in ("Treasure","Gold")) or re.search(r"\{T\}:\s*Add ", card.get("oracle_text") or "", re.IGNORECASE) is not None


def _mana_requirements(card: dict, extra_generic: int = 0, x_value:int=0) -> tuple[list[set[str]], int]:
    colored=[];generic=extra_generic
    for symbol in _mana_symbols(card):
        if symbol.isdigit():generic+=int(symbol);continue
        if symbol.upper()=="X":generic+=x_value;continue
        choices={part for part in symbol.upper().split("/") if part in "WUBRGC"}
        if choices:colored.append(choices)
    return colored,generic


def _has_keyword(card: dict, keyword: str) -> bool:
    printed={value.casefold() for value in card.get("keywords", [])};temporary={value.casefold() for value in card.get("temporary_keywords", [])};attached={value.casefold() for values in card.get("attachment_keywords",{}).values() for value in values}
    return keyword.casefold() in printed|temporary|attached or re.search(rf"\b{re.escape(keyword.casefold())}\b", (card.get("oracle_text") or "").casefold()) is not None


def _attachment_keywords(card:dict)->list[str]:
    text=(card.get("oracle_text") or "").casefold();supported=("defender","flying","first strike","double strike","deathtouch","haste","hexproof","indestructible","lifelink","menace","reach","trample","vigilance")
    clauses=[clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if re.search(r"(?:equipped|enchanted) creature .*?\b(?:has|gains?)\b",clause)]
    return [keyword for keyword in supported if any(re.search(rf"\b{re.escape(keyword)}\b",clause) for clause in clauses)]


def _detach(state:dict,attachment:dict)->None:
    target_id=attachment.pop("attached_to",None)
    if not target_id:return
    target=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==target_id),None)
    if target:target.get("attachment_keywords",{}).pop(attachment["instance_id"],None);target.get("attachment_rules",{}).pop(attachment["instance_id"],None)


def _attach(state:dict,attachment:dict,target:dict)->None:
    _detach(state,attachment);attachment["attached_to"]=target.get("instance_id",target.get("id"));keywords=_attachment_keywords(attachment)
    if target.get("instance_id"):
        target.setdefault("attachment_rules",{})[attachment["instance_id"]]=attachment.get("oracle_text") or ""
        if keywords:target.setdefault("attachment_keywords",{})[attachment["instance_id"]]=keywords


def _equip_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Equip\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _cycling_ability(card:dict)->dict|None:
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^((?:[A-Za-z][A-Za-z ]*)?cycling)\s+((?:\{[^}]+\})+)",line.strip(),re.IGNORECASE)
        if not match:continue
        keyword,cost=match.group(1),match.group(2).upper();descriptor=keyword[:-7].strip()
        effect="Draw a card." if not descriptor else f"Search your library for a {descriptor} card, reveal it, put it into your hand, then shuffle."
        return {"keyword":keyword,"mana_cost":cost,"effect":effect,"card":{**card,"name":f"{card['name']} — {keyword}","oracle_text":effect,"type_line":"Ability","mana_cost":""}}
    return None


def _flashback_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)Flashback[ —-]*((?:\{[^}]+\})+)(?:,\s*Behold\s+(a|one|two|three|four|five|\d+)\s+([A-Za-z]+))?",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5};amount_word=(match.group(2) or "").casefold();amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 0)
    return {"mana_cost":match.group(1).upper(),"behold_amount":amount,"behold_type":(match.group(3) or "").removesuffix("s").casefold()}


def _kicker_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Kicker\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _kicked_rules_card(card:dict,kicked:bool)->dict:
    clauses=re.split(r"(?<=[.!])\s+|\n",card.get("oracle_text") or "");resolved=[]
    for clause in clauses:
        if re.match(r"\s*Kicker\b",clause,re.IGNORECASE):continue
        conditional=re.match(r"\s*If (?:this spell|it) was kicked,\s*(.+)",clause,re.IGNORECASE)
        if not conditional:resolved.append(clause);continue
        if not kicked:continue
        effect=conditional.group(1)
        if re.search(r"\binstead\b",effect,re.IGNORECASE):
            effect=re.sub(r"\s+instead(?=[,.]|$)","",effect,flags=re.IGNORECASE)
            if resolved:resolved.pop()
        resolved.append(effect)
    return {**card,"oracle_text":" ".join(filter(None,resolved))}


def _aura_allowed_types(card:dict)->set[str]:
    match=re.search(r"(?:^|\n)Enchant ([^\n.]+)",card.get("oracle_text") or "",re.IGNORECASE)
    if not match or "permanent" in match.group(1).casefold():return set()
    return {kind for kind in ("artifact","creature","enchantment","land","planeswalker","player") if re.search(rf"\b{kind}\b",match.group(1),re.IGNORECASE)}


def _effective_rules_text(state:dict,card:dict)->str:
    attachment_texts=[attachment.get("oracle_text") or "" for owner in state["players"] for attachment in owner["battlefield"] if attachment.get("attached_to")==card.get("instance_id")]
    return "\n".join([card.get("oracle_text") or "",*attachment_texts]).casefold()


def _can_attack(state:dict,card:dict,attacker:dict,defender:dict)->bool:
    text=_effective_rules_text(state,card)
    if card.get("cant_attack_until_turn")==state["turn"]:return False
    defender_override="defender" in text and "can attack as though it didn't have defender" in text and any("Creature" in permanent.get("type_line","") and _parse_stats(permanent,state)[0]>=4 for permanent in attacker["battlefield"])
    if _has_keyword(card,"Defender") and not defender_override:return False
    if "can't attack unless" in text or "can't attack or block unless" in text:
        if "seven or more cards in your graveyard" in text and len(attacker["graveyard"])<7:return False
        if "there is a mountain on the battlefield" in text and not any("mountain" in permanent.get("type_line","").casefold() for owner in state["players"] for permanent in owner["battlefield"]):return False
        if "defending player controls an enchantment or an enchanted permanent" in text and not any("Enchantment" in permanent.get("type_line","") or permanent.get("attached_to") for permanent in defender["battlefield"]):return False
        if "you control another creature with power 4 or greater" in text and not any(permanent["instance_id"]!=card["instance_id"] and "Creature" in permanent.get("type_line","") and _parse_stats(permanent,state)[0]>=4 for permanent in attacker["battlefield"]):return False
        if re.search(r"unless (?:its controller|you) pay",text):return False
    elif "can't attack" in text and "can't attack or block alone" not in text:return False
    return True


def _can_block_pair(state:dict,attacker:dict,blocker:dict)->bool:
    attacker_text=_effective_rules_text(state,attacker);blocker_text=_effective_rules_text(state,blocker)
    if blocker.get("cant_block_until_turn")==state["turn"]:return False
    conditional="can't attack or block unless" in blocker_text
    if conditional and "you control another creature with power 4 or greater" in blocker_text:
        controller=_player(state,blocker.get("controller_id"));conditional=any(permanent["instance_id"]!=blocker["instance_id"] and "Creature" in permanent.get("type_line","") and _parse_stats(permanent,state)[0]>=4 for permanent in controller["battlefield"])
        if not conditional:return False
    elif conditional:return False
    if "can't block" in blocker_text and "can't attack or block alone" not in blocker_text and "can't attack or block unless" not in blocker_text and "can't block or be blocked by non-spirit creatures" not in blocker_text:return False
    if "can't be blocked" in attacker_text or "unblockable" in attacker_text:return False
    if "can't be blocked by non-spirit creatures" in attacker_text and "spirit" not in blocker.get("type_line","").casefold():return False
    if "can't block or be blocked by non-spirit creatures" in blocker_text and "spirit" not in attacker.get("type_line","").casefold():return False
    return (not _has_keyword(attacker,"Flying") or _has_keyword(blocker,"Flying") or _has_keyword(blocker,"Reach")) and not _protected_from(attacker,blocker)


def _toxic_value(card: dict) -> int:
    match=re.search(r"\btoxic (\d+)\b",card.get("oracle_text") or "",re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _card_colors(card: dict) -> set[str]:
    return {part for symbol in _mana_symbols(card) for part in symbol.upper().split("/") if part in "WUBRG"}


def _protected_from(card: dict, source: dict) -> bool:
    text = "\n".join([card.get("oracle_text") or "",*card.get("attachment_rules",{}).values()]).casefold()
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


def _remove_from_combat(state:dict,card_id:str)->None:
    combat=state.get("combat",{});combat["attackers"]=[attacker for attacker in combat.get("attackers",[]) if attacker!=card_id];combat.get("attack_targets",{}).pop(card_id,None);combat.get("block_orders",{}).pop(card_id,None)
    combat["blocks"]={blocker:attacker for blocker,attacker in combat.get("blocks",{}).items() if blocker!=card_id and attacker!=card_id}
    for order in combat.get("block_orders",{}).values():
        if card_id in order:order.remove(card_id)


def _destroy_permanent(state:dict,owner:dict,card:dict,cant_regenerate:bool=False)->bool:
    if _has_keyword(card,"Indestructible"):return False
    if _consume_shield(state,card,"destruction"):return False
    regenerations=card.get("regeneration_shields",0)
    if regenerations and not cant_regenerate:
        card["regeneration_shields"]=regenerations-1;card["tapped"]=True;card["damage"]=0;_remove_from_combat(state,card["instance_id"]);_log(state,f"{card['name']} regenerated instead of being destroyed.");return False
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
    text=card.get("oracle_text") or "";quoted=re.findall(r'"([^"]+:[^"]+)"',text);lines=[*(line for line in text.splitlines() if '"' not in line),*quoted]
    for line in lines:
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
        sacrifice_match=None if self_sacrifice else re.search(r"\bsacrifice (another |a |an |one |two |three |two other |three other )?(creature|artifact|permanent)s?\b",cost,re.IGNORECASE)
        if sacrifice_match:
            count_word=(sacrifice_match.group(1) or "a").strip().casefold();selection_cost={"kind":"sacrifice","filter":sacrifice_match.group(2).casefold(),"amount":2 if count_word=="two other" else 3 if count_word=="three other" else words.get(count_word,1),"exclude_source":"other" in count_word or count_word=="another"}
        unsupported=("discard" in cost.casefold() and not selection_cost) or ("sacrifice" in cost.casefold() and not self_sacrifice and not selection_cost) or ("remove" in cost.casefold() and "counter" in cost.casefold() and not counter_cost)
        if unsupported or (not taps and not mana_cost and not self_sacrifice and not life_cost and not counter_cost and not selection_cost):continue
        if re.match(r"add (?:\{|one mana)", effect, re.IGNORECASE): continue
        ability_card = {**card, "name": f"{card['name']} ability", "oracle_text": effect, "type_line": "Ability", "mana_cost": ""}
        abilities.append({"cost":cost,"mana_cost":mana_cost,"taps":taps,"self_sacrifice":self_sacrifice,"life_cost":life_cost,"counter_cost":counter_cost,"selection_cost":selection_cost,"effect":effect,"card":ability_card})
    return abilities


def _permanent_abilities(state:dict,card:dict)->list[dict]:
    granted=[ability for rules in card.get("attachment_rules",{}).values() for ability in re.findall(r'"([^"]+:[^"]+)"',rules)]
    return _activated_abilities({**card,"oracle_text":"\n".join([card.get("oracle_text") or "",*granted])})


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


def _can_pay(player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None,x_value:int=0) -> bool:
    available = []
    for permanent in player["battlefield"]:
        if permanent.get("instance_id")!=excluded_id and not permanent.get("tapped") and _mana_source(permanent) and not ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste")):
            available.append(_land_colors(permanent) or {"C"})
    colored,generic=_mana_requirements(card,extra_generic,x_value)
    for choices in colored:
        match = next((colors for colors in available if colors & choices), None)
        if not match:
            return False
        available.remove(match)
    return len(available) >= generic


def _pay_mana(state:dict,player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None,x_value:int=0) -> None:
    colored,generic=_mana_requirements(card,extra_generic,x_value)
    lands = [permanent for permanent in player["battlefield"] if permanent.get("instance_id")!=excluded_id and not permanent.get("tapped") and _mana_source(permanent) and not ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))]
    lands.sort(key=lambda permanent:any(kind in permanent.get("type_line","") for kind in ("Treasure","Gold")))
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
        if any(kind in land.get("type_line","") for kind in ("Treasure","Gold")):
            _leave_battlefield(state,player,land,"graveyard");_log(state,f"{player['name']} sacrificed {land['name']} for mana.")
        else:land["tapped"] = True


def _predefined_token(owner:dict,kind:str,tapped:bool=False)->dict:
    oracle={"Clue":"{2}, Sacrifice this artifact: Draw a card.","Food":"{2}, {T}, Sacrifice this artifact: You gain 3 life.","Treasure":"{T}, Sacrifice this artifact: Add one mana of any color.","Blood":"{1}, {T}, Discard a card, Sacrifice this artifact: Draw a card.","Gold":"Sacrifice this artifact: Add one mana of any color."}[kind]
    return {"instance_id":_id(),"scryfall_id":f"token-{kind.casefold()}","name":f"{kind} Token","image_url":None,"type_line":f"Token Artifact — {kind}","oracle_text":oracle,"mana_cost":"","mana_value":0,"power":None,"toughness":None,"owner_id":owner["id"],"controller_id":owner["id"],"tapped":tapped,"damage":0,"counters":{},"summoning_sick":False,"token":True,"keywords":[]}


def _has_x_cost(card:dict)->bool:
    return any(symbol.upper()=="X" for symbol in _mana_symbols(card))


def _maximum_x(player:dict,card:dict,extra_generic:int=0,excluded_id:str|None=None)->int:
    if not _has_x_cost(card):return 0
    value=0
    while value<99 and _can_pay(player,card,extra_generic,excluded_id,x_value=value+1):value+=1
    return value


def _x_rules_card(card:dict,x_value:int|None)->dict:
    if x_value is None:return card
    return {**card,"oracle_text":re.sub(r"\bX\b",str(max(0,x_value)),card.get("oracle_text") or "",flags=re.IGNORECASE)}


def _library_search_spec(card:dict)->dict|None:
    text=card.get("oracle_text") or "";match=re.search(r"(?:may )?search your library for (up to )?(a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) (.+?) cards?\b",text,re.IGNORECASE)
    if not match:return None
    tail=text[match.start():];clause=re.split(r"\bthen shuffle\b",tail,maxsplit=1,flags=re.IGNORECASE)[0].casefold();lower_tail=tail.casefold()
    battlefield="onto the battlefield" in clause;hand="into your hand" in clause or "put it into your hand" in clause or "put them into your hand" in clause;top="shuffle and put that card on top" in lower_tail
    if "into your graveyard" in clause:return None
    if sum((battlefield,hand,top))!=1:return None
    words={"a":1,"an":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};word=match.group(2).casefold();amount=words.get(word,int(word) if word.isdigit() else 1);descriptor=match.group(3).strip().casefold()
    return {"amount":amount,"descriptor":descriptor,"destination":"battlefield" if battlefield else "library_top" if top else "hand","tapped":battlefield and "battlefield tapped" in clause,"different_names":"different names" in descriptor,"shared_land_type":"share a land type" in clause,"label":match.group(0)}


def _matches_library_search(card:dict,descriptor:str)->bool:
    type_line=card.get("type_line","").casefold();name=card.get("name","").casefold();descriptor=descriptor.casefold()
    named=re.search(r"card named ([^,.]+)",descriptor)
    if named:return name==named.group(1).strip()
    value=re.search(r"mana value (\d+) or less",descriptor)
    if value and float(card.get("mana_value") or 0)>int(value.group(1)):return False
    if "basic " in descriptor and "basic" not in type_line:return False
    if "basic land" in descriptor and not ("basic" in type_line and "land" in type_line):return False
    elif "land" in descriptor and "land" not in type_line:return False
    if "creature" in descriptor and "creature" not in type_line:return False
    qualities=[quality for quality in ("aura","equipment","shrine","lesson","noble","forest","island","mountain","plains","swamp","cave") if re.search(rf"\b{quality}\b",descriptor)]
    if qualities and not any(quality in type_line for quality in qualities):return False
    return any(term in descriptor for term in ("card","land","creature","aura","equipment","shrine","lesson","noble","forest","island","mountain","plains","swamp","cave"))


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
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "beginning_draw_pending":True,"first_turn_draw_skipped":False,"active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id, "players": players, "stack": [], "combat": {"attackers": [], "blocks": {}, "attack_targets": {},"block_orders":{},"damage_pending":False}, "consecutive_passes": 0, "pending_phase_advance": False, "pending_discard": None, "pending_mulligan_bottom": None, "pending_sacrifice": None, "pending_legendary": None,"pending_commander_zone":[],"pending_library_search":None,"pending_scry":None,"pending_damage_order":None,"pending_ward":None,"pending_trigger_targets":[], "log": []}
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
    pending_search=visible.get("pending_library_search")
    if pending_search and pending_search.get("player_id")!=viewer_id:pending_search["card_ids"]=[]
    return visible


def _target_kind(card: dict) -> str | None:
    text = (card.get("oracle_text") or "").casefold()
    type_line=card.get("type_line","").casefold()
    if "aura" in type_line:
        allowed=_aura_allowed_types(card)
        if len(allowed)==1:return next(iter(allowed))
        if allowed=={"player"}:return "player"
        if allowed or re.search(r"\benchant (?:nonland )?permanent\b",text):return "permanent"
    if "counter target spell" in text: return "spell"
    if re.search(r"target creature card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_creature"
    if re.search(r"target (?:nonland )?card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_card"
    if re.search(r"target player mills?", text): return "player"
    if re.search(r"target player sacrifices?",text):return "player"
    if re.search(r"(?:destroy|exile) target (?:artifact, creature, enchantment, planeswalker|nonland permanent|permanent)", text): return "permanent"
    if re.search(r"(?:destroy|exile|tap|untap|return|regenerate) target creature", text) or re.search(r"target creature .*(?:gets [+-](?:\d+|x)/[+-](?:\d+|x)|gains? [^.]+ until end of turn|can(?:not|'t) (?:attack|block))", text) or re.search(r"(?:deals (?:\d+|x) damage|put .+ counters?) (?:to|on) target creature", text): return "creature"
    for kind in ("artifact","enchantment","land","planeswalker"):
        if re.search(rf"(?:destroy|exile|tap|untap|return) target {kind}\b",text):return kind
    if re.search(r"return target (?:nonland )?permanent", text): return "permanent"
    if re.search(r"deals (?:\d+|x) damage to any target", text): return "any"
    return None


def _spell_targeting_card(card:dict)->dict:
    if not any(kind in card.get("type_line","") for kind in ("Creature","Artifact","Enchantment","Planeswalker","Battle")):return card
    clauses=re.split(r"(?<=[.!])\s+|\n",card.get("oracle_text") or "");spell_text=" ".join(clause for clause in clauses if not re.match(r"\s*(?:when|whenever|at the beginning)\b",clause,re.IGNORECASE))
    return {**card,"oracle_text":spell_text}


def _modal_spec(card:dict)->dict|None:
    lines=(card.get("oracle_text") or "").splitlines();header=next(((index,line.strip()) for index,line in enumerate(lines) if re.match(r"^choose (?:one|two|three|one or both|one or more)\b",line.strip(),re.IGNORECASE)),None)
    if header is None:return None
    index,label=header;oracle=(card.get("oracle_text") or "").casefold();lower=label.casefold();repeat="same mode more than once" in oracle;distinct_targets="different target" in oracle or "different player" in oracle
    if "one or more" in lower:min_modes,max_modes=1,5
    elif "one or both" in lower:min_modes,max_modes=1,2
    elif "choose three" in lower:min_modes=max_modes=3
    elif "choose two" in lower:min_modes=max_modes=2
    else:min_modes=max_modes=1
    options=[]
    for line in lines[index+1:]:
        stripped=line.strip()
        if stripped.startswith(("•","-")):options.append(stripped[1:].strip())
        elif options and stripped:options[-1]=f"{options[-1]} {stripped}"
    if not options:return None
    return {"min_modes":min_modes,"max_modes":min(max_modes,len(options) if not repeat else max_modes),"repeatable":repeat,"distinct_targets":distinct_targets,"options":[{"index":option_index,"label":text} for option_index,text in enumerate(options)]}


def _modal_options(card:dict)->list[dict]:
    return (_modal_spec(card) or {}).get("options",[])


def _selected_mode_card(card:dict,indices:list[int]|None)->dict:
    options=_modal_options(card)
    if not options:return card
    selected=[option["label"] for option in options if option["index"] in set(indices or [])]
    return {**card,"oracle_text":" ".join(selected)}


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
        aura_types=_aura_allowed_types(card)
        if kind in {"any", "player"} or (kind=="permanent" and "player" in aura_types): targets.append({"id": player["id"], "name": player["name"], "kind": "player", "controller_id": player["id"]})
        for permanent in player["battlefield"]:
            if kind in {"any", "permanent"} or (kind in {"creature","artifact","enchantment","land","planeswalker"} and kind in permanent.get("type_line", "").casefold()):
                aura_types=_aura_allowed_types(card)
                if "Aura" in card.get("type_line","") and aura_types and not any(allowed in permanent.get("type_line","").casefold() for allowed in aura_types if allowed!="player"):continue
                if "you control" in text and player["id"] != caster_id: continue
                if "an opponent controls" in text and player["id"] == caster_id: continue
                if "nonland permanent" in text and "Land" in permanent.get("type_line", ""): continue
                if _has_keyword(permanent,"Shroud") or (player["id"] != caster_id and _has_keyword(permanent,"Hexproof")): continue
                if _protected_from(permanent,card): continue
                targets.append({"id": permanent["instance_id"], "name": permanent["name"], "kind": "permanent", "controller_id": player["id"]})
    return targets


def _fight_target_steps(state:dict,caster_id:str,card:dict,source:dict|None=None)->list[dict]:
    text=(card.get("oracle_text") or "").casefold()
    if "fight" not in text:return []
    source_fight=bool(source and (re.search(r"(?:this creature|this permanent|it) fights? (?:up to one )?target creature",text) or re.search(rf"\b{re.escape(source.get('name','').casefold())}\b fights? (?:up to one )?target creature",text)))
    two_target=bool(re.search(r"target creature(?: you control)? fights? (?:another )?target creature",text) or re.search(r"two target creatures fight",text) or "fight each other" in text)
    if not source_fight and not two_target:return []
    candidates=[]
    for owner in state["players"]:
        for creature in owner["battlefield"]:
            if "Creature" not in creature.get("type_line","") or _has_keyword(creature,"Shroud") or (owner["id"]!=caster_id and _has_keyword(creature,"Hexproof")) or _protected_from(creature,card):continue
            candidates.append({"id":creature["instance_id"],"name":creature["name"],"kind":"permanent","controller_id":owner["id"]})
    opponent_only=bool(re.search(r"target creature (?:you don.t control|an opponent controls)",text))
    if source_fight:
        targets=[target for target in candidates if target["id"]!=source["instance_id"] and (not opponent_only or target["controller_id"]!=caster_id)]
        return [{"label":f"Choose a creature for {source['name']} to fight","targets":targets}]
    first=[target for target in candidates if target["controller_id"]==caster_id] if "target creature you control" in text else candidates
    second=[target for target in candidates if target["controller_id"]!=caster_id] if opponent_only else candidates
    return [{"label":"Choose the first fighting creature","targets":first},{"label":"Choose the other fighting creature","targets":second,"distinct":True}]


def _countered_spell_destination(state:dict,controller:dict,card:dict,flashback:bool=False)->None:
    owner=_player(state,card.get("owner_id",controller["id"]));card["controller_id"]=owner["id"]
    destination="exile" if flashback else "graveyard";owner[destination].append(card)
    _queue_commander_zone_choice(state,owner,card,destination)


def _multiplayer(state: dict) -> bool:
    return not any(player.get("is_bot") for player in state["players"])


def _pending_decision(state:dict)->bool:
    return bool(state.get("pending_discard") or state.get("pending_sacrifice") or state.get("pending_legendary") or state.get("pending_commander_zone") or state.get("pending_library_search") or state.get("pending_scry") or state.get("pending_damage_order") or state.get("pending_ward") or state.get("pending_trigger_targets"))


def _queue_commander_zone_choice(state:dict,owner:dict,card:dict,zone:str)->None:
    if not card.get("commander") or zone=="command":return
    pending=state.setdefault("pending_commander_zone",[])
    if any(entry["card_id"]==card["instance_id"] for entry in pending):return
    pending.append({"player_id":owner["id"],"card_id":card["instance_id"],"card_name":card["name"],"zone":zone})
    state["priority_player_id"]=pending[0]["player_id"]
    _log(state,f"{owner['name']} may move {card['name']} from {zone} to the command zone.")


def _queue_ward(state:dict,caster:dict,target_id:str|None,stack_item:dict)->None:
    if not target_id:return
    target_owner=next((owner for owner in state["players"] if any(card["instance_id"]==target_id for card in owner["battlefield"])),None)
    target=next((card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"]==target_id),None);details=_ward_details(target or {})
    if target and target_owner and target_owner["id"]!=caster["id"] and details:
        entry={"player_id":caster["id"],"stack_id":stack_item["id"],"source_name":target["name"],**details}
        if state.get("pending_ward"):state["pending_ward"].setdefault("remaining",[]).append(entry)
        else:state["pending_ward"]={**entry,"remaining":[]}
        state["priority_player_id"]=caster["id"];_log(state,f"{target['name']}'s ward requires {details['label']}.")


def _commander_tax(player: dict, card: dict) -> int:
    return player.get("commander_casts", 0) * 2 if card.get("commander") else 0


def _maximum_hand_size(player:dict)->int|None:
    texts=[(card.get("oracle_text") or "").casefold() for card in player["battlefield"]]
    if any("you have no maximum hand size" in text for text in texts):return None
    increases=sum(int(value) for text in texts for value in re.findall(r"maximum hand size is increased by (\d+)",text))
    return 7+increases


def legal_actions(state: dict, player_id: str, allow_direct_resolution:bool=True) -> list[dict]:
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
    pending_commanders=state.get("pending_commander_zone") or []
    if pending_commanders:
        pending=pending_commanders[0]
        if pending["player_id"]!=player_id:return []
        common={"card_id":pending["card_id"],"card_name":pending["card_name"],"zone":pending["zone"]}
        return [{"type":"move_commander",**common},{"type":"keep_commander",**common},{"type":"concede"}]
    pending_search=state.get("pending_library_search")
    if pending_search:
        if pending_search["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["library"]};cards=[cards_by_id[card_id] for card_id in pending_search["card_ids"] if card_id in cards_by_id]
        return [{"type":"search_library","card_ids":pending_search["card_ids"],"cards":cards,"min_amount":pending_search["min_amount"],"max_amount":pending_search["max_amount"],"destination":pending_search["destination"],"tapped":pending_search["tapped"],"label":pending_search["label"],"different_names":pending_search.get("different_names",False),"shared_land_type":pending_search.get("shared_land_type",False)},{"type":"concede"}]
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
        if pending.get("target_steps"):return [{"type":"choose_trigger_targets","target_steps":pending["target_steps"],"label":pending["card"]["oracle_text"],"source_name":pending["source_name"]},{"type":"concede"}]
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
    castable.extend((card,"flashback") for card in player["graveyard"] if _flashback_ability(card))
    for card, source in castable:
        flashback=_flashback_ability(card) if source=="flashback" else None;cost_card={**card,"mana_cost":flashback["mana_cost"]} if flashback else card;kicker_cost=_kicker_cost(card)
        instant_speed = "Instant" in card.get("type_line", "") or _has_keyword(card, "Flash")
        total_tax=_commander_tax(player,card) if source=="command" else 0
        behold_options=[candidate for zone in (player["hand"],player["battlefield"]) for candidate in zone if flashback and flashback["behold_type"] in candidate.get("type_line","").casefold()]
        if "Land" in card.get("type_line", "") or not ((active and main and not state["stack"]) or instant_speed) or not _can_pay(player,cost_card,total_tax) or (flashback and len(behold_options)<flashback["behold_amount"]): continue
        cost_label=cost_card.get("mana_cost") or "{0}";action = {"type": "cast", "card_id": card["instance_id"], "source": source, "commander_tax": total_tax,"label":f"{'Flashback' if flashback else 'Cast'} {card['name']} · {cost_label}{f' + {{2}}×{player.get("commander_casts",0)} commander tax' if total_tax else ''}"}
        if flashback:action.update({"flashback":True,"cost_kind":"behold" if flashback["behold_amount"] else None,"cost_amount":flashback["behold_amount"],"cost_options":[candidate["instance_id"] for candidate in behold_options]})
        if _has_x_cost(cost_card):action.update({"x_min":0,"x_max":_maximum_x(player,cost_card,total_tax)})
        modal_spec=_modal_spec(card);modal_options=(modal_spec or {}).get("options",[])
        if modal_spec:
            modes=[]
            for option in modal_options:
                mode_card={**card,"oracle_text":option["label"]};targeting_card=_spell_targeting_card(mode_card);targets=_targets(state,player_id,targeting_card)
                if _target_kind(targeting_card) and not targets:continue
                modes.append({**option,"targets":targets})
            if len(modes)<modal_spec["min_modes"] and not modal_spec["repeatable"]:continue
            action.update({"mode_count":modal_spec["min_modes"],"mode_min":modal_spec["min_modes"],"mode_max":min(modal_spec["max_modes"],len(modes) if not modal_spec["repeatable"] else modal_spec["max_modes"]),"mode_repeatable":modal_spec["repeatable"],"mode_distinct_targets":modal_spec["distinct_targets"],"modes":modes})
        else:
            base_rules=_kicked_rules_card(card,False);fight_steps=_fight_target_steps(state,player_id,base_rules)
            if fight_steps:
                if any(not step["targets"] for step in fight_steps):continue
                action["target_steps"]=fight_steps
            else:
                targeting_card=_spell_targeting_card(base_rules);targets = _targets(state, player_id, targeting_card)
                if _target_kind(targeting_card) and not targets: continue
                if targets: action["targets"] = targets
        actions.append(action)
        if kicker_cost:
            kicked_cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{kicker_cost}"}
            if _can_pay(player,kicked_cost_card,total_tax):
                kicked={**action,"kicked":True,"kicker_cost":kicker_cost,"label":f"{action['label']} + kicker {kicker_cost}"}
                if not modal_spec:
                    kicked.pop("targets",None);kicked.pop("target_steps",None);kicked_rules=_kicked_rules_card(card,True);kicked_fight=_fight_target_steps(state,player_id,kicked_rules)
                    if kicked_fight:
                        if any(not step["targets"] for step in kicked_fight):continue
                        kicked["target_steps"]=kicked_fight
                    else:
                        kicked_targeting=_spell_targeting_card(kicked_rules);kicked_targets=_targets(state,player_id,kicked_targeting)
                        if _target_kind(kicked_targeting) and not kicked_targets:continue
                        if kicked_targets:kicked["targets"]=kicked_targets
                if _has_x_cost(kicked_cost_card):kicked.update({"x_min":0,"x_max":_maximum_x(player,kicked_cost_card,total_tax)})
                actions.append(kicked)
    for card in player["hand"]:
        cycling=_cycling_ability(card)
        if cycling and _can_pay(player,{"mana_cost":cycling["mana_cost"]}):
            actions.append({"type":"cycle","card_id":card["instance_id"],"label":f"{cycling['keyword']} · {cycling['mana_cost']}","mana_cost":cycling["mana_cost"]})
    for permanent in player["battlefield"]:
        for index, ability in enumerate(_permanent_abilities(state,permanent)):
            if ability["taps"] and (permanent.get("tapped") or ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))):continue
            if ability["mana_cost"] and not _can_pay(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None):continue
            if ability["life_cost"] and player["life"]<ability["life_cost"]:continue
            if ability["counter_cost"] and permanent.get("counters",{}).get(ability["counter_cost"]["name"],0)<ability["counter_cost"]["amount"]:continue
            cost_options=_activated_cost_options(player,permanent,ability["selection_cost"])
            if ability["selection_cost"] and len(cost_options)<ability["selection_cost"]["amount"]:continue
            fight_steps=_fight_target_steps(state,player_id,ability["card"],permanent);targets=[] if fight_steps else _targets(state, player_id, ability["card"])
            if (fight_steps and any(not step["targets"] for step in fight_steps)) or (not fight_steps and _target_kind(ability["card"]) and not targets): continue
            action = {"type": "activate", "card_id": permanent["instance_id"], "ability_index": index, "label": f"{ability['cost']}: {ability['effect']}","life_cost":ability["life_cost"],"self_sacrifice":ability["self_sacrifice"],"counter_cost":ability["counter_cost"],"cost_kind":ability["selection_cost"]["kind"] if ability["selection_cost"] else None,"cost_amount":ability["selection_cost"]["amount"] if ability["selection_cost"] else 0,"cost_options":[card["instance_id"] for card in cost_options]}
            if _has_x_cost({"mana_cost":ability["mana_cost"]}):action.update({"x_min":0,"x_max":_maximum_x(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None)})
            if fight_steps:action["target_steps"]=fight_steps
            elif targets: action["targets"] = targets
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
        for equipment in player["battlefield"]:
            equip_cost=_equip_cost(equipment)
            if not equip_cost or not _can_pay(player,{"mana_cost":equip_cost}):continue
            targets=[{"id":creature["instance_id"],"name":creature["name"],"kind":"permanent","controller_id":player_id} for creature in player["battlefield"] if "Creature" in creature.get("type_line","") and not _has_keyword(creature,"Shroud") and not _protected_from(creature,equipment)]
            if targets:actions.append({"type":"equip","card_id":equipment["instance_id"],"label":f"Equip {equipment['name']} · {equip_cost}","mana_cost":equip_cost,"targets":targets})
    if _multiplayer(state) or not allow_direct_resolution:
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
        defender=opponent(state,player_id);eligible = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped") and _can_attack(state,card,player,defender) and (not card.get("summoning_sick") or _has_keyword(card, "Haste"))]
        if eligible:
            defending=opponent(state,player_id);defenders=[{"id":defending["id"],"name":defending["name"],"kind":"player","controller_id":defending["id"]}]
            defenders.extend({"id":card["instance_id"],"name":card["name"],"kind":"permanent","controller_id":defending["id"]} for card in defending["battlefield"] if "Planeswalker" in card.get("type_line",""))
            actions.append({"type": "declare_attackers", "card_ids": eligible,"defenders":defenders})
    elif not active and state["phase"] == "combat" and state["combat"]["attackers"] and not state["combat"].get("damage_pending"):
        attackers = [card for card in opponent(state, player_id)["battlefield"] if card["instance_id"] in state["combat"]["attackers"]]
        blockers = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped")]
        if blockers:
            legal_blocks = {blocker["instance_id"]:[attacker["instance_id"] for attacker in attackers if _can_block_pair(state,attacker,blocker)] for blocker in player["battlefield"] if blocker["instance_id"] in blockers}
            eligible_blockers=[blocker_id for blocker_id,attacker_ids in legal_blocks.items() if attacker_ids]
            if eligible_blockers: actions.append({"type": "declare_blockers", "card_ids": eligible_blockers, "legal_blocks": legal_blocks})
    return actions


def _resolve_spell(state: dict) -> None:
    item = state["stack"].pop()
    card, caster = item["card"], _player(state, item["controller_id"])
    if item.get("kind")=="equip_ability":
        equipment=next((permanent for permanent in caster["battlefield"] if permanent["instance_id"]==item.get("source_id") and "Equipment" in permanent.get("type_line","")),None);target=next((permanent for permanent in caster["battlefield"] if permanent["instance_id"]==item.get("target_id") and "Creature" in permanent.get("type_line","")),None)
        if not equipment or not target or _has_keyword(target,"Shroud") or _protected_from(target,equipment):_log(state,f"{card['name']} did not resolve because its source or target was no longer legal.");return
        _attach(state,equipment,target);_log(state,f"{caster['name']} equipped {target['name']} with {equipment['name']}.");return
    if item.get("kind","spell")=="spell" and len(item.get("mode_indices") or [])>1:
        options={option["index"]:option for option in _modal_options(card)};targets=item.get("mode_targets") or []
        for position,index in enumerate(item["mode_indices"]):
            option=options[index];mode_card=_x_rules_card({**card,"name":f"{card['name']} — mode {position+1}","oracle_text":option["label"]},item.get("x_value"));state["stack"].append({"id":_id(),"kind":"modal_effect","card":mode_card,"controller_id":caster["id"],"target_id":targets[position] if position<len(targets) else None,"x_value":item.get("x_value")});_resolve_spell(state)
        caster["exile" if item.get("flashback") else "graveyard"].append(card);_log(state,f"{card['name']} resolved with {len(item['mode_indices'])} modes.");return
    rules_card=_selected_mode_card(card,item.get("mode_indices")) if item.get("kind","spell")=="spell" else card
    if item.get("kind","spell")=="spell":rules_card=_kicked_rules_card(rules_card,bool(item.get("kicked")))
    rules_card=_x_rules_card(rules_card,item.get("x_value"));targeting_card=_spell_targeting_card(rules_card) if item.get("kind","spell")=="spell" else rules_card;target_kind=_target_kind(targeting_card);target_id=item.get("target_id")
    source_permanent=next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"]==item.get("source_id")),None);target_ids=item.get("target_ids") or [];fight_steps=_fight_target_steps(state,caster["id"],rules_card,source_permanent);valid_fight_ids=[target_value for position,target_value in enumerate(target_ids) if position<len(fight_steps) and target_value in {target["id"] for target in fight_steps[position]["targets"]}]
    if target_ids and not valid_fight_ids:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _log(state,f"{card['name']} was countered because all of its fight targets were no longer legal.");return
    if target_kind and target_id not in {target["id"] for target in _targets(state,caster["id"],targeting_card)}:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _log(state,f"{card['name']} was countered because its target was no longer legal.");return
    text = (rules_card.get("oracle_text") or "").casefold()
    is_permanent_spell = item.get("kind", "spell") == "spell" and any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"))
    effect_text = "" if is_permanent_spell and re.search(r"\b(?:when|whenever|at the beginning)\b", text) else text
    other = opponent(state, caster["id"])
    target_player = next((player for player in state["players"] if player["id"] == target_id), None)
    target_owner = next((player for player in state["players"] if any(permanent["instance_id"] == target_id for permanent in player["battlefield"])), None)
    target = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == target_id), None)
    target_stack_item = next((entry for entry in state["stack"] if entry["id"] == target_id), None)
    graveyard_owner=next((player for player in state["players"] if any(graveyard_card["instance_id"]==target_id for graveyard_card in player["graveyard"])),None)
    graveyard_target=next((graveyard_card for player in state["players"] for graveyard_card in player["graveyard"] if graveyard_card["instance_id"]==target_id),None)
    draw_match = re.search(r"draw (?:a|one|two|three|four|\d+) cards?", effect_text)
    if draw_match:
        word = draw_match.group(0).split()[1]
        _draw(state, caster, {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}.get(word,int(word) if word.isdigit() else 0))
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
    if fight_steps and len(valid_fight_ids)==len(fight_steps):
        fighters=([source_permanent,next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==valid_fight_ids[0]),None)] if len(fight_steps)==1 else [next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==fighter_id),None) for fighter_id in valid_fight_ids[:2]])
        if all(fighters) and fighters[0] is not fighters[1]:
            first,second=fighters;first_power=max(0,_parse_stats(first,state)[0]);second_power=max(0,_parse_stats(second,state)[0]);_damage_permanent(state,second,first_power,first);_damage_permanent(state,first,second_power,second);first["fought_turn"]=state["turn"];second["fought_turn"]=state["turn"];_log(state,f"{first['name']} fought {second['name']}.")
    if target and target_owner and re.search(r"destroy target (?:creature|permanent|nonland permanent)", effect_text):
        if _destroy_permanent(state,target_owner,target,"can't be regenerated" in effect_text):_log(state, f"{target['name']} was destroyed.")
    if target and target_owner and re.search(r"exile target (?:creature|permanent|nonland permanent)", effect_text):
        _leave_battlefield(state, target_owner, target, "exile"); _log(state, f"{target['name']} was exiled.")
    if target and target_owner and re.search(r"return target (?:creature|permanent|nonland permanent).* to (?:its|their) owner'?s hand", effect_text):
        _leave_battlefield(state, target_owner, target, "hand"); _log(state, f"{target['name']} returned to its owner's hand.")
    if target and re.search(r"\btap target creature", effect_text): target["tapped"] = True
    if target and re.search(r"\buntap target creature", effect_text): target["tapped"] = False
    regeneration_target=target if target and ("regenerate target creature" in effect_text or "regenerate it" in effect_text) else source_permanent if source_permanent and "regenerate this creature" in effect_text else None
    if regeneration_target:regeneration_target["regeneration_shields"]=regeneration_target.get("regeneration_shields",0)+1;_log(state,f"{regeneration_target['name']} gained a regeneration shield until end of turn.")
    if "regenerate each other creature you control" in effect_text and sum(any(kind in graveyard_card.get("type_line","") for kind in ("Instant","Sorcery")) for graveyard_card in caster["graveyard"])>=2:
        for permanent in caster["battlefield"]:
            if permanent is not regeneration_target and "Creature" in permanent.get("type_line",""):permanent["regeneration_shields"]=permanent.get("regeneration_shields",0)+1
    if target and re.search(r"(?:target|that) creature can(?:not|'t) attack(?: or block)? this turn",effect_text):target["cant_attack_until_turn"]=state["turn"]
    if target and re.search(r"(?:target|that) creature can(?:not|'t) (?:attack or )?block this turn",effect_text):target["cant_block_until_turn"]=state["turn"]
    global_no_blocks=re.search(r"(?:other )?creatures(?: controlled by that player| without flying)? can(?:not|'t) block this turn",effect_text)
    if global_no_blocks:
        artifact_condition="if you control three or more artifacts" in effect_text
        if artifact_condition and sum("Artifact" in permanent.get("type_line","") for permanent in caster["battlefield"])<3:global_no_blocks=None
    if global_no_blocks:
        affected=(target_player or target_owner) if "controlled by that player" in global_no_blocks.group(0) else None
        for owner in state["players"]:
            if affected and owner["id"]!=affected["id"]:continue
            for permanent in owner["battlefield"]:
                if "Creature" not in permanent.get("type_line","") or ("other creatures" in global_no_blocks.group(0) and target and permanent["instance_id"]==target["instance_id"]) or ("without flying" in global_no_blocks.group(0) and _has_keyword(permanent,"Flying")):continue
                permanent["cant_block_until_turn"]=state["turn"]
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
    search_spec=_library_search_spec({**rules_card,"oracle_text":effect_text})
    if search_spec and not state.get("pending_library_search"):
        eligible=[library_card["instance_id"] for library_card in caster["library"] if _matches_library_search(library_card,search_spec["descriptor"])];maximum=min(search_spec["amount"],len(eligible))
        state["pending_library_search"]={"player_id":caster["id"],"card_ids":eligible,"min_amount":0,"max_amount":maximum,**search_spec};state["priority_player_id"]=caster["id"]
        _log(state,f"{caster['name']} is searching their library for up to {maximum} matching card(s).")
    discard_match = re.search(r"(?:(target|each) opponent|you) discards? (a|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?", effect_text)
    if discard_match:
        amount_word=discard_match.group(2);words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 0);affected=caster if discard_match.group(0).startswith("you") else other;required=min(amount,len(affected["hand"]))
        if required:state["pending_discard"]={"player_id":affected["id"],"amount":required,"reason":"effect"};state["priority_player_id"]=affected["id"];_log(state,f"{affected['name']} must discard {required} card(s).")
    elif re.search(r"(?:then |you )?discard (?:a|one|two|three|four|\d+) cards?",effect_text):
        match=re.search(r"discard (a|one|two|three|four|\d+) cards?",effect_text);word=match.group(1);words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(word,int(word) if word.isdigit() else 1);required=min(amount,len(caster["hand"]))
        if required:state["pending_discard"]={"player_id":caster["id"],"amount":required,"reason":"effect"};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} must discard {required} card(s).")
    if target_stack_item and "counter target spell" in effect_text:
        state["stack"].remove(target_stack_item); countered=target_stack_item["card"]
        if target_stack_item.get("kind", "spell") == "spell": _countered_spell_destination(state,_player(state,target_stack_item["controller_id"]),countered,target_stack_item.get("flashback",False))
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
                    if destination=="graveyard":_destroy_permanent(state,owner,permanent,"can't be regenerated" in effect_text)
                    else:_leave_battlefield(state,owner,permanent,destination)
        _log(state,f"All {kind} were {'destroyed' if destination=='graveyard' else 'exiled'}.")
    global_stats=re.search(r"(?:all|each) creatures?(?: you control| your opponents control)? get ([+-]\d+)/([+-]\d+) until end of turn",effect_text)
    if global_stats:
        own_only="you control" in global_stats.group(0);opponents_only="opponents control" in global_stats.group(0)
        for owner in state["players"]:
            if own_only and owner["id"]!=caster["id"] or opponents_only and owner["id"]==caster["id"]:continue
            for permanent in owner["battlefield"]:
                if "Creature" in permanent.get("type_line",""):permanent["temporary_power"]=permanent.get("temporary_power",0)+int(global_stats.group(1));permanent["temporary_toughness"]=permanent.get("temporary_toughness",0)+int(global_stats.group(2))
    token_match = re.search(r"create (a|one|two|three|four|\d+) (\d+)/(\d+) ([^.]*?) creature tokens?", effect_text)
    if token_match:
        amount = {"a":1,"one":1,"two":2,"three":3,"four":4}.get(token_match.group(1),int(token_match.group(1)) if token_match.group(1).isdigit() else 0)
        for _ in range(amount):
            caster["battlefield"].append({"instance_id":_id(),"scryfall_id":"token","name":f"{token_match.group(4).title()} Token","image_url":None,"type_line":f"Token Creature — {token_match.group(4).title()}","oracle_text":"","mana_cost":"","mana_value":0,"power":token_match.group(2),"toughness":token_match.group(3),"owner_id":caster["id"],"controller_id":caster["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True})
        _log(state, f"{caster['name']} created {amount} token(s).")
    predefined_matches=list(re.finditer(r"create (a|one|two|three|four|five|\d+) (tapped )?(clue|food|treasure|blood|gold) tokens?",effect_text,re.IGNORECASE))
    for predefined in predefined_matches:
        word=predefined.group(1).casefold();amount={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(word,int(word) if word.isdigit() else 1);kind=predefined.group(3).title()
        for _ in range(amount):caster["battlefield"].append(_predefined_token(caster,kind,bool(predefined.group(2))))
        _log(state,f"{caster['name']} created {amount} {kind} token(s).")
    entered = False
    if is_permanent_spell:
        card["was_kicked"]=bool(item.get("kicked"))
        card["summoning_sick"] = "Creature" in card.get("type_line", "")
        if re.search(r"\benters (?:the battlefield )?tapped\b",text):card["tapped"]=True
        enters_counters=re.search(r"enters(?: the battlefield)? with (\d+) ([+−-]\d+/[+−-]\d+|loyalty|charge|shield|stun) counters?",text)
        if enters_counters:
            counter_name=enters_counters.group(2).replace("−","-");card["counters"][counter_name]=card["counters"].get(counter_name,0)+int(enters_counters.group(1))
        caster["battlefield"].append(card)
        if "Aura" in card.get("type_line","") and (target or target_player):_attach(state,card,target or target_player)
        entered = True
    elif item.get("kind", "spell") == "spell":
        caster["exile" if item.get("flashback") else "graveyard"].append(card)
    _log(state, f"{card['name']} resolved.")
    if entered: _queue_triggers(state, "enters", card, caster)


def _leave_battlefield(state: dict, owner: dict, card: dict, destination: str) -> None:
    if card.get("attached_to"):_detach(state,card)
    attachments=[(attachment_owner,attachment) for attachment_owner in state["players"] for attachment in list(attachment_owner["battlefield"]) if attachment.get("attached_to")==card.get("instance_id")]
    for attachment_owner,attachment in attachments:
        _detach(state,attachment)
        if "Aura" in attachment.get("type_line",""):_leave_battlefield(state,attachment_owner,attachment,"graveyard")
    if card in owner["battlefield"]: owner["battlefield"].remove(card)
    card["damage"] = 0; card["tapped"] = False
    _queue_triggers(state, "dies" if destination == "graveyard" else "leaves", card, owner)
    if card.get("token"): return
    zone_owner=_player(state,card.get("owner_id",owner["id"]));card["controller_id"]=zone_owner["id"]
    zone_owner[destination].append(card)
    _queue_commander_zone_choice(state,zone_owner,card,destination)


def _queue_triggers(state: dict, event: str, event_card: dict | None, event_owner: dict) -> None:
    sources = [(owner, permanent) for owner in state["players"] for permanent in owner["battlefield"]]
    if event in {"dies","cycling"} and event_card: sources.append((event_owner, event_card))
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
                if "if it was kicked" in lower:matches=matches and bool(event_card.get("was_kicked"))
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
            elif event == "cycling" and event_card:
                cycled_name=re.escape(event_card.get("name","").casefold());same_card=source is event_card and re.search(rf"when you cycle (?:~|this card|{cycled_name})\b",lower) is not None
                matches=same_card or (source is not event_card and owner["id"]==event_owner["id"] and "whenever you cycle a card" in lower)
            if not matches or "," not in clause: continue
            effect = clause.split(",", 1)[1].strip()
            if event=="enters" and re.match(r"if it was kicked,",effect,re.IGNORECASE):effect=effect.split(",",1)[1].strip()
            ability_card = {**source, "name": f"{source['name']} trigger", "oracle_text": effect, "type_line": "Ability", "mana_cost": ""}
            fight_steps=_fight_target_steps(state,owner["id"],ability_card,source);targets=[] if fight_steps else _targets(state, owner["id"], ability_card)
            for _ in range(trigger_count):
                trigger={"id":_id(),"kind":"trigger","card":ability_card,"controller_id":owner["id"],"target_id":None,"source_id":source["instance_id"]}
                if fight_steps:
                    if all(step["targets"] for step in fight_steps):state.setdefault("pending_trigger_targets",[]).append({"controller_id":owner["id"],"source_name":source["name"],"trigger":trigger,"card":ability_card,"target_steps":fight_steps});state["priority_player_id"]=state["pending_trigger_targets"][0]["controller_id"]
                    else:_log(state,f"{source['name']}'s fight trigger had no legal targets and was removed.")
                elif _target_kind(ability_card):
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
                if permanent.get("attached_to"):
                    target=next((target for target_owner in state["players"] for target in target_owner["battlefield"] if target["instance_id"]==permanent["attached_to"]),None) or next((player for player in state["players"] if player["id"]==permanent["attached_to"]),None);aura="Aura" in permanent.get("type_line","");aura_text=(permanent.get("oracle_text") or "").casefold();allowed_types=_aura_allowed_types(permanent);target_types=(target or {}).get("type_line","").casefold();type_illegal=bool(aura and allowed_types and not (("player" in allowed_types and target and target.get("id")) or any(kind in target_types for kind in allowed_types-{"player"})));wrong_controller=bool(aura and target and (("enchant creature you control" in aura_text and target.get("controller_id")!=permanent.get("controller_id")) or ("enchant creature an opponent controls" in aura_text and target.get("controller_id")==permanent.get("controller_id"))));illegal=not target or type_illegal or wrong_controller or (target is not None and target.get("instance_id") is not None and _protected_from(target,permanent))
                    if illegal:
                        _detach(state,permanent)
                        if aura:_leave_battlefield(state,owner,permanent,"graveyard");changed=True;continue
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
    state["pending_discard"]=None;state["turn"] += 1; state["phase"] = PHASES[0];state["beginning_draw_pending"]=True; state["active_player_id"] = opponent(state, state["active_player_id"])["id"]
    active = _player(state, state["active_player_id"]); active["land_plays_remaining"] = 1
    for owner in state["players"]:
        for permanent in owner["battlefield"]: permanent.pop("temporary_power",None); permanent.pop("temporary_toughness",None);permanent.pop("temporary_keywords",None);permanent.pop("cant_attack_until_turn",None);permanent.pop("cant_block_until_turn",None);permanent.pop("regeneration_shields",None); permanent["damage"] = 0
    for permanent in active["battlefield"]: permanent["tapped"] = False; permanent["summoning_sick"] = False
    _log(state, f"Turn {state['turn']} began for {active['name']}. Untap and upkeep started."); _queue_triggers(state,"upkeep",None,active)


def _advance_turn_phase(state: dict) -> None:
    if state["phase"] == "combat" and state["combat"]["attackers"]: _combat_damage(state)
    index = PHASES.index(state["phase"])
    if index == len(PHASES) - 1:
        ending=_player(state,state["active_player_id"]);maximum=_maximum_hand_size(ending);excess=max(0,len(ending["hand"])-maximum) if maximum is not None else 0
        if excess:
            state["pending_discard"]={"player_id":ending["id"],"amount":excess};state["priority_player_id"]=ending["id"];state["pending_phase_advance"]=False;state["consecutive_passes"]=0;_log(state,f"{ending['name']} must discard {excess} card(s) to hand size.");return
        _begin_next_turn(state)
    else:
        if state["phase"]=="beginning" and state.get("beginning_draw_pending",False):
            active=_player(state,state["active_player_id"])
            if state["turn"]==1 and not state.get("first_turn_draw_skipped"):
                state["first_turn_draw_skipped"]=True;_log(state,f"{active['name']} skipped the first turn's draw.")
            else:
                _draw(state,active)
                if state["status"]=="complete":return
                _log(state,f"{active['name']} drew for the turn.")
            state["beginning_draw_pending"]=False
        state["phase"] = PHASES[index + 1]
        if state["phase"] == "ending":
            _queue_triggers(state,"end_step",None,_player(state,state["active_player_id"]))
    if not state.get("pending_trigger_targets"):
        state["priority_player_id"] = state["active_player_id"]
    state["pending_phase_advance"] = False; state["consecutive_passes"] = 0


def perform_action(state: dict, player_id: str, action: dict, allow_direct_resolution:bool=True) -> dict:
    state = deepcopy(state)
    player = _player(state, player_id)
    action_type = action.get("type")
    allowed = {entry["type"] for entry in legal_actions(state, player_id,allow_direct_resolution)}
    manual_actions = {"adjust_life", "add_counter", "create_token", "move_zone"}
    if action_type not in allowed and action_type not in manual_actions:
        raise RuleViolation(f"{action_type} is not legal right now")
    if action_type == "keep":
        if player.get("mulligans",0):state["pending_mulligan_bottom"]=player_id;_log(state,f"{player['name']} kept and must put {player['mulligans']} card(s) on the bottom.")
        else:
            player["kept_hand"] = True; _log(state, f"{player['name']} kept seven cards.")
            if all(item["kept_hand"] for item in state["players"]):
                state["status"] = "active"; state["priority_player_id"] = state["active_player_id"];active=_player(state,state["active_player_id"]);_log(state,f"Turn 1 began for {active['name']}. Untap and upkeep started.");_queue_triggers(state,"upkeep",None,active)
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
        if all(item["kept_hand"] for item in state["players"]):
            state["status"]="active";state["priority_player_id"]=state["active_player_id"];active=_player(state,state["active_player_id"]);_log(state,f"Turn 1 began for {active['name']}. Untap and upkeep started.");_queue_triggers(state,"upkeep",None,active)
    elif action_type == "play_land":
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id") and "Land" in card.get("type_line", "")), None)
        if not card: raise RuleViolation("That land is not in your hand")
        player["hand"].remove(card); player["battlefield"].append(card); player["land_plays_remaining"] -= 1; _log(state, f"{player['name']} played {card['name']}."); _queue_triggers(state,"enters",card,player)
    elif action_type == "cast":
        requested_source=action.get("source");zone_name="graveyard" if requested_source=="flashback" else requested_source if requested_source in {"hand","command"} else next((zone for zone in ("hand","command") if any(card["instance_id"]==action.get("card_id") for card in player.get(zone,[]))),None)
        source="flashback" if zone_name=="graveyard" else zone_name;card=next((card for card in player.get(zone_name or "hand",[]) if card["instance_id"]==action.get("card_id")),None);flashback=_flashback_ability(card or {}) if source=="flashback" else None
        requested_kicked=bool(action.get("kicked"));available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="cast" and entry["card_id"]==action.get("card_id") and entry.get("source")==source and bool(entry.get("kicked"))==requested_kicked),None)
        tax = _commander_tax(player, card) if card and source=="command" else 0
        if not card:raise RuleViolation("That spell cannot be cast")
        if not available:raise RuleViolation("That spell cannot be cast from that zone")
        cost_card={**card,"mana_cost":flashback["mana_cost"]} if flashback else card
        if requested_kicked:cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{_kicker_cost(card) or ''}"}
        x_value=int(action.get("x_value") or 0);x_max=_maximum_x(player,cost_card,tax)
        if (_has_x_cost(cost_card) and not 0<=x_value<=x_max) or (not _has_x_cost(cost_card) and action.get("x_value") is not None): raise RuleViolation("That spell cannot be cast with the chosen X value")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        if len(selected_cost_ids)!=required_cost or len(set(selected_cost_ids))!=required_cost or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose exactly {required_cost} cards or permanents for the additional cost")
        target_ids=action.get("target_ids") or [];target_steps=available.get("target_steps") or []
        if target_steps:
            if len(target_ids)!=len(target_steps) or any(target_id not in {target["id"] for target in target_steps[index]["targets"]} for index,target_id in enumerate(target_ids)) or any(step.get("distinct") and target_ids[index] in target_ids[:index] for index,step in enumerate(target_steps)):raise RuleViolation("Choose each legal fight target exactly once")
        elif target_ids:raise RuleViolation("That spell does not use multiple targets")
        if not _can_pay(player,cost_card,tax,x_value=x_value):raise RuleViolation("That spell cannot be cast")
        modal_spec=_modal_spec(card);modal_options=(modal_spec or {}).get("options",[]);chosen_modes=action.get("chosen_modes") or [];mode_targets=action.get("mode_targets") or []
        if modal_spec and len(chosen_modes)==1 and not mode_targets:mode_targets=[action.get("target_id")]
        if modal_spec:
            legal_modes={mode["index"] for mode in (available or {}).get("modes",[])}
            if not modal_spec["min_modes"]<=len(chosen_modes)<=modal_spec["max_modes"]:raise RuleViolation(f"Choose between {modal_spec['min_modes']} and {modal_spec['max_modes']} modes")
            if (not modal_spec["repeatable"] and len(set(chosen_modes))!=len(chosen_modes)) or any(index not in legal_modes for index in chosen_modes):raise RuleViolation("Choose legal modes without repeating them")
            if len(mode_targets)!=len(chosen_modes):raise RuleViolation("Provide one target selection for each chosen mode")
            for position,index in enumerate(chosen_modes):
                option=next(option for option in modal_options if option["index"]==index);mode_card=_spell_targeting_card({**card,"oracle_text":option["label"]});legal_targets=_targets(state,player_id,mode_card);requires_target=bool(_target_kind(mode_card));selected_target=mode_targets[position]
                if requires_target and selected_target not in {target["id"] for target in legal_targets}:raise RuleViolation(f"Choose a legal target for mode {position+1}")
                if not requires_target and selected_target is not None:raise RuleViolation(f"Mode {position+1} does not require a target")
            selected_targets=[target for target in mode_targets if target]
            if modal_spec["distinct_targets"] and len(selected_targets)!=len(set(selected_targets)):raise RuleViolation("Each mode must have a different target")
        elif chosen_modes or mode_targets:raise RuleViolation("That spell has no modal choice")
        rules_card=_kicked_rules_card(_selected_mode_card(card,chosen_modes),requested_kicked);targeting_card=_spell_targeting_card(rules_card);targets = _targets(state, player_id, targeting_card); target_id = action.get("target_id")
        if not modal_spec and _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}: raise RuleViolation("Choose a legal target")
        _pay_mana(state,player,cost_card,tax,x_value=x_value);player[zone_name].remove(card)
        if card.get("commander"): player["commander_casts"] = player.get("commander_casts", 0) + 1
        effective_target=target_id or (mode_targets[0] if len(mode_targets)==1 else None);stack_item={"id": _id(), "card": card, "controller_id": player_id, "target_id": effective_target,"target_ids":target_ids,"mode_indices":chosen_modes,"mode_targets":mode_targets,"x_value":x_value,"flashback":bool(flashback),"kicked":requested_kicked};state["stack"].append(stack_item); state["consecutive_passes"] = 0; state["pending_phase_advance"] = False
        _queue_triggers(state,"cast",card,player)
        ward_targets=[effective_target] if effective_target else []
        ward_targets.extend(target for target in mode_targets if target and target not in ward_targets)
        ward_targets.extend(target for target in target_ids if target not in ward_targets)
        for ward_target in ward_targets:_queue_ward(state,player,ward_target,stack_item)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"): state["priority_player_id"] = opponent(state, player_id)["id"]
        mode_label="; ".join(next(mode["label"] for mode in modal_options if mode["index"]==index) for index in chosen_modes)
        behold_names=[next(candidate["name"] for zone in (player["hand"],player["battlefield"]) for candidate in zone if candidate["instance_id"]==card_id) for card_id in selected_cost_ids]
        _log(state, f"{player['name']} cast {card['name']}{' using flashback' if flashback else ''}{' with kicker' if requested_kicked else ''}{f' with X={x_value}' if _has_x_cost(cost_card) else ''}{f' choosing {mode_label}' if mode_label else ''}{f' with {tax} commander tax' if tax else ''}{f' by beholding {', '.join(behold_names)}' if behold_names else ''}{' targeting '+next((target['name'] for target in targets if target['id']==target_id),'') if target_id else ''}.")
    elif action_type == "cycle":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);cycling=_cycling_ability(card or {})
        available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="cycle" and entry["card_id"]==action.get("card_id")),None)
        if not card or not cycling or not available:raise RuleViolation("That card cannot be cycled")
        _pay_mana(state,player,{"mana_cost":cycling["mana_cost"]});player["hand"].remove(card);player["graveyard"].append(card)
        state["stack"].append({"id":_id(),"kind":"ability","card":cycling["card"],"controller_id":player_id,"target_id":None,"source_id":card["instance_id"]});state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        _queue_triggers(state,"cycling",card,player)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} discarded {card['name']} to activate {cycling['keyword']}.")
    elif action_type == "equip":
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="equip" and entry["card_id"]==action.get("card_id")),None);target_id=action.get("target_id")
        if not available or target_id not in {target["id"] for target in available["targets"]}:raise RuleViolation("That Equipment cannot be attached to that creature now")
        equipment=next(card for card in player["battlefield"] if card["instance_id"]==action["card_id"]);target=next(card for card in player["battlefield"] if card["instance_id"]==target_id)
        _pay_mana(state,player,{"mana_cost":available["mana_cost"]});stack_item={"id":_id(),"kind":"equip_ability","card":{**equipment,"name":f"{equipment['name']} equip ability","type_line":"Ability"},"controller_id":player_id,"target_id":target_id,"source_id":equipment["instance_id"]};state["stack"].append(stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if _multiplayer(state) or not allow_direct_resolution:state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {equipment['name']}'s equip ability targeting {target['name']}.")
    elif action_type == "activate":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That ability cannot be activated")
        ability=_permanent_abilities(state,permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        x_value=int(action.get("x_value") or 0);x_card={"mana_cost":ability["mana_cost"]};x_max=_maximum_x(player,x_card,excluded_id=permanent["instance_id"] if ability["taps"] else None)
        if (_has_x_cost(x_card) and not 0<=x_value<=x_max) or (not _has_x_cost(x_card) and action.get("x_value") is not None):raise RuleViolation("That ability cannot be activated with the chosen X value")
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        target_ids=action.get("target_ids") or [];target_steps=available.get("target_steps") or []
        if target_steps:
            if len(target_ids)!=len(target_steps) or any(target_value not in {target["id"] for target in target_steps[position]["targets"]} for position,target_value in enumerate(target_ids)) or any(step.get("distinct") and target_ids[position] in target_ids[:position] for position,step in enumerate(target_steps)):raise RuleViolation("Choose each legal fight target exactly once")
        elif target_ids:raise RuleViolation("That ability does not use multiple targets")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        if len(selected_cost_ids)!=required_cost or len(set(selected_cost_ids))!=required_cost or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose exactly {required_cost} legal card(s) for the activation cost")
        selected_cost_cards=[card for zone in (player["hand"],player["battlefield"]) for card in zone if card["instance_id"] in set(selected_cost_ids)]
        if len(selected_cost_cards)!=required_cost:raise RuleViolation("One or more activation cost cards are no longer available")
        if ability["mana_cost"]:_pay_mana(state,player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None,x_value=x_value)
        if ability["life_cost"]:player["life"]-=ability["life_cost"]
        if ability["counter_cost"]:
            name,amount=ability["counter_cost"]["name"],ability["counter_cost"]["amount"];permanent["counters"][name]-=amount
        if ability["taps"]:permanent["tapped"]=True
        stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"target_ids":target_ids,"source_id":permanent["instance_id"],"x_value":x_value};state["stack"].append(stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        for ward_target in ([target_id] if target_id else [])+target_ids:_queue_ward(state,player,ward_target,stack_item)
        if ability["self_sacrifice"]:_leave_battlefield(state,player,permanent,"graveyard")
        if available.get("cost_kind")=="discard":
            for card in selected_cost_cards:player["hand"].remove(card);player["graveyard"].append(card)
        elif available.get("cost_kind")=="sacrifice":
            for card in selected_cost_cards:_leave_battlefield(state,player,card,"graveyard")
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {permanent['name']}: {ability['effect']}")
    elif action_type == "activate_loyalty":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id") and "Planeswalker" in card.get("type_line","")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate_loyalty" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That loyalty ability cannot be activated")
        ability=_loyalty_abilities(permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        permanent["counters"]["loyalty"]=permanent["counters"].get("loyalty",0)+ability["cost"];permanent["loyalty_activated_turn"]=state["turn"];stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"source_id":permanent["instance_id"]};state["stack"].append(stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_queue_ward(state,player,target_id,stack_item)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward"):state["priority_player_id"]=opponent(state,player_id)["id"]
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
        if len(requested)==1:
            lone=next(card for card in player["battlefield"] if card["instance_id"] in requested)
            if "can't attack or block alone" in _effective_rules_text(state,lone):raise RuleViolation(f"{lone['name']} can't attack alone")
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
        if len(blocks)==1:
            lone=next(card for card in player["battlefield"] if card["instance_id"] in blocks)
            if "can't attack or block alone" in _effective_rules_text(state,lone):raise RuleViolation(f"{lone['name']} can't block alone")
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
            if kind=="mana":_pay_mana(state,player,{"mana_cost":pending["mana_cost"]})
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
            if stack_item.get("kind","spell")=="spell":_countered_spell_destination(state,player,stack_item["card"],stack_item.get("flashback",False))
            _log(state,f"{stack_item['card']['name']} was countered by {pending['source_name']}'s ward.")
        remaining=pending.get("remaining") or []
        if action_type=="pay_ward" and remaining:
            state["pending_ward"]={**remaining[0],"remaining":remaining[1:]};state["priority_player_id"]=player_id
        else:state["pending_ward"]=None;state["priority_player_id"]=opponent(state,player_id)["id"] if (_multiplayer(state) or not allow_direct_resolution) else player_id
    elif action_type in {"choose_trigger_target","choose_trigger_targets","skip_trigger"}:
        pending_list=state.get("pending_trigger_targets") or []
        if not pending_list or pending_list[0]["controller_id"]!=player_id:raise RuleViolation("There is no triggered target decision for this player")
        pending=pending_list.pop(0);targets=_targets(state,player_id,pending["card"]);target_id=action.get("target_id")
        if action_type=="choose_trigger_targets":
            steps=pending.get("target_steps") or [];target_ids=action.get("target_ids") or []
            if len(target_ids)!=len(steps) or any(target_value not in {target["id"] for target in steps[position]["targets"]} for position,target_value in enumerate(target_ids)) or any(step.get("distinct") and target_ids[position] in target_ids[:position] for position,step in enumerate(steps)):raise RuleViolation("Choose legal targets for the fight trigger")
            trigger=pending["trigger"];trigger["target_ids"]=target_ids;state["stack"].append(trigger);_log(state,f"{player['name']} chose the fighters for {pending['source_name']}'s trigger.")
        elif action_type=="choose_trigger_target":
            if target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target for the triggered ability")
            trigger=pending["trigger"];trigger["target_id"]=target_id;state["stack"].append(trigger);_log(state,f"{player['name']} chose {next(target['name'] for target in targets if target['id']==target_id)} for {pending['source_name']}'s trigger.")
        elif targets:raise RuleViolation("This triggered ability still has legal targets")
        state["pending_trigger_targets"]=pending_list;state["priority_player_id"]=pending_list[0]["controller_id"] if pending_list else state["active_player_id"]
    elif action_type == "advance_phase":
        if _multiplayer(state) or not allow_direct_resolution:
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
    elif action_type in {"move_commander","keep_commander"}:
        pending_list=state.get("pending_commander_zone") or []
        if not pending_list or pending_list[0]["player_id"]!=player_id or pending_list[0]["card_id"]!=action.get("card_id"):raise RuleViolation("There is no commander zone choice for that card")
        pending=pending_list.pop(0);zone=pending["zone"];card=next((card for card in player.get(zone,[]) if card["instance_id"]==pending["card_id"]),None)
        if not card:raise RuleViolation("That commander is no longer in the expected zone")
        if action_type=="move_commander":
            player[zone].remove(card);player["command"].append(card);_log(state,f"{player['name']} moved {card['name']} from {zone} to the command zone.")
        else:_log(state,f"{player['name']} kept {card['name']} in {zone}.")
        state["pending_commander_zone"]=pending_list
        if pending_list:state["priority_player_id"]=pending_list[0]["player_id"]
        elif state.get("pending_trigger_targets"):state["priority_player_id"]=state["pending_trigger_targets"][0]["controller_id"]
        else:state["priority_player_id"]=state["active_player_id"]
    elif action_type == "search_library":
        pending=state.get("pending_library_search") or {};requested=action.get("card_ids") or [];allowed_ids=set(pending.get("card_ids",[]));minimum=pending.get("min_amount",0);maximum=pending.get("max_amount",0)
        if pending.get("player_id")!=player_id or not minimum<=len(requested)<=maximum or len(set(requested))!=len(requested) or not set(requested).issubset(allowed_ids):raise RuleViolation(f"Choose between {minimum} and {maximum} matching card(s)")
        chosen=[card for card in player["library"] if card["instance_id"] in set(requested)]
        if pending.get("different_names") and len({card["name"].casefold() for card in chosen})!=len(chosen):raise RuleViolation("Choose cards with different names")
        if pending.get("shared_land_type") and len(chosen)>1:
            subtype_sets=[set(re.split(r"\s+",card.get("type_line","").split("—",1)[-1].casefold())) for card in chosen]
            if not set.intersection(*subtype_sets):raise RuleViolation("Choose lands that share a land type")
        for card in chosen:player["library"].remove(card)
        random.SystemRandom().shuffle(player["library"]);destination=pending.get("destination","hand")
        for card in chosen:
            if destination=="battlefield":
                card["controller_id"]=player_id;card["tapped"]=bool(pending.get("tapped"));card["summoning_sick"]="Creature" in card.get("type_line","");player["battlefield"].append(card);_queue_triggers(state,"enters",card,player)
            elif destination=="library_top":player["library"].append(card)
            else:player["hand"].append(card)
        state["pending_library_search"]=None;state["priority_player_id"]=(state.get("pending_trigger_targets") or [{"controller_id":state["active_player_id"]}])[0]["controller_id"];_log(state,f"{player['name']} found {len(chosen)} card(s), moved them to {destination.replace('_',' ')}, and shuffled.")
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
