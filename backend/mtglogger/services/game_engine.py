import random
import re
import uuid
from copy import deepcopy
from itertools import combinations, product


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


def _draw(state: dict, player: dict, amount: int = 1,emit_events:bool=True) -> None:
    trigger_dedupe:set[str]=set()
    for _ in range(amount):
        if not player["library"]:
            if not player.get("lost"):
                player["lost"] = True
                player["loss_reason"] = "empty_library"
                _log(state, f"{player['name']} tried to draw from an empty library.")
            return
        drawn=player["library"].pop();player["hand"].append(drawn)
        if emit_events:
            if player.get("draw_event_turn")!=state["turn"]:player["draw_event_turn"]=state["turn"];player["draws_this_turn"]=0
            player["draws_this_turn"]=player.get("draws_this_turn",0)+1
            _queue_triggers(state,"draw",drawn,player,trigger_dedupe)


def _gain_life(state:dict,player:dict,amount:int)->None:
    if amount<=0:return
    for owner in state["players"]:
        for permanent in owner["battlefield"]:
            text=(permanent.get("oracle_text") or "").casefold()
            prevented="players can't gain life" in text or (owner["id"]==player["id"] and "you can't gain life" in text) or (owner["id"]!=player["id"] and "your opponents can't gain life" in text)
            if prevented:_log(state,f"{permanent['name']} prevented {player['name']} from gaining {amount} life.");return
    original=amount
    for permanent in player["battlefield"]:
        text=(permanent.get("oracle_text") or "").casefold()
        if "if you would gain life" not in text:continue
        if re.search(r"gain (?:twice|two times) that much life instead",text):amount*=2
        elif re.search(r"gain (?:three times|triple) that much life instead",text):amount*=3
        else:
            extra=re.search(r"gain that much life plus (\d+) instead",text) or re.search(r"gain that much plus (\d+) life instead",text)
            if extra:amount+=int(extra.group(1))
    player["life"]+=amount
    if amount!=original:_log(state,f"{player['name']}'s life gain was replaced from {original} to {amount}.")
    if player.get("life_gain_event_turn")!=state["turn"]:player["life_gain_event_turn"]=state["turn"];player["life_gain_events_this_turn"]=0
    player["life_gain_events_this_turn"]=player.get("life_gain_events_this_turn",0)+1
    _queue_triggers(state,"life_gain",None,player)


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


_MANA_COLORS = "WUBRGC"


def _mana_pools(effect: str) -> list[tuple[int, ...]]:
    options: set[tuple[int, ...]] = set()
    for clause in re.findall(r"Add ([^.\n]+)", effect, re.IGNORECASE):
        symbols = [symbol.upper() for symbol in re.findall(r"\{([WUBRGC])\}", clause, re.IGNORECASE)]
        if symbols:
            if " or " in clause.casefold():
                for symbol in symbols:
                    options.add(tuple(1 if color == symbol else 0 for color in _MANA_COLORS))
            else:
                options.add(tuple(symbols.count(color) for color in _MANA_COLORS))
            continue
        amount_match = re.search(r"\b(one|two|three|four|five) mana\b", clause, re.IGNORECASE)
        amount = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get((amount_match.group(1).casefold() if amount_match else ""), 1)
        if re.search(r"any (?:one )?color",clause,re.IGNORECASE):
            if "any combination" in clause.casefold():
                pools = {(0, 0, 0, 0, 0, 0)}
                for _ in range(amount):
                    pools = {tuple(pool[index] + (1 if index == choice else 0) for index in range(6)) for pool in pools for choice in range(5)}
                options.update(pools)
            else:
                options.update(tuple(amount if color == choice else 0 for color in _MANA_COLORS) for choice in "WUBRG")
    return sorted(options)


def _mana_source_options(card: dict) -> list[dict]:
    """Pair every usable mana output with the costs of that exact ability."""
    type_line=card.get("type_line","");text=card.get("oracle_text") or "";options=[]
    for line in text.splitlines():
        match=re.match(r"^([^:]+):\s*(Add [^.\n]+)",line.strip(),re.IGNORECASE)
        if not match:continue
        cost,effect=match.group(1).strip(),match.group(2).strip();lower=cost.casefold();taps="{t}" in lower
        self_sacrifice=re.search(r"\bsacrifice (?:this (?:artifact|creature|permanent)|%s)\b"%re.escape(card.get("name","")),cost,re.IGNORECASE) is not None
        life_match=re.search(r"\bpay (\d+) life\b",cost,re.IGNORECASE);life_cost=int(life_match.group(1)) if life_match else 0
        residual=re.sub(r"\{t\}|pay \d+ life|sacrifice (?:this (?:artifact|creature|permanent)|%s)|[,. ]"%re.escape(card.get("name","")),"",cost,flags=re.IGNORECASE)
        if residual:continue
        options.extend({"pool":pool,"taps":taps,"life_cost":life_cost,"self_sacrifice":self_sacrifice} for pool in _mana_pools(effect))

    if any(kind in type_line for kind in ("Treasure","Gold")) and not options:
        taps="Treasure" in type_line
        options=[{"pool":tuple(1 if color==choice else 0 for color in _MANA_COLORS),"taps":taps,"life_cost":0,"self_sacrifice":True} for choice in "WUBRG"]

    if not options and "Land" in type_line:
        colors={color for land,color in BASIC_COLORS.items() if land.casefold() in type_line.casefold()}
        if not colors and "Basic Land" in type_line:
            colors = {"C"}
        options=[{"pool":tuple(1 if color==choice else 0 for color in _MANA_COLORS),"taps":True,"life_cost":0,"self_sacrifice":False} for choice in colors]
    return options


def _mana_output_options(card: dict) -> list[tuple[int, ...]]:
    """Compatibility view used by diagnostics and tests."""
    return sorted({option["pool"] for option in _mana_source_options(card)})


def _pool_pays(pool: tuple[int, ...], colored: list[set[str]], generic: int) -> bool:
    remaining = list(pool)
    requirements = sorted(colored, key=len)

    def assign(index: int) -> bool:
        if index == len(requirements):
            return sum(remaining) >= generic
        for color in requirements[index]:
            color_index = _MANA_COLORS.index(color)
            if remaining[color_index]:
                remaining[color_index] -= 1
                if assign(index + 1):
                    return True
                remaining[color_index] += 1
        return False

    return assign(0)


def _mana_payment_plan(player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None, x_value: int = 0, excluded_ids: set[str] | None = None) -> list[dict] | None:
    excluded = set(excluded_ids or ())
    if excluded_id:
        excluded.add(excluded_id)
    sources = [permanent for permanent in player["battlefield"] if permanent.get("instance_id") not in excluded and _mana_source_options(permanent)]
    sources.extend({"instance_id": f"firebending-mana-{index}", "name": "Firebending mana", "type_line": "", "oracle_text": "Add {R}.", "firebending_mana": True} for index in range(player.get("firebending_mana", 0)))
    colored, generic = _mana_requirements(card, extra_generic, x_value)
    needed = len(colored) + generic
    if needed == 0:
        return []

    # Dynamic programming keeps one cheapest source set for every useful mana pool.
    # Expiring firebending mana is preferred, reusable sources follow, and sacrifice
    # sources are conserved unless they are needed.
    empty = (0, 0, 0, 0, 0, 0)
    states: dict[tuple[int, ...], tuple[int, list[dict]]] = {empty: (0, [])}
    for source in sources:
        options = [{"pool":(0,0,0,1,0,0),"taps":False,"life_cost":0,"self_sacrifice":False}] if source.get("firebending_mana") else _mana_source_options(source)
        options=[option for option in options if player.get("life",0)>=option["life_cost"] and (not option["taps"] or (not source.get("tapped") and not ("Creature" in source.get("type_line","") and source.get("summoning_sick") and not _has_keyword(source,"Haste"))))]
        if not options:
            continue
        updated = dict(states)
        for pool, (cost, chosen) in states.items():
            for option in options:
                output=option["pool"]
                combined = tuple(min(needed, pool[index] + output[index]) for index in range(6))
                source_cost=1 if source.get("firebending_mana") else 10000 if option["self_sacrifice"] else 100+option["life_cost"]*1000
                candidate = (cost + source_cost, chosen + [{"source":source,"option":option}])
                current = updated.get(combined)
                if current is None or (candidate[0], len(candidate[1])) < (current[0], len(current[1])):
                    updated[combined] = candidate
        states = updated
    payable = [(cost, len(chosen), chosen) for pool, (cost, chosen) in states.items() if _pool_pays(pool, colored, generic)]
    return min(payable, default=(0, 0, None), key=lambda item: (item[0], item[1]))[2]


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
    return keyword.casefold() in printed|temporary|attached or (keyword.casefold()=="haste" and bool(card.get("earthbent"))) or re.search(rf"\b{re.escape(keyword.casefold())}\b", (card.get("oracle_text") or "").casefold()) is not None


def _attachment_keywords(card:dict)->list[str]:
    text=(card.get("oracle_text") or "").casefold();supported=("defender","flying","first strike","double strike","deathtouch","haste","hexproof","indestructible","lifelink","menace","reach","trample","vigilance")
    clauses=[clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if re.search(r"(?:equipped|enchanted) creature .*?\b(?:has|gains?)\b",clause)]
    return [keyword for keyword in supported if any(re.search(rf"\b{re.escape(keyword)}\b",clause) for clause in clauses)]


def _detach(state:dict,attachment:dict,restore_control:bool=True)->None:
    target_id=attachment.pop("attached_to",None)
    if not target_id:return
    target=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==target_id),None)
    return_to=attachment.pop("control_aura_return_to",None)
    if target:
        target.get("attachment_keywords",{}).pop(attachment["instance_id"],None);target.get("attachment_rules",{}).pop(attachment["instance_id"],None)
        if restore_control and return_to:_change_control(state,target,_player(state,return_to))


def _attach(state:dict,attachment:dict,target:dict)->None:
    _detach(state,attachment);attachment["attached_to"]=target.get("instance_id",target.get("id"));keywords=_attachment_keywords(attachment)
    if target.get("instance_id"):
        target.setdefault("attachment_rules",{})[attachment["instance_id"]]=attachment.get("oracle_text") or ""
        if keywords:target.setdefault("attachment_keywords",{})[attachment["instance_id"]]=keywords
        if re.search(r"\byou control enchanted (?:creature|permanent)\b",attachment.get("oracle_text") or "",re.IGNORECASE):
            current=next(owner for owner in state["players"] if target in owner["battlefield"]);controller=_player(state,attachment["controller_id"])
            attachment["control_aura_return_to"]=current["id"]
            _change_control(state,target,controller)


def _equip_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Equip\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _crew_value(card:dict)->int|None:
    match=re.search(r"(?:^|\n)Crew\s+(\d+)\b",card.get("oracle_text") or "",re.IGNORECASE)
    return int(match.group(1)) if match else None


def _cycling_ability(card:dict)->dict|None:
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^((?:[A-Za-z][A-Za-z ]*)?cycling)\s+((?:\{[^}]+\})+)",line.strip(),re.IGNORECASE)
        if not match:continue
        keyword,cost=match.group(1),match.group(2).upper();descriptor=keyword[:-7].strip()
        effect="Draw a card." if not descriptor else f"Search your library for a {descriptor} card, reveal it, put it into your hand, then shuffle."
        return {"keyword":keyword,"mana_cost":cost,"effect":effect,"card":{**card,"name":f"{card['name']} — {keyword}","oracle_text":effect,"source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""}}
    return None


def _flashback_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)Flashback[ —-]*((?:\{[^}]+\})+)(?:,\s*Behold\s+(a|one|two|three|four|five|\d+)\s+([A-Za-z]+))?",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5};amount_word=(match.group(2) or "").casefold();amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 0)
    return {"mana_cost":match.group(1).upper(),"behold_amount":amount,"behold_type":(match.group(3) or "").removesuffix("s").casefold()}


def _kicker_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Kicker\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _has_convoke(card:dict)->bool:
    return _has_keyword(card,"Convoke")


def _earthbend_value(card:dict)->int|None:
    match=re.search(r"\bearthbend\s+(\d+)\b",card.get("oracle_text") or "",re.IGNORECASE)
    return int(match.group(1)) if match else None


def _waterbend_symbol(text:str)->str|None:
    match=re.search(r"\bwaterbend\s+\{(\d+|X)\}",text or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _spell_waterbend_symbol(card:dict)->str|None:
    text=card.get("oracle_text") or ""
    return _waterbend_symbol(text) if re.search(r"additional cost to cast this spell[^.]*waterbend",text,re.IGNORECASE) else None


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
    return {**card,"oracle_text":"\n".join(filter(None,resolved))}


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
    colors={part for symbol in re.findall(r"\{([^}]+)\}",(card.get("source_mana_cost") or card.get("mana_cost") or "")) for part in symbol.upper().split("/") if part in "WUBRG"}
    colors.update(color for color in card.get("colors",[]) if color in "WUBRG")
    return colors


def _protection_text_matches(text:str,source:dict)->bool:
    text=text.casefold()
    if "protection from everything" in text:return True
    colors=_card_colors(source);names={"W":"white","U":"blue","B":"black","R":"red","G":"green"}
    if colors and ("protection from all colors" in text or "protection from each color" in text):return True
    if any(re.search(rf"(?:protection from|and from) {names[color]}\b",text) for color in colors):return True
    if not colors and "protection from colorless" in text:return True
    source_types=(source.get("source_type_line") or source.get("type_line","")).casefold()
    for kind in ("artifact","creature","enchantment","instant","land","planeswalker","sorcery"):
        if kind in source_types and re.search(rf"protection from (?:all )?{kind}s?\b",text):return True
    return len(colors)>1 and "protection from multicolored" in text


def _protected_from(card: dict, source: dict) -> bool:
    text = "\n".join([card.get("oracle_text") or "",*card.get("attachment_rules",{}).values()]).casefold()
    return _protection_text_matches(text,source)


def _player_protected_from(state:dict,player:dict,source:dict)->bool:
    for permanent in player["battlefield"]:
        text=(permanent.get("oracle_text") or "").casefold()
        if re.search(r"\b(?:you|you and permanents you control) have protection from\b",text) and _protection_text_matches(text,source):return True
    return False


def _consume_shield(state:dict,card:dict,reason:str)->bool:
    shields=card.get("counters",{}).get("shield",0)
    if shields<=0:return False
    _remove_counters(card,"shield",1)
    _log(state,f"A shield counter protected {card['name']} from {reason}.")
    return True


def _queue_damage_event(state:dict,source:dict,target:dict,amount:int,combat:bool=False)->None:
    if amount<=0:return
    controller=_player(state,source.get("controller_id",source.get("owner_id",state["active_player_id"])))
    source["damage_event_target_id"]=target.get("instance_id") or target.get("id");source["damage_event_target_kind"]="player" if target.get("id") else "permanent";source["damage_event_amount"]=amount;source["damage_event_combat"]=combat
    _queue_triggers(state,"damage",source,controller)
    for key in ("damage_event_target_id","damage_event_target_kind","damage_event_amount","damage_event_combat"):source.pop(key,None)


def _damage_player(state:dict,target:dict,amount:int,source:dict,combat:bool=False)->int:
    if amount<=0:return 0
    if _player_protected_from(state,target,source):_log(state,f"Protection prevented {amount} damage to {target['name']}.");return 0
    if _has_keyword(source,"Infect"):_add_counters(state,target,"poison",amount,source.get("controller_id"),"damage")
    else:target["life"]-=amount
    if _has_keyword(source,"Lifelink"):_gain_life(state,_player(state,source.get("controller_id",source.get("owner_id"))),amount)
    _queue_damage_event(state,source,target,amount,combat)
    return amount


def _damage_permanent(state:dict,target:dict,amount:int,source:dict)->int:
    if amount<=0:return 0
    if _protected_from(target,source):
        _log(state,f"Protection prevented {amount} damage to {target['name']}.");return 0
    if _consume_shield(state,target,"damage"):return 0
    if _has_keyword(source,"Infect") or _has_keyword(source,"Wither"):
        _add_counters(state,target,"-1/-1",amount,source.get("controller_id"),"damage")
    elif "Planeswalker" in target.get("type_line",""):
        _remove_counters(target,"loyalty",amount)
    else:target["damage"]+=amount
    if _has_keyword(source,"Deathtouch"):target["deathtouch_damage"]=True
    if _has_keyword(source,"Lifelink"):_gain_life(state,_player(state,source.get("controller_id",source.get("owner_id"))),amount)
    _queue_damage_event(state,source,target,amount)
    return amount


def _remove_from_combat(state:dict,card_id:str)->None:
    combat=state.get("combat",{});combat["attackers"]=[attacker for attacker in combat.get("attackers",[]) if attacker!=card_id];combat.get("attack_targets",{}).pop(card_id,None);combat.get("block_orders",{}).pop(card_id,None)
    combat["blocks"]={blocker:attacker for blocker,attacker in combat.get("blocks",{}).items() if blocker!=card_id and attacker!=card_id}
    for order in combat.get("block_orders",{}).values():
        if card_id in order:order.remove(card_id)


def _destroy_permanent(state:dict,owner:dict,card:dict,cant_regenerate:bool=False,trigger_sources:list[tuple[dict,dict]]|None=None,trigger_dedupe:set[str]|None=None)->bool:
    if _has_keyword(card,"Indestructible"):return False
    if _consume_shield(state,card,"destruction"):return False
    regenerations=card.get("regeneration_shields",0)
    if regenerations and not cant_regenerate:
        card["regeneration_shields"]=regenerations-1;_set_tapped(state,[card],True,card.get("controller_id"),"regenerate");card["damage"]=0;card.pop("deathtouch_damage",None);_remove_from_combat(state,card["instance_id"]);_log(state,f"{card['name']} regenerated instead of being destroyed.");return False
    _leave_battlefield(state,owner,card,"graveyard",trigger_sources,trigger_dedupe);return True


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


def _optional_blight_cost(card:dict)->int|None:
    match=re.search(r"as an additional cost to cast this spell, you may blight (\d+)",card.get("oracle_text") or "",re.IGNORECASE)
    return int(match.group(1)) if match else None


def _apply_blight(state:dict,player:dict,creature:dict,amount:int)->None:
    original=amount;plus=creature.setdefault("counters",{}).get("+1/+1",0);cancel=min(plus,amount)
    if cancel:_remove_counters(creature,"+1/+1",cancel);amount-=cancel
    if amount:_add_counters(state,creature,"-1/-1",amount,player["id"],"cost")
    _log(state,f"{player['name']} blighted {creature['name']} for {original}.")


def _set_card_face(card:dict,index:int)->bool:
    faces=card.get("card_faces") or []
    if len(faces)<2 or not 0<=index<len(faces):return False
    face=faces[index]
    for key in ("name","oracle_text","mana_cost","type_line","power","toughness","loyalty","image_url","keywords"):
        if key in face:card[key]=face[key]
        elif key in {"power","toughness","loyalty"}:card[key]=None
    card["current_face"]=index;return True


def _transform(state:dict,card:dict)->bool:
    faces=card.get("card_faces") or []
    if len(faces)<2:return False
    previous=card.get("name","This permanent");next_index=1 if int(card.get("current_face",0))==0 else 0
    if not _set_card_face(card,next_index):return False
    _log(state,f"{previous} transformed into {card['name']}.");return True


def _saga_chapters(card:dict)->dict[int,str]:
    chapters={};roman={"I":1,"II":2,"III":3,"IV":4,"V":5}
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^((?:I|V)+(?:\s*,\s*(?:I|V)+)*)\s*[—-]\s*(.+)$",line.strip())
        if not match:continue
        for numeral in re.split(r"\s*,\s*",match.group(1)):
            if numeral in roman:chapters[roman[numeral]]=match.group(2).strip()
    return chapters


def _queue_saga_chapter(state:dict,owner:dict,saga:dict,chapter:int)->None:
    chapters=_saga_chapters(saga);effect=chapters.get(chapter)
    if not effect:return
    ability={"name":f"{saga['name']} — chapter {chapter}","oracle_text":effect,"source_type_line":saga.get("type_line",""),"source_mana_cost":saga.get("mana_cost",""),"type_line":"Ability","mana_cost":""};trigger={"id":_id(),"kind":"trigger","card":ability,"controller_id":owner["id"],"target_id":None,"source_id":saga["instance_id"],"saga_final":chapter==max(chapters)};targets=_targets(state,owner["id"],ability)
    if _target_kind(ability):
        if targets:state.setdefault("pending_trigger_targets",[]).append({"controller_id":owner["id"],"source_name":saga["name"],"trigger":trigger,"card":ability});state["priority_player_id"]=owner["id"]
        else:_log(state,f"{saga['name']}'s chapter {chapter} had no legal target.");_finish_saga_final_chapter(state,trigger)
    else:state["stack"].append(trigger)
    _log(state,f"{saga['name']} reached chapter {chapter}: {effect}")


def _add_saga_lore(state:dict,owner:dict,saga:dict)->None:
    if "Saga" not in saga.get("type_line","") or int(saga.get("current_face",0))!=0:return
    _add_counters(state,saga,"lore",1,owner["id"],"turn_based");_queue_saga_chapter(state,owner,saga,saga["counters"]["lore"])


def _activated_abilities(card: dict) -> list[dict]:
    abilities = []
    text=card.get("oracle_text") or "";quoted=re.findall(r'"([^"]+:[^"]+)"',text);lines=[*(line for line in text.splitlines() if '"' not in line),*quoted]
    for line in lines:
        match = re.match(r"^([^:]+):\s*(.+)$", line.strip())
        if not match: continue
        cost,effect = match.group(1).strip(),match.group(2).strip()
        waterbend_symbol=_waterbend_symbol(cost);regular_cost=re.sub(r"\bwaterbend\s+\{(?:\d+|X)\}","",cost,flags=re.IGNORECASE)
        mana_cost="".join(re.findall(r"\{[^}]+\}",regular_cost,re.IGNORECASE)).upper().replace("{T}","").replace("{Q}","")
        taps="{T}" in cost.upper()
        source_name=re.escape(card.get("name", ""));self_reference=rf"(?:~|this (?:artifact|creature|permanent)|{source_name})"
        self_sacrifice=re.search(rf"\bsacrifice {self_reference}\b",cost,re.IGNORECASE) is not None
        life_match=re.search(r"\bpay (\d+) life\b",cost,re.IGNORECASE);life_cost=int(life_match.group(1)) if life_match else 0
        counter_match=re.search(rf"\bremove (a|one|two|three|four|five|\d+) ([\w+/-]+) counters? from {self_reference}\b",cost,re.IGNORECASE)
        counter_cost=None
        if counter_match:
            word=counter_match.group(1).casefold();amount={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(word,int(word) if word.isdigit() else 1);counter_cost={"name":counter_match.group(2).replace("−","-"),"amount":amount}
        words={"a":1,"an":1,"one":1,"two":2,"three":3,"four":4,"five":5};selection_costs=[]
        discard_match=re.search(r"\bdiscard (a|an|one|two|three|four|five|X|\d+) (?:(nonland|land|creature|artifact|enchantment|planeswalker|instant|sorcery) )?cards?\b",cost,re.IGNORECASE)
        if discard_match:
            word=discard_match.group(1).casefold();selection_costs.append({"kind":"discard","filter":discard_match.group(2).casefold() if discard_match.group(2) else "card","amount":"X" if word=="x" else words.get(word,int(word) if word.isdigit() else 1),"exclude_source":False})
        sacrifice_match=None if self_sacrifice else re.search(r"\bsacrifice (another |a |an |one |two |three |X |two other |three other )?(artifact or creature|creature or artifact|nonland permanent|land|creature|artifact|enchantment|planeswalker|permanent|token)s?\b",cost,re.IGNORECASE)
        if sacrifice_match:
            count_word=(sacrifice_match.group(1) or "a").strip().casefold();selection_costs.append({"kind":"sacrifice","filter":sacrifice_match.group(2).casefold(),"amount":"X" if count_word=="x" else 2 if count_word=="two other" else 3 if count_word=="three other" else words.get(count_word,1),"exclude_source":"other" in count_word or count_word=="another"})
        blight_match=re.search(r"\bblight (\d+)\b",cost,re.IGNORECASE)
        if blight_match:selection_costs.append({"kind":"blight","filter":"creature","amount":1,"blight_amount":int(blight_match.group(1)),"exclude_source":False})
        unsupported=("discard" in cost.casefold() and not selection_costs) or ("sacrifice" in cost.casefold() and not self_sacrifice and not selection_costs) or ("remove" in cost.casefold() and "counter" in cost.casefold() and not counter_cost) or (waterbend_symbol and selection_costs)
        if unsupported or (not taps and not mana_cost and not waterbend_symbol and not self_sacrifice and not life_cost and not counter_cost and not selection_costs):continue
        if re.match(r"add (?:\{|one mana)", effect, re.IGNORECASE): continue
        ability_card = {**card, "name": f"{card['name']} ability", "oracle_text": effect, "source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""), "type_line": "Ability", "mana_cost": ""}
        lower_effect=effect.casefold();restrictions={"sorcery":bool(re.search(r"activate (?:this ability )?only (?:as a sorcery|any time you could cast a sorcery)",lower_effect)),"your_turn":bool(re.search(r"activate (?:this ability )?only during your turn",lower_effect)),"opponent_turn":bool(re.search(r"activate (?:this ability )?only during an opponent's turn",lower_effect)),"combat":bool(re.search(r"activate (?:this ability )?only during combat",lower_effect)),"before_attackers":bool(re.search(r"activate (?:this ability )?only before attackers are declared",lower_effect)),"upkeep":bool(re.search(r"activate (?:this ability )?only during your upkeep",lower_effect)),"end_step":bool(re.search(r"activate (?:this ability )?only during your end step",lower_effect)),"once_each_turn":bool(re.search(r"activate (?:this ability )?(?:only |no more than )?once (?:each|per) turn",lower_effect)),"once":bool(re.search(r"activate (?:this ability )?only once(?:\.|$)",lower_effect))}
        abilities.append({"cost":cost,"mana_cost":mana_cost,"waterbend_symbol":waterbend_symbol,"taps":taps,"self_sacrifice":self_sacrifice,"life_cost":life_cost,"counter_cost":counter_cost,"selection_cost":selection_costs[0] if len(selection_costs)==1 else None,"selection_costs":selection_costs,"restrictions":restrictions,"effect":effect,"card":ability_card})
    return abilities


def _activation_timing_legal(state:dict,player_id:str,permanent:dict,index:int,ability:dict)->bool:
    restrictions=ability.get("restrictions",{});active=state["active_player_id"]==player_id;phase=state["phase"]
    if restrictions.get("sorcery") and not (active and phase in {"precombat_main","postcombat_main"} and not state["stack"]):return False
    if restrictions.get("your_turn") and not active:return False
    if restrictions.get("opponent_turn") and active:return False
    if restrictions.get("combat") and phase!="combat":return False
    if restrictions.get("before_attackers") and (phase!="combat" or state["combat"].get("attackers_declared")):return False
    if restrictions.get("upkeep") and not (active and phase=="beginning" and state.get("beginning_draw_pending")):return False
    if restrictions.get("end_step") and not (active and phase=="ending"):return False
    usage=permanent.get("activated_ability_usage",{}).get(str(index),{})
    if restrictions.get("once_each_turn") and usage.get("turn")==state["turn"]:return False
    if restrictions.get("once") and usage.get("ever"):return False
    return True


def _permanent_abilities(state:dict,card:dict)->list[dict]:
    granted=[ability for rules in card.get("attachment_rules",{}).values() for ability in re.findall(r'"([^"]+:[^"]+)"',rules)]
    return _activated_abilities({**card,"oracle_text":"\n".join([card.get("oracle_text") or "",*granted])})


def _loyalty_abilities(card:dict)->list[dict]:
    abilities=[]
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^([+−-]?\d+):\s*(.+)$",line.strip())
        if not match:continue
        cost=int(match.group(1).replace("−","-"));effect=match.group(2).strip();ability_card={**card,"name":f"{card['name']} loyalty ability","oracle_text":effect,"source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""};abilities.append({"cost":cost,"effect":effect,"card":ability_card})
    return abilities


def _activated_cost_options(player:dict,source:dict,selection_cost:dict|None)->list[dict]:
    if not selection_cost:return []
    kind=selection_cost["filter"].casefold()
    def matches(card:dict)->bool:
        type_line=card.get("type_line","").casefold()
        if kind in {"card","permanent"}:return True
        if kind=="nonland":return "land" not in type_line
        if kind=="nonland permanent":return "land" not in type_line
        if kind=="token":return bool(card.get("token"))
        if " or " in kind:return any(part in type_line for part in kind.split(" or "))
        return kind in type_line
    zone=player["hand"] if selection_cost["kind"]=="discard" else player["battlefield"]
    return [card for card in zone if (not selection_cost.get("exclude_source") or card["instance_id"]!=source["instance_id"]) and matches(card)]


def _selection_cost_combinations(player:dict,source:dict,costs:list[dict],x_value:int=0)->tuple[list[dict],list[list[str]]]:
    requirements=[];groups=[]
    for cost in costs:
        options=_activated_cost_options(player,source,cost);ids=[card["instance_id"] for card in options];amount=x_value if cost["amount"]=="X" else cost["amount"]
        requirements.append({"kind":cost["kind"],"filter":cost["filter"],"amount":amount,"options":ids})
        groups.append(list(combinations(ids,amount)))
    valid=[]
    for selected_groups in product(*groups):
        flattened=[card_id for group in selected_groups for card_id in group]
        if len(flattened)==len(set(flattened)):valid.append(flattened)
        if len(valid)>=500:break
    return requirements,valid


def _can_pay(player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None,x_value:int=0,excluded_ids:set[str]|None=None) -> bool:
    return _mana_payment_plan(player,card,extra_generic,excluded_id,x_value,excluded_ids) is not None


def _pay_mana(state:dict,player: dict, card: dict, extra_generic: int = 0, excluded_id: str | None = None,x_value:int=0,excluded_ids:set[str]|None=None) -> None:
    chosen = _mana_payment_plan(player,card,extra_generic,excluded_id,x_value,excluded_ids)
    if chosen is None:
        raise RuleViolation("Not enough mana")
    for payment in chosen:
        land=payment["source"];option=payment["option"]
        if land.get("firebending_mana"):player["firebending_mana"]=max(0,player.get("firebending_mana",0)-1)
        elif option["self_sacrifice"]:
            player["life"]-=option["life_cost"]
            _sacrifice_permanents(state,player,[land]);_log(state,f"{player['name']} sacrificed {land['name']} for mana.")
        else:
            player["life"]-=option["life_cost"]
            if option["taps"]:_set_tapped(state,[land],True,player["id"],"mana")


def _convoke_residual(player:dict,card:dict,selected_ids:list[str],extra_generic:int=0,x_value:int=0)->dict|None:
    """Return the mana cost left after every selected creature contributes once."""
    if len(selected_ids)!=len(set(selected_ids)):return None
    creatures=[]
    for card_id in selected_ids:
        creature=next((item for item in player["battlefield"] if item["instance_id"]==card_id and "Creature" in item.get("type_line","") and not item.get("tapped")),None)
        if not creature:return None
        creatures.append(creature)
    colored,generic=_mana_requirements(card,extra_generic,x_value)
    states={(tuple(tuple(sorted(choice)) for choice in colored),generic)}
    for creature in creatures:
        colors=_card_colors(creature);next_states=set()
        for remaining,remaining_generic in states:
            if remaining_generic>0:next_states.add((remaining,remaining_generic-1))
            for index,choices in enumerate(remaining):
                if colors&set(choices):next_states.add((remaining[:index]+remaining[index+1:],remaining_generic))
        states=next_states
        if not states:return None
    for remaining,remaining_generic in states:
        mana_cost="".join("{"+"/".join(choices)+"}" for choices in remaining)+(f"{{{remaining_generic}}}" if remaining_generic else "")
        residual={"mana_cost":mana_cost}
        if _can_pay(player,residual,excluded_ids=set(selected_ids)):return residual
    return None


def _convoke_combinations(player:dict,card:dict,extra_generic:int=0,x_value:int=0)->list[list[str]]:
    options=[item["instance_id"] for item in player["battlefield"] if "Creature" in item.get("type_line","") and not item.get("tapped")]
    for amount in range(1,len(options)+1):
        valid=[list(group) for group in combinations(options,amount) if _convoke_residual(player,card,list(group),extra_generic,x_value) is not None]
        if valid:return valid[:128]
    return []


def _waterbend_residual(player:dict,base_card:dict,waterbend_amount:int,selected_ids:list[str],excluded_ids:set[str]|None=None,x_value:int=0)->dict|None:
    if len(selected_ids)!=len(set(selected_ids)) or len(selected_ids)>waterbend_amount:return None
    excluded=set(excluded_ids or ());eligible=[]
    for card_id in selected_ids:
        permanent=next((item for item in player["battlefield"] if item["instance_id"]==card_id and not item.get("tapped") and any(kind in item.get("type_line","") for kind in ("Artifact","Creature"))),None)
        if not permanent or card_id in excluded:return None
        eligible.append(permanent)
    excluded.update(selected_ids);remaining=waterbend_amount-len(eligible);mana_cost=(base_card.get("mana_cost") or "")+(f"{{{remaining}}}" if remaining else "");residual={"mana_cost":mana_cost}
    return residual if _can_pay(player,residual,x_value=x_value,excluded_ids=excluded) else None


def _waterbend_combinations(player:dict,base_card:dict,waterbend_amount:int,excluded_ids:set[str]|None=None,x_value:int=0)->list[list[str]]:
    excluded=set(excluded_ids or ());options=[item["instance_id"] for item in player["battlefield"] if item["instance_id"] not in excluded and not item.get("tapped") and any(kind in item.get("type_line","") for kind in ("Artifact","Creature"))]
    valid=[]
    for amount in range(0,min(waterbend_amount,len(options))+1):
        found_at_amount=0
        for group in combinations(options,amount):
            if _waterbend_residual(player,base_card,waterbend_amount,list(group),excluded,x_value) is not None:valid.append(list(group));found_at_amount+=1
            if found_at_amount>=64:break
    return valid


def _maximum_waterbend_x(player:dict,base_card:dict,excluded_ids:set[str]|None=None)->int:
    value=0
    while value<99 and _waterbend_combinations(player,base_card,value+1,excluded_ids):value+=1
    return value


def _predefined_token(owner:dict,kind:str,tapped:bool=False)->dict:
    oracle={"Clue":"{2}, Sacrifice this artifact: Draw a card.","Food":"{2}, {T}, Sacrifice this artifact: You gain 3 life.","Treasure":"{T}, Sacrifice this artifact: Add one mana of any color.","Blood":"{1}, {T}, Discard a card, Sacrifice this artifact: Draw a card.","Gold":"Sacrifice this artifact: Add one mana of any color."}[kind]
    return {"instance_id":_id(),"scryfall_id":f"token-{kind.casefold()}","name":f"{kind} Token","image_url":None,"type_line":f"Token Artifact — {kind}","oracle_text":oracle,"mana_cost":"","mana_value":0,"power":None,"toughness":None,"owner_id":owner["id"],"controller_id":owner["id"],"tapped":tapped,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":[]}


def _incubator_token(owner:dict)->dict:
    front={"name":"Incubator Token","type_line":"Token Artifact — Incubator","oracle_text":"{2}: Transform this token.","mana_cost":"","power":None,"toughness":None,"image_url":None,"keywords":[]};back={"name":"Phyrexian Token","type_line":"Token Artifact Creature — Phyrexian","oracle_text":"","mana_cost":"","power":"0","toughness":"0","image_url":None,"keywords":[]}
    return {"instance_id":_id(),"scryfall_id":"token-incubator","owner_id":owner["id"],"controller_id":owner["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True,"card_faces":[front,back],"current_face":0,**front}


def _finish_amass(state:dict,player:dict,army:dict,subtype:str,amount:int)->None:
    type_line=army.get("type_line","")
    if not re.search(rf"\b{re.escape(subtype)}\b",type_line,re.IGNORECASE):army["type_line"]=f"{type_line} {subtype}".strip()
    placed=_add_counters(state,army,"+1/+1",amount,player["id"],"amass")
    _log(state,f"{player['name']} amassed {subtype}s {placed} on {army['name']}.")


def _amass(state:dict,player:dict,subtype:str,amount:int,source_name:str)->None:
    armies=[permanent for permanent in player["battlefield"] if "Creature" in permanent.get("type_line","") and re.search(r"\bArmy\b",permanent.get("type_line",""),re.IGNORECASE)]
    if not armies:
        army={"instance_id":_id(),"scryfall_id":f"token-{subtype.casefold()}-army","name":f"{subtype} Army Token","image_url":None,"type_line":f"Token Creature — {subtype} Army","oracle_text":"","mana_cost":"","mana_value":0,"colors":["B"],"power":"0","toughness":"0","owner_id":player["id"],"controller_id":player["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":[]};_enter_battlefield(state,player,[army],"token");_finish_amass(state,player,army,subtype,amount);return
    if len(armies)==1:_finish_amass(state,player,armies[0],subtype,amount);return
    state["pending_amass"]={"player_id":player["id"],"source_name":source_name,"subtype":subtype,"amount":amount,"card_ids":[army["instance_id"] for army in armies]};state["priority_player_id"]=player["id"]


def _bottom_randomized_exiled(state:dict,player:dict,card_ids:list[str])->None:
    cards=[card for card in player["exile"] if card["instance_id"] in set(card_ids)]
    if cards:_leave_exile(state,player,cards)
    random.SystemRandom().shuffle(cards);player["library"][0:0]=cards


def _start_discovery(state:dict,player:dict,value:int,mode:str,source_name:str)->None:
    revealed=[];candidate=None
    while player["library"]:
        card=player["library"].pop();_put_into_exile(state,player,[card],"library",player["id"]);revealed.append(card["instance_id"])
        if "Land" not in card.get("type_line","") and float(card.get("mana_value") or 0)<=(value if mode=="discover" else value-1):candidate=card;break
    if not candidate:
        _bottom_randomized_exiled(state,player,revealed);_log(state,f"{player['name']} found no eligible card while using {source_name}.")
        if mode=="discover":player["discover_event_value"]=value;_queue_triggers(state,"discover",None,player);player.pop("discover_event_value",None)
        return
    state["pending_discovery"]={"player_id":player["id"],"source_name":source_name,"mode":mode,"value":value,"candidate_id":candidate["instance_id"],"revealed_ids":revealed};state["priority_player_id"]=player["id"]
    _log(state,f"{player['name']} {mode}d {candidate['name']} at mana value {value}.")


def _cascade_count(card:dict)->int:
    rules=(card.get("oracle_text") or "").split("(",1)[0];count=len(re.findall(r"\bcascade\b",rules,re.IGNORECASE))
    return count or (1 if _has_keyword(card,"Cascade") else 0)


def _queue_cascade_triggers(state:dict,player:dict,card:dict)->None:
    for _ in range(_cascade_count(card)):
        ability={"name":f"{card['name']} — Cascade","oracle_text":"Cascade","source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""}
        state["stack"].append({"id":_id(),"kind":"cascade","card":ability,"controller_id":player["id"],"target_id":None,"source_id":card["instance_id"],"cascade_value":int(float(card.get("mana_value") or 0))})
        _log(state,f"{card['name']}'s cascade ability triggered.")


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
    return {"id": player_id, "name": name, "is_bot": is_bot, "format": format_name, "life": 40 if is_commander else 20, "poison": 0,"firebending_mana":0,"bent_this_turn":[], "library": library, "hand": [], "battlefield": [], "graveyard": [], "exile": [], "command": command, "commander_casts": 0, "commander_damage": {}, "commander_damage_names": {}, "land_plays_remaining": 1, "kept_hand": False, "mulligans": 0, "lost": False}


def new_game(player_deck: list[dict], opponent_deck: list[dict], play_first: bool = True, opponent_is_bot: bool = True, player_format: str = "", opponent_format: str = "") -> dict:
    human_id, bot_id = "player", "bot"
    players = [_new_player(human_id, "You", player_deck, False, player_format), _new_player(bot_id, "Bot" if opponent_is_bot else "Guest", opponent_deck, opponent_is_bot, opponent_format)]
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "beginning_draw_pending":True,"first_turn_draw_skipped":False,"active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id, "players": players, "stack": [], "combat": {"attackers": [], "attackers_declared":False,"blocks": {}, "attack_targets": {},"block_orders":{},"damage_pending":False,"damage_step":None,"first_strike_damage_ids":[],"block_triggers_pending":False}, "consecutive_passes": 0, "pending_phase_advance": False, "pending_discard": None, "pending_mulligan_bottom": None, "pending_sacrifice": None, "pending_legendary": None,"pending_commander_zone":[],"pending_library_search":None,"pending_scry":None,"pending_damage_order":None,"pending_ward":None,"pending_blight":None,"pending_proliferate":None,"pending_amass":None,"pending_discovery":None,"pending_transform":None,"pending_trigger_targets":[], "log": []}
    for player in players:
        _draw(state, player, 7,False)
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
    if re.search(r"counter target (?:spell or (?:activated or triggered )?ability|spell or ability)",text):return "stack"
    if re.search(r"counter target (?:activated or triggered|activated|triggered) ability",text):return "ability"
    if "counter target spell" in text:return "spell"
    if re.search(r"\bairbend (?:up to one )?target creature or spell\b",text):return "creature_or_spell"
    if re.search(r"\bairbend (?:up to one )?target spell\b",text):return "spell"
    if re.search(r"\bairbend (?:up to one )?target creature\b",text):return "creature"
    if re.search(r"\bearthbend\s+\d+\b",text):return "land"
    if re.search(r"target creature card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_creature"
    if re.search(r"target (?:nonland )?card (?:from|in) (?:your|a|any) graveyard",text):return "graveyard_card"
    if re.search(r"target player mills?", text): return "player"
    if re.search(r"target player sacrifices?",text):return "player"
    if re.search(r"deals (?:\d+|x) damage to target (?:opponent|player)",text):return "player"
    if re.search(r"(?:destroy|exile|gain control of) target (?:artifact, creature, enchantment, planeswalker|nonland permanent|permanent)", text): return "permanent"
    if re.search(r"(?:destroy|exile|tap|untap|return|regenerate|gain control of) target creature", text) or re.search(r"target creature .*(?:gets [+-](?:\d+|x)/[+-](?:\d+|x)|gains? [^.]+ until end of turn|can(?:not|'t) (?:attack|block))", text) or re.search(r"(?:deals (?:\d+|x) damage|put .+ counters?) (?:to|on) target creature", text): return "creature"
    for kind in ("artifact","enchantment","land","planeswalker"):
        if re.search(rf"(?:destroy|exile|tap|untap|return) target {kind}\b",text):return kind
    if re.search(r"return target (?:nonland )?permanent", text): return "permanent"
    if re.search(r"deals (?:\d+|x) damage to any target", text): return "any"
    return None


def _spell_targeting_card(card:dict)->dict:
    if not any(kind in card.get("type_line","") for kind in ("Creature","Artifact","Enchantment","Planeswalker","Battle")):return card
    clauses=re.split(r"(?<=[.!])\s+|\n",card.get("oracle_text") or "");spell_text="\n".join(clause for clause in clauses if not re.match(r"\s*(?:when|whenever|at the beginning)\b",clause,re.IGNORECASE))
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
    own_target_only=bool(re.search(r"(?:target|enchant)[^.\n]*\byou control\b",text));opponent_target_only=bool(re.search(r"(?:target|enchant)[^.\n]*\b(?:an opponent|opponents?) controls?\b",text))
    targets = []
    if kind in {"spell","ability","stack"}:
        def allowed(item:dict)->bool:
            is_spell=item.get("kind","spell")=="spell"
            return kind=="stack" or (kind=="spell" and is_spell) or (kind=="ability" and not is_spell)
        return [{"id":item["id"],"name":item["card"]["name"],"kind":"spell" if item.get("kind","spell")=="spell" else "ability","controller_id":item["controller_id"]} for item in state["stack"] if allowed(item)]
    if kind == "creature_or_spell":
        targets=[{"id":item["id"],"name":item["card"]["name"],"kind":"spell","controller_id":item["controller_id"]} for item in state["stack"]]
    if kind in {"graveyard_creature","graveyard_card"}:
        own_only="your graveyard" in text
        return [{"id":graveyard_card["instance_id"],"name":graveyard_card["name"],"kind":"card","controller_id":owner["id"]} for owner in state["players"] if not own_only or owner["id"]==caster_id for graveyard_card in owner["graveyard"] if kind=="graveyard_card" or "Creature" in graveyard_card.get("type_line","")]
    for player in state["players"]:
        aura_types=_aura_allowed_types(card)
        if (kind in {"any", "player"} or (kind=="permanent" and "player" in aura_types)) and not ("target opponent" in text and player["id"]==caster_id) and not _player_protected_from(state,player,card): targets.append({"id": player["id"], "name": player["name"], "kind": "player", "controller_id": player["id"]})
        for permanent in player["battlefield"]:
            if kind in {"any", "permanent"} or (kind=="creature_or_spell" and "Creature" in permanent.get("type_line","")) or (kind in {"creature","artifact","enchantment","land","planeswalker"} and kind in permanent.get("type_line", "").casefold()):
                aura_types=_aura_allowed_types(card)
                if "Aura" in card.get("type_line","") and aura_types and not any(allowed in permanent.get("type_line","").casefold() for allowed in aura_types if allowed!="player"):continue
                if own_target_only and player["id"] != caster_id: continue
                if opponent_target_only and player["id"] == caster_id: continue
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
    destination="exile" if flashback else "graveyard"
    if flashback:_put_into_exile(state,owner,[card],"stack",controller["id"])
    else:owner["graveyard"].append(card)
    _queue_commander_zone_choice(state,owner,card,destination)


def _finish_saga_final_chapter(state:dict,item:dict)->None:
    if not item.get("saga_final"):return
    saga=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id") and "Saga" in permanent.get("type_line","")),None)
    if not saga:return
    owner=next(owner for owner in state["players"] if saga in owner["battlefield"]);_sacrifice_permanents(state,owner,[saga]);_log(state,f"{saga['name']} was sacrificed after its final chapter.")


def _counter_stack_item(state:dict,item:dict)->None:
    if item.get("kind","spell")=="spell":_countered_spell_destination(state,_player(state,item["controller_id"]),item["card"],item.get("flashback",False))
    _finish_saga_final_chapter(state,item)


def _remove_from_combat(state:dict,card_id:str)->None:
    combat=state["combat"]
    combat["attackers"]=[attacker_id for attacker_id in combat.get("attackers",[]) if attacker_id!=card_id]
    combat["attack_targets"].pop(card_id,None);combat["block_orders"].pop(card_id,None)
    combat["blocks"]={blocker_id:attacker_id for blocker_id,attacker_id in combat.get("blocks",{}).items() if blocker_id!=card_id and attacker_id!=card_id}
    combat["first_strike_damage_ids"]=[permanent_id for permanent_id in combat.get("first_strike_damage_ids",[]) if permanent_id!=card_id]


def _change_control(state:dict,card:dict,new_controller:dict,until_end_of_turn:bool=False)->None:
    current=next((owner for owner in state["players"] if card in owner["battlefield"]),None)
    if not current or current["id"]==new_controller["id"]:return
    if until_end_of_turn and not card.get("temporary_control_return_to"):
        card["temporary_control_return_to"]=current["id"]
    current["battlefield"].remove(card);new_controller["battlefield"].append(card);card["controller_id"]=new_controller["id"];card["summoning_sick"]=True
    _remove_from_combat(state,card["instance_id"])


def _multiplayer(state: dict) -> bool:
    return not any(player.get("is_bot") for player in state["players"])


def _pending_decision(state:dict)->bool:
    return bool(state.get("pending_discard") or state.get("pending_sacrifice") or state.get("pending_legendary") or state.get("pending_commander_zone") or state.get("pending_library_search") or state.get("pending_scry") or state.get("pending_damage_order") or state.get("pending_ward") or state.get("pending_blight") or state.get("pending_proliferate") or state.get("pending_amass") or state.get("pending_discovery") or state.get("pending_transform") or state.get("pending_trigger_targets"))


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
    pending_blight=state.get("pending_blight")
    if pending_blight:
        if pending_blight["player_id"]!=player_id:return []
        creatures=[card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line","")]
        actions=[{"type":"decline_blight","source_name":pending_blight["source_name"],"blight_amount":pending_blight["amount"]},{"type":"concede"}]
        if creatures:actions.insert(0,{"type":"pay_blight","source_name":pending_blight["source_name"],"blight_amount":pending_blight["amount"],"cost_kind":"blight","cost_amount":1,"cost_options":creatures})
        return actions
    pending_proliferate=state.get("pending_proliferate")
    if pending_proliferate:
        if pending_proliferate["player_id"]!=player_id:return []
        targets=[]
        for owner in state["players"]:
            if owner.get("poison",0)>0:targets.append({"id":owner["id"],"name":owner["name"],"kind":"player","controller_id":owner["id"],"counters":{"poison":owner["poison"]}})
            targets.extend({"id":card["instance_id"],"name":card["name"],"kind":"permanent","controller_id":owner["id"],"counters":{name:amount for name,amount in card.get("counters",{}).items() if amount>0}} for card in owner["battlefield"] if any(amount>0 for amount in card.get("counters",{}).values()))
        return [{"type":"choose_proliferate","targets":targets,"source_name":pending_proliferate["source_name"]},{"type":"concede"}]
    pending_amass=state.get("pending_amass")
    if pending_amass:
        if pending_amass["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["battlefield"]};targets=[{"id":card_id,"name":cards_by_id[card_id]["name"],"kind":"permanent","controller_id":player_id} for card_id in pending_amass["card_ids"] if card_id in cards_by_id]
        return [{"type":"choose_amass_army","targets":targets,"source_name":pending_amass["source_name"],"amount":pending_amass["amount"],"subtype":pending_amass["subtype"]},{"type":"concede"}]
    pending_discovery=state.get("pending_discovery")
    if pending_discovery:
        if pending_discovery["player_id"]!=player_id:return []
        candidate=next((card for card in player["exile"] if card["instance_id"]==pending_discovery["candidate_id"]),None)
        if not candidate:return [{"type":"decline_discovery","source_name":pending_discovery["source_name"]},{"type":"concede"}]
        common={"card_id":candidate["instance_id"],"card":candidate,"source_name":pending_discovery["source_name"],"mode":pending_discovery["mode"],"value":pending_discovery["value"]};actions=[]
        targeting_card=_spell_targeting_card(candidate);targets=_targets(state,player_id,targeting_card);modal=bool(_modal_spec(candidate));target_required=bool(_target_kind(targeting_card))
        if not modal and (not target_required or targets):actions.append({"type":"cast_discovered",**common,**({"targets":targets} if targets else {})})
        if pending_discovery["mode"]=="discover":actions.append({"type":"hand_discovered",**common})
        else:actions.append({"type":"decline_discovery",**common})
        actions.append({"type":"concede"});return actions
    pending_transform=state.get("pending_transform")
    if pending_transform:
        if pending_transform["player_id"]!=player_id:return []
        return [{"type":"accept_transform","source_name":pending_transform["source_name"]},{"type":"decline_transform","source_name":pending_transform["source_name"]},{"type":"concede"}]
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
    castable.extend((card,"airbend") for card in player["exile"] if card.get("airbent"))
    for card, source in castable:
        card_action_start=len(actions)
        flashback=_flashback_ability(card) if source=="flashback" else None;cost_card={**card,"mana_cost":"{2}"} if source=="airbend" else {**card,"mana_cost":flashback["mana_cost"]} if flashback else card;kicker_cost=_kicker_cost(card)
        instant_speed = "Instant" in card.get("type_line", "") or _has_keyword(card, "Flash")
        total_tax=_commander_tax(player,card) if source=="command" else 0
        behold_options=[candidate for zone in (player["hand"],player["battlefield"]) for candidate in zone if flashback and flashback["behold_type"] in candidate.get("type_line","").casefold()]
        waterbend_symbol=_spell_waterbend_symbol(card);waterbend_base_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{f'{{{total_tax}}}' if total_tax else ''}"};waterbend_x_max=_maximum_waterbend_x(player,waterbend_base_card) if waterbend_symbol=="X" else None;waterbend_amount=int(waterbend_symbol) if waterbend_symbol and waterbend_symbol.isdigit() else waterbend_x_max or 0
        waterbend_combinations=_waterbend_combinations(player,waterbend_base_card,waterbend_amount) if waterbend_symbol else []
        normal_payable=bool(waterbend_combinations) if waterbend_symbol else _can_pay(player,cost_card,total_tax)
        convoke_combinations=[] if waterbend_symbol or _has_x_cost(cost_card) or (flashback and flashback["behold_amount"]) or not _has_convoke(card) else _convoke_combinations(player,cost_card,total_tax);convoke_min=len(convoke_combinations[0]) if convoke_combinations else None
        if "Land" in card.get("type_line", "") or not ((active and main and not state["stack"]) or instant_speed) or (not normal_payable and convoke_min is None) or (flashback and len(behold_options)<flashback["behold_amount"]): continue
        cost_label=cost_card.get("mana_cost") or "{0}";action = {"type": "cast", "card_id": card["instance_id"], "source": source, "commander_tax": total_tax,"label":f"{'Flashback' if flashback else 'Airbend cast' if source=='airbend' else 'Cast'} {card['name']} · {cost_label}{f' + {{2}}×{player.get("commander_casts",0)} commander tax' if total_tax else ''}"}
        if flashback:action.update({"flashback":True,"cost_kind":"behold" if flashback["behold_amount"] else None,"cost_amount":flashback["behold_amount"],"cost_options":[candidate["instance_id"] for candidate in behold_options]})
        if waterbend_symbol:
            options=[candidate["instance_id"] for candidate in player["battlefield"] if not candidate.get("tapped") and any(kind in candidate.get("type_line","") for kind in ("Artifact","Creature"))]
            action.update({"waterbend":True,"waterbend_amount":waterbend_amount,"cost_kind":"waterbend","cost_min_amount":min(map(len,waterbend_combinations)),"cost_max_amount":max(map(len,waterbend_combinations)),"cost_options":options,"cost_combinations":waterbend_combinations,"label":f"{action['label']} + waterbend {{{waterbend_symbol}}}"})
        if _has_x_cost(cost_card):action.update({"x_min":0,"x_max":_maximum_x(player,cost_card,total_tax)})
        elif waterbend_symbol=="X":
            by_x={value:_waterbend_combinations(player,waterbend_base_card,value) for value in range(0,(waterbend_x_max or 0)+1)};action.update({"x_min":1 if "x can't be 0" in (card.get("oracle_text") or "").casefold() else 0,"x_max":waterbend_x_max,"cost_combinations_by_x":by_x})
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
        if normal_payable:actions.append(action)
        if convoke_min is not None:
            convoke_options=[candidate for candidate in player["battlefield"] if "Creature" in candidate.get("type_line","") and not candidate.get("tapped")]
            colored,generic=_mana_requirements(cost_card,total_tax)
            actions.append({**action,"convoke":True,"cost_kind":"convoke","cost_min_amount":convoke_min,"cost_max_amount":len(colored)+generic,"cost_options":[candidate["instance_id"] for candidate in convoke_options],"cost_combinations":convoke_combinations,"label":f"{action['label']} · Convoke"})
        if kicker_cost:
            kicked_cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{kicker_cost}"}
            kicked_waterbend_base={**kicked_cost_card,"mana_cost":f"{kicked_cost_card.get('mana_cost') or ''}{f'{{{total_tax}}}' if total_tax else ''}"};kicked_waterbend_x_max=_maximum_waterbend_x(player,kicked_waterbend_base) if waterbend_symbol=="X" else None;kicked_waterbend_amount=kicked_waterbend_x_max if waterbend_symbol=="X" else waterbend_amount;kicked_waterbend_combinations=_waterbend_combinations(player,kicked_waterbend_base,kicked_waterbend_amount or 0) if waterbend_symbol else [];kicked_normal_payable=bool(kicked_waterbend_combinations) if waterbend_symbol else _can_pay(player,kicked_cost_card,total_tax);kicked_convoke_combinations=[] if waterbend_symbol or _has_x_cost(kicked_cost_card) or not _has_convoke(card) else _convoke_combinations(player,kicked_cost_card,total_tax);kicked_convoke_min=len(kicked_convoke_combinations[0]) if kicked_convoke_combinations else None
            if kicked_normal_payable or kicked_convoke_min is not None:
                kicked={**action,"kicked":True,"kicker_cost":kicker_cost,"label":f"{action['label']} + kicker {kicker_cost}"}
                if waterbend_symbol:
                    kicked.update({"cost_min_amount":min(map(len,kicked_waterbend_combinations)),"cost_max_amount":max(map(len,kicked_waterbend_combinations)),"cost_combinations":kicked_waterbend_combinations})
                    if waterbend_symbol=="X":kicked.update({"x_max":kicked_waterbend_x_max,"waterbend_amount":kicked_waterbend_x_max,"cost_combinations_by_x":{value:_waterbend_combinations(player,kicked_waterbend_base,value) for value in range(0,(kicked_waterbend_x_max or 0)+1)}})
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
                if kicked_normal_payable:actions.append(kicked)
                if kicked_convoke_min is not None:
                    convoke_options=[candidate for candidate in player["battlefield"] if "Creature" in candidate.get("type_line","") and not candidate.get("tapped")];colored,generic=_mana_requirements(kicked_cost_card,total_tax)
                    actions.append({**kicked,"convoke":True,"cost_kind":"convoke","cost_min_amount":kicked_convoke_min,"cost_max_amount":len(colored)+generic,"cost_options":[candidate["instance_id"] for candidate in convoke_options],"cost_combinations":kicked_convoke_combinations,"label":f"{kicked['label']} · Convoke"})
        blight_amount=_optional_blight_cost(card);blight_options=[creature["instance_id"] for creature in player["battlefield"] if "Creature" in creature.get("type_line","")]
        if blight_amount and blight_options:
            for base_action in list(actions[card_action_start:]):
                if base_action.get("cost_kind"):continue
                blighted={**base_action,"blighted":True,"blight_amount":blight_amount,"cost_kind":"blight","cost_amount":1,"cost_options":blight_options,"label":f"{base_action['label']} · Blight {blight_amount}"}
                if "if this spell's additional cost was paid, choose both instead" in (card.get("oracle_text") or "").casefold() and len(blighted.get("modes",[]))>=2:blighted["mode_min"]=blighted["mode_max"]=blighted["mode_count"]=2
                actions.append(blighted)
    for card in player["hand"]:
        cycling=_cycling_ability(card)
        if cycling and _can_pay(player,{"mana_cost":cycling["mana_cost"]}):
            actions.append({"type":"cycle","card_id":card["instance_id"],"label":f"{cycling['keyword']} · {cycling['mana_cost']}","mana_cost":cycling["mana_cost"]})
    for permanent in player["battlefield"]:
        for index, ability in enumerate(_permanent_abilities(state,permanent)):
            if not _activation_timing_legal(state,player_id,permanent,index,ability):continue
            if ability["taps"] and (permanent.get("tapped") or ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))):continue
            waterbend_symbol=ability.get("waterbend_symbol");excluded={permanent["instance_id"]} if ability["taps"] else set();waterbend_x_max=_maximum_waterbend_x(player,{"mana_cost":ability["mana_cost"]},excluded) if waterbend_symbol=="X" else None;waterbend_amount=int(waterbend_symbol) if waterbend_symbol and waterbend_symbol.isdigit() else waterbend_x_max or 0;waterbend_combinations=_waterbend_combinations(player,{"mana_cost":ability["mana_cost"]},waterbend_amount,excluded) if waterbend_symbol else []
            if (waterbend_symbol and not waterbend_combinations) or (not waterbend_symbol and ability["mana_cost"] and not _can_pay(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None)):continue
            if ability["life_cost"] and player["life"]<ability["life_cost"]:continue
            if ability["counter_cost"] and permanent.get("counters",{}).get(ability["counter_cost"]["name"],0)<ability["counter_cost"]["amount"]:continue
            selection_costs=ability.get("selection_costs",[]);selection_has_x=any(cost["amount"]=="X" for cost in selection_costs);cost_requirements,cost_combinations=_selection_cost_combinations(player,permanent,selection_costs)
            if selection_costs and not selection_has_x and not cost_combinations:continue
            cost_options=list(dict.fromkeys(card_id for requirement in cost_requirements for card_id in requirement["options"]))
            fight_steps=_fight_target_steps(state,player_id,ability["card"],permanent);targets=[] if fight_steps else _targets(state, player_id, ability["card"])
            if (fight_steps and any(not step["targets"] for step in fight_steps)) or (not fight_steps and _target_kind(ability["card"]) and not targets): continue
            fixed_cost_amount=sum(cost["amount"] for cost in selection_costs if cost["amount"]!="X");action = {"type": "activate", "card_id": permanent["instance_id"], "ability_index": index, "label": f"{ability['cost']}: {ability['effect']}","life_cost":ability["life_cost"],"self_sacrifice":ability["self_sacrifice"],"counter_cost":ability["counter_cost"],"cost_kind":selection_costs[0]["kind"] if len(selection_costs)==1 else "compound" if selection_costs else None,"cost_amount":fixed_cost_amount,"cost_options":cost_options,"cost_requirements":cost_requirements,"cost_combinations":cost_combinations,"selection_x":selection_has_x}
            if len(selection_costs)==1 and selection_costs[0]["kind"]=="blight":action["blight_amount"]=selection_costs[0]["blight_amount"]
            if waterbend_symbol:
                options=[candidate["instance_id"] for candidate in player["battlefield"] if candidate["instance_id"] not in excluded and not candidate.get("tapped") and any(kind in candidate.get("type_line","") for kind in ("Artifact","Creature"))];action.update({"waterbend":True,"waterbend_amount":waterbend_amount,"cost_kind":"waterbend","cost_min_amount":min(map(len,waterbend_combinations)),"cost_max_amount":max(map(len,waterbend_combinations)),"cost_options":options,"cost_combinations":waterbend_combinations})
            if selection_has_x:
                option_cap=min(len(_activated_cost_options(player,permanent,cost)) for cost in selection_costs if cost["amount"]=="X");mana_cap=_maximum_x(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None) if _has_x_cost({"mana_cost":ability["mana_cost"]}) else 99;candidate_cap=min(option_cap,mana_cap);by_x={};requirements_by_x={}
                for value in range(candidate_cap+1):
                    requirements,groups=_selection_cost_combinations(player,permanent,selection_costs,value)
                    if groups:by_x[value]=groups;requirements_by_x[value]=requirements
                x_min=1 if "x can't be 0" in ability["effect"].casefold() else 0;x_max=max(by_x,default=0)
                if x_max<x_min:continue
                action.update({"x_min":x_min,"x_max":x_max,"cost_combinations_by_x":by_x,"cost_requirements_by_x":requirements_by_x,"cost_amount_fixed":fixed_cost_amount,"cost_x_components":sum(cost["amount"]=="X" for cost in selection_costs)})
            elif _has_x_cost({"mana_cost":ability["mana_cost"]}):action.update({"x_min":0,"x_max":_maximum_x(player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None)})
            elif waterbend_symbol=="X":
                by_x={value:_waterbend_combinations(player,{"mana_cost":ability["mana_cost"]},value,excluded) for value in range(0,(waterbend_x_max or 0)+1)};action.update({"x_min":1 if "x can't be 0" in ability["effect"].casefold() else 0,"x_max":waterbend_x_max,"cost_combinations_by_x":by_x})
            if fight_steps:action["target_steps"]=fight_steps
            elif targets: action["targets"] = targets
            actions.append(action)
    for vehicle in player["battlefield"]:
        required_power=_crew_value(vehicle)
        if required_power is None:continue
        crew_options=[creature for creature in player["battlefield"] if creature["instance_id"]!=vehicle["instance_id"] and "Creature" in creature.get("type_line","") and not creature.get("tapped")]
        if sum(max(0,_parse_stats(creature,state)[0]) for creature in crew_options)>=required_power:
            actions.append({"type":"crew","card_id":vehicle["instance_id"],"label":f"Crew {required_power} · {vehicle['name']}","cost_kind":"crew","cost_required_power":required_power,"cost_options":[creature["instance_id"] for creature in crew_options]})
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
        participants=[card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"] in state["combat"]["attackers"] or card["instance_id"] in state["combat"]["blocks"]]
        first_step=state["combat"].get("damage_step") is None and any(_has_keyword(card,"First strike") or _has_keyword(card,"Double strike") for card in participants)
        actions.append({"type":"resolve_combat_damage","damage_step":"first_strike" if first_step else "regular"})
    else:
        actions.append({"type": "advance_phase"})
    if active and state["phase"] == "combat" and not state["combat"].get("attackers_declared"):
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
    if item.get("kind")=="cascade":
        _start_discovery(state,caster,int(item.get("cascade_value") or 0),"cascade",card["name"]);return
    if item.get("kind")=="equip_ability":
        equipment=next((permanent for permanent in caster["battlefield"] if permanent["instance_id"]==item.get("source_id") and "Equipment" in permanent.get("type_line","")),None);target=next((permanent for permanent in caster["battlefield"] if permanent["instance_id"]==item.get("target_id") and "Creature" in permanent.get("type_line","")),None)
        if not equipment or not target or _has_keyword(target,"Shroud") or _protected_from(target,equipment):_log(state,f"{card['name']} did not resolve because its source or target was no longer legal.");return
        _attach(state,equipment,target);_log(state,f"{caster['name']} equipped {target['name']} with {equipment['name']}.");return
    if item.get("kind")=="crew_ability":
        vehicle=next((permanent for permanent in caster["battlefield"] if permanent["instance_id"]==item.get("source_id") and _crew_value(permanent) is not None),None)
        if not vehicle:_log(state,f"{card['name']} did not resolve because its Vehicle left the battlefield.");return
        if "Creature" not in vehicle.get("type_line",""):
            vehicle["base_type_line"]=vehicle.get("type_line","");vehicle["type_line"]=vehicle["type_line"].replace("Artifact — Vehicle","Artifact Creature — Vehicle").replace("Artifact —","Artifact Creature —")
        vehicle["crewed_turn"]=state["turn"];_log(state,f"{vehicle['name']} became an artifact creature until end of turn.");return
    if item.get("kind","spell")=="spell" and len(item.get("mode_indices") or [])>1:
        options={option["index"]:option for option in _modal_options(card)};targets=item.get("mode_targets") or []
        for position,index in enumerate(item["mode_indices"]):
            option=options[index];mode_card=_x_rules_card({**card,"name":f"{card['name']} — mode {position+1}","oracle_text":option["label"]},item.get("x_value"));state["stack"].append({"id":_id(),"kind":"modal_effect","card":mode_card,"controller_id":caster["id"],"target_id":targets[position] if position<len(targets) else None,"x_value":item.get("x_value")});_resolve_spell(state)
        (_put_into_exile(state,caster,[card],"stack",caster["id"]) if item.get("flashback") else caster["graveyard"].append(card));_log(state,f"{card['name']} resolved with {len(item['mode_indices'])} modes.");return
    rules_card=_selected_mode_card(card,item.get("mode_indices")) if item.get("kind","spell")=="spell" else card
    if item.get("kind","spell")=="spell":rules_card=_kicked_rules_card(rules_card,bool(item.get("kicked")))
    rules_card=_x_rules_card(rules_card,item.get("x_value"));targeting_card=_spell_targeting_card(rules_card) if item.get("kind","spell")=="spell" else rules_card;target_kind=_target_kind(targeting_card);target_id=item.get("target_id")
    source_permanent=next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"]==item.get("source_id")),None);target_ids=item.get("target_ids") or [];fight_steps=_fight_target_steps(state,caster["id"],rules_card,source_permanent);valid_fight_ids=[target_value for position,target_value in enumerate(target_ids) if position<len(fight_steps) and target_value in {target["id"] for target in fight_steps[position]["targets"]}]
    if target_ids and not valid_fight_ids:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _finish_saga_final_chapter(state,item)
        _log(state,f"{card['name']} was countered because all of its fight targets were no longer legal.");return
    if target_kind and target_id not in {target["id"] for target in _targets(state,caster["id"],targeting_card)}:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _finish_saga_final_chapter(state,item)
        _log(state,f"{card['name']} was countered because its target was no longer legal.");return
    text = (rules_card.get("oracle_text") or "").casefold()
    is_permanent_spell = item.get("kind", "spell") == "spell" and any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"))
    effect_text = "" if is_permanent_spell and re.search(r"\b(?:when|whenever|at the beginning)\b", text) else text
    other = opponent(state, caster["id"])
    target_player = next((player for player in state["players"] if player["id"] == target_id), None)
    target_owner = next((player for player in state["players"] if any(permanent["instance_id"] == target_id for permanent in player["battlefield"])), None)
    target = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == target_id), None)
    event_permanent=next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"]==item.get("event_card_id")),None);event_controller=_player(state,event_permanent.get("controller_id")) if event_permanent else None
    target_stack_item = next((entry for entry in state["stack"] if entry["id"] == target_id), None)
    graveyard_owner=next((player for player in state["players"] if any(graveyard_card["instance_id"]==target_id for graveyard_card in player["graveyard"])),None)
    graveyard_target=next((graveyard_card for player in state["players"] for graveyard_card in player["graveyard"] if graveyard_card["instance_id"]==target_id),None)
    control_change=bool(target and target_owner and re.search(r"\bgain control of target (?:creature|permanent|artifact|enchantment|land|planeswalker)\b",effect_text))
    if control_change:
        temporary="until end of turn" in effect_text;previous_controller=target_owner
        _change_control(state,target,caster,temporary)
        target_owner=caster
        if re.search(r"\buntap (?:it|that creature|target creature)\b",effect_text):_set_tapped(state,[target],False,caster["id"],"effect")
        if re.search(r"\b(?:it|that creature|target creature) gains? haste\b",effect_text):target["temporary_keywords"]=sorted(set(target.get("temporary_keywords",[]))|{"haste"})
        duration=" until end of turn" if temporary else ""
        _log(state,f"{caster['name']} gained control of {target['name']} from {previous_controller['name']}{duration}.")
    if event_permanent and re.search(r"\buntap (?:it|that (?:creature|permanent|artifact|land))\b",effect_text):_set_tapped(state,[event_permanent],False,caster["id"],"trigger")
    event_life_loss=re.search(r"(?:its|that (?:creature|permanent|artifact|land)'?s) controller loses (\d+) life",effect_text)
    if event_controller and event_life_loss:event_controller["life"]-=int(event_life_loss.group(1))
    event_mill=re.search(r"(?:its|that (?:creature|permanent|artifact|land)'?s) controller mills? (a|one|two|three|four|\d+) cards?",effect_text)
    if event_controller and event_mill:
        words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(event_mill.group(1),int(event_mill.group(1)) if event_mill.group(1).isdigit() else 1)
        for _ in range(min(amount,len(event_controller["library"]))):event_controller["graveyard"].append(event_controller["library"].pop())
    optional_blight=re.search(r"\byou may blight (\d+)\b",effect_text)
    if optional_blight:
        original_text=(source_permanent or card).get("oracle_text") or "";continuation_match=re.search(r"when you do,\s*(.+?)(?:\n|$)",original_text,re.IGNORECASE)
        state["pending_blight"]={"player_id":caster["id"],"amount":int(optional_blight.group(1)),"source_name":source_permanent.get("name",card["name"]) if source_permanent else card["name"],"source_id":source_permanent.get("instance_id") if source_permanent else item.get("source_id"),"continuation":continuation_match.group(1).strip() if continuation_match else ""};state["priority_player_id"]=caster["id"]
        _log(state,f"{caster['name']} may blight {optional_blight.group(1)} for {state['pending_blight']['source_name']}.");return
    optional_transform=re.search(r"\byou may transform\b",effect_text)
    if optional_transform and source_permanent and source_permanent.get("card_faces"):
        continuation_match=re.search(r"if you do,\s*(.+)",effect_text,re.IGNORECASE)
        state["pending_transform"]={"player_id":caster["id"],"source_id":source_permanent["instance_id"],"source_name":source_permanent["name"],"continuation":continuation_match.group(1).strip() if continuation_match else ""};state["priority_player_id"]=caster["id"]
        _log(state,f"{caster['name']} may transform {source_permanent['name']}.");return
    transform_instruction=source_permanent and re.search(r"\btransform (?:this (?:creature|permanent)|it|[a-z][^.]+)\b",effect_text)
    all_bending_required="if you've done all four this turn" in effect_text
    if transform_instruction and (not all_bending_required or set(caster.get("bent_this_turn",[]))>={"waterbend","earthbend","firebend","airbend"}):_transform(state,source_permanent)
    if re.search(r"\bproliferate\b",effect_text):
        state["pending_proliferate"]={"player_id":caster["id"],"source_name":source_permanent.get("name",card["name"]) if source_permanent else card["name"]};state["priority_player_id"]=caster["id"]
        _log(state,f"{caster['name']} will choose permanents and players to proliferate.")
    keyword_text=re.sub(r"\([^()]*(?:to investigate|to amass|to incubate)[^()]*\)","",effect_text)
    investigate_match=re.search(r"\binvestigates?(?: (twice|three times|\d+ times))?\b",keyword_text)
    if investigate_match:
        count={"twice":2,"three times":3}.get(investigate_match.group(1),int(investigate_match.group(1).split()[0]) if investigate_match.group(1) and investigate_match.group(1)[0].isdigit() else 1)
        recipients=state["players"] if "each player investigates" in keyword_text else [target_owner or event_controller] if ("its controller investigates" in keyword_text and (target_owner or event_controller)) else [caster]
        for recipient in recipients:
            clues=[_predefined_token(recipient,"Clue") for _ in range(count)];_enter_battlefield(state,recipient,clues,"token");_log(state,f"{recipient['name']} investigated {count} time(s).")
    amass_match=re.search(r"\bamass(?:es)? (zombies|orcs|goblins|oozes|rats) (\d+)\b",keyword_text)
    if amass_match:_amass(state,caster,amass_match.group(1).title().rstrip("s"),int(amass_match.group(2)),source_permanent.get("name",card["name"]) if source_permanent else card["name"])
    incubate_match=re.search(r"\bincubates? (\d+)\b",keyword_text)
    if incubate_match:
        amount=int(incubate_match.group(1));token=_incubator_token(caster);_enter_battlefield(state,caster,[token],"token");_add_counters(state,token,"+1/+1",amount,caster["id"],"incubate");_log(state,f"{caster['name']} incubated {amount}.")
    discover_match=re.search(r"\bdiscover (\d+)\b",keyword_text)
    if discover_match:_start_discovery(state,caster,int(discover_match.group(1)),"discover",source_permanent.get("name",card["name"]) if source_permanent else card["name"])
    if re.search(r"\bairbend (?:up to one )?target (?:creature|spell|creature or spell)\b",effect_text):
        airbent=None;airbend_owner=None
        if target and target_owner:
            airbent=target;airbend_owner=_player(state,target.get("owner_id",target_owner["id"]));target["airbent"]=True;_leave_battlefield(state,target_owner,target,"exile",exile_actor_id=caster["id"])
        elif target_stack_item:
            state["stack"].remove(target_stack_item);airbent=target_stack_item["card"];airbend_owner=_player(state,airbent.get("owner_id",target_stack_item["controller_id"]))
            if not airbent.get("token"):
                airbent["controller_id"]=airbend_owner["id"];airbent["airbent"]=True;_put_into_exile(state,airbend_owner,[airbent],"stack",caster["id"])
        if airbent:
            _log(state,f"{caster['name']} airbent {airbent['name']}.");_queue_triggers(state,"airbend",source_permanent or card,caster)
    if (card.get("firebending_trigger") or "lasts until end of combat" in effect_text or "don't lose this mana as steps end" in effect_text) and re.search(r"\badd\b",effect_text):
        fire_mana=len(re.findall(r"\{r\}",effect_text));number=re.search(r"add (\d+) \{r\}",effect_text)
        if number:fire_mana=max(fire_mana,int(number.group(1)))
        if fire_mana:
            caster["firebending_mana"]=caster.get("firebending_mana",0)+fire_mana;_log(state,f"{caster['name']} added {fire_mana} firebending mana for this combat.");_queue_triggers(state,"firebend",source_permanent or card,caster)
    earthbend=_earthbend_value(rules_card)
    if earthbend is not None and target and target_owner and "Land" in target.get("type_line","") and target["controller_id"]==caster["id"]:
        if not target.get("earthbent"):
            target["earthbend_base_type_line"]=target.get("type_line","");target["earthbend_base_power"]=target.get("power");target["earthbend_base_toughness"]=target.get("toughness")
        if "Creature" not in target.get("type_line",""):
            parts=target["type_line"].split(" — ",1);target["type_line"]=f"{parts[0]} Creature"+(f" — {parts[1]}" if len(parts)>1 else "")
        target["power"]="0";target["toughness"]="0";target["earthbent"]=True;target["earthbend_controller"]=caster["id"];_add_counters(state,target,"+1/+1",earthbend,caster["id"],"effect")
        _log(state,f"{caster['name']} earthbent {target['name']} for {earthbend}.");_queue_triggers(state,"earthbend",target,caster)
    each_draw_match=re.search(r"each player draws? (?:a|one|two|three|four|\d+) cards?",effect_text)
    draw_match = re.search(r"(?<!each player )draw (?:a|one|two|three|four|\d+) cards?", effect_text);ordered_scry_draw=bool(draw_match and re.search(r"(?:scry|surveil) [^,.]+, then draw",effect_text))
    if each_draw_match:
        word=each_draw_match.group(0).split()[-2];amount={"a":1,"one":1,"two":2,"three":3,"four":4}.get(word,int(word) if word.isdigit() else 0)
        for drawing_player in state["players"]:_draw(state,drawing_player,amount)
    elif draw_match and not ordered_scry_draw:
        word = draw_match.group(0).split()[1]
        _draw(state, caster, {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}.get(word,int(word) if word.isdigit() else 0))
    life_match = re.search(r"you gain (\d+) life", effect_text)
    if life_match:
        _gain_life(state,caster,int(life_match.group(1)))
    damage_match = re.search(r"deals (\d+) damage to (?:target opponent|each opponent)", effect_text)
    if damage_match:
        amount=int(damage_match.group(1))
        _damage_player(state,other,amount,source_permanent or card)
    lose_life = re.search(r"(?:target opponent|each opponent) loses (\d+) life", effect_text)
    if lose_life: other["life"] -= int(lose_life.group(1))
    you_lose = re.search(r"you lose (\d+) life", effect_text)
    if you_lose: caster["life"] -= int(you_lose.group(1))
    targeted_damage = re.search(r"deals (\d+) damage to (?:any target|target creature|target opponent|target player)", effect_text)
    if targeted_damage and (target_player or target):
        amount = int(targeted_damage.group(1))
        if target_player:
            _damage_player(state,target_player,amount,source_permanent or card)
        elif target:_damage_permanent(state,target,amount,source_permanent or card)
    if fight_steps and len(valid_fight_ids)==len(fight_steps):
        fighters=([source_permanent,next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==valid_fight_ids[0]),None)] if len(fight_steps)==1 else [next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==fighter_id),None) for fighter_id in valid_fight_ids[:2]])
        if all(fighters) and fighters[0] is not fighters[1]:
            first,second=fighters;first_power=max(0,_parse_stats(first,state)[0]);second_power=max(0,_parse_stats(second,state)[0]);_damage_permanent(state,second,first_power,first);_damage_permanent(state,first,second_power,second);first["fought_turn"]=state["turn"];second["fought_turn"]=state["turn"];_log(state,f"{first['name']} fought {second['name']}.")
    if target and target_owner and re.search(r"destroy target (?:creature|permanent|nonland permanent)", effect_text):
        if _destroy_permanent(state,target_owner,target,"can't be regenerated" in effect_text):_log(state, f"{target['name']} was destroyed.")
    if target and target_owner and re.search(r"exile target (?:creature|permanent|nonland permanent)", effect_text):
        _leave_battlefield(state,target_owner,target,"exile",exile_actor_id=caster["id"]);_log(state,f"{target['name']} was exiled.")
    if target and target_owner and re.search(r"return target (?:creature|permanent|nonland permanent).* to (?:its|their) owner'?s hand", effect_text):
        _leave_battlefield(state, target_owner, target, "hand"); _log(state, f"{target['name']} returned to its owner's hand.")
    if target and re.search(r"\btap target creature",effect_text):_set_tapped(state,[target],True,caster["id"],"effect")
    if target and re.search(r"\buntap target creature",effect_text):_set_tapped(state,[target],False,caster["id"],"effect")
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
    counter_match=re.search(r"put (a|one|two|three|four|five|six|seven|eight|nine|ten|\d+) ([+−-]\d+/[+−-]\d+|[a-z][a-z-]*) counters? on target (?:creature|permanent|artifact|planeswalker)",effect_text)
    if target and counter_match:
        words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};amount=words.get(counter_match.group(1),int(counter_match.group(1)) if counter_match.group(1).isdigit() else 1);name=counter_match.group(2).replace("−","-")
        placed=_add_counters(state,target,name,amount,caster["id"],"effect");_log(state,f"{target['name']} received {placed} {name} counter(s).")
    source_counter_name=re.escape((source_permanent or {}).get("name","").casefold());self_counter=re.search(rf"put (a|one|two|three|four|five|\d+) ([+−-]\d+/[+−-]\d+|[a-z][a-z-]*) counters? on (?:him|her|them|it|this (?:creature|permanent|artifact|enchantment|planeswalker)|{source_counter_name})",effect_text)
    if source_permanent and self_counter:
        words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5};amount=words.get(self_counter.group(1),int(self_counter.group(1)) if self_counter.group(1).isdigit() else 1);name=self_counter.group(2).replace("−","-");_add_counters(state,source_permanent,name,amount,caster["id"],"effect")
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
        if ids:
            state["pending_scry"]={"player_id":caster["id"],"amount":amount,"card_ids":ids,"mode":mode,"draw_after":1 if ordered_scry_draw else 0};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} is {mode}ing {amount}.")
        elif ordered_scry_draw:_draw(state,caster,1)
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
    if target_stack_item and target_kind in {"spell","ability","stack"} and "counter target" in effect_text:
        state["stack"].remove(target_stack_item);countered=target_stack_item["card"];_counter_stack_item(state,target_stack_item)
        _log(state, f"{countered['name']} was countered.")
    if graveyard_target and graveyard_owner:
        if re.search(r"(?:return|put) target (?:creature )?card .*graveyard (?:to|into|onto) (?:the battlefield|play)",effect_text):
            _leave_graveyard(state,graveyard_owner,[graveyard_target]);graveyard_target["controller_id"]=caster["id"];graveyard_target["summoning_sick"]=True;_enter_battlefield(state,caster,[graveyard_target],"graveyard");_log(state,f"{graveyard_target['name']} returned to the battlefield under {caster['name']}'s control.")
        elif re.search(r"return target (?:creature )?card .*graveyard to (?:your|its owner'?s) hand",effect_text):
            _leave_graveyard(state,graveyard_owner,[graveyard_target]);graveyard_target["controller_id"]=graveyard_target.get("owner_id",graveyard_owner["id"]);_player(state,graveyard_target["controller_id"])["hand"].append(graveyard_target);_log(state,f"{graveyard_target['name']} returned to its owner's hand.")
        elif re.search(r"exile target (?:creature )?card .*graveyard",effect_text):
            _leave_graveyard(state,graveyard_owner,[graveyard_target]);_put_into_exile(state,graveyard_owner,[graveyard_target],"graveyard",caster["id"]);_log(state,f"{graveyard_target['name']} was exiled from a graveyard.")
    sacrifice_match=re.search(r"(?:target player|each opponent) sacrifices? (a|one|two|three|four|\d+) (creature|permanent)s?",effect_text)
    if sacrifice_match:
        words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(sacrifice_match.group(1),int(sacrifice_match.group(1)) if sacrifice_match.group(1).isdigit() else 1);affected=target_player if "target player" in sacrifice_match.group(0) and target_player else other;kind=sacrifice_match.group(2)
        choices=[permanent["instance_id"] for permanent in affected["battlefield"] if kind=="permanent" or "Creature" in permanent.get("type_line","")];required=min(amount,len(choices))
        if required:state["pending_sacrifice"]={"player_id":affected["id"],"amount":required,"card_ids":choices};state["priority_player_id"]=affected["id"];_log(state,f"{affected['name']} must sacrifice {required} {kind}(s).")
    destroy_all = re.search(r"destroy all (creatures|artifacts|enchantments|nonland permanents)", effect_text)
    exile_all = re.search(r"exile all (creatures|artifacts|enchantments|nonland permanents)", effect_text)
    for match,destination in ((destroy_all,"graveyard"),(exile_all,"exile")):
        if not match: continue
        kind=match.group(1);trigger_sources=[(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]];trigger_dedupe=set();affected=[(owner,permanent) for owner in state["players"] for permanent in list(owner["battlefield"]) if (kind=="nonland permanents" and "land" not in permanent.get("type_line","").casefold()) or kind[:-1] in permanent.get("type_line","").casefold()]
        for owner,permanent in affected:
            if destination=="graveyard":_destroy_permanent(state,owner,permanent,"can't be regenerated" in effect_text,trigger_sources,trigger_dedupe)
            else:_leave_battlefield(state,owner,permanent,destination,trigger_sources,trigger_dedupe,caster["id"],len(affected))
        _log(state,f"All {kind} were {'destroyed' if destination=='graveyard' else 'exiled'}.")
    global_stats=re.search(r"(?:all|each) creatures?(?: you control| your opponents control)? get ([+-]\d+)/([+-]\d+) until end of turn",effect_text)
    if global_stats:
        own_only="you control" in global_stats.group(0);opponents_only="opponents control" in global_stats.group(0)
        for owner in state["players"]:
            if own_only and owner["id"]!=caster["id"] or opponents_only and owner["id"]==caster["id"]:continue
            for permanent in owner["battlefield"]:
                if "Creature" in permanent.get("type_line",""):permanent["temporary_power"]=permanent.get("temporary_power",0)+int(global_stats.group(1));permanent["temporary_toughness"]=permanent.get("temporary_toughness",0)+int(global_stats.group(2))
    token_match = re.search(r"create (a|one|two|three|four|five|\d+) (tapped )?(\d+)/(\d+) ([^.]*?) creature tokens?", effect_text)
    if token_match:
        amount = {"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(token_match.group(1),int(token_match.group(1)) if token_match.group(1).isdigit() else 0)
        attacking="tapped and attacking" in effect_text;tapped=bool(token_match.group(2)) or attacking or "tokens enter tapped" in effect_text;created=[]
        descriptor=token_match.group(5).strip();color_names={"white":"W","blue":"U","black":"B","red":"R","green":"G"};colors=[symbol for name,symbol in color_names.items() if re.search(rf"\b{name}\b",descriptor)]
        subtype=re.sub(r"\b(?:white|blue|black|red|green|colorless|and)\b"," ",descriptor).strip();subtype=re.sub(r"\s+"," ",subtype) or "Creature"
        keywords=[keyword.title() for keyword in ("flying","first strike","double strike","deathtouch","haste","lifelink","menace","reach","trample","vigilance") if re.search(rf"\b{keyword}\b",effect_text)]
        for _ in range(amount):
            token={"instance_id":_id(),"scryfall_id":"token","name":f"{subtype.title()} Token","image_url":None,"type_line":f"Token Creature — {subtype.title()}","oracle_text":"","mana_cost":"","mana_value":0,"colors":colors,"power":token_match.group(3),"toughness":token_match.group(4),"owner_id":caster["id"],"controller_id":caster["id"],"tapped":tapped,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":keywords};created.append(token)
        if attacking and state.get("phase")=="combat":
            source_target=state["combat"].get("attack_targets",{}).get(item.get("source_id"),other["id"])
            for token in created:state["combat"]["attackers"].append(token["instance_id"]);state["combat"]["attack_targets"][token["instance_id"]]=source_target
        _enter_battlefield(state,caster,created,"token")
        _log(state, f"{caster['name']} created {amount} token(s){' tapped and attacking' if attacking else ''}.")
    predefined_matches=list(re.finditer(r"create (a|one|two|three|four|five|\d+) (tapped )?(clue|food|treasure|blood|gold) tokens?",effect_text,re.IGNORECASE))
    for predefined in predefined_matches:
        word=predefined.group(1).casefold();amount={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(word,int(word) if word.isdigit() else 1);kind=predefined.group(3).title()
        created=[]
        for _ in range(amount):created.append(_predefined_token(caster,kind,bool(predefined.group(2))))
        _enter_battlefield(state,caster,created,"token")
        _log(state,f"{caster['name']} created {amount} {kind} token(s).")
    saga_transformed=False
    if source_permanent and "exile this saga, then return it to the battlefield transformed under your control" in effect_text:
        saga_owner=next((owner for owner in state["players"] if source_permanent in owner["battlefield"]),None)
        if saga_owner:
            _leave_battlefield(state,saga_owner,source_permanent,"exile",exile_actor_id=caster["id"]);zone_owner=_player(state,source_permanent.get("owner_id",saga_owner["id"]))
            _leave_exile(state,zone_owner,[source_permanent])
            source_permanent["controller_id"]=caster["id"];source_permanent["counters"]={};source_permanent["summoning_sick"]=True;source_permanent["tapped"]=False;_set_card_face(source_permanent,1);_enter_battlefield(state,caster,[source_permanent],"exile");saga_transformed=True;_log(state,f"{source_permanent['name']} returned transformed under {caster['name']}'s control.")
    entered = False
    if is_permanent_spell:
        card["was_kicked"]=bool(item.get("kicked"))
        card["summoning_sick"] = True
        if re.search(r"\benters (?:the battlefield )?tapped\b",text):card["tapped"]=True
        enters_counters=re.search(r"enters(?: the battlefield)? with (\d+) ([+−-]\d+/[+−-]\d+|loyalty|charge|shield|stun) counters?",text)
        _enter_battlefield(state,caster,[card],item.get("cast_source_zone","stack"),True)
        if enters_counters:_add_counters(state,card,enters_counters.group(2).replace("−","-"),int(enters_counters.group(1)),caster["id"],"enters")
        if "Aura" in card.get("type_line","") and (target or target_player):_attach(state,card,target or target_player)
        entered = True
    elif item.get("kind", "spell") == "spell":
        if item.get("flashback"):_put_into_exile(state,caster,[card],"stack",caster["id"])
        else:caster["graveyard"].append(card)
    _log(state, f"{card['name']} resolved.")
    if entered and "Saga" in card.get("type_line",""):_add_saga_lore(state,caster,card)
    if not saga_transformed:_finish_saga_final_chapter(state,item)


def _leave_battlefield(state: dict, owner: dict, card: dict, destination: str, trigger_sources:list[tuple[dict,dict]]|None=None, trigger_dedupe:set[str]|None=None, exile_actor_id:str|None=None, exile_batch_size:int|None=None) -> None:
    exile_sources=trigger_sources or ([(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]] if destination=="exile" else None)
    if card.get("attached_to"):_detach(state,card)
    attachments=[(attachment_owner,attachment) for attachment_owner in state["players"] for attachment in list(attachment_owner["battlefield"]) if attachment.get("attached_to")==card.get("instance_id")]
    for attachment_owner,attachment in attachments:
        _detach(state,attachment,restore_control=False)
        if "Aura" in attachment.get("type_line",""):_leave_battlefield(state,attachment_owner,attachment,"graveyard")
    if card in owner["battlefield"]: owner["battlefield"].remove(card)
    earthbend_controller=card.get("earthbend_controller") if destination in {"graveyard","exile"} else None
    _queue_triggers(state,"leaves",card,owner,trigger_dedupe,trigger_sources)
    if destination=="graveyard":_queue_triggers(state,"dies",card,owner,trigger_dedupe,trigger_sources)
    card["damage"] = 0; card["tapped"] = False;card.pop("deathtouch_damage",None);card.pop("crewed_turn",None);card.pop("temporary_control_return_to",None);card.pop("activated_ability_usage",None)
    if card.get("base_type_line") is not None:card["type_line"]=card.pop("base_type_line")
    if card.get("earthbend_base_type_line") is not None:
        card["type_line"]=card.pop("earthbend_base_type_line");card["power"]=card.pop("earthbend_base_power",None);card["toughness"]=card.pop("earthbend_base_toughness",None)
    card.pop("earthbent",None);card.pop("earthbend_controller",None)
    if card.get("card_faces"):_set_card_face(card,0)
    if card.get("token"): return
    zone_owner=_player(state,card.get("owner_id",owner["id"]));previous_controller=card.get("controller_id",owner["id"]);card["controller_id"]=zone_owner["id"]
    if destination=="exile":card["controller_id"]=previous_controller;_put_into_exile(state,zone_owner,[card],"battlefield",exile_actor_id,trigger_dedupe,exile_sources,exile_batch_size);card["controller_id"]=zone_owner["id"]
    else:zone_owner[destination].append(card)
    _queue_commander_zone_choice(state,zone_owner,card,destination)
    if earthbend_controller:
        if destination=="exile":_leave_exile(state,zone_owner,[card])
        else:zone_owner[destination].remove(card)
        controller=_player(state,earthbend_controller);card["controller_id"]=controller["id"];card["tapped"]=True;card["summoning_sick"]=True;card["counters"]={};_enter_battlefield(state,controller,[card],destination);_log(state,f"{card['name']} returned to the battlefield tapped after being earthbent.")


def _sacrifice_permanents(state:dict,owner:dict,cards:list[dict])->None:
    cards=[card for card in cards if card in owner["battlefield"]]
    if not cards:return
    sources=[(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]];dedupe=set()
    for card in cards:
        _queue_triggers(state,"sacrifice",card,owner,dedupe,sources)
        _leave_battlefield(state,owner,card,"graveyard",sources,dedupe)


def _discard_cards(state:dict,player:dict,cards:list[dict])->None:
    cards=[card for card in cards if card in player["hand"]]
    if not cards:return
    sources=[(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]];dedupe=set()
    for card in cards:
        player["hand"].remove(card);player["graveyard"].append(card)
        if player.get("discard_event_turn")!=state["turn"]:player["discard_event_turn"]=state["turn"];player["discards_this_turn"]=0
        player["discards_this_turn"]=player.get("discards_this_turn",0)+1
        _queue_triggers(state,"discard",card,player,dedupe,sources)


def _leave_graveyard(state:dict,owner:dict,cards:list[dict])->list[dict]:
    """Remove a batch from one graveyard and announce the resulting zone event.

    Trigger sources are snapshotted once and a shared dedupe set makes wording such
    as "one or more cards" trigger once for the entire batch, while ordinary
    per-card wording still triggers for every card that actually left.
    """
    leaving=[card for card in cards if card in owner["graveyard"]]
    if not leaving:return []
    ordered_owners=sorted(state["players"],key=lambda source_owner:source_owner["id"]!=state.get("active_player_id"));sources=[(source_owner,source) for source_owner in ordered_owners for source in source_owner["battlefield"]];dedupe=set()
    for card in leaving:
        owner["graveyard"].remove(card)
        _queue_triggers(state,"graveyard_leave",card,owner,dedupe,sources)
    return leaving


def _put_into_exile(state:dict,owner:dict,cards:list[dict],origin:str,actor_id:str|None=None,dedupe:set[str]|None=None,sources:list[tuple[dict,dict]]|None=None,batch_size:int|None=None)->list[dict]:
    entering=[card for card in cards if card not in owner["exile"] and not card.get("token")]
    if not entering:return []
    if sources is None:
        ordered_owners=sorted(state["players"],key=lambda source_owner:source_owner["id"]!=state.get("active_player_id"));sources=[(source_owner,source) for source_owner in ordered_owners for source in source_owner["battlefield"]]
    event_dedupe=dedupe if dedupe is not None else set();event_size=batch_size or len(entering)
    for card in entering:
        card["exile_event_origin"]=origin;card["exile_event_actor_id"]=actor_id;card["exile_event_previous_controller_id"]=card.get("controller_id");card["exile_event_batch_size"]=event_size
        owner["exile"].append(card);_queue_triggers(state,"exile",card,owner,event_dedupe,sources)
        for key in ("exile_event_origin","exile_event_actor_id","exile_event_previous_controller_id","exile_event_batch_size"):card.pop(key,None)
    return entering


def _leave_exile(state:dict,owner:dict,cards:list[dict])->list[dict]:
    leaving=[card for card in cards if card in owner["exile"]]
    for card in leaving:owner["exile"].remove(card)
    return leaving


def _enter_battlefield(state:dict,controller:dict,cards:list[dict],origin:str="effect",was_cast:bool=False,played:bool=False)->list[dict]:
    entering=[card for card in cards if not any(card in owner["battlefield"] for owner in state["players"])]
    if not entering:return []
    batch_size=len(entering);dedupe:set[str]=set()
    for card in entering:
        card["controller_id"]=controller["id"];card["entry_event_origin"]=origin;card["entry_event_was_cast"]=was_cast;card["entry_event_played"]=played;card["entry_event_batch_size"]=batch_size
        controller["battlefield"].append(card)
    ordered_owners=sorted(state["players"],key=lambda owner:owner["id"]!=state.get("active_player_id"));sources=[(owner,permanent) for owner in ordered_owners for permanent in owner["battlefield"]]
    for card in entering:_queue_triggers(state,"enters",card,controller,dedupe,sources)
    for card in entering:
        for key in ("entry_event_origin","entry_event_was_cast","entry_event_played","entry_event_batch_size"):card.pop(key,None)
    return entering


def _set_tapped(state:dict,cards:list[dict],tapped:bool,actor_id:str|None=None,cause:str="effect")->list[dict]:
    changing=[card for card in cards if bool(card.get("tapped"))!=tapped and any(card in owner["battlefield"] for owner in state["players"])]
    if not changing:return []
    event="tapped" if tapped else "untapped";dedupe:set[str]=set()
    for card in changing:
        card["tapped"]=tapped;card["tap_event_actor_id"]=actor_id;card["tap_event_cause"]=cause;card["tap_event_batch_size"]=len(changing)
        if tapped:
            if card.get("tap_event_turn")!=state.get("turn"):card["tap_event_turn"]=state.get("turn");card["times_tapped_this_turn"]=0
            card["times_tapped_this_turn"]=card.get("times_tapped_this_turn",0)+1
    ordered_owners=sorted(state["players"],key=lambda owner:owner["id"]!=state.get("active_player_id"));sources=[(owner,permanent) for owner in ordered_owners for permanent in owner["battlefield"]]
    for card in changing:_queue_triggers(state,event,card,_player(state,card.get("controller_id",card.get("owner_id"))),dedupe,sources)
    for card in changing:
        for key in ("tap_event_actor_id","tap_event_cause","tap_event_batch_size"):card.pop(key,None)
    return changing


def _counter_replacement_amount(state:dict,target:dict,name:str,amount:int,actor_id:str|None,cause:str)->int:
    """Apply the common static replacement effects that modify counter placement."""
    if amount<=0:return max(0,amount)
    target_type=(target.get("type_line") or "").casefold();target_controller=target.get("controller_id",target.get("id"));is_player="type_line" not in target
    target_text=(target.get("oracle_text") or "").casefold()
    if "can't have counters put on it" in target_text or "cannot have counters put on it" in target_text:return 0
    for owner in state["players"]:
        for source in owner["battlefield"]:
            static=(source.get("oracle_text") or "").casefold()
            if source.get("attached_to")==target.get("instance_id") and "enchanted creature can't" in static and "can't have counters put on it" in static:return 0
            if "counters can't be put on artifacts, creatures, enchantments, or lands" in static and any(kind in target_type for kind in ("artifact","creature","enchantment","land")):return 0
            if is_player and "players can't get counters" in static:return 0
    result=amount
    for owner in state["players"]:
        for source in owner["battlefield"]:
            text=(source.get("oracle_text") or "").casefold()
            clauses=[clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if "would be put" in clause and "counter" in clause]
            for clause in clauses:
                clause=clause.replace("−","-");condition=clause.split(",",1)[0];source_name=(source.get("name") or "").casefold()
                if cause=="cost" and "if an effect would put" in condition:continue
                controlled=target_controller==owner["id"]
                actor_is_opponent=actor_id is not None and actor_id!=owner["id"]
                if "an opponent would put" in clause:
                    if not actor_is_opponent:continue
                elif any(scope in clause for scope in ("you control","your team controls")) and not controlled:continue
                elif "on you" in clause and not (is_player and controlled):continue
                if "creature or vehicle" in clause and not any(kind in target_type for kind in ("creature","vehicle")):continue
                if "artifact or creature" in clause and not any(kind in target_type for kind in ("artifact","creature")):continue
                if "creature, spacecraft, or planet" in clause and not any(kind in target_type for kind in ("creature","spacecraft","planet")):continue
                if re.search(r"on (?:a |another )?creature\b",clause) and "permanent" not in clause and "artifact" not in clause and "vehicle" not in clause and "creature" not in target_type:continue
                if "on a permanent" in clause and is_player:continue
                self_scope=any(reference in condition for reference in ("on this creature","on this permanent","on this artifact","on this enchantment","on this planeswalker")) or bool(source_name and re.search(rf"\bon {re.escape(source_name)}\b",condition))
                if self_scope and target is not source:continue
                named=re.search(r"one or more ([+\-]\d+/[+\-]\d+|[a-z][a-z-]*) counters? would be put",clause)
                if named and named.group(1).replace("−","-")!=name:continue
                if "can't have counters put" in clause or "cannot have counters put" in clause:result=0
                elif "twice that many" in clause:result*=2
                elif "that many plus one" in clause:result+=1
                elif "half that many" in clause:result//=2
    return result


def _add_counters(state:dict,target:dict,name:str,amount:int,actor_id:str|None=None,cause:str="effect",dedupe:set[str]|None=None)->int:
    amount=_counter_replacement_amount(state,target,name,max(0,amount),actor_id,cause)
    if amount<=0:return 0
    if name=="poison" and "id" in target:target["poison"]=target.get("poison",0)+amount
    else:
        counters=target.setdefault("counters",{});counters[name]=counters.get(name,0)+amount
    if target.get("counter_event_turn")!=state.get("turn"):
        target["counter_event_turn"]=state.get("turn");target["counter_events_this_turn"]=0
    target["counter_events_this_turn"]=target.get("counter_events_this_turn",0)+1
    target["counter_event_name"]=name;target["counter_event_amount"]=amount;target["counter_event_actor_id"]=actor_id;target["counter_event_cause"]=cause
    owner=_player(state,target.get("controller_id",target.get("id",target.get("owner_id"))))
    _queue_triggers(state,"counter_added",target,owner,dedupe)
    for key in ("counter_event_name","counter_event_amount","counter_event_actor_id","counter_event_cause"):target.pop(key,None)
    return amount


def _remove_counters(target:dict,name:str,amount:int)->int:
    if name=="poison" and "id" in target:
        removed=min(max(0,amount),max(0,target.get("poison",0)));target["poison"]-=removed;return removed
    counters=target.setdefault("counters",{});removed=min(max(0,amount),max(0,counters.get(name,0)))
    if removed:counters[name]-=removed
    return removed


def _queue_triggers(state: dict, event: str, event_card: dict | None, event_owner: dict, dedupe:set[str]|None=None, sources_override:list[tuple[dict,dict]]|None=None) -> None:
    if event in {"earthbend","waterbend","firebend","airbend"}:
        event_owner["bent_this_turn"]=sorted(set(event_owner.get("bent_this_turn",[]))|{event})
    ordered_owners=sorted(state["players"],key=lambda owner:owner["id"]!=state.get("active_player_id"))
    sources = list(sources_override) if sources_override is not None else [(owner, permanent) for owner in ordered_owners for permanent in owner["battlefield"]]
    if event=="upkeep":
        for owner,permanent in sources:
            if permanent.pop("transform_next_upkeep",False):_transform(state,permanent)
    if event in {"dies","cycling","discard","cast","damage"} and event_card:
        if not any(source is event_card for _,source in sources):
            insert_at=max((index+1 for index,(owner,_) in enumerate(sources) if owner["id"]==event_owner["id"]),default=len(sources));sources.insert(insert_at,(event_owner,event_card))
    for owner, source in sources:
        text = source.get("oracle_text") or ""
        raw_clauses = re.split(r"(?<=[.!])\s+|\n", text);clauses=[]
        for clause in raw_clauses:
            if clauses and re.match(r"(?:then if|if you do),?\b",clause.strip(),re.IGNORECASE):clauses[-1]=f"{clauses[-1]} {clause.strip()}"
            else:clauses.append(clause)
        for clause in clauses:
            lower = clause.casefold(); matches = False
            trigger_count = 1
            if event == "enters" and event_card:
                etb_boundary=re.search(r",\s*(?=(?:you\b|put\b|create\b|draw\b|each\b|target\b|this\b|that\b|it\b|its\b|gain\b|tap\b|untap\b|exile\b|investigate\b|proliferate\b|scry\b|mill\b|add\b|amass\b|venture\b|return\b|search\b|[a-z0-9' -]+ deals?\b))",lower);condition=lower[:etb_boundary.start()] if etb_boundary else lower.split(",",1)[0];type_line=event_card.get("type_line","").casefold();under_control=event_card.get("controller_id")==owner["id"];owned=event_card.get("owner_id")==owner["id"];one_or_more="one or more" in condition;dedupe_key=f"enters:{source.get('instance_id')}:{condition}"
                is_creature="creature" in type_line;is_land="land" in type_line;is_artifact="artifact" in type_line;is_enchantment="enchantment" in type_line;is_token=bool(event_card.get("token"));is_permanent=any(kind in type_line for kind in ("artifact","battle","creature","enchantment","land","planeswalker"))
                kind_ok=(("token" in condition and is_token) or ("creature" in condition and is_creature) or ("land" in condition and is_land) or ("artifact" in condition and is_artifact) or ("enchantment" in condition and is_enchantment) or ("permanent" in condition and is_permanent))
                kind_ok=kind_ok and not (("nontoken" in condition and is_token) or ("noncreature" in condition and is_creature) or ("nonland" in condition and is_land) or ("artifact creature" in condition and not (is_artifact and is_creature)))
                source_name=(source.get("name") or "").casefold();self_reference="this creature" in condition or "this permanent" in condition or "this artifact" in condition or "this enchantment" in condition or "this land" in condition or bool(source_name and source_name in condition);self_enters=source is event_card and self_reference and "enter" in condition
                kind_ok=kind_ok or self_enters
                controlled_scope=("you control" in condition or "under your control" in condition) and under_control;owned_scope="you own" in condition and owned;opponent_scope=("opponent controls" in condition or "opponents control" in condition or "under an opponent's control" in condition) and not under_control;enchanted_scope="enchanted player controls" in condition and source.get("attached_to")==event_card.get("controller_id")
                global_scope=not self_reference and not any(phrase in condition for phrase in ("you control","under your control","you own","opponent controls","opponents control","under an opponent's control","enchanted player controls"))
                another_ok="another" not in condition and "other " not in condition or source is not event_card
                relationship_ok=self_enters or (another_ok and (controlled_scope or owned_scope or opponent_scope or enchanted_scope or global_scope))
                colors={color.casefold() for color in event_card.get("colors",[])};color_words={"white":"w","blue":"u","black":"b","red":"r","green":"g"};color_ok=all(symbol in colors for word,symbol in color_words.items() if re.search(rf"\b{word} (?:artifact |enchantment |land |)?creature",condition))
                subtype_terms=[]
                subtype_match=re.search(r"(?:another |a |one or more (?:other )?)([a-z, /'-]+?)s? you control (?:with [^,]+ )?enter",condition)
                if subtype_match:
                    descriptor=subtype_match.group(1);ignored={"creature","creatures","artifact","artifacts","enchantment","enchantments","land","lands","token","tokens","nontoken","noncreature","nonland","green","white","blue","black","red","colorless","permanent","permanents","artifact creature"};subtype_terms=[term.strip() for term in re.split(r",| and/or | or | and ",descriptor) if term.strip() not in ignored]
                if subtype_terms:kind_ok=kind_ok or is_creature
                subtype_ok=not subtype_terms or any(re.search(rf"\b{re.escape(term.rstrip('s'))}s?\b",type_line) for term in subtype_terms)
                power,toughness=_parse_stats(event_card,state) if is_creature else (0,0);threshold=re.search(r"(?:power|mana value) (\d+) or (?:greater|less)",condition);value=(event_card.get("mana_value") or 0) if threshold and "mana value" in condition else power;threshold_ok=not threshold or (value>=int(threshold.group(1)) if "greater" in threshold.group(0) else value<=int(threshold.group(1)))
                feature_ok=("with flying" not in condition or _has_keyword(event_card,"Flying")) and ("face-down" not in condition or event_card.get("face_down"))
                origin=event_card.get("entry_event_origin");origin_ok=("entered from a graveyard" not in lower and "entered or were cast from a graveyard" not in lower or origin=="graveyard" or event_card.get("entry_event_was_cast") and origin=="graveyard") and ("entered from exile" not in lower and "entered or was cast from exile" not in lower or origin=="exile" or event_card.get("entry_event_was_cast") and origin=="exile") and ("without being played" not in lower or not event_card.get("entry_event_played"))
                during_turn="during your turn" not in condition or state.get("active_player_id")==owner["id"]
                once_each_turn="triggers only once each turn" in text.casefold();resolved_turns=source.get("entry_trigger_turns",{});already_triggered=resolved_turns.get(condition)==state.get("turn")
                entry_phrase=re.search(r"\benters?\b",condition) is not None
                matches=entry_phrase and kind_ok and relationship_ok and color_ok and subtype_ok and threshold_ok and feature_ok and origin_ok and during_turn and (not one_or_more or dedupe is None or dedupe_key not in dedupe) and (not once_each_turn or not already_triggered)
                if "if it was kicked" in lower:matches=matches and bool(event_card.get("was_kicked"))
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
                if matches and once_each_turn:source.setdefault("entry_trigger_turns",{})[condition]=state.get("turn")
            elif event == "dies" and event_card:
                is_creature="creature" in event_card.get("type_line","").casefold();same_controller=event_card.get("controller_id")==source.get("controller_id",owner["id"]);self_dies=source is event_card and re.search(r"when (?:~|this creature|[^,]+) dies",lower) is not None
                another=source is not event_card and "whenever another creature dies" in lower
                controlled=is_creature and same_controller and re.search(r"whenever (?:another |a )?creature you control dies",lower) is not None and ("another creature" not in lower or source is not event_card)
                opposing=is_creature and not same_controller and re.search(r"whenever (?:another |a )?creature an opponent controls dies",lower) is not None
                any_creature=is_creature and re.search(r"whenever a creature dies",lower) is not None
                one_or_more=is_creature and source is not event_card and "whenever one or more other creatures die" in lower;dedupe_key=f"dies:{source.get('instance_id')}"
                matches=self_dies or another or controlled or opposing or any_creature or (one_or_more and (dedupe is None or dedupe_key not in dedupe))
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "sacrifice" and event_card:
                under_control=event_card.get("controller_id")==source.get("controller_id",owner["id"]);type_line=event_card.get("type_line","").casefold();is_token=bool(event_card.get("token"));one_or_more="one or more" in lower;dedupe_key=f"sacrifice:{source.get('instance_id')}"
                kind_match=(("permanent" in lower and not ("nonland permanent" in lower and "land" in type_line)) or ("artifact" in lower and "artifact" in type_line) or ("creature" in lower and "creature" in type_line) or ("token" in lower and is_token))
                yours=under_control and re.search(r"whenever you sacrifice (?:a|an|another|one or more)",lower) is not None
                opponent_sacrifice=not under_control and re.search(r"whenever an opponent sacrifices (?:a|an|one or more)",lower) is not None
                any_player=re.search(r"whenever a player sacrifices (?:a|an|one or more)",lower) is not None
                another_ok="another" not in lower or source is not event_card
                matches=kind_match and another_ok and (yours or opponent_sacrifice or any_player) and (not one_or_more or dedupe is None or dedupe_key not in dedupe)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "life_gain":
                controlled=owner["id"]==event_owner["id"];count=event_owner.get("life_gain_events_this_turn",0)
                yours=controlled and (re.search(r"whenever you gain life(?:,|$)",lower) is not None or ("whenever you gain life for the first time each turn" in lower and count==1) or ("whenever you gain life for the second time each turn" in lower and count==2))
                opposing=not controlled and re.search(r"whenever an opponent gains life(?:,|$)",lower) is not None
                any_player=re.search(r"whenever a player gains life(?:,|$)",lower) is not None
                matches=yours or opposing or any_player
            elif event == "draw":
                controlled=owner["id"]==event_owner["id"];count=event_owner.get("draws_this_turn",0);one_or_more="one or more cards" in lower;dedupe_key=f"draw:{source.get('instance_id')}"
                yours=controlled and (re.search(r"whenever you draw (?:a|one or more) cards?",lower) is not None or ("whenever you draw your first card each turn" in lower and count==1) or ("whenever you draw your second card each turn" in lower and count==2) or ("whenever you draw your third card each turn" in lower and count==3))
                opposing=not controlled and re.search(r"whenever (?:an|one or more) opponents? draws? (?:a|one or more) cards?",lower) is not None
                any_player=re.search(r"whenever a player draws? (?:a|one or more) cards?",lower) is not None
                matches=(yours or opposing or any_player) and (not one_or_more or dedupe is None or dedupe_key not in dedupe)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "discard" and event_card:
                controlled=owner["id"]==event_owner["id"];count=event_owner.get("discards_this_turn",0);one_or_more="one or more cards" in lower;dedupe_key=f"discard:{source.get('instance_id')}";type_line=event_card.get("type_line","").casefold()
                kind_ok=not (("nonland card" in lower and "land" in type_line) or ("creature card" in lower and "creature" not in type_line) or ("land card" in lower and "land" not in type_line))
                self_discard=source is event_card and ("when you discard this card" in lower or re.search(r"when (?:~|this card|[^,]+) is discarded",lower) is not None)
                yours=controlled and (re.search(r"whenever you discard (?:a|one or more) cards?",lower) is not None or ("whenever you discard your first card each turn" in lower and count==1) or ("whenever you discard your second card each turn" in lower and count==2))
                opposing=not controlled and re.search(r"whenever (?:an|one or more) opponents? discards? (?:a|one or more) cards?",lower) is not None
                any_player=re.search(r"whenever (?:a|one or more) players? discards? (?:a|one or more) cards?",lower) is not None
                matches=kind_ok and (self_discard or yours or opposing or any_player) and (not one_or_more or dedupe is None or dedupe_key not in dedupe)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "graveyard_leave" and event_card:
                controlled=owner["id"]==event_owner["id"];type_line=event_card.get("type_line","").casefold();one_or_more="one or more" in lower;dedupe_key=f"graveyard-leave:{source.get('instance_id')}"
                kind_ok=not (("creature card" in lower and "creature" not in type_line) or ("noncreature card" in lower and "creature" in type_line) or ("land card" in lower and "land" not in type_line) or ("nonland card" in lower and "land" in type_line))
                during_your_turn="during your turn" not in lower or state.get("active_player_id")==owner["id"]
                yours=controlled and re.search(r"whenever (?:a|one or more) (?:creature |noncreature |land |nonland )?cards? leaves? your graveyard",lower) is not None
                opposing=not controlled and re.search(r"whenever (?:a|one or more) (?:creature |noncreature |land |nonland )?cards? leaves? an opponent'?s graveyard",lower) is not None
                any_graveyard=re.search(r"whenever (?:a|one or more) (?:creature |noncreature |land |nonland )?cards? leaves? a graveyard",lower) is not None
                matches=kind_ok and during_your_turn and (yours or opposing or any_graveyard) and (not one_or_more or dedupe is None or dedupe_key not in dedupe)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "exile" and event_card:
                type_line=event_card.get("type_line","").casefold();origin=event_card.get("exile_event_origin","");actor_id=event_card.get("exile_event_actor_id");previous_controller=event_card.get("exile_event_previous_controller_id");owned=event_owner["id"]==owner["id"];controlled=previous_controller==owner["id"];one_or_more="one or more" in lower;dedupe_key=f"exile:{source.get('instance_id')}"
                is_creature="creature" in type_line;is_artifact="artifact" in type_line;is_permanent=any(kind in type_line for kind in ("artifact","battle","creature","enchantment","land","planeswalker"));from_battlefield=origin=="battlefield";from_graveyard=origin=="graveyard";from_library=origin=="library";from_hand=origin=="hand"
                kind_ok=not (("creature card" in lower and not is_creature) or ("creatures" in lower and not is_creature) or ("artifact" in lower and not is_artifact) or ("permanent" in lower and not is_permanent))
                if "from your hand or a spell or ability you control exiles" in lower:origin_ok=(owned and from_hand) or (actor_id==owner["id"] and from_battlefield)
                elif "creatures you control and/or creature cards in your graveyard" in lower:origin_ok=is_creature and ((controlled and from_battlefield) or (owned and from_graveyard))
                elif "from your library and/or your graveyard" in lower:origin_ok=owned and origin in {"library","graveyard"}
                elif "from graveyards and/or the battlefield" in lower:origin_ok=origin in {"graveyard","battlefield"}
                else:origin_ok=not (("from the battlefield" in lower and not from_battlefield) or ("from your graveyard" in lower and not (owned and from_graveyard)) or ("from your library" in lower and not (owned and from_library)) or ("from your hand" in lower and not (owned and from_hand)))
                self_name=(source.get("name") or "").casefold();self_event=source is event_card and "is put into exile" in lower and ("this creature" in lower or "this artifact" in lower or self_name in lower)
                controlled_event=controlled and ((is_creature and re.search(r"(?:another )?creature you control[^,]* (?:dies or )?is put into exile",lower) is not None) or (is_artifact and re.search(r"(?:another )?nontoken artifact you control[^,]*is put into exile",lower) is not None))
                generic=re.search(r"whenever [^,]*(?:card|cards|creature|creatures|artifact|artifacts|permanent|permanents)[^,]*(?:is|are) put into exile",lower) is not None or "spell or ability you control exiles one or more permanents" in lower
                ownership_ok="opponent owns" not in lower or not owned
                actor_ok="spell or ability you control exiles" not in lower or actor_id==owner["id"] or (owned and from_hand)
                during_turn="during your turn" not in lower or state.get("active_player_id")==owner["id"]
                coin_ok="with a coin counter" not in lower or bool(event_card.get("counters",{}).get("coin"))
                another_ok="another" not in lower and "other cards" not in lower or source is not event_card
                once_each_turn="triggers only once each turn" in text.casefold();already_triggered=source.get("exile_trigger_turn")==state.get("turn")
                matches=kind_ok and origin_ok and ownership_ok and actor_ok and during_turn and coin_ok and another_ok and (self_event or controlled_event or generic) and (not one_or_more or dedupe is None or dedupe_key not in dedupe) and (not once_each_turn or not already_triggered)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
                if matches and once_each_turn:source["exile_trigger_turn"]=state.get("turn")
            elif event in {"tapped","untapped"} and event_card:
                tap_boundary=re.search(r",\s*(?=(?:you\b|put\b|create\b|draw\b|each\b|target\b|this\b|that\b|it\b|its\b|gain\b|tap\b|untap\b|exile\b|investigate\b|proliferate\b|scry\b|mill\b|remove\b|destroy\b|return\b|[a-z0-9' -]+ deals?\b))",lower);condition=lower[:tap_boundary.start()] if tap_boundary else lower.split(",",1)[0];type_line=event_card.get("type_line","").casefold();same_controller=event_card.get("controller_id")==owner["id"];one_or_more="one or more" in condition;dedupe_key=f"{event}:{source.get('instance_id')}:{condition}"
                verb="becomes tapped" if event=="tapped" else "becomes untapped";plural_verb="become tapped" if event=="tapped" else "become untapped";event_phrase=verb in condition or plural_verb in condition
                is_creature="creature" in type_line;is_land="land" in type_line;is_artifact="artifact" in type_line;is_token=bool(event_card.get("token"));kind_ok=not (("creature" in condition and not is_creature) or ("land" in condition and not is_land) or ("artifact" in condition and not is_artifact) or ("nontoken" in condition and is_token))
                source_name=(source.get("name") or "").casefold();self_reference="this creature" in condition or "this permanent" in condition or "this artifact" in condition or "this land" in condition or bool(source_name and source_name in condition);self_event=source is event_card and self_reference
                attached_reference=any(phrase in condition for phrase in ("enchanted creature","enchanted land","enchanted artifact","equipped creature","fortified land"));attached_event=attached_reference and source.get("attached_to")==event_card.get("instance_id")
                controlled_scope="you control" in condition and same_controller;opponent_scope=("opponent controls" in condition or "opponents control" in condition) and not same_controller;global_scope=not self_reference and not attached_reference and "you control" not in condition and "opponent controls" not in condition and "opponents control" not in condition
                relationship_ok=self_event or attached_event or controlled_scope or opponent_scope or global_scope
                subtype_match=re.search(r"(?:a|one or more) (?:other )?([a-z'-]+)s? you control become",condition);subtype=subtype_match.group(1).rstrip("s") if subtype_match and subtype_match.group(1) not in {"creature","artifact","land","permanent"} else "";subtype_ok=not subtype or re.search(rf"\b{re.escape(subtype)}s?\b",type_line) is not None
                counter_match=re.search(r"with (?:a|an) ([a-z+/-]+) counter",condition);counter_ok=not counter_match or bool(event_card.get("counters",{}).get(counter_match.group(1)))
                cause=event_card.get("tap_event_cause");attacker_ok="isn't being declared as an attacker" not in lower or cause!="attack";during_turn="during your turn" not in condition or state.get("active_player_id")==owner["id"]
                first_tap_required="becomes tapped for the first time" in lower or "first time that creature has become tapped" in lower;first_time=not first_tap_required or event_card.get("times_tapped_this_turn",0)==1
                once_each_turn="triggers only once each turn" in text.casefold();usage=source.get("tap_trigger_turns",{});already_triggered=usage.get(condition)==state.get("turn")
                matches=event_phrase and kind_ok and relationship_ok and subtype_ok and counter_ok and attacker_ok and during_turn and first_time and (not one_or_more or dedupe is None or dedupe_key not in dedupe) and (not once_each_turn or not already_triggered)
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
                if matches and once_each_turn:source.setdefault("tap_trigger_turns",{})[condition]=state.get("turn")
            elif event=="counter_added" and event_card:
                condition=lower.split(",",1)[0];counter_name=event_card.get("counter_event_name","");amount=event_card.get("counter_event_amount",0);type_line=(event_card.get("type_line") or "").casefold();same_controller=event_owner["id"]==owner["id"]
                active_placement="whenever you put one or more counters on" in condition;placement_phrase=re.search(r"(?:counter|counters) (?:is|are) put on",condition) is not None or active_placement
                named=None if re.search(r"(?:one or more |a |an )counters? (?:is|are) put",condition) else re.search(r"(?:one or more |a |an )?([+\-]\d+/[+\-]\d+|[a-z][a-z-]*) counters? (?:is|are) put",condition)
                counter_ok=not named or named.group(1).replace("−","-")==counter_name
                is_creature="creature" in type_line;is_planeswalker="planeswalker" in type_line;is_permanent=bool(type_line)
                kind_ok=not (("creature" in condition and not is_creature) or ("planeswalker" in condition and not is_planeswalker) or ("permanent" in condition and "or player" not in condition and not is_permanent))
                source_name=(source.get("name") or "").casefold();self_reference=any(reference in condition for reference in ("this creature","this permanent","this artifact","this enchantment")) or bool(source_name and source_name in condition);self_event=source is event_card and self_reference
                another_ok="another" not in condition or source is not event_card
                controlled_scope=same_controller and "you control" in condition
                opposing_scope=not same_controller and ("opponent controls" in condition or "an opponent" in condition)
                uncontrolled_scope=not same_controller and "you don't control" in condition
                global_scope=not self_reference and "you control" not in condition and "opponent controls" not in condition and "an opponent" not in condition
                first_required="first time each turn" in condition;first_ok=not first_required or event_card.get("counter_events_this_turn")==1
                one_or_more="one or more" in condition;dedupe_key=f"counter-added:{source.get('instance_id')}:{event_card.get('instance_id',event_card.get('id'))}:{counter_name}"
                ordinal_words={"first":1,"second":2,"third":3,"fourth":4,"fifth":5,"sixth":6,"seventh":7,"eighth":8,"ninth":9,"tenth":10,"eleventh":11,"twelfth":12};threshold_match=re.search(r"when the (first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth) ([a-z][a-z-]*) counter is put",condition);threshold_ok=True
                if threshold_match:
                    threshold=ordinal_words[threshold_match.group(1)];threshold_ok=threshold_match.group(2)==counter_name and event_card.get("counters",{}).get(counter_name,0)-amount<threshold<=event_card.get("counters",{}).get(counter_name,0)
                actor_ok=not active_placement or event_card.get("counter_event_actor_id")==owner["id"]
                matches=placement_phrase and actor_ok and counter_ok and kind_ok and another_ok and (self_event or controlled_scope or opposing_scope or uncontrolled_scope or global_scope) and first_ok and threshold_ok and (not one_or_more or dedupe is None or dedupe_key not in dedupe)
                if matches:
                    trigger_count=1 if one_or_more or threshold_match else amount
                    if one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event=="discover":
                matches=owner["id"]==event_owner["id"] and "whenever you discover" in lower
            elif event == "leaves" and event_card:
                matches=source is not event_card and owner["id"]==event_owner["id"] and "Creature" in event_card.get("type_line","") and "when another creature you control leaves the battlefield" in lower
            elif event == "upkeep":
                matches = "at the beginning of each upkeep" in lower or "at the beginning of each player's upkeep" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your upkeep" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's upkeep" in lower)
            elif event == "end_step":
                matches = "at the beginning of each end step" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your end step" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's end step" in lower)
            elif event == "beginning_combat":
                matches = owner["id"]==event_owner["id"] and re.search(r"at the beginning of combat on your turn",lower) is not None
            elif event == "cast" and event_card:
                controlled=event_owner["id"]==owner["id"];type_line=event_card.get("type_line","").casefold();count=event_owner.get("spells_cast_this_turn",0);cast_zone=event_card.get("cast_source_zone","hand");colors=set(event_card.get("colors") or [])
                kind_match=((re.search(r"casts? (?:a|an) spell(?: from (?:exile|your graveyard))?(?:,|$)",lower) is not None) or ("spell with cascade" in lower and _cascade_count(event_card)>0) or ("creature spell" in lower and "creature" in type_line) or ("noncreature spell" in lower and "creature" not in type_line) or ("instant or sorcery spell" in lower and any(kind in type_line for kind in ("instant","sorcery"))) or ("artifact spell" in lower and "artifact" in type_line) or ("enchantment spell" in lower and "enchantment" in type_line) or ("planeswalker spell" in lower and "planeswalker" in type_line) or ("permanent spell" in lower and any(kind in type_line for kind in ("creature","artifact","enchantment","planeswalker","battle"))) or ("legendary spell" in lower and "legendary" in type_line) or ("historic spell" in lower and ("legendary" in type_line or "artifact" in type_line or "saga" in type_line)) or ("multicolored spell" in lower and len(colors)>=2))
                zone_ok=("from exile" not in lower or cast_zone=="exile") and ("from your graveyard" not in lower or cast_zone=="graveyard")
                ordinal=("whenever you cast your first spell each turn" in lower and count==1) or ("whenever you cast your second spell each turn" in lower and count==2) or ("whenever you cast your third spell each turn" in lower and count==3)
                yours=controlled and source is not event_card and ("whenever you cast" in lower or "whenever you cast or copy" in lower) and (kind_match or ordinal) and zone_ok
                opposing=not controlled and "whenever an opponent casts" in lower and kind_match and zone_ok
                any_player="whenever a player casts" in lower and kind_match and zone_ok
                self_cast=source is event_card and re.search(r"when you cast (?:this spell|~)",lower) is not None
                matches=yours or opposing or any_player or self_cast
            elif event == "attackers_declared":
                attacking_ids = set(state.get("combat", {}).get("attackers", []))
                controlled_attackers = [card for card in owner["battlefield"] if card.get("instance_id") in attacking_ids]
                source_attacked = source.get("instance_id") in attacking_ids
                source_name = re.escape(source.get("name", "").casefold())
                if source_attacked and "attacks and isn't blocked" not in lower and "attacks and is not blocked" not in lower and re.search(rf"whenever (?:~|this creature|{source_name}) attacks\b", lower):
                    matches = True
                elif controlled_attackers and "whenever one or more creatures you control attack" in lower:
                    matches = True
                elif controlled_attackers and "whenever a creature you control attacks" in lower:
                    matches = True; trigger_count = len(controlled_attackers)
            elif event == "blockers_declared":
                combat=state.get("combat",{});blocks=combat.get("blocks",{});attacking_ids=set(combat.get("attackers",[]));blocked_ids=set(blocks.values());source_id=source.get("instance_id");source_name=re.escape(source.get("name","").casefold());source_blocking=source_id in blocks;source_blocked=source_id in blocked_ids;source_attacked=source_id in attacking_ids;controlled_blockers=[card for card in owner["battlefield"] if card.get("instance_id") in blocks]
                if source_blocking and re.search(rf"whenever (?:~|this creature|{source_name}) (?:attacks or )?blocks\b",lower):matches=True
                elif source_blocked and re.search(rf"whenever (?:~|this creature|{source_name}) becomes blocked\b",lower):matches=True
                elif source_attacked and not source_blocked and re.search(rf"whenever (?:~|this creature|{source_name}) attacks and (?:isn't|is not) blocked\b",lower):matches=True
                elif controlled_blockers and "whenever one or more creatures you control block" in lower:matches=True
                elif controlled_blockers and "whenever a creature you control blocks" in lower:matches=True;trigger_count=len(controlled_blockers)
            elif event == "combat_damage_player" and event_card:
                source_hit = source.get("instance_id") == event_card.get("instance_id")
                source_name = re.escape(source.get("name", "").casefold())
                controlled_hit=event_card.get("controller_id")==owner["id"]
                source_match=source_hit and re.search(rf"whenever (?:~|this creature|{source_name}) deals combat damage to (?:a player|an opponent)", lower) is not None
                one_or_more=controlled_hit and re.search(r"whenever one or more creatures you control deal combat damage to (?:a player|an opponent)",lower) is not None
                each_creature=controlled_hit and re.search(r"whenever a creature you control deals combat damage to (?:a player|an opponent)",lower) is not None
                dedupe_key=f"combat-damage:{source.get('instance_id')}"
                matches=source_match or each_creature or (one_or_more and (dedupe is None or dedupe_key not in dedupe))
                if matches and one_or_more and dedupe is not None:dedupe.add(dedupe_key)
            elif event == "damage" and event_card:
                target_id=event_card.get("damage_event_target_id");target_kind=event_card.get("damage_event_target_kind");combat=bool(event_card.get("damage_event_combat"));source_name=re.escape(event_card.get("name","").casefold());source_self=source is event_card;controlled_source=event_card.get("controller_id")==owner["id"]
                target_is_source=source.get("instance_id")==target_id;target_player=next((player for player in state["players"] if player["id"]==target_id),None);opponent_target=bool(target_player and target_player["id"]!=owner["id"])
                destination_ok="any target" in lower or (target_kind=="player" and (("an opponent" in lower and opponent_target) or "a player" in lower))
                dealt_by_source=source_self and destination_ok and re.search(rf"whenever (?:~|this (?:creature|permanent)|{source_name}) deals (?:combat |noncombat )?damage to (?:a player|an opponent|any target)",lower) is not None
                controlled_dealt=source is not event_card and controlled_source and (("whenever a source you control deals damage" in lower) or (destination_ok and "creature" in event_card.get("type_line","").casefold() and re.search(r"whenever a creature you control deals (?:combat |noncombat )?damage to (?:a player|an opponent)",lower) is not None))
                was_dealt=target_is_source and re.search(rf"whenever (?:~|this (?:creature|permanent)|{re.escape(source.get('name','').casefold())}) is dealt damage",lower) is not None
                player_dealt=target_kind=="player" and ((target_player and target_player["id"]==owner["id"] and "whenever you are dealt damage" in lower) or (opponent_target and "whenever an opponent is dealt damage" in lower))
                requires_noncombat="noncombat damage" in lower;requires_combat=not requires_noncombat and "combat damage" in lower;qualifier_ok=(not requires_noncombat or not combat) and (not requires_combat or combat)
                matches=qualifier_ok and not requires_combat and (dealt_by_source or controlled_dealt or was_dealt or player_dealt)
            elif event == "cycling" and event_card:
                cycled_name=re.escape(event_card.get("name","").casefold());same_card=source is event_card and re.search(rf"when you cycle (?:~|this card|{cycled_name})\b",lower) is not None
                matches=same_card or (source is not event_card and owner["id"]==event_owner["id"] and "whenever you cycle a card" in lower)
            elif event in {"earthbend","waterbend","firebend","airbend"}:
                multi_bend="whenever you waterbend, earthbend, firebend, or airbend" in lower
                matches=owner["id"]==event_owner["id"] and (f"whenever you {event}" in lower or multi_bend)
            if not matches or "," not in clause: continue
            effect = clause.split(",", 1)[1].strip()
            if event=="enters":
                etb_effect_boundary=re.search(r",\s*(?=(?:you\b|put\b|create\b|draw\b|each\b|target\b|this\b|that\b|it\b|its\b|gain\b|tap\b|untap\b|exile\b|investigate\b|proliferate\b|scry\b|mill\b|add\b|amass\b|venture\b|return\b|search\b|[a-z0-9' -]+ deals?\b))",clause,re.IGNORECASE)
                if etb_effect_boundary:effect=clause[etb_effect_boundary.end():].strip()
            if event in {"tapped","untapped"}:
                tap_effect_boundary=re.search(r",\s*(?=(?:you\b|put\b|create\b|draw\b|each\b|target\b|this\b|that\b|it\b|its\b|gain\b|tap\b|untap\b|exile\b|investigate\b|proliferate\b|scry\b|mill\b|remove\b|destroy\b|return\b|[a-z0-9' -]+ deals?\b))",clause,re.IGNORECASE)
                if tap_effect_boundary:effect=clause[tap_effect_boundary.end():].strip()
            if event=="exile":
                exile_effect=re.search(r",\s*((?:you (?:may|draw)|put|create|return|target|it deals|destroy)\b.*)$",clause,re.IGNORECASE)
                if exile_effect:effect=exile_effect.group(1).strip()
            if event=="exile" and "that many" in effect.casefold():effect=re.sub(r"\bthat many\b",str(event_card.get("exile_event_batch_size",1)),effect,flags=re.IGNORECASE)
            if event=="enters" and re.search(r"\b(?:that many|that much)\b",effect,re.IGNORECASE):effect=re.sub(r"\b(?:that many|that much)\b",str(event_card.get("entry_event_batch_size",1)),effect,flags=re.IGNORECASE)
            if event=="counter_added" and re.search(r"\b(?:that many|that much|the same number)\b",effect,re.IGNORECASE):effect=re.sub(r"\b(?:that many|that much|the same number)\b",str(event_card.get("counter_event_amount",1)),effect,flags=re.IGNORECASE)
            if event=="discover" and "same value" in effect.casefold():effect=re.sub(r"discover again for the same value",f"discover {event_owner.get('discover_event_value',0)}",effect,flags=re.IGNORECASE)
            if event in {"earthbend","waterbend","firebend","airbend"} and "whenever you waterbend, earthbend, firebend, or airbend" in lower:effect=re.split(r"whenever you waterbend, earthbend, firebend, or airbend,",clause,flags=re.IGNORECASE)[1].strip()
            if event=="enters" and re.match(r"if it was kicked,",effect,re.IGNORECASE):effect=effect.split(",",1)[1].strip()
            if event=="leaves" and "transform" in effect and "next upkeep" in effect:
                source["transform_next_upkeep"]=True;_log(state,f"{source['name']} will transform at the beginning of the next upkeep.");continue
            ability_card = {**source, "name": f"{source['name']} trigger", "oracle_text": effect, "source_type_line":source.get("type_line",""),"source_mana_cost":source.get("mana_cost",""), "type_line": "Ability", "mana_cost": ""}
            if event=="attackers_declared" and "firebending" in lower and re.search(r"\badd\b[^.]*\{r\}",lower):ability_card["firebending_trigger"]=True
            fight_steps=_fight_target_steps(state,owner["id"],ability_card,source);targets=[] if fight_steps else _targets(state, owner["id"], ability_card)
            for _ in range(trigger_count):
                trigger={"id":_id(),"kind":"trigger","card":ability_card,"controller_id":owner["id"],"target_id":None,"source_id":source["instance_id"]}
                if event_card and event in {"enters","exile","tapped","untapped","counter_added","dies","discard","graveyard_leave","damage","combat_damage_player"}:trigger["event_card_id"]=event_card.get("instance_id");trigger["event_owner_id"]=event_owner.get("id")
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
    def hit_defender(creature:dict, amount:int,target_id:str,trigger_dedupe:set[str])->None:
        planeswalker=next((card for card in defender["battlefield"] if card["instance_id"]==target_id and "Planeswalker" in card.get("type_line","")),None)
        if planeswalker:_damage_permanent(state,planeswalker,amount,creature)
        elif not _damage_player(state,defender,amount,creature,True):return
        toxic=_toxic_value(creature)
        if not planeswalker and amount>0 and toxic:_add_counters(state,defender,"poison",toxic,attacker["id"],"toxic")
        if not planeswalker and creature.get("commander"):
            source = creature["instance_id"];damage=defender.setdefault("commander_damage", {});names=defender.setdefault("commander_damage_names", {})
            # Games saved before individual commander tracking used the owner's id.
            # A legacy total can only represent one commander, so migrate it on the
            # first subsequent hit instead of silently resetting that game's clock.
            legacy_source=creature.get("owner_id",attacker["id"])
            if legacy_source in damage and source not in damage and not names:damage[source]=damage.pop(legacy_source)
            damage[source]=damage.get(source,0)+amount;names[source]=creature.get("rules_name") or creature["name"]
        if not planeswalker and amount > 0:
            _queue_triggers(state,"combat_damage_player",creature,attacker,trigger_dedupe)

    first_strike_ids=set(state["combat"].get("first_strike_damage_ids") or [])
    def damage_step(first: bool) -> None:
        battlefield = {card["instance_id"]: card for player in state["players"] for card in player["battlefield"]}
        deathtouch_hit:set[str]=set();trigger_dedupe:set[str]=set()
        def strikes(card:dict)->bool:
            has_first=_has_keyword(card,"First strike");double=_has_keyword(card,"Double strike")
            return has_first or double if first else card["instance_id"] not in first_strike_ids or double
        for attacker_id in state["combat"]["attackers"]:
            creature=battlefield.get(attacker_id)
            if not creature or not strikes(creature):continue
            power=max(0,_parse_stats(creature,state)[0]);assigned_ids=state["combat"].get("block_orders",{}).get(attacker_id) or [blocker_id for blocker_id,target_id in state["combat"]["blocks"].items() if target_id==attacker_id];blockers=[battlefield[blocker_id] for blocker_id in assigned_ids if blocker_id in battlefield]
            attack_target=state["combat"].get("attack_targets",{}).get(attacker_id,defender["id"])
            if attacker_id not in originally_blocked:
                hit_defender(creature,power,attack_target,trigger_dedupe);continue
            remaining=power
            for blocker in blockers:
                _,toughness=_parse_stats(blocker,state);lethal=1 if _has_keyword(creature,"Deathtouch") else max(1,toughness-blocker.get("damage",0));assigned=min(remaining,lethal);dealt=_damage_permanent(state,blocker,assigned,creature);remaining-=assigned
                if dealt and _has_keyword(creature,"Deathtouch"):deathtouch_hit.add(blocker["instance_id"])
            if remaining and _has_keyword(creature,"Trample"):hit_defender(creature,remaining,attack_target,trigger_dedupe)
        for blocker_id,attacker_id in state["combat"]["blocks"].items():
            blocker,creature=battlefield.get(blocker_id),battlefield.get(attacker_id)
            if not blocker or not creature or not strikes(blocker):continue
            amount=max(0,_parse_stats(blocker,state)[0]);dealt=_damage_permanent(state,creature,amount,blocker)
            if dealt and _has_keyword(blocker,"Deathtouch"):deathtouch_hit.add(creature["instance_id"])
        for owner in (attacker,defender):
            for creature in list(owner["battlefield"]):
                if "Creature" not in creature.get("type_line",""):continue
                _,toughness=_parse_stats(creature,state)
                if creature.get("damage",0)>=toughness or creature["instance_id"] in deathtouch_hit or creature.get("deathtouch_damage"):_destroy_permanent(state,owner,creature)

    participants=[card for owner in (attacker,defender) for card in owner["battlefield"] if card["instance_id"] in state["combat"]["attackers"] or card["instance_id"] in state["combat"]["blocks"]]
    if state["combat"].get("damage_step") is None:
        first_strike_ids={card["instance_id"] for card in participants if _has_keyword(card,"First strike") or _has_keyword(card,"Double strike")}
        if first_strike_ids:
            state["combat"]["first_strike_damage_ids"]=list(first_strike_ids);damage_step(True);state["combat"]["damage_step"]="regular";state["combat"]["damage_pending"]=True
            if not state.get("pending_trigger_targets"):state["priority_player_id"]=state["active_player_id"]
            state["consecutive_passes"]=0
            _log(state,"First-strike combat damage resolved. Players may respond before regular combat damage.");return
    damage_step(False)
    _log(state, "Combat damage resolved.")
    state["combat"] = {"attackers": [], "attackers_declared":False,"blocks": {},"attack_targets":{},"block_orders":{},"damage_pending":False,"damage_step":None,"first_strike_damage_ids":[],"block_triggers_pending":False}


def _check_winner(state: dict) -> None:
    losers = [player for player in state["players"] if player["life"] <= 0 or player.get("poison",0)>=10 or player.get("lost") or any(amount >= 21 for amount in player.get("commander_damage", {}).values())]
    if len(losers)==len(state["players"]):
        state["status"]="complete";state["winner_id"]=None;state["result_reason"]="draw"
        _log(state,"The game ended in a draw because all remaining players lost simultaneously.")
    elif losers:
        state["status"] = "complete"
        state["winner_id"] = opponent(state, losers[0]["id"])["id"]
        state["result_reason"] = losers[0].get("loss_reason") or "game_rule"
        _log(state, f"{opponent(state, losers[0]['id'])['name']} wins the game.")


def _state_based_actions(state: dict) -> None:
    changed=True
    while changed:
        changed=False;trigger_sources=[(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]];trigger_dedupe=set()
        for owner in state["players"]:
            for permanent in list(owner["battlefield"]):
                counters=permanent.setdefault("counters",{});opposing=min(counters.get("+1/+1",0),counters.get("-1/-1",0))
                if opposing:
                    for name in ("+1/+1","-1/-1"):
                        _remove_counters(permanent,name,opposing)
                        if not counters[name]:counters.pop(name)
                    changed=True;_log(state,f"{opposing} opposing +1/+1 and -1/-1 counter pair(s) were removed from {permanent['name']}.")
                if permanent.get("attached_to"):
                    target=next((target for target_owner in state["players"] for target in target_owner["battlefield"] if target["instance_id"]==permanent["attached_to"]),None) or next((player for player in state["players"] if player["id"]==permanent["attached_to"]),None);aura="Aura" in permanent.get("type_line","");aura_text=(permanent.get("oracle_text") or "").casefold();allowed_types=_aura_allowed_types(permanent);target_types=(target or {}).get("type_line","").casefold();type_illegal=bool(aura and allowed_types and not (("player" in allowed_types and target and target.get("id")) or any(kind in target_types for kind in allowed_types-{"player"})));wrong_controller=bool(aura and target and (("enchant creature you control" in aura_text and target.get("controller_id")!=permanent.get("controller_id")) or ("enchant creature an opponent controls" in aura_text and target.get("controller_id")==permanent.get("controller_id"))));illegal=not target or type_illegal or wrong_controller or (target is not None and target.get("instance_id") is not None and _protected_from(target,permanent)) or (target is not None and target.get("id") is not None and _player_protected_from(state,target,permanent))
                    if target and target.get("instance_id") and permanent.get("control_aura_return_to") and target.get("controller_id")!=permanent.get("controller_id"):_change_control(state,target,_player(state,permanent["controller_id"]))
                    if illegal:
                        _detach(state,permanent)
                        if aura:_leave_battlefield(state,owner,permanent,"graveyard",trigger_sources,trigger_dedupe);changed=True;continue
                _,toughness=_parse_stats(permanent,state)
                if "Creature" in permanent.get("type_line","") and (toughness<=0 or ((permanent.get("damage",0)>=toughness or permanent.get("deathtouch_damage")) and not _has_keyword(permanent,"Indestructible"))):
                    if toughness<=0:_leave_battlefield(state,owner,permanent,"graveyard",trigger_sources,trigger_dedupe)
                    else:_destroy_permanent(state,owner,permanent,trigger_sources=trigger_sources,trigger_dedupe=trigger_dedupe)
                    changed=True
                elif "Planeswalker" in permanent.get("type_line","") and permanent.get("counters",{}).get("loyalty",0)<=0:
                    _leave_battlefield(state,owner,permanent,"graveyard",trigger_sources,trigger_dedupe);changed=True
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
    temporary_controlled=[card for owner in state["players"] for card in owner["battlefield"] if card.get("temporary_control_return_to")]
    for permanent in temporary_controlled:
        return_to=_player(state,permanent.pop("temporary_control_return_to"));current=next(owner for owner in state["players"] if permanent in owner["battlefield"])
        if current["id"]!=return_to["id"]:
            current["battlefield"].remove(permanent);return_to["battlefield"].append(permanent);permanent["controller_id"]=return_to["id"];permanent["summoning_sick"]=True
            _log(state,f"{permanent['name']} returned to {return_to['name']}'s control.")
    for owner in state["players"]:
        owner["firebending_mana"]=0;owner["bent_this_turn"]=[]
        for permanent in owner["battlefield"]:
            permanent.pop("temporary_power",None);permanent.pop("temporary_toughness",None);permanent.pop("temporary_keywords",None);permanent.pop("cant_attack_until_turn",None);permanent.pop("cant_block_until_turn",None);permanent.pop("regeneration_shields",None);permanent.pop("deathtouch_damage",None);permanent.pop("crewed_turn",None);permanent["damage"]=0
            if permanent.get("base_type_line") is not None:permanent["type_line"]=permanent.pop("base_type_line")
    _set_tapped(state,list(active["battlefield"]),False,active["id"],"untap_step")
    for permanent in active["battlefield"]:permanent["summoning_sick"]=False
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
            for saga in list(active["battlefield"]):
                _add_saga_lore(state,active,saga)
        leaving_combat=state["phase"]=="combat";state["phase"] = PHASES[index + 1]
        if leaving_combat:
            for owner in state["players"]:owner["firebending_mana"]=0
        if state["phase"] == "ending":
            _queue_triggers(state,"end_step",None,_player(state,state["active_player_id"]))
        elif state["phase"] == "combat":
            state["combat"]={"attackers":[],"attackers_declared":False,"blocks":{},"attack_targets":{},"block_orders":{},"damage_pending":False,"damage_step":None,"first_strike_damage_ids":[],"block_triggers_pending":False}
            _queue_triggers(state,"beginning_combat",None,_player(state,state["active_player_id"]))
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
        random.SystemRandom().shuffle(player["library"]);_draw(state,player,7,False);_log(state,f"{player['name']} took mulligan {player['mulligans']} and drew seven new cards.")
    elif action_type == "bottom_mulligan_cards":
        required=player.get("mulligans",0);requested=action.get("card_ids") or []
        if state.get("pending_mulligan_bottom")!=player_id or len(requested)!=required or len(set(requested))!=required:raise RuleViolation(f"Choose exactly {required} cards to put on the bottom")
        chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected cards are not in your hand")
        for card in chosen:player["hand"].remove(card);player["library"].insert(0,card)
        state["pending_mulligan_bottom"]=None;player["kept_hand"]=True;_log(state,f"{player['name']} put {required} card(s) on the bottom and kept {len(player['hand'])}.")
        if all(item["kept_hand"] for item in state["players"]):
            state["status"]="active";state["priority_player_id"]=state["active_player_id"];active=_player(state,state["active_player_id"]);_log(state,f"Turn 1 began for {active['name']}. Untap and upkeep started.");_queue_triggers(state,"upkeep",None,active)
    elif action_type in {"cast_discovered","hand_discovered","decline_discovery"}:
        pending=state.get("pending_discovery") or {};candidate=next((card for card in player["exile"] if card["instance_id"]==pending.get("candidate_id")),None)
        if pending.get("player_id")!=player_id or not candidate:raise RuleViolation("That discovered card is no longer available")
        target_id=action.get("target_id");targeting_card=_spell_targeting_card(candidate);targets=_targets(state,player_id,targeting_card)
        if action_type=="cast_discovered" and _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target for the discovered spell")
        remaining=[card_id for card_id in pending["revealed_ids"] if card_id!=candidate["instance_id"]];state["pending_discovery"]=None
        if action_type=="cast_discovered":
            _leave_exile(state,player,[candidate]);stack_item={"id":_id(),"card":candidate,"controller_id":player_id,"target_id":target_id,"target_ids":[],"mode_indices":[],"mode_targets":[],"x_value":0,"free_cast":True,"cast_source_zone":"exile"};state["stack"].append(stack_item);player["spells_cast_this_turn"]=player.get("spells_cast_this_turn",0)+1;candidate["cast_source_zone"]="exile";_queue_triggers(state,"cast",candidate,player);_queue_cascade_triggers(state,player,candidate);candidate.pop("cast_source_zone",None);_log(state,f"{player['name']} cast {candidate['name']} without paying its mana cost.")
        elif action_type=="hand_discovered":
            _leave_exile(state,player,[candidate]);player["hand"].append(candidate);_log(state,f"{player['name']} put {candidate['name']} into their hand.")
        else:
            remaining.append(candidate["instance_id"]);_log(state,f"{player['name']} declined to cast {candidate['name']} with cascade.")
        _bottom_randomized_exiled(state,player,remaining);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if pending["mode"]=="discover":player["discover_event_value"]=pending["value"];_queue_triggers(state,"discover",None,player);player.pop("discover_event_value",None)
        if not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"] if (_multiplayer(state) or not allow_direct_resolution) and action_type=="cast_discovered" else state["active_player_id"]
    elif action_type == "play_land":
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id") and "Land" in card.get("type_line", "")), None)
        if not card: raise RuleViolation("That land is not in your hand")
        player["hand"].remove(card);card["summoning_sick"]=True;_enter_battlefield(state,player,[card],"hand",played=True);player["land_plays_remaining"]-=1;_log(state,f"{player['name']} played {card['name']}.")
    elif action_type == "cast":
        requested_source=action.get("source");zone_name="graveyard" if requested_source=="flashback" else "exile" if requested_source=="airbend" else requested_source if requested_source in {"hand","command"} else next((zone for zone in ("hand","command") if any(card["instance_id"]==action.get("card_id") for card in player.get(zone,[]))),None)
        source="flashback" if zone_name=="graveyard" else "airbend" if zone_name=="exile" and requested_source=="airbend" else zone_name;card=next((card for card in player.get(zone_name or "hand",[]) if card["instance_id"]==action.get("card_id")),None);flashback=_flashback_ability(card or {}) if source=="flashback" else None
        requested_kicked=bool(action.get("kicked"));requested_convoke=bool(action.get("convoke"));requested_waterbend=bool(action.get("waterbend"));requested_blight=bool(action.get("blighted") or (action.get("cost_card_ids") and _optional_blight_cost(card or {})));available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="cast" and entry["card_id"]==action.get("card_id") and entry.get("source")==source and bool(entry.get("kicked"))==requested_kicked and bool(entry.get("convoke"))==requested_convoke and bool(entry.get("waterbend"))==requested_waterbend and bool(entry.get("blighted"))==requested_blight),None)
        tax = _commander_tax(player, card) if card and source=="command" else 0
        if not card:raise RuleViolation("That spell cannot be cast")
        if not available:raise RuleViolation("That spell cannot be cast from that zone")
        cost_card={**card,"mana_cost":"{2}"} if source=="airbend" else {**card,"mana_cost":flashback["mana_cost"]} if flashback else card
        if requested_kicked:cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{_kicker_cost(card) or ''}"}
        waterbend_symbol=_spell_waterbend_symbol(card);x_value=int(action.get("x_value") or 0);has_x=_has_x_cost(cost_card) or waterbend_symbol=="X";x_max=available.get("x_max",_maximum_x(player,cost_card,tax))
        if (has_x and not available.get("x_min",0)<=x_value<=x_max) or (not has_x and action.get("x_value") is not None): raise RuleViolation("That spell cannot be cast with the chosen X value")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        stack_before_cost=len(state["stack"])
        if requested_waterbend:
            combinations_for_x=(available.get("cost_combinations_by_x") or {}).get(x_value,available.get("cost_combinations",[]));valid_groups={tuple(sorted(group)) for group in combinations_for_x}
            if tuple(sorted(selected_cost_ids)) not in valid_groups:raise RuleViolation("Choose artifacts and creatures that produce a legal waterbend payment")
        elif requested_convoke:
            minimum=available.get("cost_min_amount",1);maximum=available.get("cost_max_amount",len(cost_options))
            if not minimum<=len(selected_cost_ids)<=maximum or len(selected_cost_ids)!=len(set(selected_cost_ids)) or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose between {minimum} and {maximum} untapped creatures for convoke")
        elif len(selected_cost_ids)!=required_cost or len(set(selected_cost_ids))!=required_cost or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose exactly {required_cost} cards or permanents for the additional cost")
        target_ids=action.get("target_ids") or [];target_steps=available.get("target_steps") or []
        if target_steps:
            if len(target_ids)!=len(target_steps) or any(target_id not in {target["id"] for target in target_steps[index]["targets"]} for index,target_id in enumerate(target_ids)) or any(step.get("distinct") and target_ids[index] in target_ids[:index] for index,step in enumerate(target_steps)):raise RuleViolation("Choose each legal fight target exactly once")
        elif target_ids:raise RuleViolation("That spell does not use multiple targets")
        convoke_residual=_convoke_residual(player,cost_card,selected_cost_ids,tax,x_value) if requested_convoke else None;waterbend_amount=x_value if waterbend_symbol=="X" else int(waterbend_symbol or 0);waterbend_base={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{f'{{{tax}}}' if tax else ''}"};waterbend_residual=_waterbend_residual(player,waterbend_base,waterbend_amount,selected_cost_ids,x_value=x_value if _has_x_cost(cost_card) else 0) if requested_waterbend else None
        if (requested_waterbend and waterbend_residual is None) or (requested_convoke and convoke_residual is None) or (not requested_waterbend and not requested_convoke and not _can_pay(player,cost_card,tax,x_value=x_value)):raise RuleViolation("That spell cannot be cast with the chosen payment")
        modal_spec=_modal_spec(card);modal_options=(modal_spec or {}).get("options",[]);chosen_modes=action.get("chosen_modes") or [];mode_targets=action.get("mode_targets") or []
        if requested_blight and modal_spec and "if this spell's additional cost was paid, choose both instead" in (card.get("oracle_text") or "").casefold():modal_spec={**modal_spec,"min_modes":2,"max_modes":2}
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
        if requested_waterbend:
            _pay_mana(state,player,waterbend_residual or {"mana_cost":""},excluded_ids=set(selected_cost_ids),x_value=x_value if _has_x_cost(cost_card) else 0)
            _set_tapped(state,[permanent for permanent in player["battlefield"] if permanent["instance_id"] in selected_cost_ids],True,player_id,"waterbend")
        elif requested_convoke:
            _pay_mana(state,player,convoke_residual or {"mana_cost":""},excluded_ids=set(selected_cost_ids))
            _set_tapped(state,[creature for creature in player["battlefield"] if creature["instance_id"] in selected_cost_ids],True,player_id,"convoke")
        else:_pay_mana(state,player,cost_card,tax,x_value=x_value)
        if requested_blight:
            blight_target=next((creature for creature in player["battlefield"] if creature["instance_id"] in set(selected_cost_ids) and "Creature" in creature.get("type_line","")),None)
            if not blight_target:raise RuleViolation("Choose one creature you control to blight")
            _apply_blight(state,player,blight_target,int(available.get("blight_amount",0)))
        if zone_name=="graveyard":_leave_graveyard(state,player,[card])
        elif zone_name=="exile":_leave_exile(state,player,[card])
        else:player[zone_name].remove(card)
        cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        card.pop("airbent",None)
        if card.get("commander"): player["commander_casts"] = player.get("commander_casts", 0) + 1
        effective_target=target_id or (mode_targets[0] if len(mode_targets)==1 else None);stack_item={"id": _id(), "card": card, "controller_id": player_id, "target_id": effective_target,"target_ids":target_ids,"mode_indices":chosen_modes,"mode_targets":mode_targets,"x_value":x_value,"flashback":bool(flashback),"kicked":requested_kicked,"blighted":requested_blight,"cast_source_zone":"graveyard" if source=="flashback" else "exile" if source=="airbend" else source};state["stack"].append(stack_item);state["stack"].extend(cost_triggers); state["consecutive_passes"] = 0; state["pending_phase_advance"] = False
        if requested_waterbend:_queue_triggers(state,"waterbend",card,player)
        if player.get("cast_event_turn")!=state["turn"]:player["cast_event_turn"]=state["turn"];player["spells_cast_this_turn"]=0
        player["spells_cast_this_turn"]=player.get("spells_cast_this_turn",0)+1;card["cast_source_zone"]="graveyard" if source=="flashback" else "exile" if source=="airbend" else source
        _queue_triggers(state,"cast",card,player);_queue_cascade_triggers(state,player,card);card.pop("cast_source_zone",None)
        ward_targets=[effective_target] if effective_target else []
        ward_targets.extend(target for target in mode_targets if target and target not in ward_targets)
        ward_targets.extend(target for target in target_ids if target not in ward_targets)
        for ward_target in ward_targets:_queue_ward(state,player,ward_target,stack_item)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"): state["priority_player_id"] = opponent(state, player_id)["id"]
        mode_label="; ".join(next(mode["label"] for mode in modal_options if mode["index"]==index) for index in chosen_modes)
        behold_names=[next(candidate["name"] for zone in (player["hand"],player["battlefield"]) for candidate in zone if candidate["instance_id"]==card_id) for card_id in selected_cost_ids] if available.get("cost_kind")=="behold" else []
        _log(state, f"{player['name']} cast {card['name']}{' using flashback' if flashback else ' using airbend' if source=='airbend' else ''}{' with kicker' if requested_kicked else ''}{' using waterbend' if requested_waterbend else ''}{' using convoke' if requested_convoke else ''}{' after blighting' if requested_blight else ''}{f' with X={x_value}' if has_x else ''}{f' choosing {mode_label}' if mode_label else ''}{f' with {tax} commander tax' if tax else ''}{f' by beholding {', '.join(behold_names)}' if behold_names else ''}{' targeting '+next((target['name'] for target in targets if target['id']==target_id),'') if target_id else ''}.")
    elif action_type == "cycle":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);cycling=_cycling_ability(card or {})
        available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="cycle" and entry["card_id"]==action.get("card_id")),None)
        if not card or not cycling or not available:raise RuleViolation("That card cannot be cycled")
        stack_before_cost=len(state["stack"]);_pay_mana(state,player,{"mana_cost":cycling["mana_cost"]});_discard_cards(state,player,[card]);cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        state["stack"].append({"id":_id(),"kind":"ability","card":cycling["card"],"controller_id":player_id,"target_id":None,"source_id":card["instance_id"]});state["stack"].extend(cost_triggers);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
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
    elif action_type == "crew":
        available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="crew" and entry["card_id"]==action.get("card_id")),None);selected_ids=action.get("cost_card_ids") or []
        if not available or not selected_ids or len(selected_ids)!=len(set(selected_ids)) or not set(selected_ids).issubset(set(available["cost_options"])):raise RuleViolation("Choose untapped creatures you control to crew that Vehicle")
        selected=[creature for creature in player["battlefield"] if creature["instance_id"] in set(selected_ids)]
        if sum(max(0,_parse_stats(creature,state)[0]) for creature in selected)<available["cost_required_power"]:raise RuleViolation(f"Choose creatures with at least {available['cost_required_power']} total power")
        vehicle=next(card for card in player["battlefield"] if card["instance_id"]==action["card_id"])
        _set_tapped(state,selected,True,player_id,"crew")
        state["stack"].append({"id":_id(),"kind":"crew_ability","card":{**vehicle,"name":f"{vehicle['name']} crew ability","type_line":"Ability"},"controller_id":player_id,"target_id":None,"source_id":vehicle["instance_id"]});state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if _multiplayer(state) or not allow_direct_resolution:state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} tapped {len(selected)} creature(s) with {sum(max(0,_parse_stats(creature,state)[0]) for creature in selected)} total power to crew {vehicle['name']}.")
    elif action_type == "activate":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That ability cannot be activated")
        ability=_permanent_abilities(state,permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        waterbend_symbol=ability.get("waterbend_symbol");x_value=int(action.get("x_value") or 0);x_card={"mana_cost":ability["mana_cost"]};has_x=_has_x_cost(x_card) or waterbend_symbol=="X" or bool(available.get("selection_x"));x_max=available.get("x_max",_maximum_x(player,x_card,excluded_id=permanent["instance_id"] if ability["taps"] else None))
        if (has_x and not available.get("x_min",0)<=x_value<=x_max) or (not has_x and action.get("x_value") is not None):raise RuleViolation("That ability cannot be activated with the chosen X value")
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        target_ids=action.get("target_ids") or [];target_steps=available.get("target_steps") or []
        if target_steps:
            if len(target_ids)!=len(target_steps) or any(target_value not in {target["id"] for target in target_steps[position]["targets"]} for position,target_value in enumerate(target_ids)) or any(step.get("distinct") and target_ids[position] in target_ids[:position] for position,step in enumerate(target_steps)):raise RuleViolation("Choose each legal fight target exactly once")
        elif target_ids:raise RuleViolation("That ability does not use multiple targets")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        if available.get("selection_x"):
            valid_groups={tuple(sorted(group)) for group in (available.get("cost_combinations_by_x") or {}).get(x_value,[])}
            if tuple(sorted(selected_cost_ids)) not in valid_groups:raise RuleViolation(f"Choose a complete legal activation payment for X={x_value}")
        elif waterbend_symbol:
            combinations_for_x=(available.get("cost_combinations_by_x") or {}).get(x_value,available.get("cost_combinations",[]));valid_groups={tuple(sorted(group)) for group in combinations_for_x}
            if tuple(sorted(selected_cost_ids)) not in valid_groups:raise RuleViolation("Choose artifacts and creatures that produce a legal waterbend payment")
        elif available.get("cost_kind")=="compound":
            valid_groups={tuple(sorted(group)) for group in available.get("cost_combinations",[])}
            if tuple(sorted(selected_cost_ids)) not in valid_groups:raise RuleViolation("Choose one complete legal payment for every activation cost")
        elif len(selected_cost_ids)!=required_cost or len(set(selected_cost_ids))!=required_cost or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation(f"Choose exactly {required_cost} legal card(s) for the activation cost")
        selected_cost_cards=[card for zone in (player["hand"],player["battlefield"]) for card in zone if card["instance_id"] in set(selected_cost_ids)]
        if len(selected_cost_cards)!=(len(selected_cost_ids) if waterbend_symbol or available.get("selection_x") else required_cost):raise RuleViolation("One or more activation cost cards are no longer available")
        stack_before_cost=len(state["stack"])
        if waterbend_symbol:
            excluded={permanent["instance_id"]} if ability["taps"] else set();waterbend_amount=x_value if waterbend_symbol=="X" else int(waterbend_symbol);residual=_waterbend_residual(player,x_card,waterbend_amount,selected_cost_ids,excluded,x_value=x_value if _has_x_cost(x_card) else 0)
            if residual is None:raise RuleViolation("That waterbend payment is no longer available")
            _pay_mana(state,player,residual,excluded_ids=excluded|set(selected_cost_ids),x_value=x_value if _has_x_cost(x_card) else 0)
            _set_tapped(state,[selected for selected in player["battlefield"] if selected["instance_id"] in selected_cost_ids],True,player_id,"waterbend")
        elif ability["mana_cost"]:_pay_mana(state,player,{"mana_cost":ability["mana_cost"]},excluded_id=permanent["instance_id"] if ability["taps"] else None,x_value=x_value)
        if ability["life_cost"]:player["life"]-=ability["life_cost"]
        if ability["counter_cost"]:
            name,amount=ability["counter_cost"]["name"],ability["counter_cost"]["amount"];_remove_counters(permanent,name,amount)
        blight_cost=next((cost for cost in ability.get("selection_costs",[]) if cost["kind"]=="blight"),None)
        if blight_cost:
            blight_options={card["instance_id"] for card in _activated_cost_options(player,permanent,blight_cost)};blight_target=next(card for card in selected_cost_cards if card["instance_id"] in blight_options);_apply_blight(state,player,blight_target,blight_cost["blight_amount"])
        if ability["taps"]:_set_tapped(state,[permanent],True,player_id,"activation")
        if ability.get("restrictions",{}).get("once_each_turn") or ability.get("restrictions",{}).get("once"):
            permanent.setdefault("activated_ability_usage",{})[str(index)]={"turn":state["turn"],"ever":True}
        cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"target_ids":target_ids,"source_id":permanent["instance_id"],"x_value":x_value};state["stack"].append(stack_item);state["stack"].extend(cost_triggers);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if waterbend_symbol:_queue_triggers(state,"waterbend",permanent,player)
        for ward_target in ([target_id] if target_id else [])+target_ids:_queue_ward(state,player,ward_target,stack_item)
        sacrifice_cards=[permanent] if ability["self_sacrifice"] else []
        discard_ids={card_id for requirement in available.get("cost_requirements",[]) if requirement["kind"]=="discard" for card_id in requirement["options"]}
        sacrifice_ids={card_id for requirement in available.get("cost_requirements",[]) if requirement["kind"]=="sacrifice" for card_id in requirement["options"]}
        discard_cards=[]
        for card in list(selected_cost_cards):
            if card["instance_id"] in discard_ids and card in player["hand"]:discard_cards.append(card)
            elif card["instance_id"] in sacrifice_ids and card in player["battlefield"]:sacrifice_cards.append(card)
        _discard_cards(state,player,discard_cards)
        _sacrifice_permanents(state,player,list({card["instance_id"]:card for card in sacrifice_cards}.values()))
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {permanent['name']}: {ability['effect']}")
    elif action_type == "activate_loyalty":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id") and "Planeswalker" in card.get("type_line","")),None);index=action.get("ability_index")
        available=next((entry for entry in legal_actions(state,player_id) if entry["type"]=="activate_loyalty" and entry["card_id"]==action.get("card_id") and entry["ability_index"]==index),None)
        if not permanent or not available:raise RuleViolation("That loyalty ability cannot be activated")
        ability=_loyalty_abilities(permanent)[index];target_id=action.get("target_id");targets=available.get("targets",[])
        if targets and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target")
        stack_before_cost=len(state["stack"])
        if ability["cost"]>=0:_add_counters(state,permanent,"loyalty",ability["cost"],player_id,"cost")
        else:_remove_counters(permanent,"loyalty",-ability["cost"])
        cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        permanent["loyalty_activated_turn"]=state["turn"];stack_item={"id":_id(),"kind":"ability","card":ability["card"],"controller_id":player_id,"target_id":target_id,"source_id":permanent["instance_id"]};state["stack"].append(stack_item);state["stack"].extend(cost_triggers);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_queue_ward(state,player,target_id,stack_item)
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
            elif state["phase"]=="combat" and state["combat"]["attackers"]:
                _queue_triggers(state,"blockers_declared",None,opponent(state,state["active_player_id"]));state["combat"]["damage_pending"]=True;_log(state,"No blockers were declared. Players may respond before combat damage.")
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
        state["combat"]["attackers"] = list(requested);state["combat"]["attackers_declared"]=True
        state["combat"]["attack_targets"]={attacker_id:requested_targets.get(attacker_id,default_target) for attacker_id in requested}
        _set_tapped(state,[card for card in player["battlefield"] if card["instance_id"] in requested and not _has_keyword(card,"Vigilance")],True,player_id,"attack")
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
        if groups:state["pending_damage_order"]={"player_id":state["active_player_id"],"groups":groups};state["combat"]["block_triggers_pending"]=True;state["priority_player_id"]=state["active_player_id"]
        else:
            _queue_triggers(state,"blockers_declared",None,player);state["combat"]["damage_pending"]=True
            if not state.get("pending_trigger_targets"):state["priority_player_id"] = state["active_player_id"]
            _log(state,"Blockers were finalized. Players may respond before combat damage.")
    elif action_type == "order_blockers":
        pending=state.get("pending_damage_order") or {};orders=action.get("block_orders") or {};expected=pending.get("groups",{})
        if pending.get("player_id")!=player_id or set(orders)!=set(expected) or any(len(order)!=len(expected[attacker_id]) or len(set(order))!=len(order) or set(order)!=set(expected[attacker_id]) for attacker_id,order in orders.items()):raise RuleViolation("Order every creature blocking each attacker exactly once")
        state["combat"]["block_orders"]=orders;state["pending_damage_order"]=None;state["combat"]["damage_pending"]=True
        if state["combat"].pop("block_triggers_pending",False):_queue_triggers(state,"blockers_declared",None,opponent(state,state["active_player_id"]))
        if not state.get("pending_trigger_targets"):state["priority_player_id"]=state["active_player_id"]
        _log(state,"Damage order was chosen. Players may respond before combat damage.")
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
                _discard_cards(state,player,chosen)
            _log(state,f"{player['name']} paid {pending.get('label',pending.get('mana_cost',''))} for {pending['source_name']}'s ward.")
        else:
            state["stack"].remove(stack_item)
            _counter_stack_item(state,stack_item)
            _log(state,f"{stack_item['card']['name']} was countered by {pending['source_name']}'s ward.")
        remaining=pending.get("remaining") or []
        if action_type=="pay_ward" and remaining:
            state["pending_ward"]={**remaining[0],"remaining":remaining[1:]};state["priority_player_id"]=player_id
        else:state["pending_ward"]=None;state["priority_player_id"]=opponent(state,player_id)["id"] if (_multiplayer(state) or not allow_direct_resolution) else player_id
    elif action_type in {"pay_blight","decline_blight"}:
        pending=state.get("pending_blight") or {}
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no optional blight decision for this player")
        if action_type=="pay_blight":
            chosen=action.get("cost_card_ids") or [];creature=next((card for card in player["battlefield"] if card["instance_id"] in set(chosen) and "Creature" in card.get("type_line","")),None)
            if len(chosen)!=1 or not creature:raise RuleViolation("Choose exactly one creature you control to blight")
            _apply_blight(state,player,creature,pending["amount"])
            _state_based_actions(state)
            continuation=pending.get("continuation") or ""
            if continuation:
                ability_card={"name":f"{pending['source_name']} reflexive trigger","oracle_text":continuation,"type_line":"Ability","mana_cost":""};trigger={"id":_id(),"kind":"trigger","card":ability_card,"controller_id":player_id,"target_id":None,"source_id":pending.get("source_id")};targets=_targets(state,player_id,ability_card)
                if _target_kind(ability_card) and targets:state.setdefault("pending_trigger_targets",[]).append({"controller_id":player_id,"source_name":pending["source_name"],"trigger":trigger,"card":ability_card})
                elif not _target_kind(ability_card):state["stack"].append(trigger)
                else:_log(state,f"{pending['source_name']}'s reflexive trigger had no legal target.")
        else:_log(state,f"{player['name']} chose not to blight for {pending['source_name']}.")
        state["pending_blight"]=None;state["priority_player_id"]=(state.get("pending_trigger_targets") or [{"controller_id":state["active_player_id"]}])[0]["controller_id"]
    elif action_type=="choose_proliferate":
        pending=state.get("pending_proliferate") or {}
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no proliferate decision for this player")
        requested=action.get("target_ids") or []
        if len(requested)!=len(set(requested)):raise RuleViolation("Choose each proliferate target at most once")
        eligible={owner["id"] for owner in state["players"] if owner.get("poison",0)>0}|{card["instance_id"] for owner in state["players"] for card in owner["battlefield"] if any(amount>0 for amount in card.get("counters",{}).values())}
        if not set(requested).issubset(eligible):raise RuleViolation("Choose only permanents and players that already have counters")
        for owner in state["players"]:
            if owner["id"] in requested:_add_counters(state,owner,"poison",1,player_id,"proliferate")
            for permanent in owner["battlefield"]:
                if permanent["instance_id"] in requested:
                    for name in list(permanent.get("counters",{})):
                        if permanent["counters"][name]>0:_add_counters(state,permanent,name,1,player_id,"proliferate")
        state["pending_proliferate"]=None;state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} proliferated {len(requested)} permanent(s) and player(s).")
    elif action_type=="choose_amass_army":
        pending=state.get("pending_amass") or {};target_id=action.get("target_id")
        if pending.get("player_id")!=player_id or target_id not in pending.get("card_ids",[]):raise RuleViolation("Choose one of your Armies to amass onto")
        army=next((card for card in player["battlefield"] if card["instance_id"]==target_id and "Army" in card.get("type_line","")),None)
        if not army:raise RuleViolation("That Army is no longer on the battlefield")
        state["pending_amass"]=None;_finish_amass(state,player,army,pending["subtype"],pending["amount"]);state["priority_player_id"]=state["active_player_id"]
    elif action_type in {"accept_transform","decline_transform"}:
        pending=state.get("pending_transform") or {}
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no optional transform decision for this player")
        source=next((card for card in player["battlefield"] if card["instance_id"]==pending.get("source_id")),None)
        if action_type=="accept_transform":
            if not source or not _transform(state,source):raise RuleViolation("That permanent can no longer transform")
            continuation=pending.get("continuation") or "";state["pending_transform"]=None
            if continuation:
                state["stack"].append({"id":_id(),"kind":"ability","card":{"name":f"{source['name']} transform effect","oracle_text":continuation,"type_line":"Ability","mana_cost":""},"controller_id":player_id,"target_id":None,"source_id":source["instance_id"]});_resolve_spell(state)
        else:
            state["pending_transform"]=None;_log(state,f"{player['name']} chose not to transform {pending['source_name']}.")
        state["priority_player_id"]=state["active_player_id"]
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
            _queue_triggers(state,"blockers_declared",None,player);state["combat"]["damage_pending"]=True
            if not state.get("pending_trigger_targets"):state["priority_player_id"]=state["active_player_id"]
            _log(state,"No blockers were declared. Players may respond before combat damage.")
        else:
            _advance_turn_phase(state)
    elif action_type == "concede":
        state["status"] = "complete"; state["winner_id"] = opponent(state, player_id)["id"];state["result_reason"]="concession"; _log(state, f"{player['name']} conceded.")
    elif action_type == "discard_cards":
        pending=state.get("pending_discard") or {};requested=action.get("card_ids") or [];required=pending.get("amount",0)
        if pending.get("player_id")!=player_id or len(requested)!=required or len(set(requested))!=required:raise RuleViolation(f"Choose exactly {required} cards to discard")
        chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected cards are not in your hand")
        _discard_cards(state,player,chosen)
        reason=pending.get("reason","cleanup");state["pending_discard"]=None;_log(state,f"{player['name']} discarded {required} card(s){' to maximum hand size' if reason=='cleanup' else ''}.")
        if reason=="cleanup" and not state["stack"] and not state.get("pending_trigger_targets"):_begin_next_turn(state)
        elif reason=="cleanup":state["priority_player_id"]=(state.get("pending_trigger_targets") or [{"controller_id":state["active_player_id"]}])[0]["controller_id"]
        else:state["priority_player_id"]=state["active_player_id"]
    elif action_type == "sacrifice_permanents":
        pending=state.get("pending_sacrifice") or {};requested=action.get("card_ids") or [];required=pending.get("amount",0);allowed_ids=set(pending.get("card_ids",[]))
        if pending.get("player_id")!=player_id or len(requested)!=required or len(set(requested))!=required or not set(requested).issubset(allowed_ids):raise RuleViolation(f"Choose exactly {required} legal permanent(s) to sacrifice")
        chosen=[card for card in player["battlefield"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected permanents are no longer available")
        _sacrifice_permanents(state,player,chosen)
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
                card["controller_id"]=player_id;card["tapped"]=bool(pending.get("tapped"));card["summoning_sick"]=True;_enter_battlefield(state,player,[card],"library")
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
        player["library"].extend(cards[card_id] for card_id in reversed(top_ids));draw_after=int(pending.get("draw_after",0));state["pending_scry"]=None;state["priority_player_id"]=state["active_player_id"]
        if draw_after:_draw(state,player,draw_after)
        _log(state,f"{player['name']} kept {len(top_ids)} card(s) on top and put {len(away_ids)} in {'the graveyard' if action_type=='surveil' else 'the bottom of the library'}.")
    elif action_type == "adjust_life":
        target_player = _player(state, action.get("target_id") or player_id); amount = max(-100, min(100, int(action.get("amount") or 0))); target_player["life"] += amount; _log(state, f"{target_player['name']}'s life was adjusted by {amount:+d}.")
    elif action_type == "add_counter":
        permanent = next((card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"] == action.get("target_id")), None)
        if not permanent: raise RuleViolation("Choose a permanent")
        name = (action.get("counter_name") or "+1/+1")[:32]; amount = max(-20, min(20, int(action.get("amount") or 1)))
        if amount>=0:_add_counters(state,permanent,name,amount,player_id,"manual")
        else:_remove_counters(permanent,name,-amount)
        _log(state, f"{permanent['name']} now has {permanent['counters'].get(name,0)} {name} counter(s).")
    elif action_type == "create_token":
        token = {"instance_id":_id(),"scryfall_id":"token","name":(action.get("token_name") or "Creature Token")[:80],"image_url":None,"type_line":"Token Creature","oracle_text":"","mana_cost":"","mana_value":0,"power":str(max(0,min(99,int(action.get("power") or 1)))),"toughness":str(max(1,min(99,int(action.get("toughness") or 1)))),"owner_id":player_id,"controller_id":player_id,"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True};_enter_battlefield(state,player,[token],"token");_log(state,f"{player['name']} created {token['name']}.")
    elif action_type == "move_zone":
        source_owner = next((owner for owner in state["players"] if any(card["instance_id"] == action.get("target_id") for zone in ("hand","battlefield","graveyard","exile") for card in owner[zone])), None)
        destination = action.get("destination")
        if not source_owner or destination not in {"hand","battlefield","graveyard","exile"}: raise RuleViolation("Choose a card and destination zone")
        source = next(zone for zone in ("hand","battlefield","graveyard","exile") if any(card["instance_id"] == action.get("target_id") for card in source_owner[zone])); card = next(card for card in source_owner[source] if card["instance_id"] == action.get("target_id"))
        moved_name=card["name"]
        if source=="battlefield":_leave_battlefield(state,source_owner,card,destination,exile_actor_id=player_id if destination=="exile" else None)
        else:
            if source==destination:pass
            elif source=="graveyard":_leave_graveyard(state,source_owner,[card])
            elif source=="exile":_leave_exile(state,source_owner,[card])
            else:source_owner[source].remove(card)
            if card.get("card_faces"):_set_card_face(card,0)
            if source!=destination:
                if destination=="exile":_put_into_exile(state,source_owner,[card],source,player_id)
                else:source_owner[destination].append(card)
        _log(state, f"{moved_name} moved from {source} to {destination}.")
    _state_based_actions(state);_check_winner(state)
    state["version"] += 1
    return state
