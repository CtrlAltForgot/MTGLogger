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


_NUMBER_WORDS={"a":1,"an":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}


def _energy_quantity(text:str,verb:str)->int|str:
    symbols=re.search(rf"\b{verb}\s+((?:\{{E\}})+)",text,re.IGNORECASE)
    if symbols:return symbols.group(1).upper().count("{E}")
    word=re.search(rf"\b{verb}\s+(X|a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+\{{E\}}",text,re.IGNORECASE)
    if not word:return 0
    value=word.group(1).casefold();return "X" if value=="x" else _NUMBER_WORDS.get(value,int(value) if value.isdigit() else 0)


def _gain_energy(state:dict,player:dict,amount:int)->int:
    if amount<=0:return 0
    original=amount
    for permanent in player["battlefield"]:
        text=(permanent.get("oracle_text") or "").casefold()
        if "if you would get one or more {e}" not in text:continue
        if "twice that many {e}" in text:amount*=2
        elif "that many plus one {e}" in text:amount+=1
    player["energy"]=player.get("energy",0)+amount;player["energy_event_amount"]=amount;_log(state,f"{player['name']} got {amount} energy counter{'s' if amount!=1 else ''}.");_queue_triggers(state,"energy_gain",None,player);player.pop("energy_event_amount",None)
    if amount!=original:_log(state,f"Energy replacement effects increased the gain from {original} to {amount}.")
    return amount


def _record_spell_cast(state:dict,player:dict)->None:
    if player.get("cast_event_turn")!=state["turn"]:player["cast_event_turn"]=state["turn"];player["spells_cast_this_turn"]=0
    player["spells_cast_this_turn"]=player.get("spells_cast_this_turn",0)+1


def _pay_energy(state:dict,player:dict,amount:int)->None:
    if amount<0 or player.get("energy",0)<amount:raise RuleViolation("Not enough energy")
    player["energy"]-=amount;player["energy_paid_this_turn"]=player.get("energy_paid_this_turn",0)+amount
    if amount:_log(state,f"{player['name']} paid {amount} energy counter{'s' if amount!=1 else ''}.")


def _take_monarch(state:dict,player:dict)->None:
    previous=state.get("monarch_id")
    state["monarch_id"]=player["id"]
    if previous!=player["id"]:_log(state,f"{player['name']} became the monarch.")


def _dungeon_token(player:dict,name:str,power:str,toughness:str,subtype:str,keywords:list[str])->dict:
    return {"instance_id":_id(),"scryfall_id":"token","name":name,"image_url":None,"type_line":f"Token Creature — {subtype}","oracle_text":"\n".join(keywords),"mana_cost":"","mana_value":0,"keywords":keywords,"power":power,"toughness":toughness,"owner_id":player["id"],"controller_id":player["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True}


def _enter_undercity_room(state:dict,player:dict,room:str)->None:
    player.setdefault("undercity_rooms",[]).append(room);_log(state,f"{player['name']} entered the Undercity room {room}.")
    if room=="Secret Entrance":
        cards=[card for card in player["library"] if "Basic Land" in card.get("type_line","")]
        state["pending_library_search"]={"player_id":player["id"],"card_ids":[card["instance_id"] for card in cards],"min_amount":0,"max_amount":1,"destination":"hand","tapped":False,"label":"Secret Entrance — search for a basic land"}
    elif room=="Forge":
        targets=[card for card in player["battlefield"] if "Creature" in card.get("type_line","")]
        if targets:state["pending_dungeon"]={"player_id":player["id"],"kind":"target","room":room,"targets":[{"id":card["instance_id"],"name":card["name"],"kind":"permanent","controller_id":player["id"]} for card in targets]}
    elif room=="Lost Well":
        amount=min(2,len(player["library"]));state["pending_scry"]={"player_id":player["id"],"amount":amount,"card_ids":[card["instance_id"] for card in player["library"][-amount:]],"mode":"scry"}
    elif room=="Trap!":opponent(state,player["id"])["life"]-=5
    elif room=="Arena":
        targets=[card for owner in state["players"] for card in owner["battlefield"] if "Creature" in card.get("type_line","")]
        if targets:state["pending_dungeon"]={"player_id":player["id"],"kind":"target","room":room,"targets":[{"id":card["instance_id"],"name":card["name"],"kind":"permanent","controller_id":card["controller_id"]} for card in targets]}
    elif room=="Stash":
        token=_dungeon_token(player,"Treasure Token","0","1","Treasure",[]);token["type_line"]="Token Artifact — Treasure";token["oracle_text"]="{T}, Sacrifice this artifact: Add one mana of any color.";_enter_battlefield(state,player,[token],"token")
    elif room=="Archives":_draw(state,player)
    elif room=="Catacombs":_enter_battlefield(state,player,[_dungeon_token(player,"Skeleton Token","4","1","Skeleton",["Menace"])],"token")
    elif room=="Throne of the Dead Three":
        top=player["library"][-10:];creatures=[card for card in top if "Creature" in card.get("type_line","")]
        state["pending_dungeon"]={"player_id":player["id"],"kind":"throne","room":room,"card_ids":[card["instance_id"] for card in creatures],"cards":creatures,"top_ids":[card["instance_id"] for card in top]}
    if state.get("pending_dungeon") or state.get("pending_library_search") or state.get("pending_scry"):state["priority_player_id"]=player["id"]


def _venture_undercity(state:dict,player:dict)->None:
    path=player.setdefault("undercity_rooms",[])
    if path and path[-1] in {"Archives","Catacombs","Throne of the Dead Three"}:path.clear()
    current=path[-1] if path else None
    if current is None:_enter_undercity_room(state,player,"Secret Entrance")
    elif current=="Secret Entrance":state["pending_dungeon"]={"player_id":player["id"],"kind":"room","options":["Forge","Lost Well"]};state["priority_player_id"]=player["id"]
    elif current=="Forge":_enter_undercity_room(state,player,"Trap!")
    elif current=="Lost Well":_enter_undercity_room(state,player,"Arena")
    elif current in {"Trap!","Arena"}:_enter_undercity_room(state,player,"Stash")
    elif current=="Stash":state["pending_dungeon"]={"player_id":player["id"],"kind":"room","options":["Archives","Catacombs","Throne of the Dead Three"]};state["priority_player_id"]=player["id"]


def _take_initiative(state:dict,player:dict)->None:
    previous=state.get("initiative_id");state["initiative_id"]=player["id"]
    if previous!=player["id"]:_log(state,f"{player['name']} took the initiative.")
    _venture_undercity(state,player)


def _continuous_stats(state:dict|None,card:dict)->tuple[int,int]:
    if not state or "Creature" not in card.get("type_line",""):return 0,0
    power=toughness=0;controller=card.get("controller_id");type_line=card.get("type_line","").casefold()
    for owner in state["players"]:
        for source in owner["battlefield"]:
            chosen_type=(source.get("chosen_creature_type") or "").casefold()
            if chosen_type and controller==source.get("controller_id",owner["id"]) and re.search(rf"\b{re.escape(chosen_type)}\b",type_line) and "creatures you control of the chosen type get +1/+1" in (source.get("oracle_text") or "").casefold():power+=1;toughness+=1
            if source.get("attached_to")==card.get("instance_id"):
                attachment_text=(source.get("oracle_text") or "").casefold();attachment_match=re.search(r"(?:equipped|enchanted) creature gets ([+-]\d+)/([+-]\d+)(?! until end of turn)",attachment_text)
                if attachment_match:power+=int(attachment_match.group(1));toughness+=int(attachment_match.group(2))
            clauses=re.split(r"(?<=[.!])\s+|\n",_active_level_text(source))
            for clause in clauses:
                lower=clause.casefold()
                blessing_static="as long as you have the city's blessing" in lower and owner.get("city_blessing")
                if ":" in clause or "until end of turn" in lower or ("as long as" in lower and not blessing_static) or re.match(r"\s*(?:when|whenever|if)\b",lower):continue
                for match in re.finditer(r"\b(other )?((?:[a-z]+ )?creatures|creature tokens) (you|your opponents) control get ([+-]\d+)/([+-]\d+)",clause,re.IGNORECASE):
                    other,group,scope=bool(match.group(1)),match.group(2).casefold(),match.group(3).casefold();source_controller=source.get("controller_id",owner["id"])
                    if other and source["instance_id"]==card.get("instance_id"):continue
                    if (scope=="you" and controller!=source_controller) or (scope=="your opponents" and controller==source_controller):continue
                    if group=="creature tokens" and not card.get("token"):continue
                    qualifier=group.removesuffix(" creatures")
                    if qualifier not in {"creature","creatures"} and group!="creature tokens" and qualifier not in type_line:continue
                    power+=int(match.group(4));toughness+=int(match.group(5))
                subtype_bonus=re.search(r"\b(other )?([A-Za-z][A-Za-z'-]+)s you control get ([+-]\d+)/([+-]\d+)",clause,re.IGNORECASE)
                if subtype_bonus and subtype_bonus.group(2).casefold() not in {"creature","artifact","enchantment","permanent","token"} and controller==source.get("controller_id",owner["id"]) and (not subtype_bonus.group(1) or source.get("instance_id")!=card.get("instance_id")) and re.search(rf"\b{re.escape(subtype_bonus.group(2))}\b",type_line,re.IGNORECASE):power+=int(subtype_bonus.group(3));toughness+=int(subtype_bonus.group(4))
    return power,toughness


def _has_ascend(card:dict)->bool:
    return re.search(r"(?:^|\n)Ascend(?:\s|\()",card.get("oracle_text") or "",re.IGNORECASE) is not None


def _sync_city_blessing(state:dict)->None:
    blessed={player["id"] for player in state["players"] if player.get("city_blessing")}
    for owner in state["players"]:
        for card in owner["battlefield"]:
            controller_id=card.get("controller_id",owner["id"]);card["controller_city_blessing"]=controller_id in blessed;granted=[]
            for source_owner in state["players"]:
                for source in source_owner["battlefield"]:
                    chosen=(source.get("chosen_creature_type") or "").casefold()
                    if chosen and source.get("controller_id",source_owner["id"])==controller_id and controller_id in blessed and "they also have vigilance" in (source.get("oracle_text") or "").casefold() and re.search(rf"\b{re.escape(chosen)}\b",card.get("type_line","").casefold()):granted.append("Vigilance")
            card["continuous_keywords"]=sorted(set(granted))


def _creature_subtypes(player:dict)->list[str]:
    subtypes=[]
    for zone in (player.get("hand",[]),player.get("battlefield",[]),player.get("graveyard",[]),player.get("exile",[]),player.get("command",[]),player.get("library",[])):
        for card in zone:
            if "Creature" not in card.get("type_line","") or "—" not in card.get("type_line",""):continue
            subtypes.extend(re.findall(r"[A-Za-z][A-Za-z'-]*",card["type_line"].split("—",1)[1]))
    return sorted(set(subtypes))


def _check_ascend(state:dict,player:dict,spell:dict|None=None)->None:
    if player.get("city_blessing") or len(player["battlefield"])<10:return
    if not (_has_ascend(spell or {}) or any(_has_ascend(card) for card in player["battlefield"])):return
    player["city_blessing"]=True;_sync_city_blessing(state);_log(state,f"{player['name']} received the city's blessing for the rest of the game.")


def _additional_land_plays(player:dict)->int:
    return sum(len(re.findall(r"you may play an additional land on each of your turns",card.get("oracle_text") or "",re.IGNORECASE)) for card in player["battlefield"])


def _ensure_land_play_tracking(player:dict)->None:
    if "lands_played_this_turn" not in player:player["lands_played_this_turn"]=max(0,1+_additional_land_plays(player)-int(player.get("land_plays_remaining",0)))


def _refresh_land_plays(state:dict,player:dict)->None:
    if state.get("active_player_id")!=player["id"]:return
    player["land_plays_remaining"]=max(0,1+_additional_land_plays(player)-int(player.get("lands_played_this_turn",0)))


def _level_sections(card:dict)->tuple[list[str],list[tuple[int,int|None,list[str]]]]:
    preamble=[];sections=[];current=None
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.fullmatch(r"\s*LEVEL\s+(\d+)(?:-(\d+)|\+)\s*",line,re.IGNORECASE)
        if match:
            current=(int(match.group(1)),int(match.group(2)) if match.group(2) else None,[]);sections.append(current);continue
        (current[2] if current else preamble).append(line)
    return preamble,sections


def _active_level_text(card:dict)->str:
    preamble,sections=_level_sections(card)
    if not sections:return card.get("oracle_text") or ""
    level=int(card.get("counters",{}).get("level",0));active=next((lines for minimum,maximum,lines in sections if level>=minimum and (maximum is None or level<=maximum)),[])
    return "\n".join([*preamble,*active])


def _level_stats(card:dict)->tuple[int,int]|None:
    _,sections=_level_sections(card);level=int(card.get("counters",{}).get("level",0));active=next((lines for minimum,maximum,lines in sections if level>=minimum and (maximum is None or level<=maximum)),None)
    if active is None:return None
    match=next((re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*",line) for line in active if re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*",line)),None)
    return (int(match.group(1)),int(match.group(2))) if match else None


def _level_up_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Level up\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _parse_stats(card: dict,state:dict|None=None) -> tuple[int, int]:
    try:
        plus = card.get("counters", {}).get("+1/+1", 0); minus = card.get("counters", {}).get("-1/-1", 0)
        static_power,static_toughness=_continuous_stats(state,card)
        level_stats=_level_stats(card);base_power,base_toughness=level_stats or (int(card.get("power") or 0),int(card.get("toughness") or 0))
        blessing_power=blessing_toughness=0
        if card.get("controller_city_blessing"):
            blessing=re.search(r"(?:this creature|[A-Z][^.\n]+) gets ([+-]\d+)/([+-]\d+) as long as you have the city's blessing",card.get("oracle_text") or "",re.IGNORECASE)
            if blessing:blessing_power,blessing_toughness=int(blessing.group(1)),int(blessing.group(2))
        return base_power + plus - minus + card.get("temporary_power", 0)+static_power+blessing_power, base_toughness + plus - minus + card.get("temporary_toughness", 0)+static_toughness+blessing_toughness
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
        if "if you don't have the city's blessing, you lose 1 life" in text.casefold() and not card.get("controller_city_blessing"):life_cost+=1
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
    return colored,max(0,generic)


def _has_keyword(card: dict, keyword: str) -> bool:
    printed={value.casefold() for value in card.get("keywords", [])};temporary={value.casefold() for value in card.get("temporary_keywords", [])};continuous={value.casefold() for value in card.get("continuous_keywords", [])};attached={value.casefold() for values in card.get("attachment_keywords",{}).values() for value in values};counter_keywords={name.casefold() for name,amount in card.get("counters",{}).items() if amount>0}
    _,level_sections=_level_sections(card)
    if level_sections and any(re.search(rf"\b{re.escape(keyword)}\b","\n".join(lines),re.IGNORECASE) for _,_,lines in level_sections):printed.discard(keyword.casefold())
    lower_keyword=keyword.casefold();text=_active_level_text(card).casefold();conditional=[clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if "as long as this creature is monstrous" in clause];blessing_conditional=[clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if "city's blessing" in clause];unconditional="\n".join(clause for clause in re.split(r"(?<=[.!])\s+|\n",text) if clause not in conditional and clause not in blessing_conditional)
    if any(re.search(rf"\b{re.escape(lower_keyword)}\b",clause) for clause in blessing_conditional):printed.discard(lower_keyword)
    monstrous_match=card.get("monstrous") and any(re.search(rf"\b{re.escape(lower_keyword)}\b",clause) for clause in conditional)
    blessing_match=card.get("controller_city_blessing") and any(re.search(rf"\b{re.escape(lower_keyword)}\b",clause) for clause in blessing_conditional)
    return lower_keyword in printed|temporary|continuous|attached|counter_keywords or (lower_keyword=="haste" and bool(card.get("earthbent") or card.get("suspend_haste"))) or bool(monstrous_match) or bool(blessing_match) or re.search(rf"\b{re.escape(lower_keyword)}\b",unconditional) is not None


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


def _ninjutsu_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)(Commander\s+)?ninjutsu\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    return {"commander":bool(match.group(1)),"mana_cost":match.group(2).upper()}


def _cycling_ability(card:dict)->dict|None:
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^((?:[A-Za-z][A-Za-z ]*)?cycling)\s+((?:\{[^}]+\})+)",line.strip(),re.IGNORECASE)
        if not match:continue
        keyword,cost=match.group(1),match.group(2).upper();descriptor=keyword[:-7].strip()
        effect="Draw a card." if not descriptor else f"Search your library for a {descriptor} card, reveal it, put it into your hand, then shuffle."
        return {"keyword":keyword,"mana_cost":cost,"effect":effect,"card":{**card,"name":f"{card['name']} — {keyword}","oracle_text":effect,"source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""}}
    return None


def _suspend_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)Suspend\s+(\d+|X)\s*[—-]\s*((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    count=match.group(1).upper()
    return {"count":count,"mana_cost":match.group(2).upper()}


def _foretell_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Foretell\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _madness_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Madness\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    if re.search(r"pay six \{C\}",line,re.IGNORECASE):mana_cost="{C}"*6
    else:
        match=re.search(r"Madness\s*[—-]*\s*((?:\{[^}]+\})+)",line,re.IGNORECASE)
        if not match:return None
        mana_cost=match.group(1).upper()
    life=re.search(r"Pay (\d+) life",line,re.IGNORECASE)
    return {"mana_cost":mana_cost,"life_cost":int(life.group(1)) if life else 0}


def _unearth_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Unearth\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    energy=re.search(r"Pay (\w+|\d+) \{E\}",line,re.IGNORECASE)
    if energy:
        words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};value=energy.group(1).casefold();return {"mana_cost":"","energy_cost":words.get(value,int(value) if value.isdigit() else 0)}
    match=re.search(r"Unearth\s*[—-]*\s*((?:\{[^}]+\})+)",line,re.IGNORECASE)
    return {"mana_cost":match.group(1).upper(),"energy_cost":0} if match else None


def _plot_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Plot\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _escape_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Escape\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    words={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
    cost=re.search(r"Escape\s*[—-]*\s*((?:\{[^}]+\})+)",line,re.IGNORECASE);exile=re.search(r"Exile\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+other cards? from your graveyard",line,re.IGNORECASE);variable_types=re.search(r"Exile any number of other cards from your graveyard with (\d+|one|two|three|four|five|six|seven|eight|nine|ten) or more card types among them",line,re.IGNORECASE)
    if not cost or not (exile or variable_types):return None
    counters=re.search(r"escapes? with (a|\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve) \+1/\+1 counters? on it(?: instead)?",card.get("oracle_text") or "",re.IGNORECASE)
    words.update({"a":1,"twelve":12});amount=lambda value: int(value) if value.isdigit() else words[value.casefold()]
    return {"mana_cost":cost.group(1).upper(),"exile_count":amount(exile.group(1)) if exile else 0,"card_types_required":amount(variable_types.group(1)) if variable_types else 0,"land_count":1 if re.search(r"Exile a land you control",line,re.IGNORECASE) else 0,"counter_choice":"your choice of a +1/+1 counter or a flying counter" in (card.get("oracle_text") or "").casefold(),"counters":amount(counters.group(1)) if counters else 0}


def _graveyard_card_types(cards:list[dict])->set[str]:
    card_types={"artifact","battle","creature","enchantment","instant","kindred","land","planeswalker","sorcery","tribal"};return {word for card in cards for word in re.findall(r"[a-z]+",card.get("type_line","").split("—",1)[0].casefold()) if word in card_types}


def _plot_reduction(player:dict)->int:
    return sum(int(match.group(1)) for permanent in player["battlefield"] for match in re.finditer(r"Plotting cards from your hand costs \{(\d+)\} less",permanent.get("oracle_text") or "",re.IGNORECASE))


def _flashback_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)Flashback[ —-]*((?:\{[^}]+\})+)(?:,\s*Behold\s+(a|one|two|three|four|five|\d+)\s+([A-Za-z]+))?",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5};amount_word=(match.group(2) or "").casefold();amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 0)
    return {"mana_cost":match.group(1).upper(),"behold_amount":amount,"behold_type":(match.group(3) or "").removesuffix("s").casefold()}


def _kicker_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Kicker\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _overload_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Overload\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _entwine_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Entwine\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    mana=re.search(r"Entwine\s+((?:\{[^}]+\})+)",line,re.IGNORECASE);lands=re.search(r"Entwine\s*[—-]+\s*Sacrifice (two|three|\d+) lands?",line,re.IGNORECASE);words={"two":2,"three":3}
    if mana:return {"mana_cost":mana.group(1).upper(),"sacrifice_lands":0}
    if lands:return {"mana_cost":"","sacrifice_lands":words.get(lands.group(1).casefold(),int(lands.group(1)) if lands.group(1).isdigit() else 0)}
    return None


def _buyback_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Buyback\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    mana=re.search(r"Buyback\s*[—-]*\s*((?:\{[^}]+\})+)",line,re.IGNORECASE);life=re.search(r"Pay (\d+) life",line,re.IGNORECASE);discard=re.search(r"Discard (a|one|two|three|\d+) cards?",line,re.IGNORECASE);sacrifice=re.search(r"Sacrifice (a|one|two|three|\d+) (Islands?|lands?)",line,re.IGNORECASE);words={"a":1,"one":1,"two":2,"three":3};amount=lambda value: int(value) if value.isdigit() else words[value.casefold()]
    if not any((mana,life,discard,sacrifice)):return None
    return {"mana_cost":mana.group(1).upper() if mana else "","life_cost":int(life.group(1)) if life else 0,"discard_count":amount(discard.group(1)) if discard else 0,"discard_random":bool(discard and "at random" in line.casefold()),"sacrifice_count":amount(sacrifice.group(1)) if sacrifice else 0,"sacrifice_filter":"island" if sacrifice and sacrifice.group(2).casefold().startswith("island") else "land"}


def _buyback_reduction(player:dict)->int:
    return sum(int(match.group(1)) for permanent in player["battlefield"] for match in re.finditer(r"Buyback costs cost \{(\d+)\} less",permanent.get("oracle_text") or "",re.IGNORECASE))


def _channel_abilities(card:dict)->list[dict]:
    abilities=[]
    for line in (card.get("oracle_text") or "").splitlines():
        match=re.match(r"^Channel\s*[—-]+\s*(.+?),\s*Discard this card:\s*(.+)$",line.strip(),re.IGNORECASE)
        if not match:continue
        cost_text,effect=match.groups();mana_cost="".join(re.findall(r"\{[^}]+\}",cost_text)).upper()
        if not mana_cost:continue
        ability_card={**card,"name":f"{card['name']} — Channel","oracle_text":effect,"source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""}
        abilities.append({"mana_cost":mana_cost,"effect":effect,"card":ability_card,"sorcery_only":"activate only as a sorcery" in effect.casefold(),"legendary_reduction":"costs {1} less to activate for each legendary creature you control" in effect.casefold()})
    return abilities


def _channel_reduction(player:dict,ability:dict)->int:
    return sum("Legendary" in permanent.get("type_line","") and "Creature" in permanent.get("type_line","") for permanent in player["battlefield"]) if ability["legendary_reduction"] else 0


def _mutate_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Mutate\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _evoke_ability(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Evoke\b",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    mana=re.search(r"Evoke\s*[—-]*\s*((?:\{[^}]+\})+)",line,re.IGNORECASE);pitch=re.search(r"Exile an? (white|blue|black|red|green) card from your hand",line,re.IGNORECASE)
    colors={"white":"W","blue":"U","black":"B","red":"R","green":"G"}
    if mana:return {"mana_cost":mana.group(1).upper(),"exile_color":None,"celebrate":False}
    if pitch:return {"mana_cost":"","exile_color":colors[pitch.group(1).casefold()],"celebrate":False}
    if "celebrate an opponent" in line.casefold():return {"mana_cost":"","exile_color":None,"celebrate":True}
    return None


def _backup_specs(card:dict)->list[dict]:
    lines=(card.get("oracle_text") or "").splitlines();index=next((index for index,line in enumerate(lines) if re.match(r"^Backup\b",line.strip(),re.IGNORECASE)),None)
    if index is None:return []
    amounts=[int(value) for value in re.findall(r"\bbackup\s+(\d+)\b",lines[index],re.IGNORECASE)];granted="\n".join(lines[index+1:]).strip()
    return [{"amount":amount,"granted_text":granted} for amount in amounts]


def _dash_cost(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Dash\s+((?:\{[^}]+\})+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).upper() if match else None


def _dash_reduction(player:dict)->int:
    return sum(int(value) for permanent in player["battlefield"] for value in re.findall(r"Dash costs you pay cost \{(\d+)\} less",permanent.get("oracle_text") or "",re.IGNORECASE))


def _echo_cost(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Echo(?:\s|—|-)",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    mana=re.match(r"Echo\s+((?:\{[^}]+\})+)",line,re.IGNORECASE)
    if mana:return {"kind":"mana","mana_cost":mana.group(1).upper(),"amount":0}
    if re.search(r"Discard a card",line,re.IGNORECASE):return {"kind":"discard","mana_cost":"","amount":1}
    lands=re.search(r"Sacrifice (a|one|two|three|\d+) lands?",line,re.IGNORECASE)
    if lands:
        words={"a":1,"one":1,"two":2,"three":3};value=lands.group(1).casefold();return {"kind":"sacrifice","mana_cost":"","amount":int(value) if value.isdigit() else words[value]}
    return None


def _cumulative_upkeep_cost(card:dict)->dict|None:
    line=next((line.strip() for line in (card.get("oracle_text") or "").splitlines() if re.match(r"^Cumulative upkeep(?:\s|—|-)",line.strip(),re.IGNORECASE)),None)
    if not line:return None
    prefix=line.split("(",1)[0].strip();mana_options=re.findall(r"((?:\{[^}]+\})+)",prefix)
    life=re.search(r"Pay (\d+) life",prefix,re.IGNORECASE)
    if life:
        mana="".join(re.findall(r"\{[^}]+\}",prefix)).upper();return {"kind":"mana_life" if mana else "life","mana_options":[mana] if mana else [],"amount":int(life.group(1)),"effect":""}
    if mana_options:return {"kind":"mana","mana_options":[cost.upper() for cost in mana_options],"amount":0,"effect":""}
    if re.search(r"Discard a card",prefix,re.IGNORECASE):return {"kind":"discard","mana_options":[],"amount":1,"effect":""}
    sacrifice=re.search(r"Sacrifice a (creature|land)",prefix,re.IGNORECASE)
    if sacrifice:return {"kind":f"sacrifice_{sacrifice.group(1).casefold()}","mana_options":[],"amount":1,"effect":""}
    effect=re.sub(r"^Cumulative upkeep\s*[—-]\s*","",prefix,flags=re.IGNORECASE).rstrip(".")
    return {"kind":"effect","mana_options":[],"amount":1,"effect":effect} if effect else None


def _mutate_original(card:dict)->dict:
    runtime={"tapped","damage","counters","summoning_sick","temporary_power","temporary_toughness","temporary_keywords","attachment_keywords","attached_to","mutate_pile","mutate_count","mutate_top_component_id","effective_power","effective_toughness","entry_trigger_turns","activated_ability_usage"}
    return {key:deepcopy(value) for key,value in card.items() if key not in runtime}


def _merge_mutate(target:dict,card:dict,position:str)->None:
    pile=deepcopy(target.get("mutate_pile") or [_mutate_original(target)]);component=_mutate_original(card);pile=[component,*pile] if position=="over" else [*pile,component];top=pile[0]
    stable={key:deepcopy(target.get(key)) for key in ("instance_id","controller_id","owner_id","tapped","damage","counters","summoning_sick","attached_to","attachment_keywords") if key in target}
    identity=("scryfall_id","name","image_url","type_line","mana_cost","mana_value","power","toughness","colors","card_faces","rules_name")
    for key in identity:
        if key in top:target[key]=deepcopy(top[key])
        else:target.pop(key,None)
    texts=[];keywords=[]
    for member in pile:
        text=member.get("oracle_text") or ""
        if text:texts.append(text)
        for keyword in member.get("keywords",[]):
            if keyword not in keywords:keywords.append(keyword)
    target.update(stable);target["oracle_text"]="\n".join(texts);target["keywords"]=keywords;target["mutate_pile"]=pile;target["mutate_count"]=target.get("mutate_count",0)+1;target["mutate_top_component_id"]=top.get("instance_id");target["commander"]=any(member.get("commander") for member in pile)
    commander=next((member for member in pile if member.get("commander")),None)
    if commander:target["commander_source_id"]=commander["instance_id"]


def _has_convoke(card:dict)->bool:
    return _has_keyword(card,"Convoke")


def _affinity_kind(card:dict)->str|None:
    match=re.search(r"(?:^|\n)Affinity for ([^\n.(]+)",card.get("oracle_text") or "",re.IGNORECASE)
    return match.group(1).strip().casefold() if match else None


def _affinity_reduction(player:dict,card:dict)->int:
    kind=_affinity_kind(card)
    if not kind:return 0
    outlaws={"assassin","mercenary","pirate","rogue","warlock"}
    def matches(permanent:dict)->bool:
        type_line=permanent.get("type_line","").casefold();words=set(re.findall(r"[a-z]+",type_line));text=(permanent.get("oracle_text") or "").casefold()
        if kind=="artifacts":return "artifact" in words
        if kind=="artifact creatures":return {"artifact","creature"}.issubset(words)
        if kind=="creatures":return "creature" in words
        if kind=="enchantments":return "enchantment" in words
        if kind=="historic permanents":return "artifact" in words or "legendary" in words or "saga" in words
        if kind=="outlaws":return bool(words&outlaws)
        if kind=="snow lands":return {"snow","land"}.issubset(words)
        if kind=="tokens":return bool(permanent.get("token"))
        if kind=="affinity":return "affinity for " in text
        singular=kind[:-1] if kind.endswith("s") else kind
        return singular in words
    return sum(matches(permanent) for permanent in player["battlefield"])


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
    text="\n".join([_active_level_text(card),*(card.get("temporary_backup_rules") or []),*attachment_texts]);blessed=_player(state,card.get("controller_id",card.get("owner_id"))).get("city_blessing")
    clauses=re.split(r"(?<=[.!])\s+|\n",text);visible=[clause for clause in clauses if blessed or "city's blessing" not in clause.casefold() or "unless you have the city's blessing" in clause.casefold()]
    return "\n".join(visible).casefold()


def _can_attack(state:dict,card:dict,attacker:dict,defender:dict)->bool:
    text=_effective_rules_text(state,card)
    if card.get("cant_attack_until_turn")==state["turn"]:return False
    defender_override="defender" in text and "can attack as though it didn't have defender" in text and any("Creature" in permanent.get("type_line","") and _parse_stats(permanent,state)[0]>=4 for permanent in attacker["battlefield"])
    if _has_keyword(card,"Defender") and not defender_override:return False
    if "can't attack unless" in text or "can't attack or block unless" in text:
        if "unless you have the city's blessing" in text and attacker.get("city_blessing"):return True
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
    conditional="can't attack or block unless" in blocker_text and not ("unless you have the city's blessing" in blocker_text and _player(state,blocker.get("controller_id")).get("city_blessing"))
    if conditional and "you control another creature with power 4 or greater" in blocker_text:
        controller=_player(state,blocker.get("controller_id"));conditional=any(permanent["instance_id"]!=blocker["instance_id"] and "Creature" in permanent.get("type_line","") and _parse_stats(permanent,state)[0]>=4 for permanent in controller["battlefield"])
        if not conditional:return False
    elif conditional:return False
    if "can't block" in blocker_text and "can't attack or block alone" not in blocker_text and "can't attack or block unless" not in blocker_text and "can't block or be blocked by non-spirit creatures" not in blocker_text:return False
    if "can't be blocked" in attacker_text or "unblockable" in attacker_text:return False
    attacker_controller=_player(state,attacker.get("controller_id"));attacker_type=attacker.get("type_line","").casefold()
    if attacker_controller.get("city_blessing") and any("detectives you control can't be blocked" in (source.get("oracle_text") or "").casefold() for source in attacker_controller["battlefield"]) and "detective" in attacker_type:return False
    if "can't be blocked by non-spirit creatures" in attacker_text and "spirit" not in blocker.get("type_line","").casefold():return False
    if "can't block or be blocked by non-spirit creatures" in blocker_text and "spirit" not in attacker.get("type_line","").casefold():return False
    defender=opponent(state,attacker_controller["id"]);defender_lands=[card for card in defender["battlefield"] if "Land" in card.get("type_line","")]
    global_text="\n".join(card.get("oracle_text") or "" for owner in state["players"] for card in owner["battlefield"]).casefold()
    for kind in ("plains","island","swamp","mountain","forest","desert"):
        if re.search(rf"\b{kind}walk\b",attacker_text) and (any(re.search(rf"\b{kind}\b",land.get("type_line",""),re.IGNORECASE) for land in defender_lands) or f"all lands are {kind}s" in global_text):return False
    if "legendary landwalk" in attacker_text and any("Legendary" in land.get("type_line","") for land in defender_lands):return False
    if "nonbasic landwalk" in attacker_text and any("Basic" not in land.get("type_line","") for land in defender_lands):return False
    if _has_keyword(attacker,"Fear") and not ("Artifact" in blocker.get("type_line","") or "B" in _card_colors(blocker)):return False
    if _has_keyword(attacker,"Intimidate") and not ("Artifact" in blocker.get("type_line","") or bool(_card_colors(attacker)&_card_colors(blocker))):return False
    if _has_keyword(attacker,"Skulk") and _parse_stats(blocker,state)[0]>_parse_stats(attacker,state)[0]:return False
    attacker_shadow=_has_keyword(attacker,"Shadow");blocker_shadow=_has_keyword(blocker,"Shadow")
    if attacker_shadow!=blocker_shadow:return False
    if _has_keyword(attacker,"Horsemanship") and not _has_keyword(blocker,"Horsemanship"):return False
    return (not _has_keyword(attacker,"Flying") or _has_keyword(blocker,"Flying") or _has_keyword(blocker,"Reach")) and not _protected_from(attacker,blocker)


def _keyword_instances(state:dict,card:dict,keyword:str)->list[int]:
    values=[];pattern=rf"(?:^|[,;/—]\s*){re.escape(keyword)}(?:\s+(\d+))?\b"
    for line in _effective_rules_text(state,card).splitlines():
        rules=line.split("(",1)[0]
        values.extend(int(match.group(1) or 1) for match in re.finditer(pattern,rules,re.IGNORECASE))
    if not values and _has_keyword(card,keyword):values=[1]
    return values


def _granted_exalted_count(state:dict,controller:dict)->int:
    count=0
    for source in controller["battlefield"]:
        text=(source.get("oracle_text") or "").casefold()
        if "other creatures you control have exalted" in text:count+=sum("Creature" in card.get("type_line","") and card is not source for card in controller["battlefield"])
        if "other vampires you control have exalted" in text:count+=sum(card is not source and re.search(r"\bVampire\b",card.get("type_line",""),re.IGNORECASE) is not None for card in controller["battlefield"])
    return count


def _granted_flanking_count(state:dict,attacker:dict,controller:dict)->int:
    if not _keyword_instances(state,attacker,"Flanking"):return 0
    return sum(source is not attacker and "other creatures you control with flanking have flanking" in (source.get("oracle_text") or "").casefold() for source in controller["battlefield"])


def _queue_combat_stat_trigger(state:dict,controller_id:str,source:dict,target:dict,keyword:str,power:int,toughness:int)->None:
    ability={"name":f"{source['name']} — {keyword}","oracle_text":f"{target['name']} gets {power:+d}/{toughness:+d} until end of turn.","type_line":"Ability","mana_cost":""};state["stack"].append({"id":_id(),"kind":"combat_stat_trigger","card":ability,"controller_id":controller_id,"source_id":source["instance_id"],"target_id":target["instance_id"],"power_change":power,"toughness_change":toughness,"keyword":keyword});_log(state,f"{source['name']}'s {keyword} ability triggered for {target['name']}.")


def _queue_exalted_triggers(state:dict,controller:dict,attacker:dict)->None:
    for source in controller["battlefield"]:
        for _ in _keyword_instances(state,source,"Exalted"):_queue_combat_stat_trigger(state,controller["id"],source,attacker,"exalted",1,1)
    for _ in range(_granted_exalted_count(state,controller)):_queue_combat_stat_trigger(state,controller["id"],attacker,attacker,"granted exalted",1,1)


def _queue_block_keyword_triggers(state:dict,attacker_controller:dict,defender:dict)->None:
    battlefield={card["instance_id"]:card for owner in state["players"] for card in owner["battlefield"]};blocks=state["combat"].get("blocks",{});bushido_attackers=set()
    for blocker_id,attacker_id in blocks.items():
        attacker= battlefield.get(attacker_id);blocker=battlefield.get(blocker_id)
        if not attacker or not blocker:continue
        if attacker_id not in bushido_attackers:
            for amount in _keyword_instances(state,attacker,"Bushido"):_queue_combat_stat_trigger(state,attacker_controller["id"],attacker,attacker,"bushido",amount,amount)
            bushido_attackers.add(attacker_id)
        for amount in _keyword_instances(state,blocker,"Bushido"):_queue_combat_stat_trigger(state,defender["id"],blocker,blocker,"bushido",amount,amount)
        if not _keyword_instances(state,blocker,"Flanking"):
            count=len(_keyword_instances(state,attacker,"Flanking"))+_granted_flanking_count(state,attacker,attacker_controller)
            for _ in range(count):_queue_combat_stat_trigger(state,attacker_controller["id"],attacker,blocker,"flanking",-1,-1)


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
    if card.get("face_down") and (card.get("cloaked") or card.get("disguised")):return {"cost_type":"mana","mana_cost":"{2}","amount":0,"label":"{2}"}
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


def _set_day_night(state:dict,value:str)->None:
    previous=state.get("day_night")
    if value not in {"day","night"} or previous==value:return
    state["day_night"]=value;_log(state,f"It became {value}.")
    for owner in state["players"]:
        for permanent in list(owner["battlefield"]):
            if value=="night" and _has_keyword(permanent,"Daybound"):_transform(state,permanent)
            elif value=="day" and _has_keyword(permanent,"Nightbound"):_transform(state,permanent)
    state["day_night_event"]=value;_queue_triggers(state,"day_night",None,_player(state,state["active_player_id"]));state.pop("day_night_event",None)


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
        if re.match(r"^\s*Level up\b",line,re.IGNORECASE):continue
        match = re.match(r"^([^:]+):\s*(.+)$", line.strip())
        if not match: continue
        cost,effect = match.group(1).strip(),match.group(2).strip()
        waterbend_symbol=_waterbend_symbol(cost);energy_cost=_energy_quantity(cost,"pay");regular_cost=re.sub(r"\bwaterbend\s+\{(?:\d+|X)\}","",cost,flags=re.IGNORECASE)
        mana_cost="".join(re.findall(r"\{[^}]+\}",regular_cost,re.IGNORECASE)).upper().replace("{T}","").replace("{Q}","").replace("{E}","")
        taps="{T}" in cost.upper()
        source_name=re.escape(card.get("name", ""));self_reference=rf"(?:~|this (?:artifact|creature|permanent)|{source_name})"
        self_sacrifice=re.search(rf"\bsacrifice {self_reference}\b",cost,re.IGNORECASE) is not None
        self_bottom=re.search(rf"\bput {self_reference} on the bottom of (?:its|your) owner's library\b",cost,re.IGNORECASE) is not None
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
        if unsupported or (not taps and not mana_cost and not waterbend_symbol and not energy_cost and not self_sacrifice and not self_bottom and not life_cost and not counter_cost and not selection_costs):continue
        if re.match(r"add (?:\{|one mana)", effect, re.IGNORECASE): continue
        adapt=re.search(r"\badapt (\d+)\b",effect,re.IGNORECASE);monstrosity=re.search(r"\bmonstrosity (X|\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",effect,re.IGNORECASE);words_amount={"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10};mechanic="adapt" if adapt else "monstrosity" if monstrosity else None;amount=int(adapt.group(1)) if adapt else (monstrosity.group(1).upper() if monstrosity and monstrosity.group(1).upper()=="X" else words_amount.get(monstrosity.group(1).casefold(),int(monstrosity.group(1)) if monstrosity and monstrosity.group(1).isdigit() else 0) if monstrosity else 0)
        if mechanic=="monstrosity" and "where x is the result" in effect.casefold():amount="D8"
        if mechanic=="monstrosity" and "where x is the number of counters among creatures you control" in effect.casefold():amount="CREATURE_COUNTERS"
        ability_card = {**card, "name": f"{card['name']} ability", "oracle_text": effect, "source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""), "type_line": "Ability", "mana_cost": ""}
        if mechanic:ability_card.update({"growth_mechanic":mechanic,"growth_amount":amount})
        lower_effect=effect.casefold();restrictions={"sorcery":bool(re.search(r"activate (?:this ability )?only (?:as a sorcery|any time you could cast a sorcery)",lower_effect)),"your_turn":bool(re.search(r"activate (?:this ability )?only during your turn",lower_effect)),"opponent_turn":bool(re.search(r"activate (?:this ability )?only during an opponent's turn",lower_effect)),"combat":bool(re.search(r"activate (?:this ability )?only during combat",lower_effect)),"before_attackers":bool(re.search(r"activate (?:this ability )?only before attackers are declared",lower_effect)),"upkeep":bool(re.search(r"activate (?:this ability )?only during your upkeep",lower_effect)),"end_step":bool(re.search(r"activate (?:this ability )?only during your end step",lower_effect)),"once_each_turn":bool(re.search(r"activate (?:this ability )?(?:only |no more than )?once (?:each|per) turn",lower_effect)),"once":bool(re.search(r"activate (?:this ability )?only once(?:\.|$)",lower_effect))}
        abilities.append({"cost":cost,"mana_cost":mana_cost,"waterbend_symbol":waterbend_symbol,"energy_cost":energy_cost,"taps":taps,"self_sacrifice":self_sacrifice,"self_bottom":self_bottom,"life_cost":life_cost,"counter_cost":counter_cost,"selection_cost":selection_costs[0] if len(selection_costs)==1 else None,"selection_costs":selection_costs,"restrictions":restrictions,"effect":effect,"card":ability_card})
    return abilities


def _activation_timing_legal(state:dict,player_id:str,permanent:dict,index:int,ability:dict)->bool:
    restrictions=ability.get("restrictions",{});active=state["active_player_id"]==player_id;phase=state["phase"]
    if "activate only if you have the city's blessing" in ability.get("effect","").casefold() and not _player(state,player_id).get("city_blessing"):return False
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


def _activation_generic_reduction(state:dict,player:dict,permanent:dict,ability:dict)->int:
    text=ability.get("effect","").casefold();reduction=0
    if "for each other artifact you control" in text:reduction+=sum(card is not permanent and "Artifact" in card.get("type_line","") for card in player["battlefield"])
    if "for each instant and sorcery card in your graveyard" in text:reduction+=sum(any(kind in card.get("type_line","") for kind in ("Instant","Sorcery")) for card in player["graveyard"])
    if "for each creature card in your graveyard" in text:reduction+=sum("Creature" in card.get("type_line","") for card in player["graveyard"])
    if "for each creature with power 4 or greater your opponents control" in text:reduction+=sum("Creature" in card.get("type_line","") and _parse_stats(card,state)[0]>=4 for owner in state["players"] if owner["id"]!=player["id"] for card in owner["battlefield"])
    return reduction


def _permanent_abilities(state:dict,card:dict)->list[dict]:
    granted=[ability for rules in card.get("attachment_rules",{}).values() for ability in re.findall(r'"([^"]+:[^"]+)"',rules)]
    rules="\n".join([_active_level_text(card),*(card.get("temporary_backup_rules") or []),*granted]);level_cost=_level_up_cost(card)
    if level_cost:rules=f"{rules}\n{level_cost}: Put a level counter on this. Activate only as a sorcery."
    abilities=_activated_abilities({**card,"oracle_text":rules})
    for ability in abilities:
        if ability["effect"].casefold().startswith("put a level counter on this"):
            ability["card"]["growth_mechanic"]="level_up";ability["card"]["growth_amount"]=1
    return abilities


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


def _populate(state:dict,player:dict,source_name:str,repeats:int=1,tapped_attacking:bool=False,haste:bool=False,sacrifice_end:bool=False)->None:
    if repeats<=0:return
    tokens=[card for card in player["battlefield"] if card.get("token") and "Creature" in card.get("type_line","")]
    if not tokens:_log(state,f"{player['name']} could not populate because they controlled no creature token.");return
    state["pending_populate"]={"player_id":player["id"],"source_name":source_name,"repeats":repeats,"card_ids":[card["instance_id"] for card in tokens],"tapped_attacking":tapped_attacking,"haste":haste,"sacrifice_end":sacrifice_end};state["priority_player_id"]=player["id"]


def _finish_populate(state:dict,player:dict,source:dict,pending:dict)->None:
    token=deepcopy(source);token["instance_id"]=_id();token["owner_id"]=player["id"];token["controller_id"]=player["id"];token["token"]=True;token["damage"]=0;token["counters"]={};token["tapped"]=bool(pending.get("tapped_attacking"));token["summoning_sick"]=True
    for key in ("attached_to","attachment_keywords","attachment_rules","temporary_power","temporary_toughness","temporary_keywords","temporary_backup_rules","deathtouch_damage","activated_ability_usage"):token.pop(key,None)
    if pending.get("haste"):token["temporary_keywords"]=sorted(set(token.get("temporary_keywords",[]))|{"Haste"})
    if pending.get("sacrifice_end"):token["populate_sacrifice_turn"]=state["turn"]
    _enter_battlefield(state,player,[token],"token")
    if pending.get("tapped_attacking") and state["phase"]=="combat":state["combat"]["attackers"].append(token["instance_id"]);state["combat"]["attack_targets"][token["instance_id"]]=opponent(state,player["id"])["id"]
    _log(state,f"{player['name']} populated a copy of {source['name']}.")
    repeats=int(pending.get("repeats",1))-1
    if repeats:_populate(state,player,pending["source_name"],repeats,bool(pending.get("tapped_attacking")),bool(pending.get("haste")),bool(pending.get("sacrifice_end")))


def _bolster(state:dict,player:dict,amount:int,source_name:str,grant_trample:bool=False)->None:
    creatures=[card for card in player["battlefield"] if "Creature" in card.get("type_line","")]
    if not creatures or amount<=0:return
    minimum=min(_parse_stats(card,state)[1] for card in creatures);eligible=[card for card in creatures if _parse_stats(card,state)[1]==minimum]
    if len(eligible)==1:
        _add_counters(state,eligible[0],"+1/+1",amount,player["id"],"bolster")
        if grant_trample:eligible[0]["temporary_keywords"]=sorted(set(eligible[0].get("temporary_keywords",[]))|{"Trample"})
        _log(state,f"{player['name']} bolstered {eligible[0]['name']} {amount}.");return
    state["pending_bolster"]={"player_id":player["id"],"source_name":source_name,"amount":amount,"card_ids":[card["instance_id"] for card in eligible],"grant_trample":grant_trample};state["priority_player_id"]=player["id"]


def _continue_explore(state:dict,player:dict)->None:
    queue=state.setdefault("pending_explore_queue",[])
    while queue:
        creature_id=queue.pop(0);creature=next((card for card in player["battlefield"] if card["instance_id"]==creature_id and "Creature" in card.get("type_line","")),None)
        if not creature:continue
        if not player["library"]:_log(state,f"{creature['name']} explored, but {player['name']}'s library was empty.");continue
        revealed=player["library"][-1];_log(state,f"{player['name']} revealed {revealed['name']} as {creature['name']} explored.")
        if "Land" in revealed.get("type_line",""):
            player["library"].pop();player["hand"].append(revealed);_log(state,f"{player['name']} put the revealed land into their hand.");continue
        _add_counters(state,creature,"+1/+1",1,player["id"],"explore")
        state["pending_explore"]={"player_id":player["id"],"creature_id":creature_id,"creature_name":creature["name"],"card_id":revealed["instance_id"],"card":revealed};state["priority_player_id"]=player["id"]
        _log(state,f"{creature['name']} received a +1/+1 counter. {player['name']} may put {revealed['name']} into their graveyard.");return
    state["pending_explore"]=None;state["pending_explore_queue"]=[]


def _explore(state:dict,player:dict,creatures:list[dict])->None:
    state.setdefault("pending_explore_queue",[]).extend(card["instance_id"] for card in creatures)
    if not state.get("pending_explore"):_continue_explore(state,player)


def _continue_connive(state:dict)->None:
    queue=state.setdefault("pending_connive_queue",[])
    while queue:
        entry=queue.pop(0);player=_player(state,entry["player_id"]);creature=next((card for card in player["battlefield"] if card["instance_id"]==entry["creature_id"] and "Creature" in card.get("type_line","")),None)
        if not creature:continue
        amount=entry["amount"];_draw(state,player,amount)
        if state.get("status")=="complete":state["pending_connive"]=None;queue.clear();return
        required=min(amount,len(player["hand"]));state["pending_connive"]={**entry,"creature_name":creature["name"],"amount":required};state["priority_player_id"]=player["id"]
        _log(state,f"{creature['name']} connived {amount}. {player['name']} must discard {required} card{'s' if required!=1 else ''}.");return
    state["pending_connive"]=None;state["pending_connive_queue"]=[]


def _connive(state:dict,player:dict,creature:dict,amount:int=1)->None:
    state.setdefault("pending_connive_queue",[]).append({"player_id":player["id"],"creature_id":creature["instance_id"],"amount":max(1,amount)})
    if not state.get("pending_connive"):_continue_connive(state)


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


def _queue_storm_trigger(state:dict,player:dict,card:dict,stack_item:dict)->None:
    rules=(card.get("oracle_text") or "").split("(",1)[0]
    if not re.search(r"(?:^|\n)Storm\b",rules,re.IGNORECASE) and not (_has_keyword(card,"Storm") and not re.search(r"\b(?:Gravestorm|Channelstorm)\b",rules,re.IGNORECASE)):return
    count=max(0,player.get("spells_cast_this_turn",0)-1);ability={"name":f"{card['name']} — Storm","oracle_text":"Copy this spell for each spell cast before it this turn.","source_type_line":card.get("type_line",""),"source_mana_cost":card.get("mana_cost",""),"type_line":"Ability","mana_cost":""}
    state["stack"].append({"id":_id(),"kind":"storm_trigger","card":ability,"controller_id":player["id"],"target_id":None,"source_id":card["instance_id"],"storm_count":count,"copy_item":deepcopy(stack_item)});_log(state,f"{card['name']}'s storm ability triggered with storm count {count}.")


_FACE_DOWN_KEYS=("name","image_url","type_line","oracle_text","mana_cost","mana_value","power","toughness","loyalty","keywords","colors")


def _face_down_ability(card:dict)->dict|None:
    match=re.search(r"(?:^|\n)(Morph|Megamorph|Disguise)\s*[—-]?\s*([^(.\n]+)",card.get("oracle_text") or "",re.IGNORECASE)
    if not match:return None
    cost_text=match.group(2).strip();mana=re.fullmatch(r"(?:\{[^}]+\})+",cost_text)
    ability={"mechanic":match.group(1).casefold(),"mana_cost":mana.group(0).upper() if mana else "","cost_text":cost_text}
    if mana:return ability
    life=re.fullmatch(r"pay (\d+) life",cost_text,re.IGNORECASE)
    if life:return {**ability,"cost_kind":"life","amount":int(life.group(1))}
    reveal=re.fullmatch(r"reveal an? (white|blue|black|red|green) card in your hand",cost_text,re.IGNORECASE)
    if reveal:return {**ability,"cost_kind":"reveal","amount":1,"filter":reveal.group(1).casefold()}
    discard=re.fullmatch(r"discard an?(?: ([A-Za-z]+))? card",cost_text,re.IGNORECASE)
    if discard:return {**ability,"cost_kind":"discard","amount":1,"filter":discard.group(1).casefold() if discard.group(1) else "card"}
    returning=re.fullmatch(r"return (a|one|two|three|\d+) ([A-Za-z]+)(?:s)? you control to (?:its|their) owner's hand",cost_text,re.IGNORECASE)
    sacrifice=re.fullmatch(r"sacrifice (another|a|one|two|three|\d+) ([A-Za-z]+)(?:s)?",cost_text,re.IGNORECASE)
    words={"a":1,"one":1,"two":2,"three":3,"another":1}
    if returning:return {**ability,"cost_kind":"return","amount":words.get(returning.group(1).casefold(),int(returning.group(1)) if returning.group(1).isdigit() else 1),"filter":returning.group(2).casefold().removesuffix("s")}
    if sacrifice:return {**ability,"cost_kind":"sacrifice","amount":words.get(sacrifice.group(1).casefold(),int(sacrifice.group(1)) if sacrifice.group(1).isdigit() else 1),"filter":sacrifice.group(2).casefold().removesuffix("s"),"exclude_source":sacrifice.group(1).casefold()=="another"}
    return ability


def _make_face_down(card:dict,controller:dict,cloaked:bool=False,ability:dict|None=None)->dict:
    values={key:deepcopy(card.get(key)) for key in _FACE_DOWN_KEYS}
    if ability:values.update({"turn_up_cost":ability["mana_cost"],"turn_up_ability":deepcopy(ability),"face_down_mechanic":ability["mechanic"]})
    card["face_down_values"]=values;card["face_down"]=True;card["cloaked"]=cloaked;card["disguised"]=bool(ability and ability["mechanic"]=="disguise")
    card.update({"name":"Face-Down Creature","image_url":None,"type_line":"Creature","oracle_text":"","mana_cost":"","mana_value":0,"power":"2","toughness":"2","loyalty":None,"keywords":[],"colors":[]});card["controller_id"]=controller["id"];card["summoning_sick"]=True;card["tapped"]=False;card["damage"]=0;card["counters"]={}
    return card


def _restore_face_down_identity(card:dict)->None:
    values=card.pop("face_down_values",None)
    if not values:return
    for key in _FACE_DOWN_KEYS:
        if key in values:card[key]=values[key]
    card.pop("face_down",None);card.pop("cloaked",None);card.pop("disguised",None)


def _turn_face_up_action(player:dict,permanent:dict)->dict|None:
    values=permanent.get("face_down_values") or {};underlying_type=values.get("type_line") or ""
    if not permanent.get("face_down") or (not values.get("turn_up_ability") and "Creature" not in underlying_type):return None
    ability=values.get("turn_up_ability") or {};cost=ability.get("mana_cost") or values.get("mana_cost") or "";name=values.get("name","card")
    if cost:return {"type":"turn_face_up","card_id":permanent["instance_id"],"mana_cost":cost,"label":f"Turn {name} face up · {cost}"} if _can_pay(player,{"mana_cost":cost}) else None
    kind=ability.get("cost_kind");amount=int(ability.get("amount") or 0);filter_name=ability.get("filter","");options=[]
    if kind=="life":return {"type":"turn_face_up","card_id":permanent["instance_id"],"turn_cost_kind":"life","life_cost":amount,"label":f"Turn {name} face up · pay {amount} life"} if player["life"]>=amount else None
    if kind in {"reveal","discard"}:
        color_symbol={"white":"W","blue":"U","black":"B","red":"R","green":"G"}.get(filter_name)
        options=[card for card in player["hand"] if filter_name=="card" or (color_symbol and color_symbol in _card_colors(card)) or filter_name in card.get("type_line","").casefold()]
    elif kind in {"return","sacrifice"}:
        options=[card for card in player["battlefield"] if (not ability.get("exclude_source") or card["instance_id"]!=permanent["instance_id"]) and filter_name in card.get("type_line","").casefold()]
    if kind and len(options)>=amount:return {"type":"turn_face_up","card_id":permanent["instance_id"],"turn_cost_kind":kind,"cost_kind":kind,"cost_amount":amount,"cost_options":[card["instance_id"] for card in options],"label":f"Turn {name} face up · {ability.get('cost_text','special cost')}"}
    return None


def _manifest_card(state:dict,controller:dict,card:dict,cloaked:bool=False,origin:str="library")->dict:
    _make_face_down(card,controller,cloaked)
    _enter_battlefield(state,controller,[card],origin);_log(state,f"{controller['name']} put a card onto the battlefield face down{' with cloak' if cloaked else ''}.");return card


def _turn_face_up(state:dict,player:dict,card:dict)->None:
    values=deepcopy(card.get("face_down_values"))
    if not values:return
    mechanic=values.get("face_down_mechanic")
    _restore_face_down_identity(card)
    if mechanic=="megamorph":_add_counters(state,card,"+1/+1",1,player["id"],"megamorph")
    _log(state,f"{player['name']} turned {card['name']} face up.");_queue_triggers(state,"turned_face_up",card,player)


def _start_manifest_dread(state:dict,player:dict,source_name:str,repeats:int=1)->None:
    ids=[card["instance_id"] for card in reversed(player["library"][-2:])]
    if not ids:return
    if len(ids)==1:
        card=player["library"].pop();_manifest_card(state,player,card,False,"library");return
    state["pending_manifest"]={"player_id":player["id"],"source_name":source_name,"card_ids":ids,"repeats":repeats};state["priority_player_id"]=player["id"]


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


def _city_blessing_rules_card(card:dict,blessed:bool)->dict:
    if not blessed:return card
    text=card.get("oracle_text") or ""
    text=re.sub(r"Draw two cards\.\s*If you have the city's blessing, draw three cards instead\.","Draw three cards.",text,flags=re.IGNORECASE)
    text=re.sub(r"Creatures you control get \+1/\+1 until end of turn\.\s*If you have the city's blessing, those creatures get \+2/\+2 until end of turn instead\.","Creatures you control get +2/+2 until end of turn.",text,flags=re.IGNORECASE)
    text=re.sub(r"All creatures get -2/-2 until end of turn\.\s*If you have the city's blessing, instead only creatures your opponents control get -2/-2 until end of turn\.","Creatures your opponents control get -2/-2 until end of turn.",text,flags=re.IGNORECASE)
    text=re.sub(r"Each opponent sacrifices a creature of their choice\.\s*If you have the city's blessing, instead each opponent sacrifices half the creatures they control of their choice, rounded up\.","Each opponent sacrifices half the creatures they control, rounded up.",text,flags=re.IGNORECASE)
    text=re.sub(r"Each player draws a card\.\s*If you have the city's blessing, instead only you draw a card\.","Draw a card.",text,flags=re.IGNORECASE)
    text=re.sub(r"put a \+1/\+1 counter on each creature you control\.\s*If you have the city's blessing, put two \+1/\+1 counters on each creature you control instead\.","put two +1/+1 counters on each creature you control.",text,flags=re.IGNORECASE)
    return {**card,"oracle_text":text}


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
    return {"id": player_id, "name": name, "is_bot": is_bot, "format": format_name, "life": 40 if is_commander else 20, "poison": 0,"energy":0,"energy_paid_this_turn":0,"firebending_mana":0,"bent_this_turn":[],"undercity_rooms":[],"city_blessing":False, "library": library, "hand": [], "battlefield": [], "graveyard": [], "exile": [], "command": command, "commander_casts": 0, "commander_damage": {}, "commander_damage_names": {}, "land_plays_remaining": 1,"lands_played_this_turn":0, "kept_hand": False, "mulligans": 0, "lost": False}


def new_game(player_deck: list[dict], opponent_deck: list[dict], play_first: bool = True, opponent_is_bot: bool = True, player_format: str = "", opponent_format: str = "") -> dict:
    human_id, bot_id = "player", "bot"
    players = [_new_player(human_id, "You", player_deck, False, player_format), _new_player(bot_id, "Bot" if opponent_is_bot else "Guest", opponent_deck, opponent_is_bot, opponent_format)]
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "beginning_draw_pending":True,"first_turn_draw_skipped":False,"active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id,"monarch_id":None,"initiative_id":None, "players": players, "stack": [], "combat": {"attackers": [], "attackers_declared":False,"blocks": {}, "attack_targets": {},"block_orders":{},"damage_pending":False,"damage_step":None,"first_strike_damage_ids":[],"block_triggers_pending":False}, "consecutive_passes": 0, "pending_phase_advance": False, "pending_discard": None, "pending_mulligan_bottom": None, "pending_sacrifice": None, "pending_legendary": None,"pending_commander_zone":[],"pending_library_search":None,"pending_scry":None,"pending_damage_order":None,"pending_ward":None,"pending_blight":None,"pending_proliferate":None,"pending_amass":None,"pending_populate":None,"pending_bolster":None,"pending_discovery":None,"pending_madness":None,"pending_manifest":None,"pending_transform":None,"pending_dungeon":None,"pending_trigger_targets":[], "log": []}
    state["pending_explore"]=None;state["pending_explore_queue"]=[]
    state["pending_connive"]=None;state["pending_connive_queue"]=[]
    state["day_night"]=None
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
            for card in player["exile"]:
                if card.get("foretold"):
                    concealed={key:card.get(key) for key in ("instance_id","owner_id","controller_id","foretold","foretold_turn")};card.clear();card.update({**concealed,"scryfall_id":"foretold","name":"Foretold card","image_url":None,"type_line":"Face-down card","oracle_text":"","mana_cost":"","mana_value":0,"keywords":[],"power":None,"toughness":None,"tapped":False,"damage":0,"counters":{}})
            for card in player["battlefield"]:
                if card.get("face_down"):card.pop("face_down_values",None);card.pop("disguised",None)
    for item in visible.get("stack",[]):
        if item.get("controller_id")!=viewer_id and item.get("card",{}).get("face_down"):
            item["card"].pop("face_down_values",None);item["card"].pop("disguised",None)
    pending_search=visible.get("pending_library_search")
    if pending_search and pending_search.get("player_id")!=viewer_id:pending_search["card_ids"]=[]
    pending_dungeon=visible.get("pending_dungeon")
    if pending_dungeon and pending_dungeon.get("player_id")!=viewer_id:
        pending_dungeon.pop("cards",None);pending_dungeon["card_ids"]=[];pending_dungeon.pop("top_ids",None)
    return visible


def _target_kind(card: dict) -> str | None:
    text = (card.get("oracle_text") or "").casefold()
    type_line=card.get("type_line","").casefold()
    if card.get("soulshift_value") is not None:return "graveyard_card"
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
    if re.search(r"target player discards?",text):return "player"
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


def _targets(state: dict, caster_id: str, card: dict, ignore_target_protection:bool=False) -> list[dict]:
    kind = _target_kind(card)
    if not kind: return []
    text = (card.get("oracle_text") or "").casefold()
    own_target_only=bool(re.search(r"(?:target|enchant)[^.\n]*\byou control\b",text));opponent_target_only=bool(re.search(r"(?:target|enchant)[^.\n]*\b(?:you (?:do not|don't) control|(?:an opponent|opponents?) controls?)\b",text))
    targets = []
    if kind in {"spell","ability","stack"}:
        def allowed(item:dict)->bool:
            is_spell=item.get("kind","spell")=="spell"
            return kind=="stack" or (kind=="spell" and is_spell) or (kind=="ability" and not is_spell)
        return [{"id":item["id"],"name":item["card"]["name"],"kind":"spell" if item.get("kind","spell")=="spell" else "ability","controller_id":item["controller_id"]} for item in state["stack"] if allowed(item) and not ("you don't control" in text and item["controller_id"]==caster_id)]
    if kind == "creature_or_spell":
        targets=[{"id":item["id"],"name":item["card"]["name"],"kind":"spell","controller_id":item["controller_id"]} for item in state["stack"]]
    if kind in {"graveyard_creature","graveyard_card"}:
        own_only="your graveyard" in text
        soulshift=card.get("soulshift_value")
        instant_or_sorcery="instant or sorcery" in text
        return [{"id":graveyard_card["instance_id"],"name":graveyard_card["name"],"kind":"card","controller_id":owner["id"]} for owner in state["players"] if not own_only or owner["id"]==caster_id for graveyard_card in owner["graveyard"] if (kind=="graveyard_card" or "Creature" in graveyard_card.get("type_line","")) and (not instant_or_sorcery or any(kind_name in graveyard_card.get("type_line","") for kind_name in ("Instant","Sorcery"))) and (soulshift is None or (re.search(r"\bSpirit\b",graveyard_card.get("type_line",""),re.IGNORECASE) and float(graveyard_card.get("mana_value") or 0)<=float(soulshift)))]
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
                if "noncreature artifact" in text and "Creature" in permanent.get("type_line", ""): continue
                if "creature without flying" in text and _has_keyword(permanent,"Flying"):continue
                if not ignore_target_protection and (_has_keyword(permanent,"Shroud") or (player["id"] != caster_id and (_has_keyword(permanent,"Hexproof") or permanent.get("hexproof_until_turn",0)>=state["turn"]))): continue
                if not ignore_target_protection and _protected_from(permanent,card): continue
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
    _restore_face_down_identity(card)
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
    adjusts_land_plays="you may play an additional land on each of your turns" in (card.get("oracle_text") or "").casefold()
    if adjusts_land_plays:_ensure_land_play_tracking(current);_ensure_land_play_tracking(new_controller)
    current["battlefield"].remove(card);new_controller["battlefield"].append(card);card["controller_id"]=new_controller["id"];card["summoning_sick"]=True;card.pop("suspend_haste",None)
    if adjusts_land_plays:_refresh_land_plays(state,current);_refresh_land_plays(state,new_controller)
    _sync_city_blessing(state);_check_ascend(state,new_controller)
    if _echo_cost(card):card["echo_due_controller_id"]=new_controller["id"]
    _remove_from_combat(state,card["instance_id"])


def _multiplayer(state: dict) -> bool:
    return not any(player.get("is_bot") for player in state["players"])


def _pending_decision(state:dict)->bool:
    if state.get("pending_explore"):return True
    if state.get("pending_connive"):return True
    return bool(state.get("pending_tilonalli") or state.get("pending_creature_type") or state.get("pending_discard") or state.get("pending_sacrifice") or state.get("pending_legendary") or state.get("pending_commander_zone") or state.get("pending_library_search") or state.get("pending_scry") or state.get("pending_damage_order") or state.get("pending_ward") or state.get("pending_blight") or state.get("pending_proliferate") or state.get("pending_amass") or state.get("pending_populate") or state.get("pending_bolster") or state.get("pending_discovery") or state.get("pending_madness") or state.get("pending_rebound") or state.get("pending_manifest") or state.get("pending_transform") or state.get("pending_dungeon") or state.get("pending_trigger_targets"))


def _split_second_on_stack(state:dict)->bool:
    return any(item.get("kind","spell")=="spell" and _has_keyword(item.get("card",{}),"Split second") for item in state.get("stack",[]))


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
    pending_tilonalli=state.get("pending_tilonalli")
    if pending_tilonalli:
        if pending_tilonalli["player_id"]!=player_id:return []
        actions=[{"type":"decline_tilonalli","source_name":pending_tilonalli["source_name"],"label":"Create no Elementals"},{"type":"concede"}]
        maximum=_maximum_x(player,{"mana_cost":"{X}{R}"})
        if _can_pay(player,{"mana_cost":"{R}"}):actions.insert(0,{"type":"pay_tilonalli","source_name":pending_tilonalli["source_name"],"x_min":0,"x_max":maximum,"label":f"Pay {{X}}{{R}} · create up to {maximum} Elementals"})
        return actions
    pending_types=state.get("pending_creature_type") or []
    if pending_types:
        pending=pending_types[0]
        if pending["player_id"]!=player_id:return []
        return [{"type":"choose_creature_type","card_id":pending["card_id"],"card_name":pending["card_name"],"suggested_types":_creature_subtypes(player),"label":f"Choose a creature type for {pending['card_name']}"},{"type":"concede"}]
    pending_cumulative=state.get("pending_cumulative_upkeep") or []
    if pending_cumulative:
        pending=pending_cumulative[0]
        if pending["player_id"]!=player_id:return []
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending["card_id"]),None);cost=_cumulative_upkeep_cost(permanent or {});common={"card_id":pending["card_id"],"card_name":pending["card_name"],"age":pending["age"]};actions=[{"type":"sacrifice_cumulative_upkeep","label":f"Sacrifice {pending['card_name']}",**common},{"type":"concede"}]
        if not permanent or not cost:return actions
        age=pending["age"]
        if cost["kind"]=="mana":
            for unit in cost["mana_options"]:
                total=unit*age
                if "{S}" in total:
                    options=[card for card in player["battlefield"] if not card.get("tapped") and "Snow" in card.get("type_line","")]
                    if len(options)>=age:actions.insert(0,{"type":"pay_cumulative_upkeep","mana_cost":"","cost_kind":"snow","cost_amount":age,"cost_options":[card["instance_id"] for card in options],"label":f"Pay {total} from snow sources",**common})
                elif _can_pay(player,{"mana_cost":total}):actions.insert(0,{"type":"pay_cumulative_upkeep","mana_cost":total,"cost_kind":"mana","cost_amount":0,"label":f"Pay {total}",**common})
        elif cost["kind"] in {"life","mana_life"}:
            life=cost["amount"]*age;mana=(cost["mana_options"][0]*age if cost["mana_options"] else "")
            if player["life"]>=life and (not mana or _can_pay(player,{"mana_cost":mana})):actions.insert(0,{"type":"pay_cumulative_upkeep","mana_cost":mana,"life_cost":life,"cost_kind":cost["kind"],"cost_amount":0,"label":f"Pay {mana}{f' and {life} life' if life else ''}",**common})
        elif cost["kind"] in {"discard","sacrifice_creature","sacrifice_land"}:
            options=player["hand"] if cost["kind"]=="discard" else [card for card in player["battlefield"] if ("Creature" if cost["kind"].endswith("creature") else "Land") in card.get("type_line","") and card is not permanent];amount=cost["amount"]*age
            if len(options)>=amount:actions.insert(0,{"type":"pay_cumulative_upkeep","cost_kind":cost["kind"],"cost_amount":amount,"cost_options":[card["instance_id"] for card in options],"label":f"Pay cumulative upkeep ×{age}",**common})
        else:
            effect=cost["effect"].casefold();other=opponent(state,player_id)
            if "put two cards from a single graveyard" in effect:
                for grave_owner in state["players"]:
                    amount=2*age
                    if len(grave_owner["graveyard"])>=amount:actions.insert(0,{"type":"pay_cumulative_upkeep","cost_kind":"graveyard_bottom","cost_amount":amount,"cost_options":[card["instance_id"] for card in grave_owner["graveyard"]],"upkeep_zone_owner":grave_owner["id"],"upkeep_effect":cost["effect"],"label":f"Bottom {amount} cards from {grave_owner['name']}'s graveyard",**common})
            elif "put a +1/+1 counter on a creature an opponent controls" in effect:
                options=[card for card in other["battlefield"] if "Creature" in card.get("type_line","")]
                if options:actions.insert(0,{"type":"pay_cumulative_upkeep","cost_kind":"opponent_counter","cost_amount":1,"cost_options":[card["instance_id"] for card in options],"upkeep_effect":cost["effect"],"label":f"Put {age} +1/+1 counter(s) on an opposing creature",**common})
            elif "gain control of a land you don't control" in effect:
                options=[card for card in other["battlefield"] if "Land" in card.get("type_line","")]
                if len(options)>=age:actions.insert(0,{"type":"pay_cumulative_upkeep","cost_kind":"gain_lands","cost_amount":age,"cost_options":[card["instance_id"] for card in options],"upkeep_effect":cost["effect"],"label":f"Gain control of {age} opposing land(s)",**common})
            else:actions.insert(0,{"type":"pay_cumulative_upkeep","cost_kind":"effect","cost_amount":age,"upkeep_effect":cost["effect"],"label":f"{cost['effect']} ×{age}",**common})
        return actions
    pending_echo=state.get("pending_echo") or []
    if pending_echo:
        pending=pending_echo[0]
        if pending["player_id"]!=player_id:return []
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending["card_id"]),None);cost=_echo_cost(permanent or {});actions=[{"type":"sacrifice_echo","card_id":pending["card_id"],"card_name":pending["card_name"],"label":f"Sacrifice {pending['card_name']}"},{"type":"concede"}]
        if permanent and cost:
            options=[card for card in player["hand"]] if cost["kind"]=="discard" else [card for card in player["battlefield"] if "Land" in card.get("type_line","")] if cost["kind"]=="sacrifice" else []
            if cost["kind"]!="mana" and len(options)>=cost["amount"] or cost["kind"]=="mana" and _can_pay(player,{"mana_cost":cost["mana_cost"]}):actions.insert(0,{"type":"pay_echo","card_id":permanent["instance_id"],"card_name":permanent["name"],"mana_cost":cost["mana_cost"],"cost_kind":cost["kind"],"cost_amount":cost["amount"],"cost_options":[card["instance_id"] for card in options],"label":f"Pay echo {cost['mana_cost'] or cost['kind']}"})
        return actions
    pending_escape_counter=state.get("pending_escape_counter")
    if pending_escape_counter:
        if pending_escape_counter["player_id"]!=player_id:return []
        common={"card_id":pending_escape_counter["card_id"],"card_name":pending_escape_counter["card_name"]};return [{"type":"choose_escape_counter","counter_name":"+1/+1","label":"Choose a +1/+1 counter",**common},{"type":"choose_escape_counter","counter_name":"flying","label":"Choose a flying counter",**common},{"type":"concede"}]
    pending_rebound=state.get("pending_rebound")
    if pending_rebound:
        if pending_rebound["player_id"]!=player_id:return []
        card=next((candidate for candidate in player["exile"] if candidate["instance_id"]==pending_rebound["card_id"]),None);actions=[{"type":"decline_rebound","card_id":pending_rebound["card_id"],"card_name":pending_rebound["card_name"],"label":f"Leave {pending_rebound['card_name']} in exile"},{"type":"concede"}]
        if not card:return actions
        action={"type":"cast","card_id":card["instance_id"],"card_name":card["name"],"source":"rebound","label":f"Cast {card['name']} from Rebound without paying its mana cost"};modal_spec=_modal_spec(card)
        if modal_spec:
            modes=[]
            for option in modal_spec["options"]:
                targets=_targets(state,player_id,_spell_targeting_card({**card,"oracle_text":option["label"]}))
                if _target_kind({**card,"oracle_text":option["label"]}) and not targets:continue
                modes.append({**option,"targets":targets})
            if len(modes)>=modal_spec["min_modes"]:action.update({"mode_count":modal_spec["min_modes"],"mode_min":modal_spec["min_modes"],"mode_max":min(modal_spec["max_modes"],len(modes)),"mode_repeatable":modal_spec["repeatable"],"mode_distinct_targets":modal_spec["distinct_targets"],"modes":modes})
            else:action=None
        else:
            targeting_card=_spell_targeting_card(card);targets=_targets(state,player_id,targeting_card)
            if _target_kind(targeting_card) and not targets:action=None
            elif targets:action["targets"]=targets
        if action:actions.insert(0,action)
        return actions
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
    pending_populate=state.get("pending_populate")
    if pending_populate:
        if pending_populate["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["battlefield"]};targets=[{"id":card_id,"name":cards_by_id[card_id]["name"],"kind":"permanent","controller_id":player_id} for card_id in pending_populate["card_ids"] if card_id in cards_by_id and cards_by_id[card_id].get("token") and "Creature" in cards_by_id[card_id].get("type_line","")]
        return ([{"type":"choose_populate_token","targets":targets,"source_name":pending_populate["source_name"],"repeats":pending_populate["repeats"]}] if targets else [{"type":"skip_populate","source_name":pending_populate["source_name"]}])+[{"type":"concede"}]
    pending_bolster=state.get("pending_bolster")
    if pending_bolster:
        if pending_bolster["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["battlefield"]};targets=[{"id":card_id,"name":cards_by_id[card_id]["name"],"kind":"permanent","controller_id":player_id} for card_id in pending_bolster["card_ids"] if card_id in cards_by_id]
        return [{"type":"choose_bolster_creature","targets":targets,"source_name":pending_bolster["source_name"],"amount":pending_bolster["amount"]},{"type":"concede"}]
    pending_explore=state.get("pending_explore")
    if pending_explore:
        if pending_explore["player_id"]!=player_id:return []
        common={"card_id":pending_explore["card_id"],"card":pending_explore["card"],"creature_name":pending_explore["creature_name"]}
        return [{"type":"keep_explored",**common},{"type":"graveyard_explored",**common},{"type":"concede"}]
    pending_connive=state.get("pending_connive")
    if pending_connive:
        if pending_connive["player_id"]!=player_id:return []
        return [{"type":"discard_connive","card_ids":[card["instance_id"] for card in player["hand"]],"amount":pending_connive["amount"],"creature_name":pending_connive["creature_name"]},{"type":"concede"}]
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
    pending_madness=state.get("pending_madness")
    if pending_madness:
        if pending_madness["player_id"]!=player_id:return []
        candidate=next((card for card in player["exile"] if card["instance_id"]==pending_madness["card_id"]),None);ability=_madness_ability(candidate or {})
        if not candidate or not ability:return [{"type":"decline_madness","card_id":pending_madness["card_id"]},{"type":"concede"}]
        cost_card={**candidate,"mana_cost":ability["mana_cost"]};targets=_targets(state,player_id,_spell_targeting_card(candidate));required=bool(_target_kind(_spell_targeting_card(candidate)));actions=[]
        if player["life"]>=ability["life_cost"] and (not required or targets):
            life_label=f" and pay {ability['life_cost']} life" if ability["life_cost"] else "";action={"type":"cast_madness","card_id":candidate["instance_id"],"card":candidate,"mana_cost":ability["mana_cost"],"life_cost":ability["life_cost"],"label":f"Cast {candidate['name']} for its madness cost {ability['mana_cost']}{life_label}"}
            if _has_x_cost(cost_card):
                maximum=_maximum_x(player,cost_card)
                if _can_pay(player,cost_card,x_value=0):action.update({"x_min":0,"x_max":maximum})
                else:action=None
            elif not _can_pay(player,cost_card):action=None
            if action and targets:action["targets"]=targets
            if action:actions.append(action)
        actions.extend([{"type":"decline_madness","card_id":candidate["instance_id"],"card":candidate,"label":f"Put {candidate['name']} into your graveyard"},{"type":"concede"}]);return actions
    pending_manifest=state.get("pending_manifest")
    if pending_manifest:
        if pending_manifest["player_id"]!=player_id:return []
        cards_by_id={card["instance_id"]:card for card in player["library"]};cards=[cards_by_id[card_id] for card_id in pending_manifest["card_ids"] if card_id in cards_by_id]
        return [{"type":"choose_manifest_dread","card_ids":pending_manifest["card_ids"],"cards":cards,"source_name":pending_manifest["source_name"]},{"type":"concede"}]
    pending_transform=state.get("pending_transform")
    if pending_transform:
        if pending_transform["player_id"]!=player_id:return []
        return [{"type":"accept_transform","source_name":pending_transform["source_name"]},{"type":"decline_transform","source_name":pending_transform["source_name"]},{"type":"concede"}]
    pending_dungeon=state.get("pending_dungeon")
    if pending_dungeon:
        if pending_dungeon["player_id"]!=player_id:return []
        if pending_dungeon["kind"]=="room":return [{"type":"choose_dungeon_room","room":room,"label":room} for room in pending_dungeon["options"]]+[{"type":"concede"}]
        if pending_dungeon["kind"]=="target":return [{"type":"choose_dungeon_target","room":pending_dungeon["room"],"targets":pending_dungeon["targets"],"label":f"{pending_dungeon['room']} — choose a creature"},{"type":"concede"}]
        return [{"type":"choose_dungeon_card","room":pending_dungeon["room"],"card_ids":pending_dungeon["card_ids"],"cards":pending_dungeon["cards"],"label":"Choose a creature from the top ten cards"},{"type":"skip_dungeon_card","room":pending_dungeon["room"]},{"type":"concede"}]
    pending_triggers=state.get("pending_trigger_targets") or []
    if pending_triggers:
        pending=pending_triggers[0]
        if pending["controller_id"]!=player_id:return []
        if pending.get("target_steps"):return [{"type":"choose_trigger_targets","target_steps":pending["target_steps"],"label":pending["card"]["oracle_text"],"source_name":pending["source_name"]},{"type":"concede"}]
        targets=_targets(state,player_id,pending["card"])
        actions=[{"type":"choose_trigger_target","targets":targets,"label":pending["card"]["oracle_text"],"source_name":pending["source_name"]}] if targets else []
        if pending.get("optional") or not targets:actions.append({"type":"skip_trigger","source_name":pending["source_name"]})
        return actions+[{"type":"concede"}]
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
    for permanent in player["battlefield"]:
        turn_action=_turn_face_up_action(player,permanent)
        if turn_action:actions.append(turn_action)
    main = state["phase"] in {"precombat_main", "postcombat_main"}
    if active and main and not state["stack"]:
        if player["land_plays_remaining"]:
            actions.extend({"type": "play_land", "card_id": card["instance_id"]} for card in player["hand"] if "Land" in card.get("type_line", ""))
        if _can_pay(player,{"mana_cost":"{3}"}):
            for hand_card in player["hand"]:
                face_down=_face_down_ability(hand_card)
                if face_down:actions.append({"type":"cast_face_down","card_id":hand_card["instance_id"],"mana_cost":"{3}","label":f"Cast {hand_card['name']} face down for {{3}} · {face_down['mechanic'].title()} {face_down['mana_cost'] or face_down['cost_text']}"})
        for grave_card in player["graveyard"]:
            unearth=_unearth_ability(grave_card)
            if unearth and player.get("energy",0)>=unearth["energy_cost"] and _can_pay(player,{"mana_cost":unearth["mana_cost"]}):
                cost_label=unearth["mana_cost"] or f"{unearth['energy_cost']} energy";actions.append({"type":"unearth","card_id":grave_card["instance_id"],"source":"graveyard","mana_cost":unearth["mana_cost"],"energy_cost":unearth["energy_cost"],"label":f"Unearth {grave_card['name']} · {cost_label}"})
        plot_reduction=_plot_reduction(player)
        for hand_card in player["hand"]:
            plot_cost=_plot_cost(hand_card)
            if plot_cost and _can_pay(player,{"mana_cost":plot_cost},-plot_reduction):
                reduction_label=f" · reduced by {plot_reduction}" if plot_reduction else "";actions.append({"type":"plot","card_id":hand_card["instance_id"],"source":"hand","mana_cost":plot_cost,"plot_reduction":plot_reduction,"label":f"Plot {hand_card['name']} · {plot_cost}{reduction_label}"})
    mutate_sources=[(card,"mutate_hand") for card in player["hand"] if _mutate_cost(card)]
    mutate_sources.extend((card,"mutate_command") for card in player.get("command",[]) if _mutate_cost(card))
    mutate_sources.extend((card,"mutate_graveyard") for card in player["graveyard"] if _mutate_cost(card) and card.get("name")=="Brokkos, Apex of Forever")
    mutate_targets=[{"id":candidate["instance_id"],"name":candidate["name"],"kind":"permanent","controller_id":candidate.get("controller_id",player_id)} for owner in state["players"] for candidate in owner["battlefield"] if candidate.get("owner_id")==player_id and "Creature" in candidate.get("type_line","") and not re.search(r"\bHuman\b",candidate.get("type_line",""),re.IGNORECASE)]
    for mutate_card,mutate_source in mutate_sources:
        mutate_cost=_mutate_cost(mutate_card);instant_speed=_has_keyword(mutate_card,"Flash");mutate_tax=_commander_tax(player,mutate_card) if mutate_source=="mutate_command" else 0
        if mutate_targets and ((active and main and not state["stack"]) or instant_speed) and _can_pay(player,{**mutate_card,"mana_cost":mutate_cost},mutate_tax):
            for position in ("over","under"):
                actions.append({"type":"cast","card_id":mutate_card["instance_id"],"source":mutate_source,"mutating":True,"mutate_position":position,"mana_cost":mutate_cost,"commander_tax":mutate_tax,"targets":mutate_targets,"label":f"Mutate {mutate_card['name']} {position} · {mutate_cost}{f' + commander tax {{{mutate_tax}}}' if mutate_tax else ''}"})
    for evoke_card in player["hand"]:
        evoke=_evoke_ability(evoke_card)
        if not evoke:continue
        instant_speed=_has_keyword(evoke_card,"Flash");options=[candidate for candidate in player["hand"] if candidate is not evoke_card and evoke["exile_color"] in _card_colors(candidate)] if evoke["exile_color"] else []
        payable=(evoke["celebrate"] or bool(options)) if not evoke["mana_cost"] else _can_pay(player,{**evoke_card,"mana_cost":evoke["mana_cost"]})
        if payable and ((active and main and not state["stack"]) or instant_speed):
            label="Celebrate an opponent" if evoke["celebrate"] else f"Exile a {evoke['exile_color']} card" if evoke["exile_color"] else evoke["mana_cost"]
            actions.append({"type":"cast","card_id":evoke_card["instance_id"],"source":"evoke","evoked":True,"mana_cost":evoke["mana_cost"],"evoke_celebrate":evoke["celebrate"],"cost_kind":"evoke_exile" if options else None,"cost_amount":1 if options else 0,"cost_options":[candidate["instance_id"] for candidate in options],"label":f"Evoke {evoke_card['name']} · {label}"})
    dash_reduction=_dash_reduction(player)
    for dash_card,dash_source in [*((card,"dash_hand") for card in player["hand"]),*((card,"dash_command") for card in player.get("command",[]))]:
        dash_cost=_dash_cost(dash_card)
        if not dash_cost:continue
        tax=_commander_tax(player,dash_card) if dash_source=="dash_command" else 0
        if active and main and not state["stack"] and _can_pay(player,{**dash_card,"mana_cost":dash_cost},tax-dash_reduction):actions.append({"type":"cast","card_id":dash_card["instance_id"],"source":dash_source,"dashed":True,"mana_cost":dash_cost,"dash_reduction":dash_reduction,"commander_tax":tax,"label":f"Dash {dash_card['name']} · {dash_cost}{f' · reduced by {dash_reduction}' if dash_reduction else ''}"})
    castable = [(card, "hand") for card in player["hand"]]
    castable.extend((card, "command") for card in player.get("command", []))
    castable.extend((card,"flashback") for card in player["graveyard"] if _flashback_ability(card))
    castable.extend((card,"escape") for card in player["graveyard"] if _escape_ability(card))
    castable.extend((card,"airbend") for card in player["exile"] if card.get("airbent"))
    castable.extend((card,"suspend") for card in player["exile"] if card.get("suspended_ready"))
    castable.extend((card,"foretell") for card in player["exile"] if card.get("foretold") and state["turn"]>card.get("foretold_turn",state["turn"]))
    castable.extend((card,"plot") for card in player["exile"] if card.get("plotted") and state["turn"]>card.get("plotted_turn",state["turn"]))
    for card, source in castable:
        card_action_start=len(actions)
        flashback=_flashback_ability(card) if source=="flashback" else None;escape=_escape_ability(card) if source=="escape" else None;foretell_cost=_foretell_cost(card) if source=="foretell" else None;cost_card={**card,"mana_cost":foretell_cost} if foretell_cost else {**card,"mana_cost":"{0}"} if source in {"suspend","plot"} else {**card,"mana_cost":"{2}"} if source=="airbend" else {**card,"mana_cost":flashback["mana_cost"]} if flashback else {**card,"mana_cost":escape["mana_cost"]} if escape else card;kicker_cost=_kicker_cost(card)
        instant_speed = source!="plot" and ("Instant" in card.get("type_line", "") or _has_keyword(card, "Flash"))
        total_tax=_commander_tax(player,card) if source=="command" else 0;affinity_reduction=_affinity_reduction(player,card);generic_adjustment=total_tax-affinity_reduction
        behold_options=[candidate for zone in (player["hand"],player["battlefield"]) for candidate in zone if flashback and flashback["behold_type"] in candidate.get("type_line","").casefold()]
        escape_options=[candidate for candidate in player["graveyard"] if candidate is not card] if escape else []
        escape_lands=[candidate for candidate in player["battlefield"] if escape and "Land" in candidate.get("type_line","")]
        waterbend_symbol=_spell_waterbend_symbol(card);waterbend_base_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{f'{{{generic_adjustment}}}' if generic_adjustment>0 else ''}"};waterbend_x_max=_maximum_waterbend_x(player,waterbend_base_card) if waterbend_symbol=="X" else None;waterbend_amount=int(waterbend_symbol) if waterbend_symbol and waterbend_symbol.isdigit() else waterbend_x_max or 0
        waterbend_combinations=_waterbend_combinations(player,waterbend_base_card,waterbend_amount) if waterbend_symbol else []
        normal_payable=bool(waterbend_combinations) if waterbend_symbol else _can_pay(player,cost_card,generic_adjustment)
        delve_options=list(player["graveyard"]) if _has_keyword(card,"Delve") and source in {"hand","command"} else []
        delve_possible=bool(delve_options) and any(_can_pay(player,cost_card,generic_adjustment-count,x_value=0) for count in range(1,len(delve_options)+1))
        convoke_combinations=[] if waterbend_symbol or _has_x_cost(cost_card) or (flashback and flashback["behold_amount"]) or not _has_convoke(card) else _convoke_combinations(player,cost_card,generic_adjustment);convoke_min=len(convoke_combinations[0]) if convoke_combinations else None
        overload_cost=_overload_cost(card) if source in {"hand","command"} else None;overload_card={**card,"mana_cost":overload_cost or ""}
        overload_timing=source in {"hand","command"} and ((active and main and not state["stack"]) or instant_speed)
        if overload_cost and overload_timing and _can_pay(player,overload_card,total_tax):
            overload_action={"type":"cast","card_id":card["instance_id"],"source":source,"overloaded":True,"mana_cost":overload_cost,"commander_tax":total_tax,"label":f"Overload {card['name']} · {overload_cost}{f' + {{2}}×{player.get('commander_casts',0)} commander tax' if total_tax else ''}"}
            if _has_x_cost(overload_card):overload_action.update({"x_min":0,"x_max":_maximum_x(player,overload_card,total_tax)})
            actions.append(overload_action)
        if "Land" in card.get("type_line", "") or (source!="suspend" and not ((active and main and not state["stack"]) or instant_speed)) or (not normal_payable and convoke_min is None and not delve_possible and not (_has_keyword(card,"Delve") and _has_x_cost(cost_card) and delve_options)) or (flashback and len(behold_options)<flashback["behold_amount"]) or (escape and (len(escape_options)<escape["exile_count"] or len(escape_lands)<escape["land_count"] or len(_graveyard_card_types(escape_options))<escape["card_types_required"])): continue
        cost_label=cost_card.get("mana_cost") or "{0}";action = {"type": "cast", "card_id": card["instance_id"], "source": source, "commander_tax": total_tax,"affinity_reduction":affinity_reduction,"label":f"{'Plot cast' if source=='plot' else 'Foretell cast' if source=='foretell' else 'Suspend cast' if source=='suspend' else 'Flashback' if flashback else 'Airbend cast' if source=='airbend' else 'Cast'} {card['name']} · {'without paying its mana cost' if source in {'suspend','plot'} else cost_label}{f' + {{2}}×{player.get("commander_casts",0)} commander tax' if total_tax else ''}{f' · Affinity reduces {{1}}×{affinity_reduction}' if affinity_reduction else ''}"}
        if flashback:action.update({"flashback":True,"cost_kind":"behold" if flashback["behold_amount"] else None,"cost_amount":flashback["behold_amount"],"cost_options":[candidate["instance_id"] for candidate in behold_options]})
        if escape:
            exile_label=f"exile cards with {escape['card_types_required']}+ types among them" if escape["card_types_required"] else f"exile {escape['exile_count']} other graveyard cards";land_label=" and a land you control" if escape["land_count"] else "";action.update({"escape":True,"mana_cost":escape["mana_cost"],"cost_kind":"compound" if escape["land_count"] or escape["card_types_required"] else "escape","cost_amount":escape["exile_count"]+escape["land_count"],"cost_min_amount":1 if escape["card_types_required"] else escape["exile_count"]+escape["land_count"],"cost_max_amount":len(escape_options) if escape["card_types_required"] else escape["exile_count"]+escape["land_count"],"escape_card_types_required":escape["card_types_required"],"escape_land_count":escape["land_count"],"cost_options":[candidate["instance_id"] for candidate in [*escape_options,*escape_lands]],"label":f"Escape {card['name']} · {cost_label} · {exile_label}{land_label}"})
        if waterbend_symbol:
            options=[candidate["instance_id"] for candidate in player["battlefield"] if not candidate.get("tapped") and any(kind in candidate.get("type_line","") for kind in ("Artifact","Creature"))]
            action.update({"waterbend":True,"waterbend_amount":waterbend_amount,"cost_kind":"waterbend","cost_min_amount":min(map(len,waterbend_combinations)),"cost_max_amount":max(map(len,waterbend_combinations)),"cost_options":options,"cost_combinations":waterbend_combinations,"label":f"{action['label']} + waterbend {{{waterbend_symbol}}}"})
        if _has_x_cost(cost_card):action.update({"x_min":0,"x_max":_maximum_x(player,cost_card,generic_adjustment)})
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
        entwine=_entwine_ability(card) if modal_spec and source in {"hand","command"} else None
        if entwine and len(action.get("modes",[]))==len(modal_options):
            entwine_lands=[candidate for candidate in player["battlefield"] if "Land" in candidate.get("type_line","")]
            entwine_cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{entwine['mana_cost']}"}
            if len(entwine_lands)>=entwine["sacrifice_lands"] and _can_pay(player,entwine_cost_card,generic_adjustment):
                count=len(modal_options);entwined={**action,"entwined":True,"entwine_cost":entwine["mana_cost"],"mode_count":count,"mode_min":count,"mode_max":count,"label":f"{action['label']} + entwine {entwine['mana_cost'] or 'sacrifice lands'}"}
                if entwine["sacrifice_lands"]:entwined.update({"cost_kind":"sacrifice","cost_amount":entwine["sacrifice_lands"],"cost_options":[land["instance_id"] for land in entwine_lands]})
                actions.append(entwined)
        if delve_options:
            fixed_generic=_mana_requirements(cost_card,generic_adjustment,0)[1];x_symbols=sum(symbol.upper()=="X" for symbol in _mana_symbols(cost_card));maximum_count=min(len(delve_options),fixed_generic+99*x_symbols)
            for count in range(1,maximum_count+1):
                x_min=max(0,(count-fixed_generic+x_symbols-1)//x_symbols) if x_symbols else 0;x_max=_maximum_x(player,cost_card,generic_adjustment-count) if x_symbols else 0
                if x_symbols and x_max<x_min or not x_symbols and not _can_pay(player,cost_card,generic_adjustment-count):continue
                delve_action={**action,"delve":True,"cost_kind":"delve","cost_amount":count,"cost_options":[candidate["instance_id"] for candidate in delve_options],"label":f"{action['label']} · Delve {count}"}
                if x_symbols:delve_action.update({"x_min":x_min,"x_max":x_max})
                actions.append(delve_action)
        if convoke_min is not None:
            convoke_options=[candidate for candidate in player["battlefield"] if "Creature" in candidate.get("type_line","") and not candidate.get("tapped")]
            colored,generic=_mana_requirements(cost_card,generic_adjustment)
            actions.append({**action,"convoke":True,"cost_kind":"convoke","cost_min_amount":convoke_min,"cost_max_amount":len(colored)+generic,"cost_options":[candidate["instance_id"] for candidate in convoke_options],"cost_combinations":convoke_combinations,"label":f"{action['label']} · Convoke"})
        if kicker_cost:
            kicked_cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{kicker_cost}"}
            kicked_waterbend_base={**kicked_cost_card,"mana_cost":f"{kicked_cost_card.get('mana_cost') or ''}{f'{{{generic_adjustment}}}' if generic_adjustment>0 else ''}"};kicked_waterbend_x_max=_maximum_waterbend_x(player,kicked_waterbend_base) if waterbend_symbol=="X" else None;kicked_waterbend_amount=kicked_waterbend_x_max if waterbend_symbol=="X" else waterbend_amount;kicked_waterbend_combinations=_waterbend_combinations(player,kicked_waterbend_base,kicked_waterbend_amount or 0) if waterbend_symbol else [];kicked_normal_payable=bool(kicked_waterbend_combinations) if waterbend_symbol else _can_pay(player,kicked_cost_card,generic_adjustment);kicked_convoke_combinations=[] if waterbend_symbol or _has_x_cost(kicked_cost_card) or not _has_convoke(card) else _convoke_combinations(player,kicked_cost_card,generic_adjustment);kicked_convoke_min=len(kicked_convoke_combinations[0]) if kicked_convoke_combinations else None
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
                if _has_x_cost(kicked_cost_card):kicked.update({"x_min":0,"x_max":_maximum_x(player,kicked_cost_card,generic_adjustment)})
                if kicked_normal_payable:actions.append(kicked)
                if kicked_convoke_min is not None:
                    convoke_options=[candidate for candidate in player["battlefield"] if "Creature" in candidate.get("type_line","") and not candidate.get("tapped")];colored,generic=_mana_requirements(kicked_cost_card,generic_adjustment)
                    actions.append({**kicked,"convoke":True,"cost_kind":"convoke","cost_min_amount":kicked_convoke_min,"cost_max_amount":len(colored)+generic,"cost_options":[candidate["instance_id"] for candidate in convoke_options],"cost_combinations":kicked_convoke_combinations,"label":f"{kicked['label']} · Convoke"})
        blight_amount=_optional_blight_cost(card);blight_options=[creature["instance_id"] for creature in player["battlefield"] if "Creature" in creature.get("type_line","")]
        if blight_amount and blight_options:
            for base_action in list(actions[card_action_start:]):
                if base_action.get("cost_kind"):continue
                blighted={**base_action,"blighted":True,"blight_amount":blight_amount,"cost_kind":"blight","cost_amount":1,"cost_options":blight_options,"label":f"{base_action['label']} · Blight {blight_amount}"}
                if "if this spell's additional cost was paid, choose both instead" in (card.get("oracle_text") or "").casefold() and len(blighted.get("modes",[]))>=2:blighted["mode_min"]=blighted["mode_max"]=blighted["mode_count"]=2
                actions.append(blighted)
        buyback=_buyback_ability(card)
        if buyback:
            reduction=_buyback_reduction(player) if buyback["mana_cost"] else 0;discard_options=[candidate for candidate in player["hand"] if candidate is not card];sacrifice_options=[candidate for candidate in player["battlefield"] if "Land" in candidate.get("type_line","") and (buyback["sacrifice_filter"]!="island" or re.search(r"\bIsland\b",candidate.get("type_line","")))]
            for base_action in list(actions[card_action_start:]):
                if base_action.get("buyback") or base_action.get("blighted"):continue
                buyback_cost={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{_kicker_cost(card) or '' if base_action.get('kicked') else ''}{buyback['mana_cost']}"};buyback_adjustment=generic_adjustment-reduction
                if buyback["life_cost"]>=player["life"] or len(discard_options)<buyback["discard_count"] or len(sacrifice_options)<buyback["sacrifice_count"]:continue
                variant={**base_action,"buyback":True,"buyback_cost":buyback["mana_cost"],"buyback_reduction":reduction,"buyback_life_cost":buyback["life_cost"],"buyback_discard_random":buyback["discard_random"],"label":f"{base_action['label']} · Buyback {buyback['mana_cost'] or 'additional cost'}{f' · reduced by {reduction}' if reduction else ''}"}
                if buyback["discard_count"] and not buyback["discard_random"]:variant.update({"cost_kind":"discard","cost_amount":buyback["discard_count"],"cost_options":[candidate["instance_id"] for candidate in discard_options]})
                elif buyback["sacrifice_count"]:variant.update({"cost_kind":"sacrifice","cost_amount":buyback["sacrifice_count"],"cost_options":[candidate["instance_id"] for candidate in sacrifice_options]})
                if base_action.get("convoke"):
                    groups=[] if _has_x_cost(buyback_cost) else _convoke_combinations(player,buyback_cost,buyback_adjustment)
                    if not groups:continue
                    variant.update({"cost_min_amount":min(map(len,groups)),"cost_max_amount":max(map(len,groups)),"cost_combinations":groups})
                elif not _can_pay(player,buyback_cost,buyback_adjustment):continue
                if _has_x_cost(buyback_cost):variant.update({"x_min":0,"x_max":_maximum_x(player,buyback_cost,buyback_adjustment)})
                actions.append(variant)
        if player.get("city_blessing") and "you may put that permanent on top of its owner's library instead" in (card.get("oracle_text") or "").casefold():
            for base_action in list(actions[card_action_start:]):actions.append({**base_action,"blessing_top":True,"label":f"{base_action['label']} · put target on top"})
    for card in player["hand"]:
        for ability_index,ability in enumerate(_channel_abilities(card)):
            if ability["sorcery_only"] and not (active and main and not state["stack"]):continue
            reduction=_channel_reduction(player,ability);cost_card={**card,"mana_cost":ability["mana_cost"]}
            if not _can_pay(player,cost_card,-reduction):continue
            targeting_card=_spell_targeting_card(ability["card"]);multi_match=re.search(r"\bX target (nonlegendary )?(cards|creatures)\b",ability["effect"],re.IGNORECASE);multi_x=multi_match is not None;up_to_match=re.search(r"up to (one|two|three|\d+) target creatures( you (?:do not |don't )?control)?",ability["effect"],re.IGNORECASE)
            if multi_match and multi_match.group(2).casefold()=="creatures":targets=[{"id":candidate["instance_id"],"name":candidate["name"],"kind":"permanent","controller_id":candidate["controller_id"]} for owner in state["players"] for candidate in owner["battlefield"] if "Creature" in candidate.get("type_line","")]
            elif multi_match:targets=[{"id":candidate["instance_id"],"name":candidate["name"],"kind":"graveyard","controller_id":player_id} for candidate in player["graveyard"] if not multi_match.group(1) or "Legendary" not in candidate.get("type_line","")]
            elif up_to_match:
                scope=(up_to_match.group(2) or "").casefold();targets=[{"id":candidate["instance_id"],"name":candidate["name"],"kind":"permanent","controller_id":candidate["controller_id"]} for owner in state["players"] for candidate in owner["battlefield"] if "Creature" in candidate.get("type_line","") and (not scope or (candidate["controller_id"]!=player_id if "not" in scope or "don't" in scope else candidate["controller_id"]==player_id))]
            else:targets=_targets(state,player_id,targeting_card)
            requires_target=bool(_target_kind(targeting_card))
            action={"type":"channel","card_id":card["instance_id"],"ability_index":ability_index,"mana_cost":ability["mana_cost"],"channel_reduction":reduction,"label":f"Channel {card['name']} · {ability['mana_cost']}{f' · reduced by {reduction}' if reduction else ''}: {ability['effect']}"}
            if _has_x_cost(cost_card):
                maximum=_maximum_x(player,cost_card,-reduction);maximum=min(maximum,len(targets)) if multi_x else maximum;action.update({"x_min":0,"x_max":maximum})
                if multi_x:action["target_steps_by_x"]={value:[{"label":f"Choose target {position+1}","targets":targets,"distinct":True} for position in range(value)] for value in range(maximum+1)}
            elif requires_target:
                if not targets:continue
                action["targets"]=targets
            if up_to_match:
                maximum={"one":1,"two":2,"three":3}.get(up_to_match.group(1).casefold(),int(up_to_match.group(1)) if up_to_match.group(1).isdigit() else 0)
                for amount in range(min(maximum,len(targets))+1):actions.append({**action,"channel_target_count":amount,"target_steps":[{"label":f"Choose target {position+1}","targets":targets,"distinct":True} for position in range(amount)],"label":f"{action['label']} · choose {amount} target{'s' if amount!=1 else ''}"})
                continue
            actions.append(action)
        cycling=_cycling_ability(card)
        if cycling and _can_pay(player,{"mana_cost":cycling["mana_cost"]}):
            actions.append({"type":"cycle","card_id":card["instance_id"],"label":f"{cycling['keyword']} · {cycling['mana_cost']}","mana_cost":cycling["mana_cost"]})
        suspend=_suspend_ability(card);instant_speed="Instant" in card.get("type_line","") or _has_keyword(card,"Flash")
        suspend_x_max=_maximum_x(player,{"mana_cost":suspend["mana_cost"]}) if suspend and suspend["count"]=="X" else None
        if suspend and ((active and main and not state["stack"]) or instant_speed) and _can_pay(player,{"mana_cost":suspend["mana_cost"]}) and (suspend["count"]!="X" or (suspend_x_max or 0)>=1):
            action={"type":"suspend","card_id":card["instance_id"],"label":f"Suspend {card['name']} · {suspend['count']} time counters · {suspend['mana_cost']}","mana_cost":suspend["mana_cost"],"time_counters":suspend["count"]}
            if suspend["count"]=="X":action.update({"x_min":1,"x_max":suspend_x_max})
            actions.append(action)
        foretell_cost=_foretell_cost(card)
        if active and foretell_cost and _can_pay(player,{"mana_cost":"{2}"}):actions.append({"type":"foretell","card_id":card["instance_id"],"label":f"Foretell {card['name']} face down · {{2}} · cast on a later turn for {foretell_cost}","mana_cost":"{2}","foretell_cost":foretell_cost})
    if active and state["phase"]=="combat" and state["combat"].get("damage_pending"):
        blocked=set(state["combat"].get("blocks",{}).values());battlefield={card["instance_id"]:card for card in player["battlefield"]}
        unblocked=[attacker_id for attacker_id in state["combat"].get("attackers",[]) if attacker_id not in blocked and attacker_id in battlefield]
        if unblocked:
            candidates=[(card,"hand") for card in player["hand"] if _ninjutsu_ability(card) and not _ninjutsu_ability(card)["commander"]]
            candidates.extend((card,"command") for card in player.get("command",[]) if (_ninjutsu_ability(card) or {}).get("commander"))
            targets=[{"id":attacker_id,"name":battlefield[attacker_id]["name"],"kind":"permanent","controller_id":player_id} for attacker_id in unblocked]
            for ninja,source in candidates:
                ability=_ninjutsu_ability(ninja)
                if ability and _can_pay(player,{"mana_cost":ability["mana_cost"]}):actions.append({"type":"ninjutsu","card_id":ninja["instance_id"],"source":source,"mana_cost":ability["mana_cost"],"targets":targets,"label":f"{'Commander ' if ability['commander'] else ''}Ninjutsu {ninja['name']} · {ability['mana_cost']} · return an unblocked attacker"})
    for permanent in player["battlefield"]:
        for index, ability in enumerate(_permanent_abilities(state,permanent)):
            if not _activation_timing_legal(state,player_id,permanent,index,ability):continue
            if ability["taps"] and (permanent.get("tapped") or ("Creature" in permanent.get("type_line", "") and permanent.get("summoning_sick") and not _has_keyword(permanent,"Haste"))):continue
            reduction=_activation_generic_reduction(state,player,permanent,ability);waterbend_symbol=ability.get("waterbend_symbol");excluded={permanent["instance_id"]} if ability["taps"] else set();waterbend_x_max=_maximum_waterbend_x(player,{"mana_cost":ability["mana_cost"]},excluded) if waterbend_symbol=="X" else None;waterbend_amount=int(waterbend_symbol) if waterbend_symbol and waterbend_symbol.isdigit() else waterbend_x_max or 0;waterbend_combinations=_waterbend_combinations(player,{"mana_cost":ability["mana_cost"]},waterbend_amount,excluded) if waterbend_symbol else []
            if (waterbend_symbol and not waterbend_combinations) or (not waterbend_symbol and ability["mana_cost"] and not _can_pay(player,{"mana_cost":ability["mana_cost"]},-reduction,permanent["instance_id"] if ability["taps"] else None)):continue
            energy_cost=ability.get("energy_cost",0)
            if energy_cost!="X" and player.get("energy",0)<int(energy_cost or 0):continue
            if ability["life_cost"] and player["life"]<ability["life_cost"]:continue
            if ability["counter_cost"] and permanent.get("counters",{}).get(ability["counter_cost"]["name"],0)<ability["counter_cost"]["amount"]:continue
            selection_costs=ability.get("selection_costs",[]);selection_has_x=any(cost["amount"]=="X" for cost in selection_costs);cost_requirements,cost_combinations=_selection_cost_combinations(player,permanent,selection_costs)
            if selection_costs and not selection_has_x and not cost_combinations:continue
            cost_options=list(dict.fromkeys(card_id for requirement in cost_requirements for card_id in requirement["options"]))
            fight_steps=_fight_target_steps(state,player_id,ability["card"],permanent);targets=[] if fight_steps else _targets(state, player_id, ability["card"])
            if (fight_steps and any(not step["targets"] for step in fight_steps)) or (not fight_steps and _target_kind(ability["card"]) and not targets): continue
            fixed_cost_amount=sum(cost["amount"] for cost in selection_costs if cost["amount"]!="X");action = {"type": "activate", "card_id": permanent["instance_id"], "ability_index": index, "label": f"{ability['cost']}: {ability['effect']}","life_cost":ability["life_cost"],"energy_cost":energy_cost,"self_sacrifice":ability["self_sacrifice"],"counter_cost":ability["counter_cost"],"cost_kind":selection_costs[0]["kind"] if len(selection_costs)==1 else "compound" if selection_costs else None,"cost_amount":fixed_cost_amount,"cost_options":cost_options,"cost_requirements":cost_requirements,"cost_combinations":cost_combinations,"selection_x":selection_has_x,"generic_reduction":reduction}
            if len(selection_costs)==1 and selection_costs[0]["kind"]=="blight":action["blight_amount"]=selection_costs[0]["blight_amount"]
            if waterbend_symbol:
                options=[candidate["instance_id"] for candidate in player["battlefield"] if candidate["instance_id"] not in excluded and not candidate.get("tapped") and any(kind in candidate.get("type_line","") for kind in ("Artifact","Creature"))];action.update({"waterbend":True,"waterbend_amount":waterbend_amount,"cost_kind":"waterbend","cost_min_amount":min(map(len,waterbend_combinations)),"cost_max_amount":max(map(len,waterbend_combinations)),"cost_options":options,"cost_combinations":waterbend_combinations})
            if selection_has_x:
                option_cap=min(len(_activated_cost_options(player,permanent,cost)) for cost in selection_costs if cost["amount"]=="X");mana_cap=_maximum_x(player,{"mana_cost":ability["mana_cost"]},-reduction,permanent["instance_id"] if ability["taps"] else None) if _has_x_cost({"mana_cost":ability["mana_cost"]}) else 99;candidate_cap=min(option_cap,mana_cap);by_x={};requirements_by_x={}
                for value in range(candidate_cap+1):
                    requirements,groups=_selection_cost_combinations(player,permanent,selection_costs,value)
                    if groups:by_x[value]=groups;requirements_by_x[value]=requirements
                x_min=1 if "x can't be 0" in ability["effect"].casefold() else 0;x_max=max(by_x,default=0)
                if x_max<x_min:continue
                action.update({"x_min":x_min,"x_max":x_max,"cost_combinations_by_x":by_x,"cost_requirements_by_x":requirements_by_x,"cost_amount_fixed":fixed_cost_amount,"cost_x_components":sum(cost["amount"]=="X" for cost in selection_costs)})
            elif _has_x_cost({"mana_cost":ability["mana_cost"]}):action.update({"x_min":0,"x_max":_maximum_x(player,{"mana_cost":ability["mana_cost"]},-reduction,permanent["instance_id"] if ability["taps"] else None)})
            elif waterbend_symbol=="X":
                by_x={value:_waterbend_combinations(player,{"mana_cost":ability["mana_cost"]},value,excluded) for value in range(0,(waterbend_x_max or 0)+1)};action.update({"x_min":1 if "x can't be 0" in ability["effect"].casefold() else 0,"x_max":waterbend_x_max,"cost_combinations_by_x":by_x})
            elif energy_cost=="X":action.update({"x_min":0,"x_max":player.get("energy",0)})
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
    suspended_casts=[action for action in actions if action.get("type")=="cast" and action.get("source")=="suspend"]
    if suspended_casts:return suspended_casts+[{"type":"concede"}]
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
    if _split_second_on_stack(state):
        actions=[action for action in actions if action["type"] in {"concede","turn_face_up","foretell","pass_priority","resolve"}]
    return actions


def _resolve_spell(state: dict) -> None:
    item = state["stack"].pop()
    card, caster = item["card"], _player(state, item["controller_id"])
    if item.get("kind","spell")=="spell":_check_ascend(state,caster,card)
    if item.get("kind")=="combat_stat_trigger":
        target=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("target_id")),None)
        if not target:_log(state,f"{card['name']} resolved, but its creature was no longer on the battlefield.");return
        target["temporary_power"]=target.get("temporary_power",0)+int(item.get("power_change") or 0);target["temporary_toughness"]=target.get("temporary_toughness",0)+int(item.get("toughness_change") or 0);_log(state,f"{item.get('keyword','combat')} changed {target['name']} by {int(item.get('power_change') or 0):+d}/{int(item.get('toughness_change') or 0):+d} until end of turn.");return
    if item.get("kind")=="populate_sacrifice_trigger":
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id")),None)
        if permanent:
            owner=next(owner for owner in state["players"] if permanent in owner["battlefield"]);_leave_battlefield(state,owner,permanent,"graveyard");_log(state,f"{permanent['name']} was sacrificed after its temporary population.")
        return
    if item.get("kind")=="tilonalli_exile_trigger":
        tokens=[permanent for owner in state["players"] for permanent in list(owner["battlefield"]) if permanent["instance_id"] in set(item.get("token_ids",[]))]
        if caster.get("city_blessing"):
            for token in tokens:token.pop("tilonalli_exile_group",None)
            _log(state,f"{caster['name']}'s {len(tokens)} Elemental token(s) remained because of the city's blessing.")
        else:
            for token in tokens:
                owner=next(owner for owner in state["players"] if token in owner["battlefield"]);_leave_battlefield(state,owner,token,"exile",exile_actor_id=caster["id"])
            _log(state,f"{len(tokens)} Elemental token(s) were exiled by Tilonalli's Summoner.")
        return
    if card.get("growth_mechanic"):
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id")),None);mechanic=card["growth_mechanic"]
        if not permanent:_log(state,f"{card['name']} resolved, but its source was no longer on the battlefield.");return
        if mechanic=="adapt":
            if permanent.get("counters",{}).get("+1/+1",0)<=0:_add_counters(state,permanent,"+1/+1",int(card.get("growth_amount") or 0),caster["id"],"adapt");_log(state,f"{permanent['name']} adapted {card.get('growth_amount',0)}.")
            else:_log(state,f"{permanent['name']} could not adapt because it already had a +1/+1 counter.")
            return
        if mechanic=="level_up":_add_counters(state,permanent,"level",1,caster["id"],"level_up");_log(state,f"{permanent['name']} advanced to level {permanent['counters'].get('level',0)}.");return
        if permanent.get("monstrous"):_log(state,f"{permanent['name']} was already monstrous, so monstrosity had no effect.");return
        amount=card.get("growth_amount",0)
        if amount=="X":amount=int(item.get("x_value") or 0)
        elif amount=="D8":amount=random.SystemRandom().randint(1,8)
        elif amount=="CREATURE_COUNTERS":amount=sum(sum(max(0,int(value)) for value in creature.get("counters",{}).values()) for creature in caster["battlefield"] if "Creature" in creature.get("type_line",""))
        permanent["monstrous"]=True;permanent["monstrosity_value"]=int(amount);_add_counters(state,permanent,"+1/+1",int(amount),caster["id"],"monstrosity");_queue_triggers(state,"monstrous",permanent,caster);_log(state,f"{permanent['name']} became monstrous with {amount} +1/+1 counter(s).");return
    if item.get("kind")=="evoke_sacrifice":
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id") and permanent.get("evoked")),None)
        if permanent:
            permanent_owner=next(owner for owner in state["players"] if permanent in owner["battlefield"]);permanent.pop("evoked",None);_leave_battlefield(state,permanent_owner,permanent,"graveyard");_log(state,f"{permanent['name']} was sacrificed to evoke.")
        return
    if item.get("kind")=="backup_trigger":
        target=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("target_id") and "Creature" in permanent.get("type_line","")),None)
        source=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id")),None)
        if not target:_log(state,f"{card['name']} had no legal target.");return
        _add_counters(state,target,"+1/+1",int(item.get("amount") or 0),caster["id"],"backup")
        granted=item.get("granted_text") or ""
        if source is not target and granted:
            target.setdefault("temporary_backup_rules",[]).append(granted);supported=("flying","first strike","double strike","deathtouch","haste","hexproof","indestructible","lifelink","menace","reach","trample","vigilance");keywords=[keyword.title() for keyword in supported if re.search(rf"\b{re.escape(keyword)}\b",granted,re.IGNORECASE)];target["temporary_keywords"]=sorted(set(target.get("temporary_keywords",[]))|set(keywords))
        _log(state,f"{target['name']} received backup {item.get('amount',0)} from {source['name'] if source else card['name']}.");return
    if item.get("kind")=="dash_return_trigger":
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id") and permanent.get("dashed")),None)
        if permanent:
            owner=next(owner for owner in state["players"] if permanent in owner["battlefield"]);_leave_battlefield(state,owner,permanent,"hand");_log(state,f"{permanent['name']} returned to its owner's hand from dash.")
        return
    if item.get("kind")=="cumulative_upkeep_trigger":
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id") and permanent.get("controller_id")==caster["id"]),None)
        if not permanent:return
        _add_counters(state,permanent,"age",1,caster["id"],"cumulative_upkeep");age=permanent.get("counters",{}).get("age",0)
        pending=state.get("pending_cumulative_upkeep") or [];pending.append({"player_id":caster["id"],"card_id":permanent["instance_id"],"card_name":permanent["name"],"age":age});state["pending_cumulative_upkeep"]=pending;state["priority_player_id"]=caster["id"];_log(state,f"{permanent['name']} received age counter {age}; its cumulative upkeep is due.");return
    if item.get("mutating"):
        target=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("target_id") and permanent.get("owner_id")==caster["id"] and "Creature" in permanent.get("type_line","") and not re.search(r"\bHuman\b",permanent.get("type_line",""),re.IGNORECASE)),None)
        if target:
            target_owner=next(owner for owner in state["players"] if target in owner["battlefield"]);old_name=target["name"]
            _merge_mutate(target,card,item.get("mutate_position","over"));_queue_triggers(state,"mutates",target,target_owner)
            _log(state,f"{card['name']} mutated {item.get('mutate_position','over')} {old_name}.");return
        card["summoning_sick"]=True;_enter_battlefield(state,caster,[card],item.get("cast_source_zone","hand"));_log(state,f"{card['name']} entered the battlefield because its mutate target was no longer legal.");return
    if item.get("kind")=="rebound_trigger":
        candidate=next((candidate for candidate in caster["exile"] if candidate["instance_id"]==item.get("source_id") and candidate.get("rebound_triggered")),None)
        if not candidate:_log(state,f"{card['name']} resolved, but the Rebound card was no longer in exile.");return
        state["pending_rebound"]={"player_id":caster["id"],"card_id":candidate["instance_id"],"card_name":candidate["name"]};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} may cast {candidate['name']} from Rebound.");return
    if item.get("kind")=="cascade":
        _start_discovery(state,caster,int(item.get("cascade_value") or 0),"cascade",card["name"]);return
    if item.get("kind")=="storm_trigger":
        original=item.get("copy_item") or {};count=int(item.get("storm_count") or 0)
        for _ in range(count):
            copied=deepcopy(original);copied["id"]=_id();copied["kind"]="storm_copy";copied_card=deepcopy(copied["card"]);copied_card["instance_id"]=_id();copied["card"]=copied_card;state["stack"].append(copied);_queue_triggers(state,"copy",copied_card,caster)
        _log(state,f"{card['name']} created {count} spell {'copy' if count==1 else 'copies'}.");return
    if item.get("kind")=="madness_trigger":
        candidate=next((candidate for candidate in caster["exile"] if candidate["instance_id"]==item.get("source_id")),None)
        if not candidate:_log(state,f"{card['name']} resolved, but the discarded card was no longer in exile.");return
        state["pending_madness"]={"player_id":caster["id"],"card_id":candidate["instance_id"],"card_name":candidate["name"]};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} may cast {candidate['name']} for its madness cost.");return
    if item.get("kind")=="unearth_ability":
        candidate=next((candidate for candidate in caster["graveyard"] if candidate["instance_id"]==item.get("source_id")),None)
        if not candidate:_log(state,f"{card['name']} did not resolve because the card left the graveyard.");return
        _leave_graveyard(state,caster,[candidate]);candidate["summoning_sick"]=True;candidate.setdefault("temporary_keywords",[]).append("Haste");candidate["unearthed"]=True;candidate["unearth_controller_id"]=caster["id"];_enter_battlefield(state,caster,[candidate],"graveyard");_log(state,f"{candidate['name']} returned with haste. It will be exiled at the beginning of the next end step.");return
    if item.get("kind")=="revive_trigger":
        zone_owner=_player(state,item.get("owner_id",caster["id"]));candidate=next((candidate for candidate in zone_owner["graveyard"] if candidate["instance_id"]==item.get("source_id")),None)
        if not candidate:_log(state,f"{card['name']} resolved, but the card was no longer in its owner's graveyard.");return
        _leave_graveyard(state,zone_owner,[candidate]);candidate["controller_id"]=zone_owner["id"];candidate["summoning_sick"]=True;candidate["counters"]={};counter="-1/-1" if item.get("revive_keyword")=="persist" else "+1/+1";candidate["counters"][counter]=1;_enter_battlefield(state,zone_owner,[candidate],"graveyard");_log(state,f"{candidate['name']} returned with a {counter} counter from {item.get('revive_keyword')}.");return
    if item.get("kind")=="unearth_exile_trigger":
        permanent=next((permanent for owner in state["players"] for permanent in owner["battlefield"] if permanent["instance_id"]==item.get("source_id") and permanent.get("unearthed")),None)
        if permanent:
            owner=next(owner for owner in state["players"] if permanent in owner["battlefield"]);_leave_battlefield(state,owner,permanent,"exile",exile_actor_id=caster["id"]);_log(state,f"{permanent['name']} was exiled by unearth.")
        return
    if item.get("kind")=="ninjutsu_ability":
        source=item.get("source_zone","hand");ninja=next((candidate for candidate in caster.get(source,[]) if candidate["instance_id"]==item.get("source_id")),None)
        if not ninja:_log(state,f"{card['name']} did not resolve because the Ninja was no longer in {source}.");return
        caster[source].remove(ninja);ninja["tapped"]=True;ninja["summoning_sick"]=True
        _enter_battlefield(state,caster,[ninja],source)
        state["combat"]["attackers"].append(ninja["instance_id"]);state["combat"]["attack_targets"][ninja["instance_id"]]=item["defender_id"]
        _log(state,f"{ninja['name']} entered tapped and attacking using {'commander ' if source=='command' else ''}ninjutsu.");return
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
    if item.get("kind")=="channel_ability" and item.get("target_ids"):
        for target_id in item["target_ids"]:
            single_card=_x_rules_card(item["card"],item.get("x_value"));single_text=re.sub(r"\b\d+ target (nonlegendary )?(cards|creatures)\b",lambda match:f"target {match.group(1) or ''}{match.group(2)[:-1]}",single_card.get("oracle_text") or "",flags=re.IGNORECASE);single_text=re.sub(r"(?:each of )?up to (?:one|two|three|\d+) target creatures", "target creature",single_text,flags=re.IGNORECASE);single_card={**single_card,"oracle_text":single_text};state["stack"].append({**item,"id":_id(),"kind":"ability","card":single_card,"target_id":target_id,"target_ids":[]});_resolve_spell(state)
        _log(state,f"{card['name']} resolved for {len(item['target_ids'])} targets.");return
    if item.get("kind","spell")=="spell" and item.get("overloaded"):
        targeting_card=_spell_targeting_card(_x_rules_card(card,item.get("x_value")));targets=_targets(state,caster["id"],targeting_card,ignore_target_protection=True)
        for overload_target in targets:
            state["stack"].append({"id":_id(),"kind":"overload_effect","card":card,"controller_id":caster["id"],"target_id":overload_target["id"],"x_value":item.get("x_value")})
            _resolve_spell(state)
        caster["graveyard"].append(card)
        _log(state,f"{card['name']} resolved overloaded, affecting {len(targets)} object(s).");return
    if item.get("kind","spell")=="spell" and len(item.get("mode_indices") or [])>1:
        options={option["index"]:option for option in _modal_options(card)};targets=item.get("mode_targets") or []
        for position,index in enumerate(item["mode_indices"]):
            option=options[index];mode_card=_x_rules_card({**card,"name":f"{card['name']} — mode {position+1}","oracle_text":option["label"]},item.get("x_value"));state["stack"].append({"id":_id(),"kind":"modal_effect","card":mode_card,"controller_id":caster["id"],"target_id":targets[position] if position<len(targets) else None,"x_value":item.get("x_value")});_resolve_spell(state)
        if item.get("cast_source_zone")=="hand" and _has_keyword(card,"Rebound"):
            card["rebound_pending"]=True;card["rebound_after_turn"]=state["turn"];_put_into_exile(state,caster,[card],"rebound",caster["id"])
        elif item.get("buyback"):caster["hand"].append(card)
        elif item.get("flashback"):_put_into_exile(state,caster,[card],"stack",caster["id"])
        else:caster["graveyard"].append(card)
        _log(state,f"{card['name']} resolved with {len(item['mode_indices'])} modes.");return
    rules_card=_selected_mode_card(card,item.get("mode_indices")) if item.get("kind","spell")=="spell" else card
    if item.get("kind","spell")=="spell":rules_card=_kicked_rules_card(rules_card,bool(item.get("kicked")))
    rules_card=_city_blessing_rules_card(rules_card,bool(caster.get("city_blessing")))
    if item.get("blessing_top"):rules_card={**rules_card,"oracle_text":re.sub(r"return target ([^.]+?) to its owner's hand\.\s*if you have the city's blessing, you may put that permanent on top of its owner's library instead\.",r"Put target \1 on top of its owner's library.",rules_card.get("oracle_text") or "",flags=re.IGNORECASE)}
    rules_card=_x_rules_card(rules_card,item.get("x_value"));targeting_card=_spell_targeting_card(rules_card) if item.get("kind","spell")=="spell" else rules_card;target_kind=_target_kind(targeting_card);target_id=item.get("target_id")
    source_permanent=next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"]==item.get("source_id")),None);target_ids=item.get("target_ids") or [];fight_steps=_fight_target_steps(state,caster["id"],rules_card,source_permanent);valid_fight_ids=[target_value for position,target_value in enumerate(target_ids) if position<len(fight_steps) and target_value in {target["id"] for target in fight_steps[position]["targets"]}]
    if item.get("kind")=="trigger" and source_permanent and "sacrifice it unless it escaped" in (card.get("oracle_text") or "").casefold():
        if not source_permanent.get("escaped"):
            owner=next(owner for owner in state["players"] if source_permanent in owner["battlefield"]);_leave_battlefield(state,owner,source_permanent,"graveyard");_log(state,f"{source_permanent['name']} was sacrificed because it did not escape.")
        else:_log(state,f"{source_permanent['name']} remained because it escaped.")
        return
    if target_ids and not valid_fight_ids:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _finish_saga_final_chapter(state,item)
        _log(state,f"{card['name']} was countered because all of its fight targets were no longer legal.");return
    if item.get("kind")!="overload_effect" and target_kind and target_id not in {target["id"] for target in _targets(state,caster["id"],targeting_card)}:
        if item.get("kind","spell")=="spell":_countered_spell_destination(state,caster,card,item.get("flashback",False))
        _finish_saga_final_chapter(state,item)
        _log(state,f"{card['name']} was countered because its target was no longer legal.");return
    text = (rules_card.get("oracle_text") or "").casefold()
    is_permanent_spell = item.get("kind", "spell") in {"spell","storm_copy"} and any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle"))
    effect_text = "" if is_permanent_spell and re.search(r"\b(?:when|whenever|at the beginning)\b", text) else text
    if item.get("kind")=="trigger" and re.search(r"\b(?:first|second|third|fourth) time(?: this ability has resolved)? this turn\b",effect_text):
        usage=state.setdefault("trigger_resolution_usage",{});key=f"{item.get('source_id')}:{card.get('oracle_text','')}";record=usage.get(key,{})
        count=(record.get("count",0)+1) if record.get("turn")==state.get("turn") else 1
        usage[key]={"turn":state.get("turn"),"count":count};effect_text=_resolution_order_effect(effect_text,count)
    if item.get("kind")=="trigger" and "if that land is" in effect_text:
        effect_text=_entered_land_subtype_effect(effect_text,item.get("event_card_type_line",""))
    other = opponent(state, caster["id"])
    target_player = next((player for player in state["players"] if player["id"] == target_id), None)
    target_owner = next((player for player in state["players"] if any(permanent["instance_id"] == target_id for permanent in player["battlefield"])), None)
    target = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == target_id), None)
    event_permanent=next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"]==item.get("event_card_id")),None);event_controller=_player(state,item.get("event_owner_id")) if item.get("event_owner_id") else _player(state,event_permanent.get("controller_id")) if event_permanent else None
    target_stack_item = next((entry for entry in state["stack"] if entry["id"] == target_id), None)
    graveyard_owner=next((player for player in state["players"] if any(graveyard_card["instance_id"]==target_id for graveyard_card in player["graveyard"])),None)
    graveyard_target=next((graveyard_card for player in state["players"] for graveyard_card in player["graveyard"] if graveyard_card["instance_id"]==target_id),None)
    if re.search(r"you may pay \{x\}\{r\}",effect_text) and "create x 1/1 red elemental creature tokens" in effect_text:
        state["pending_tilonalli"]={"player_id":caster["id"],"source_name":source_permanent.get("name",card["name"]) if source_permanent else card["name"],"source_id":item.get("source_id"),"defender_id":state.get("combat",{}).get("attack_targets",{}).get(item.get("source_id"),opponent(state,caster["id"])["id"])};state["priority_player_id"]=caster["id"];_log(state,f"{caster['name']} may pay {{X}}{{R}} for {state['pending_tilonalli']['source_name']}.");return
    if "take an extra turn after this one" in effect_text:
        state.setdefault("extra_turns",[]).append(caster["id"]);_log(state,f"{caster['name']} will take an extra turn after this one.");return
    if re.search(r"for each token you control that entered (?:the battlefield )?this turn, create a token that's a copy of it",effect_text) and re.search(r"create a 1/1 white cat creature token",effect_text):
        cat={"instance_id":_id(),"scryfall_id":"token-cat","name":"Cat Token","image_url":None,"type_line":"Token Creature — Cat","oracle_text":"","mana_cost":"","mana_value":0,"colors":["W"],"power":"1","toughness":"1","owner_id":caster["id"],"controller_id":caster["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":[]};_enter_battlefield(state,caster,[cat],"token")
        originals=[token for token in caster["battlefield"] if token.get("token") and token.get("entered_turn")==state["turn"]] if caster.get("city_blessing") else [];copies=[]
        for original in originals:
            token=deepcopy(original);token["instance_id"]=_id();token["owner_id"]=caster["id"];token["controller_id"]=caster["id"];token["damage"]=0;token["counters"]={};token["tapped"]=False;token["summoning_sick"]=True
            for key in ("attached_to","attachment_keywords","attachment_rules","temporary_power","temporary_toughness","temporary_keywords","temporary_backup_rules","deathtouch_damage","activated_ability_usage","entered_turn"):token.pop(key,None)
            copies.append(token)
        _enter_battlefield(state,caster,copies,"token");_log(state,f"{caster['name']} created a Cat and copied {len(originals)} token(s) with Ocelot Pride.");return
    if "reveal the top card of your library and put that card into your hand" in effect_text and "where x is that card's mana value" in effect_text:
        if caster["library"]:
            revealed=caster["library"].pop();caster["hand"].append(revealed);amount=int(revealed.get("mana_value") or 0)
            for enemy in state["players"]:
                if enemy["id"]!=caster["id"]:enemy["life"]-=amount
            _gain_life(state,caster,amount);_log(state,f"{caster['name']} revealed {revealed['name']}, put it into their hand, and drained each opponent for {amount} life.")
        else:_log(state,f"{caster['name']} had no card to reveal.")
        return
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
    if event_controller and "that player exiles all cards from their library" in effect_text:
        cards=list(event_controller["library"]);event_controller["library"].clear();_put_into_exile(state,event_controller,cards,"library",caster["id"])
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
    if re.search(r"\b(?:you become|become) the monarch\b",effect_text):_take_monarch(state,caster)
    if re.search(r"\b(?:you take|take) the initiative\b",effect_text):_take_initiative(state,caster)
    energy_gain=_energy_quantity(effect_text,"get")
    if isinstance(energy_gain,int) and energy_gain and not re.search(r"\bmay get\b",effect_text):_gain_energy(state,caster,energy_gain)
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
    each_explores=re.search(r"\b(?:each creature|creatures) you control explores?\b",keyword_text)
    explore_match=re.search(r"\b(?:(?:up to one )?target creature(?: you control)?|this creature|it) explores?(?: (twice|three times|\d+ times))?\b",keyword_text)
    if each_explores:_explore(state,caster,[permanent for permanent in caster["battlefield"] if "Creature" in permanent.get("type_line","")])
    elif explore_match:
        repeats={"twice":2,"three times":3}.get(explore_match.group(1),int(explore_match.group(1).split()[0]) if explore_match.group(1) and explore_match.group(1)[0].isdigit() else 1);explorer=target or source_permanent or event_permanent
        if explorer:_explore(state,_player(state,explorer["controller_id"]),[explorer]*repeats)
    connive_match=re.search(r"\b(?:(?:up to one )?target creature(?: you control)?|this creature|it) connives?(?: (a|one|two|three|four|five|\d+))?\b",keyword_text)
    if connive_match:
        words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5};amount_word=(connive_match.group(1) or "one").casefold();amount=words.get(amount_word,int(amount_word) if amount_word.isdigit() else 1);conniver=target or source_permanent or event_permanent
        if conniver:_connive(state,_player(state,conniver["controller_id"]),conniver,amount)
    discover_match=re.search(r"\bdiscover (\d+)\b",keyword_text)
    if discover_match:_start_discovery(state,caster,int(discover_match.group(1)),"discover",source_permanent.get("name",card["name"]) if source_permanent else card["name"])
    face_down_text=re.sub(r"\([^()]*(?:to manifest|to cloak)[^()]*\)","",effect_text);source_name=source_permanent.get("name",card["name"]) if source_permanent else card["name"]
    dread_match=re.search(r"\bmanifest dread(?: (twice|three times))?\b",face_down_text)
    if dread_match:_start_manifest_dread(state,caster,source_name,{"twice":2,"three times":3}.get(dread_match.group(1),1))
    elif re.search(r"\bmanifest(?:s)? the top card of (?:your|their|that player's) library\b",face_down_text):
        recipient=target_player or caster
        if recipient["library"]:_manifest_card(state,recipient,recipient["library"].pop(),False,"library")
    cloak_match=re.search(r"\bcloak(?:s)? the top card of (?:your|their|that player's) library\b",face_down_text)
    if cloak_match:
        recipient=target_player or caster
        if recipient["library"]:_manifest_card(state,recipient,recipient["library"].pop(),True,"library")
    if re.search(r"\bairbend (?:up to one )?target (?:creature|spell|creature or spell)\b",effect_text):
        airbent=None;airbend_owner=None
        if target and target_owner:
            airbent=target;airbend_owner=_player(state,target.get("owner_id",target_owner["id"]));target["airbent"]=True;_leave_battlefield(state,target_owner,target,"exile",exile_actor_id=caster["id"])
        elif target_stack_item:
            state["stack"].remove(target_stack_item);airbent=target_stack_item["card"];airbend_owner=_player(state,airbent.get("owner_id",target_stack_item["controller_id"]))
            if not airbent.get("token"):
                _restore_face_down_identity(airbent);airbent["controller_id"]=airbend_owner["id"];airbent["airbent"]=True;_put_into_exile(state,airbend_owner,[airbent],"stack",caster["id"])
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
    if target and target_owner and re.search(r"destroy target (?:artifact|creature|enchantment|land|planeswalker|permanent|nonland permanent)", effect_text):
        if _destroy_permanent(state,target_owner,target,"can't be regenerated" in effect_text):_log(state, f"{target['name']} was destroyed.")
    if target and target_owner and re.search(r"exile target (?:artifact|creature|enchantment|land|planeswalker|permanent|nonland permanent)", effect_text):
        _leave_battlefield(state,target_owner,target,"exile",exile_actor_id=caster["id"]);_log(state,f"{target['name']} was exiled.")
    if target and target_owner and re.search(r"return target (?:creature|permanent|nonland permanent).* to (?:its|their) owner'?s hand", effect_text):
        _leave_battlefield(state, target_owner, target, "hand"); _log(state, f"{target['name']} returned to its owner's hand.")
    if target and target_owner and re.search(r"put target (?:creature|permanent|nonland permanent).* on top of (?:its|their) owner'?s library",effect_text):
        card_owner=_player(state,target.get("owner_id",target_owner["id"]));_leave_battlefield(state,target_owner,target,"library");_log(state,f"{target['name']} was put on top of {card_owner['name']}'s library.")
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
    half_sacrifice=re.search(r"each opponent sacrifices half the creatures they control, rounded up",effect_text)
    if half_sacrifice:
        for affected in [owner for owner in state["players"] if owner["id"]!=caster["id"]]:
            choices=[permanent["instance_id"] for permanent in affected["battlefield"] if "Creature" in permanent.get("type_line","")];required=(len(choices)+1)//2
            if required:state["pending_sacrifice"]={"player_id":affected["id"],"amount":required,"card_ids":choices};state["priority_player_id"]=affected["id"];_log(state,f"{affected['name']} must sacrifice {required} creature(s).")
    sacrifice_match=None if half_sacrifice else re.search(r"(?:target player|each opponent) sacrifices? (a|one|two|three|four|\d+) (creature|permanent)s?",effect_text)
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
    global_stats=re.search(r"(?:(?:all|each) )?creatures?(?: you control| your opponents control)? get ([+-]\d+)/([+-]\d+) until end of turn",effect_text)
    if global_stats:
        own_only="you control" in global_stats.group(0);opponents_only="opponents control" in global_stats.group(0)
        for owner in state["players"]:
            if own_only and owner["id"]!=caster["id"] or opponents_only and owner["id"]==caster["id"]:continue
            for permanent in owner["battlefield"]:
                if "Creature" in permanent.get("type_line",""):permanent["temporary_power"]=permanent.get("temporary_power",0)+int(global_stats.group(1));permanent["temporary_toughness"]=permanent.get("temporary_toughness",0)+int(global_stats.group(2))
    team_counters=re.search(r"put (a|one|two|three|four|\d+) ([+\-]\d+/[+\-]\d+) counters? on each creature you control",effect_text)
    if team_counters:
        words={"a":1,"one":1,"two":2,"three":3,"four":4};amount=words.get(team_counters.group(1),int(team_counters.group(1)) if team_counters.group(1).isdigit() else 1)
        for permanent in caster["battlefield"]:
            if "Creature" in permanent.get("type_line",""):_add_counters(state,permanent,team_counters.group(2),amount,caster["id"],"effect")
    token_match = re.search(r"create (a|one|two|three|four|five|\d+) (tapped )?(\d+)/(\d+) ([^.]*?) creature tokens?", effect_text)
    if token_match:
        amount = {"a":1,"one":1,"two":2,"three":3,"four":4,"five":5}.get(token_match.group(1),int(token_match.group(1)) if token_match.group(1).isdigit() else 0)
        attacking="tapped and attacking" in effect_text;tapped=bool(token_match.group(2)) or attacking or "tokens enter tapped" in effect_text;created=[]
        descriptor=token_match.group(5).strip();color_names={"white":"W","blue":"U","black":"B","red":"R","green":"G"};colors=[symbol for name,symbol in color_names.items() if re.search(rf"\b{name}\b",descriptor)]
        artifact_token=re.search(r"\bartifact\b",descriptor,re.IGNORECASE) is not None;subtype=re.sub(r"\b(?:white|blue|black|red|green|colorless|artifact|and)\b"," ",descriptor).strip();subtype=re.sub(r"\s+"," ",subtype) or "Creature"
        keywords=[keyword.title() for keyword in ("flying","first strike","double strike","deathtouch","haste","lifelink","menace","reach","trample","vigilance") if re.search(rf"\b{keyword}\b",effect_text)]
        for _ in range(amount):
            token={"instance_id":_id(),"scryfall_id":"token","name":f"{subtype.title()} Token","image_url":None,"type_line":f"Token {'Artifact ' if artifact_token else ''}Creature — {subtype.title()}","oracle_text":"","mana_cost":"","mana_value":0,"colors":colors,"power":token_match.group(3),"toughness":token_match.group(4),"owner_id":caster["id"],"controller_id":caster["id"],"tapped":tapped,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":keywords};created.append(token)
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
    populate_match=re.search(r"\bpopulate(?: (X|\d+) times)?\b",effect_text,re.IGNORECASE)
    if populate_match:
        repeats=int(item.get("x_value") or 0) if (populate_match.group(1) or "").upper()=="X" else int(populate_match.group(1) or 1);_populate(state,caster,card["name"],repeats,"enters tapped and attacking" in effect_text,"token created this way gains haste" in effect_text,"sacrifice it at the beginning of the next end step" in effect_text)
    bolster_match=re.search(r"\bbolster (X|\d+|one|two|three|four|five)\b",effect_text,re.IGNORECASE)
    if bolster_match:
        word=bolster_match.group(1).casefold();amount={"one":1,"two":2,"three":3,"four":4,"five":5}.get(word,int(word) if word.isdigit() else int(item.get("x_value") or 0))
        if word=="x" and "number of cards in your hand" in effect_text:amount=len(caster["hand"])
        elif word=="x" and "number of tapped creatures you control" in effect_text:amount=sum(card.get("tapped") and "Creature" in card.get("type_line","") for card in caster["battlefield"])
        elif word=="x" and "number of differently named artifact tokens you control" in effect_text:amount=len({card["name"] for card in caster["battlefield"] if card.get("token") and "Artifact" in card.get("type_line","")})
        _bolster(state,caster,amount,card["name"],"chosen creature gains trample" in effect_text)
    saga_transformed=False
    if source_permanent and "exile this saga, then return it to the battlefield transformed under your control" in effect_text:
        saga_owner=next((owner for owner in state["players"] if source_permanent in owner["battlefield"]),None)
        if saga_owner:
            _leave_battlefield(state,saga_owner,source_permanent,"exile",exile_actor_id=caster["id"]);zone_owner=_player(state,source_permanent.get("owner_id",saga_owner["id"]))
            _leave_exile(state,zone_owner,[source_permanent])
            source_permanent["controller_id"]=caster["id"];source_permanent["counters"]={};source_permanent["summoning_sick"]=True;source_permanent["tapped"]=False;_set_card_face(source_permanent,1);_enter_battlefield(state,caster,[source_permanent],"exile");saga_transformed=True;_log(state,f"{source_permanent['name']} returned transformed under {caster['name']}'s control.")
    entered = False
    rebound_from_hand=item.get("kind","spell")=="spell" and item.get("cast_source_zone")=="hand" and _has_keyword(card,"Rebound")
    if is_permanent_spell and rebound_from_hand:
        card["rebound_pending"]=True;card["rebound_after_turn"]=state["turn"];_put_into_exile(state,caster,[card],"rebound",caster["id"]);_log(state,f"{card['name']} was exiled through Rebound as it resolved.")
    elif is_permanent_spell and item.get("buyback"):
        caster["hand"].append(card);_log(state,f"{card['name']} returned to {caster['name']}'s hand through buyback.")
    elif is_permanent_spell:
        if item.get("kind")=="storm_copy":
            card["token"]=True
            if "isn't legendary if it's a token" in (card.get("oracle_text") or "").casefold():card["type_line"]=re.sub(r"\bLegendary\s+","",card.get("type_line","")).strip()
        card["was_kicked"]=bool(item.get("kicked"))
        card["escaped"]=bool(item.get("escaped"))
        if item.get("suspended_cast"):card["suspend_haste"]=True
        if item.get("dashed"):card["dashed"]=True;card.setdefault("temporary_keywords",[]).append("Haste")
        card["summoning_sick"] = True
        if re.search(r"\benters (?:the battlefield )?tapped\b",text):card["tapped"]=True
        enters_counters=re.search(r"enters(?: the battlefield)? with (a|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+) ([+−-]\d+/[+−-]\d+|loyalty|charge|shield|stun) counters?",text)
        _enter_battlefield(state,caster,[card],item.get("cast_source_zone","stack"),item.get("kind","spell")=="spell")
        delved_cards=item.get("delved_cards") or []
        if delved_cards:
            card["delved_card_ids"]=[candidate["instance_id"] for candidate in delved_cards]
            if card.get("name")=="Murktide Regent":_add_counters(state,card,"+1/+1",sum(any(kind in candidate.get("type_line","") for kind in ("Instant","Sorcery")) for candidate in delved_cards),caster["id"],"delve")
            if card.get("name")=="Soulflayer":
                inherited=[keyword for candidate in delved_cards if "Creature" in candidate.get("type_line","") for keyword in ("Flying","First strike","Double strike","Deathtouch","Haste","Hexproof","Indestructible","Lifelink","Reach","Trample","Vigilance") if _has_keyword(candidate,keyword)]
                card["keywords"]=sorted(set(card.get("keywords",[]))|set(inherited))
        if item.get("evoked"):
            card["evoked"]=True;state["stack"].append({"id":_id(),"kind":"evoke_sacrifice","card":{"name":f"{card['name']} — Evoke","oracle_text":"Sacrifice this permanent.","type_line":"Ability","mana_cost":""},"controller_id":caster["id"],"target_id":None,"source_id":card["instance_id"]});_log(state,f"{card['name']}'s evoke sacrifice triggered.")
        if enters_counters and not (item.get("escaped") and "escapes with" in text and "instead" in text):
            counter_words={"a":1,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"twelve":12};counter_amount=int(enters_counters.group(1)) if enters_counters.group(1).isdigit() else counter_words[enters_counters.group(1).casefold()];_add_counters(state,card,enters_counters.group(2).replace("−","-"),counter_amount,caster["id"],"enters")
        if item.get("escaped") and item.get("escape_counters"):_add_counters(state,card,"+1/+1",int(item["escape_counters"]),caster["id"],"escape")
        if item.get("escaped") and item.get("escape_counter_choice"):
            state["pending_escape_counter"]={"player_id":caster["id"],"card_id":card["instance_id"],"card_name":card["name"]};state["priority_player_id"]=caster["id"]
        if "Aura" in card.get("type_line","") and (target or target_player):_attach(state,card,target or target_player)
        entered = True
    elif item.get("kind", "spell") == "spell":
        if rebound_from_hand:
            card["rebound_pending"]=True;card["rebound_after_turn"]=state["turn"];_put_into_exile(state,caster,[card],"rebound",caster["id"])
        elif item.get("buyback"):caster["hand"].append(card)
        elif item.get("flashback"):_put_into_exile(state,caster,[card],"stack",caster["id"])
        else:caster["graveyard"].append(card)
    _log(state, f"{card['name']} resolved.")
    if entered and "Saga" in card.get("type_line",""):_add_saga_lore(state,caster,card)
    if not saga_transformed:_finish_saga_final_chapter(state,item)


def _leave_battlefield(state: dict, owner: dict, card: dict, destination: str, trigger_sources:list[tuple[dict,dict]]|None=None, trigger_dedupe:set[str]|None=None, exile_actor_id:str|None=None, exile_batch_size:int|None=None) -> None:
    if card.get("unearthed") and destination!="exile":destination="exile";exile_actor_id=card.get("unearth_controller_id",exile_actor_id)
    exile_sources=trigger_sources or ([(source_owner,source) for source_owner in state["players"] for source in source_owner["battlefield"]] if destination=="exile" else None)
    if card.get("name")=="Herald of Leshrac":
        for land_controller in state["players"]:
            if land_controller["id"]!=card.get("controller_id"):continue
            for land in list(land_controller["battlefield"]):
                land_owner=_player(state,land.get("owner_id",land_controller["id"]))
                if "Land" in land.get("type_line","") and land_controller["id"]!=land_owner["id"]:_change_control(state,land,land_owner)
    if card.get("attached_to"):_detach(state,card)
    attachments=[(attachment_owner,attachment) for attachment_owner in state["players"] for attachment in list(attachment_owner["battlefield"]) if attachment.get("attached_to")==card.get("instance_id")]
    for attachment_owner,attachment in attachments:
        _detach(state,attachment,restore_control=False)
        if "Aura" in attachment.get("type_line",""):_leave_battlefield(state,attachment_owner,attachment,"graveyard")
    revive_keyword=None;soulshift_values=_keyword_instances(state,card,"Soulshift") if destination=="graveyard" and "Creature" in card.get("type_line","") and not card.get("token") else []
    if destination=="graveyard" and "Creature" in card.get("type_line","") and not card.get("token"):
        if _has_keyword(card,"Persist") and card.get("counters",{}).get("-1/-1",0)<=0:revive_keyword="persist"
        elif _has_keyword(card,"Undying") and card.get("counters",{}).get("+1/+1",0)<=0:revive_keyword="undying"
    adjusts_land_plays="you may play an additional land on each of your turns" in (card.get("oracle_text") or "").casefold()
    if adjusts_land_plays:_ensure_land_play_tracking(owner)
    if card in owner["battlefield"]: owner["battlefield"].remove(card)
    if adjusts_land_plays:_refresh_land_plays(state,owner)
    _sync_city_blessing(state)
    earthbend_controller=card.get("earthbend_controller") if destination in {"graveyard","exile"} else None
    _queue_triggers(state,"leaves",card,owner,trigger_dedupe,trigger_sources)
    if destination=="graveyard":_queue_triggers(state,"dies",card,owner,trigger_dedupe,trigger_sources)
    mutate_pile=card.pop("mutate_pile",None)
    if mutate_pile:
        for component in mutate_pile:
            component=deepcopy(component)
            if component.get("token"):continue
            zone_owner=_player(state,component.get("owner_id",owner["id"]));component["controller_id"]=zone_owner["id"];component["damage"]=0;component["tapped"]=False;component["counters"]={}
            if destination=="exile":_put_into_exile(state,zone_owner,[component],"battlefield",exile_actor_id,trigger_dedupe,exile_sources,exile_batch_size)
            else:zone_owner[destination].append(component)
            _queue_commander_zone_choice(state,zone_owner,component,destination)
        return
    card["damage"] = 0; card["tapped"] = False;card.pop("escaped",None);card.pop("evoked",None);card.pop("echo_due_controller_id",None);card.pop("dashed",None);card.pop("dash_return_triggered",None);card.pop("deathtouch_damage",None);card.pop("crewed_turn",None);card.pop("temporary_control_return_to",None);card.pop("activated_ability_usage",None);card.pop("temporary_power",None);card.pop("temporary_toughness",None);card.pop("temporary_keywords",None);card.pop("temporary_backup_rules",None);card.pop("unearthed",None);card.pop("unearth_controller_id",None);card.pop("unearth_end_triggered",None);card.pop("populate_sacrifice_turn",None);card.pop("monstrous",None);card.pop("monstrosity_value",None)
    if card.get("face_down"):
        values=card.pop("face_down_values",{})
        for key,value in values.items():card[key]=value
        card.pop("face_down",None);card.pop("cloaked",None)
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
    if revive_keyword and destination=="graveyard":
        ability={"name":f"{card['name']} — {revive_keyword.title()}","oracle_text":f"Return {card['name']} to the battlefield under its owner's control with a {'-1/-1' if revive_keyword=='persist' else '+1/+1'} counter on it.","type_line":"Ability","mana_cost":""};state["stack"].append({"id":_id(),"kind":"revive_trigger","card":ability,"controller_id":previous_controller,"owner_id":zone_owner["id"],"source_id":card["instance_id"],"revive_keyword":revive_keyword});_log(state,f"{card['name']}'s {revive_keyword} ability triggered.")
    for value in soulshift_values:
        ability={"name":f"{card['name']} — Soulshift {value}","oracle_text":f"Return target card from your graveyard to your hand.","type_line":"Ability","mana_cost":"","soulshift_value":value};trigger={"id":_id(),"kind":"trigger","card":ability,"controller_id":previous_controller,"target_id":None,"source_id":card["instance_id"]};targets=_targets(state,previous_controller,ability)
        if targets:state.setdefault("pending_trigger_targets",[]).append({"controller_id":previous_controller,"source_name":card["name"],"trigger":trigger,"card":ability,"optional":True});state["priority_player_id"]=state["pending_trigger_targets"][0]["controller_id"];_log(state,f"{card['name']}'s soulshift {value} ability triggered.")
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
        player["hand"].remove(card);madness=_madness_ability(card)
        if madness:
            _put_into_exile(state,player,[card],"discard",player["id"]);ability_card={**card,"name":f"{card['name']} — Madness","type_line":"Ability","mana_cost":"","oracle_text":f"You may cast {card['name']} for {madness['mana_cost']}. If you don't, put it into your graveyard."};state["stack"].append({"id":_id(),"kind":"madness_trigger","card":ability_card,"controller_id":player["id"],"source_id":card["instance_id"]})
        else:player["graveyard"].append(card)
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
    if any("you may play an additional land on each of your turns" in (card.get("oracle_text") or "").casefold() for card in entering):_ensure_land_play_tracking(controller)
    batch_size=len(entering);dedupe:set[str]=set()
    for card in entering:
        if _has_keyword(card,"Daybound"):
            if state.get("day_night") is None:_set_day_night(state,"day")
            elif state.get("day_night")=="night":_set_card_face(card,1)
        card["controller_id"]=controller["id"];card["entered_turn"]=state["turn"];card["entry_event_origin"]=origin;card["entry_event_was_cast"]=was_cast;card["entry_event_played"]=played;card["entry_event_batch_size"]=batch_size
        if _echo_cost(card):card["echo_due_controller_id"]=controller["id"]
        controller["battlefield"].append(card)
        if "as this enchantment enters, choose a creature type" in (card.get("oracle_text") or "").casefold() and not card.get("chosen_creature_type"):
            state.setdefault("pending_creature_type",[]).append({"player_id":controller["id"],"card_id":card["instance_id"],"card_name":card["name"]});state["priority_player_id"]=controller["id"]
    if any("you may play an additional land on each of your turns" in (card.get("oracle_text") or "").casefold() for card in entering):_refresh_land_plays(state,controller)
    _sync_city_blessing(state)
    ordered_owners=sorted(state["players"],key=lambda owner:owner["id"]!=state.get("active_player_id"));sources=[(owner,permanent) for owner in ordered_owners for permanent in owner["battlefield"]]
    for card in entering:_queue_triggers(state,"enters",card,controller,dedupe,sources)
    for card in entering:
        targets=[{"id":candidate["instance_id"],"name":candidate["name"],"kind":"permanent","controller_id":candidate.get("controller_id",controller["id"])} for owner in state["players"] for candidate in owner["battlefield"] if "Creature" in candidate.get("type_line","")]
        for spec in _backup_specs(card):
            ability={"name":f"{card['name']} — Backup {spec['amount']}","oracle_text":"Put a +1/+1 counter on target creature.","type_line":"Ability","mana_cost":""};trigger={"id":_id(),"kind":"backup_trigger","card":ability,"controller_id":controller["id"],"target_id":None,"source_id":card["instance_id"],**spec}
            if targets:state.setdefault("pending_trigger_targets",[]).append({"controller_id":controller["id"],"source_name":card["name"],"trigger":trigger,"card":ability});state["priority_player_id"]=state["pending_trigger_targets"][0]["controller_id"]
    for card in entering:
        for key in ("entry_event_origin","entry_event_was_cast","entry_event_played","entry_event_batch_size"):card.pop(key,None)
    _check_ascend(state,controller)
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


def _resolution_order_effect(text:str,count:int)->str:
    """Select clauses gated by how many times a triggered ability resolved this turn."""
    selected=[]
    ordinals={"first":1,"second":2,"third":3,"fourth":4}
    for sentence in re.split(r"(?<=[.!])\s+",text.strip()):
        sentence=sentence.strip()
        prefix=re.match(r"(?:then )?if (?:this is|it(?:'s| is)) the (first|second|third|fourth) time(?: this ability has resolved)?(?: this turn)?,\s*(.+)",sentence,re.IGNORECASE)
        if prefix:
            if count==ordinals[prefix.group(1).casefold()]:
                body=prefix.group(2)
                if body.casefold().endswith(" instead."):selected=[];body=re.sub(r"\s+instead(?=\.$)","",body,flags=re.IGNORECASE)
                selected.append(body)
            continue
        postfix=re.match(r"(.+?)\s+if this is the (first|second|third|fourth) time this ability has resolved this turn\.(.*)",sentence,re.IGNORECASE)
        if postfix:
            if count==ordinals[postfix.group(2).casefold()]:selected.append(f"{postfix.group(1)}.{postfix.group(3)}".strip())
            continue
        selected.append(sentence)
    return " ".join(selected)


def _entered_land_subtype_effect(text:str,type_line:str)->str:
    """Select follow-up or replacement clauses based on the entering land's subtype."""
    selected=[];subtypes=type_line.casefold()
    for sentence in re.split(r"(?<=[.!])\s+",text.strip()):
        conditional=re.match(r"if that land is (?:a |an )?([a-z]+),\s*(.+)",sentence,re.IGNORECASE)
        if not conditional:selected.append(sentence);continue
        if re.search(rf"\b{re.escape(conditional.group(1).casefold())}\b",subtypes):
            body=conditional.group(2)
            if body.casefold().endswith(" instead."):selected=[];body=re.sub(r"\s+instead(?=\.$)","",body,flags=re.IGNORECASE)
            selected.append(body)
    return " ".join(selected)


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
        text = "\n".join([source.get("oracle_text") or "",*(source.get("temporary_backup_rules") or [])])
        raw_clauses = re.split(r"(?<=[.!])\s+|\n", text);clauses=[]
        for clause in raw_clauses:
            continuation=bool(clauses and (re.match(r"(?:then if|if you do|if you have the city's blessing),?\b",clause.strip(),re.IGNORECASE) or re.match(r"if that land is (?:a |an )?[a-z]+,",clause.strip(),re.IGNORECASE) or re.match(r"if (?:this is|it(?:'s| is)) the (?:first|second|third|fourth) time(?: this ability has resolved)?(?: this turn)?,",clause.strip(),re.IGNORECASE) or ("create x 1/1 red elemental creature tokens" in clauses[-1].casefold() and re.match(r"at the beginning of the next end step, exile those tokens",clause.strip(),re.IGNORECASE)) or ("reveal the top card of your library and put that card into your hand" in clauses[-1].casefold() and re.match(r"each opponent loses x life\b",clause.strip(),re.IGNORECASE))))
            if continuation:clauses[-1]=f"{clauses[-1]} {clause.strip()}"
            else:clauses.append(clause)
        for clause in clauses:
            lower = clause.casefold(); matches = False
            if event=="cast" and re.match(r"^storm\s*\(",lower):continue
            if event in {"attackers_declared","blockers_declared"} and re.match(r"^(?:bushido(?:\s+\d+)?|flanking|exalted)\s*\(",lower):continue
            if event=="dies" and re.match(r"^soulshift\s+\d+",lower):continue
            if event=="enters" and re.match(r"^backup\b",lower):continue
            if event=="upkeep" and re.match(r"^cumulative upkeep\b",lower):continue
            trigger_count = 1
            if event == "enters" and event_card:
                etb_boundary=re.search(r",\s*(?=(?:you\b|put\b|create\b|draw\b|each\b|target\b|this\b|that\b|it\b|its\b|gain\b|tap\b|untap\b|exile\b|investigate\b|proliferate\b|scry\b|mill\b|add\b|amass\b|venture\b|return\b|search\b|[a-z0-9' -]+ deals?\b))",lower);condition=lower[:etb_boundary.start()] if etb_boundary else lower.split(",",1)[0];type_line=event_card.get("type_line","").casefold();under_control=event_card.get("controller_id")==owner["id"];owned=event_card.get("owner_id")==owner["id"];one_or_more="one or more" in condition;dedupe_key=f"enters:{source.get('instance_id')}:{condition}"
                is_creature="creature" in type_line;is_land="land" in type_line;is_artifact="artifact" in type_line;is_enchantment="enchantment" in type_line;is_token=bool(event_card.get("token"));is_permanent=any(kind in type_line for kind in ("artifact","battle","creature","enchantment","land","planeswalker"))
                kind_ok=(("token" in condition and is_token) or ("creature" in condition and is_creature) or ("land" in condition and is_land) or ("artifact" in condition and is_artifact) or ("enchantment" in condition and is_enchantment) or ("permanent" in condition and is_permanent))
                kind_ok=kind_ok and not (("nontoken" in condition and is_token) or ("noncreature" in condition and is_creature) or ("nonland" in condition and is_land) or ("artifact creature" in condition and not (is_artifact and is_creature)))
                source_name=(source.get("name") or "").casefold();short_source_name=source_name.split(",",1)[0];self_reference="this creature" in condition or "this permanent" in condition or "this artifact" in condition or "this enchantment" in condition or "this land" in condition or bool(source_name and source_name in condition) or bool(short_source_name and short_source_name in condition);self_enters=source is event_card and self_reference and "enter" in condition
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
            elif event=="mutates" and event_card:
                matches=source is event_card and re.search(r"whenever this creature mutates\b",lower) is not None
            elif event in {"coin_win","coin_lose"} and event_card:
                matches=source is event_card and ((event=="coin_win" and "whenever you win a coin flip" in lower) or (event=="coin_lose" and "whenever you lose a coin flip" in lower))
            elif event=="cumulative_unpaid" and event_card:
                matches=source is event_card and "when a player doesn't pay this enchantment's cumulative upkeep" in lower
            elif event=="energy_gain":
                matches=owner["id"]==event_owner["id"] and ("whenever you get one or more {e}" in lower or "whenever you get {e}" in lower)
            elif event=="turned_face_up" and event_card:
                same_controller=event_owner["id"]==owner["id"];self_event=source is event_card and ("this creature is turned face up" in lower or "is turned face up" in lower and (source.get("name") or "").casefold() in lower)
                controlled=same_controller and re.search(r"whenever (?:a|another) (?:creature|permanent) you control is turned face up",lower) is not None
                global_event=re.search(r"whenever (?:a|another) (?:creature|permanent) is turned face up",lower) is not None
                matches=self_event or controlled or global_event
            elif event == "leaves" and event_card:
                matches=source is not event_card and owner["id"]==event_owner["id"] and "Creature" in event_card.get("type_line","") and "when another creature you control leaves the battlefield" in lower
            elif event == "upkeep":
                matches = "at the beginning of each upkeep" in lower or "at the beginning of each player's upkeep" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your upkeep" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's upkeep" in lower)
            elif event == "end_step":
                matches = "at the beginning of each end step" in lower or (owner["id"] == event_owner["id"] and "at the beginning of your end step" in lower) or (owner["id"] != event_owner["id"] and "at the beginning of each opponent's end step" in lower)
                if matches and "if you gained life this turn" in lower:matches=owner.get("life_gain_event_turn")==state["turn"] and owner.get("life_gain_events_this_turn",0)>0
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
            elif event=="copy" and event_card:
                controlled=event_owner["id"]==owner["id"];type_line=event_card.get("type_line","").casefold();matches=controlled and source is not event_card and "whenever you cast or copy" in lower and "instant or sorcery spell" in lower and any(kind in type_line for kind in ("instant","sorcery"))
            elif event=="monstrous" and event_card:
                matches=source is event_card and ("becomes monstrous" in lower or "enters or becomes monstrous" in lower)
            elif event == "attackers_declared":
                attacking_ids = set(state.get("combat", {}).get("attackers", []))
                controlled_attackers = [card for card in owner["battlefield"] if card.get("instance_id") in attacking_ids]
                source_attacked = source.get("instance_id") in attacking_ids
                attached_attacked = source.get("attached_to") in attacking_ids
                source_name = re.escape(source.get("name", "").casefold())
                if attached_attacked and "whenever equipped creature attacks" in lower:
                    matches = True
                elif source_attacked and "attacks and isn't blocked" not in lower and "attacks and is not blocked" not in lower and re.search(rf"whenever (?:~|this creature|{source_name}) attacks\b", lower):
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
            elif event == "connive" and event_card:
                controlled=event_owner["id"]==owner["id"];self_event=source is event_card and re.search(r"whenever (?:~|this creature|[^,]+) connives?\b",lower) is not None
                controlled_event=source is not event_card and controlled and re.search(r"whenever (?:a|another) creature you control connives?\b",lower) is not None
                matches=self_event or controlled_event
            elif event == "day_night":
                transition=state.get("day_night_event");matches=(transition=="night" and "day becomes night" in lower and "whenever" in lower) or (transition=="day" and "night becomes day" in lower and "whenever" in lower)
            elif event in {"earthbend","waterbend","firebend","airbend"}:
                multi_bend="whenever you waterbend, earthbend, firebend, or airbend" in lower
                matches=owner["id"]==event_owner["id"] and (f"whenever you {event}" in lower or multi_bend)
            blessing_gate=re.search(r"^(?:when|whenever|at the beginning)[^.]*?,\s*if you have the city's blessing,",lower) is not None
            if matches and blessing_gate and not owner.get("city_blessing"):continue
            if not matches or "," not in clause: continue
            effect = clause.split(",", 1)[1].strip()
            if owner.get("city_blessing"):effect=re.sub(r"^if you have the city's blessing,\s*","",effect,flags=re.IGNORECASE)
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
            if event=="energy_gain":effect=re.sub(r"\bthat (?:many|much)\b",str(event_owner.get("energy_event_amount",1)),effect,flags=re.IGNORECASE)
            if event in {"earthbend","waterbend","firebend","airbend"} and "whenever you waterbend, earthbend, firebend, or airbend" in lower:effect=re.split(r"whenever you waterbend, earthbend, firebend, or airbend,",clause,flags=re.IGNORECASE)[1].strip()
            if event=="enters" and re.match(r"if it was kicked,",effect,re.IGNORECASE):effect=effect.split(",",1)[1].strip()
            if event=="leaves" and "transform" in effect and "next upkeep" in effect:
                source["transform_next_upkeep"]=True;_log(state,f"{source['name']} will transform at the beginning of the next upkeep.");continue
            ability_card = {**source, "name": f"{source['name']} trigger", "oracle_text": effect, "source_type_line":source.get("type_line",""),"source_mana_cost":source.get("mana_cost",""), "type_line": "Ability", "mana_cost": ""}
            if event=="attackers_declared" and "firebending" in lower and re.search(r"\badd\b[^.]*\{r\}",lower):ability_card["firebending_trigger"]=True
            fight_steps=_fight_target_steps(state,owner["id"],ability_card,source);targets=[] if fight_steps else _targets(state, owner["id"], ability_card)
            for _ in range(trigger_count):
                trigger={"id":_id(),"kind":"trigger","card":ability_card,"controller_id":owner["id"],"target_id":None,"source_id":source["instance_id"]}
                if event=="mutates":trigger["x_value"]=event_card.get("mutate_count",1)
                if event_card and event in {"enters","exile","tapped","untapped","counter_added","turned_face_up","dies","discard","graveyard_leave","damage","combat_damage_player","cumulative_unpaid"}:
                    trigger["event_card_id"]=event_card.get("instance_id");trigger["event_owner_id"]=event_owner.get("id")
                    if event=="enters":trigger["event_card_type_line"]=event_card.get("type_line","")
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
            source = creature.get("commander_source_id",creature["instance_id"]);damage=defender.setdefault("commander_damage", {});names=defender.setdefault("commander_damage_names", {})
            # Games saved before individual commander tracking used the owner's id.
            # A legacy total can only represent one commander, so migrate it on the
            # first subsequent hit instead of silently resetting that game's clock.
            legacy_source=creature.get("owner_id",attacker["id"])
            if legacy_source in damage and source not in damage and not names:damage[source]=damage.pop(legacy_source)
            damage[source]=damage.get(source,0)+amount;names[source]=creature.get("rules_name") or creature["name"]
        if not planeswalker and amount > 0:
            _queue_triggers(state,"combat_damage_player",creature,attacker,trigger_dedupe)
            if state.get("monarch_id")==defender["id"]:_take_monarch(state,attacker)
            if state.get("initiative_id")==defender["id"]:_take_initiative(state,attacker)

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
    previous_active=_player(state,state["active_player_id"]);previous_spells=previous_active.get("spells_cast_this_turn",0) if previous_active.get("cast_event_turn")==state["turn"] else 0
    state["pending_discard"]=None;state["turn"] += 1; state["phase"] = PHASES[0];state["beginning_draw_pending"]=True;state["active_player_id"]=(state.get("extra_turns") or []).pop() if state.get("extra_turns") else opponent(state,state["active_player_id"])["id"]
    active = _player(state, state["active_player_id"]);active["lands_played_this_turn"]=0;_refresh_land_plays(state,active)
    echo_due=[card for card in active["battlefield"] if card.get("echo_due_controller_id")==active["id"]]
    if echo_due:
        state["pending_echo"]=[{"player_id":active["id"],"card_id":card["instance_id"],"card_name":card["name"]} for card in echo_due]
        for card in echo_due:card.pop("echo_due_controller_id",None)
        state["priority_player_id"]=active["id"]
    for permanent in active["battlefield"]:
        if _cumulative_upkeep_cost(permanent):
            ability={"name":f"{permanent['name']} — Cumulative upkeep","oracle_text":"Put an age counter on this permanent, then pay its cumulative upkeep cost for each age counter on it or sacrifice it.","type_line":"Ability","mana_cost":""};state["stack"].append({"id":_id(),"kind":"cumulative_upkeep_trigger","card":ability,"controller_id":active["id"],"target_id":None,"source_id":permanent["instance_id"]})
    if state.get("day_night")=="day" and previous_spells==0:_set_day_night(state,"night")
    elif state.get("day_night")=="night" and previous_spells>=2:_set_day_night(state,"day")
    temporary_controlled=[card for owner in state["players"] for card in owner["battlefield"] if card.get("temporary_control_return_to")]
    for permanent in temporary_controlled:
        return_to=_player(state,permanent.pop("temporary_control_return_to"));current=next(owner for owner in state["players"] if permanent in owner["battlefield"])
        if current["id"]!=return_to["id"]:
            current["battlefield"].remove(permanent);return_to["battlefield"].append(permanent);permanent["controller_id"]=return_to["id"];permanent["summoning_sick"]=True
            if "you may play an additional land on each of your turns" in (permanent.get("oracle_text") or "").casefold():_refresh_land_plays(state,current);_refresh_land_plays(state,return_to)
            _log(state,f"{permanent['name']} returned to {return_to['name']}'s control.")
    for owner in state["players"]:
        owner["firebending_mana"]=0;owner["bent_this_turn"]=[];owner["energy_paid_this_turn"]=0
        for permanent in owner["battlefield"]:
            permanent.pop("temporary_power",None);permanent.pop("temporary_toughness",None);permanent.pop("temporary_keywords",None);permanent.pop("temporary_backup_rules",None);permanent.pop("cant_attack_until_turn",None);permanent.pop("cant_block_until_turn",None);permanent.pop("regeneration_shields",None);permanent.pop("deathtouch_damage",None);permanent.pop("crewed_turn",None);permanent["damage"]=0
            if permanent.get("goaded_until_turn",0)<state["turn"]:permanent.pop("goaded_until_turn",None);permanent.pop("goaded_by",None)
            if permanent.get("hexproof_until_turn",0)<state["turn"]:permanent.pop("hexproof_until_turn",None)
            if permanent.get("base_type_line") is not None:permanent["type_line"]=permanent.pop("base_type_line")
    _set_tapped(state,list(active["battlefield"]),False,active["id"],"untap_step")
    for permanent in active["battlefield"]:permanent["summoning_sick"]=False
    _log(state, f"Turn {state['turn']} began for {active['name']}. Untap and upkeep started.")
    for suspended in list(active["exile"]):
        if not suspended.get("suspended") or suspended.get("counters",{}).get("time",0)<=0:continue
        _remove_counters(suspended,"time",1);remaining=suspended.get("counters",{}).get("time",0);_log(state,f"A time counter was removed from {suspended['name']}; {remaining} remain.")
        if remaining==0:suspended["suspended_ready"]=True;state["priority_player_id"]=active["id"]
    for rebound_card in [card for card in active["exile"] if card.get("rebound_pending") and state["turn"]>card.get("rebound_after_turn",state["turn"])]:
        rebound_card.pop("rebound_pending",None);rebound_card["rebound_triggered"]=True;ability_card={**rebound_card,"name":f"{rebound_card['name']} — Rebound","type_line":"Ability","mana_cost":"","oracle_text":f"You may cast {rebound_card['name']} from exile without paying its mana cost."};state["stack"].append({"id":_id(),"kind":"rebound_trigger","card":ability_card,"controller_id":active["id"],"source_id":rebound_card["instance_id"]});_log(state,f"{rebound_card['name']}'s Rebound ability triggered.")
    _queue_triggers(state,"upkeep",None,active)
    if state.get("initiative_id")==active["id"]:_venture_undercity(state,active)


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
            active=_player(state,state["active_player_id"]);_queue_triggers(state,"end_step",None,active)
            for permanent in [card for owner in state["players"] for card in owner["battlefield"] if card.get("unearthed") and not card.get("unearth_end_triggered")]:
                permanent["unearth_end_triggered"]=True;controller=_player(state,permanent.get("unearth_controller_id",permanent["controller_id"]));ability_card={**permanent,"name":f"{permanent['name']} — Unearth exile","type_line":"Ability","mana_cost":"","oracle_text":f"Exile {permanent['name']}."};state["stack"].append({"id":_id(),"kind":"unearth_exile_trigger","card":ability_card,"controller_id":controller["id"],"source_id":permanent["instance_id"]});_log(state,f"{permanent['name']}'s unearth exile trigger was put on the stack.")
            for permanent in [card for owner in state["players"] for card in owner["battlefield"] if card.get("populate_sacrifice_turn")==state["turn"]]:
                permanent.pop("populate_sacrifice_turn",None);controller=_player(state,permanent["controller_id"]);ability={"name":f"{permanent['name']} — Populate sacrifice","type_line":"Ability","mana_cost":"","oracle_text":f"Sacrifice {permanent['name']}."};state["stack"].append({"id":_id(),"kind":"populate_sacrifice_trigger","card":ability,"controller_id":controller["id"],"source_id":permanent["instance_id"]});_log(state,f"{permanent['name']}'s population sacrifice triggered.")
            tilonalli_groups={card.get("tilonalli_exile_group") for owner in state["players"] for card in owner["battlefield"] if card.get("tilonalli_exile_group")}
            for group in tilonalli_groups:
                tokens=[card for owner in state["players"] for card in owner["battlefield"] if card.get("tilonalli_exile_group")==group];controller=_player(state,tokens[0]["owner_id"]);[token.pop("tilonalli_exile_group",None) for token in tokens];ability={"name":"Tilonalli's Summoner — delayed exile","type_line":"Ability","mana_cost":"","oracle_text":"Exile those Elemental tokens unless you have the city's blessing."};state["stack"].append({"id":_id(),"kind":"tilonalli_exile_trigger","card":ability,"controller_id":controller["id"],"token_ids":[token["instance_id"] for token in tokens]});_log(state,f"Tilonalli's Summoner's delayed exile triggered for {len(tokens)} token(s).")
            for permanent in [card for owner in state["players"] for card in owner["battlefield"] if card.get("dashed") and not card.get("dash_return_triggered")]:
                permanent["dash_return_triggered"]=True;controller=_player(state,permanent["controller_id"]);ability_card={"name":f"{permanent['name']} — Dash return","type_line":"Ability","mana_cost":"","oracle_text":f"Return {permanent['name']} to its owner's hand."};state["stack"].append({"id":_id(),"kind":"dash_return_trigger","card":ability_card,"controller_id":controller["id"],"source_id":permanent["instance_id"]});_log(state,f"{permanent['name']}'s dash return trigger was put on the stack.")
            if state.get("monarch_id")==active["id"]:
                emblem={"instance_id":_id(),"scryfall_id":"monarch","name":"The Monarch","image_url":None,"type_line":"Emblem Ability","oracle_text":"Draw a card.","mana_cost":"","mana_value":0,"keywords":[],"power":None,"toughness":None,"owner_id":active["id"],"controller_id":active["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":False}
                state["stack"].append({"id":_id(),"kind":"trigger","card":emblem,"controller_id":active["id"],"target_id":None});_log(state,"The monarch's end-step draw triggered.")
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
    if action_type in {"pay_tilonalli","decline_tilonalli"}:
        pending=state.get("pending_tilonalli") or {}
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no Tilonalli payment decision for this player")
        if action_type=="pay_tilonalli":
            x_value=int(action.get("x_value") or 0);maximum=_maximum_x(player,{"mana_cost":"{X}{R}"})
            if not 0<=x_value<=maximum:raise RuleViolation("Choose a payable X value for Tilonalli's Summoner")
            _pay_mana(state,player,{"mana_cost":"{X}{R}"},x_value=x_value);group=_id();tokens=[]
            for _ in range(x_value):tokens.append({"instance_id":_id(),"scryfall_id":"token-elemental","name":"Elemental Token","image_url":None,"type_line":"Token Creature — Elemental","oracle_text":"","mana_cost":"","mana_value":0,"colors":["R"],"power":"1","toughness":"1","owner_id":player_id,"controller_id":player_id,"tapped":True,"damage":0,"counters":{},"summoning_sick":True,"token":True,"keywords":[],"tilonalli_exile_group":group})
            _enter_battlefield(state,player,tokens,"token");defender_id=pending.get("defender_id") or opponent(state,player_id)["id"]
            for token in tokens:state["combat"]["attackers"].append(token["instance_id"]);state["combat"]["attack_targets"][token["instance_id"]]=defender_id
            _log(state,f"{player['name']} paid {{X}}{{R}} with X={x_value} and created {x_value} tapped and attacking Elemental token(s).")
        else:_log(state,f"{player['name']} declined to pay for {pending.get('source_name') or 'Tilonalli Summoner'}.")
        state["pending_tilonalli"]=None;state["priority_player_id"]=state["active_player_id"]
    elif action_type=="choose_creature_type":
        pending_list=state.get("pending_creature_type") or [];pending=pending_list[0] if pending_list else None;choice=" ".join(str(action.get("creature_type") or "").strip().split())
        if not pending or pending["player_id"]!=player_id:raise RuleViolation("There is no creature-type choice for this player")
        if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,39}",choice):raise RuleViolation("Choose a valid creature type")
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending["card_id"]),None)
        if not permanent:raise RuleViolation("That permanent is no longer on the battlefield")
        permanent["chosen_creature_type"]=choice.title();pending_list.pop(0);state["pending_creature_type"]=pending_list;_sync_city_blessing(state);state["priority_player_id"]=pending_list[0]["player_id"] if pending_list else state["active_player_id"];_log(state,f"{player['name']} chose {permanent['chosen_creature_type']} for {permanent['name']}.")
    elif action_type in {"pay_cumulative_upkeep","sacrifice_cumulative_upkeep"}:
        pending=(state.get("pending_cumulative_upkeep") or [None])[0]
        if not pending or pending["player_id"]!=player_id:raise RuleViolation("There is no cumulative upkeep payment due")
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending["card_id"]),None);cost=_cumulative_upkeep_cost(permanent or {});age=pending["age"]
        if action_type=="pay_cumulative_upkeep":
            available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="pay_cumulative_upkeep" and entry.get("mana_cost","")==action.get("mana_cost","")),None)
            if not cost or not available:raise RuleViolation("That cumulative upkeep cost cannot be paid")
            selected=action.get("cost_card_ids") or [];kind=cost["kind"]
            if available.get("mana_cost"):_pay_mana(state,player,{"mana_cost":available["mana_cost"]})
            if available.get("life_cost"):player["life"]-=available["life_cost"]
            if available.get("cost_kind")=="snow":
                sources=[card for card in player["battlefield"] if card["instance_id"] in set(selected) and not card.get("tapped") and "Snow" in card.get("type_line","")]
                if len(sources)!=available["cost_amount"] or len(selected)!=len(sources):raise RuleViolation("Choose enough untapped snow sources")
                _set_tapped(state,sources,True,player_id,"cumulative_upkeep")
            elif available.get("cost_kind")=="graveyard_bottom":
                grave_owner=_player(state,available["upkeep_zone_owner"]);cards=[card for card in grave_owner["graveyard"] if card["instance_id"] in set(selected)]
                if len(cards)!=available["cost_amount"] or len(selected)!=len(cards):raise RuleViolation("Choose the required cards from one graveyard")
                for grave_card in cards:grave_owner["graveyard"].remove(grave_card);grave_owner["library"].insert(0,grave_card)
            elif available.get("cost_kind")=="opponent_counter":
                target=next((card for card in opponent(state,player_id)["battlefield"] if card["instance_id"] in set(selected) and "Creature" in card.get("type_line","")),None)
                if len(selected)!=1 or not target:raise RuleViolation("Choose an opponent's creature")
                _add_counters(state,target,"+1/+1",age,player_id,"cumulative_upkeep")
            elif available.get("cost_kind")=="gain_lands":
                other=opponent(state,player_id);lands=[card for card in other["battlefield"] if card["instance_id"] in set(selected) and "Land" in card.get("type_line","")]
                if len(lands)!=age or len(selected)!=len(lands):raise RuleViolation("Choose one opposing land per age counter")
                for land in lands:_change_control(state,land,player)
            elif kind=="discard":
                cards=[card for card in player["hand"] if card["instance_id"] in set(selected)]
                if len(selected)!=available["cost_amount"] or len(cards)!=len(selected):raise RuleViolation("Choose every cumulative-upkeep discard")
                _discard_cards(state,player,cards)
            elif kind.startswith("sacrifice_"):
                cards=[card for card in player["battlefield"] if card["instance_id"] in set(selected)]
                if len(selected)!=available["cost_amount"] or len(cards)!=len(selected):raise RuleViolation("Choose every cumulative-upkeep sacrifice")
                _sacrifice_permanents(state,player,cards)
            elif kind=="effect":
                effect=cost["effect"].casefold();other=opponent(state,player_id)
                for _ in range(age):
                    life=re.search(r"opponent gains? (\d+) life",effect)
                    if life:_gain_life(state,other,int(life.group(1)))
                    elif "draw a card" in effect:_draw(state,player)
                    elif "exile the top card of your library" in effect and player["library"]:_put_into_exile(state,player,[player["library"].pop()],"cumulative_upkeep",player_id)
                    elif "add {r}" in effect:player["firebending_mana"]=player.get("firebending_mana",0)+1
                    elif "put a -1/-1 counter on this creature" in effect:_add_counters(state,permanent,"-1/-1",1,player_id,"cumulative_upkeep")
                    elif "have an opponent create a 1/1 red survivor" in effect:_enter_battlefield(state,other,[_dungeon_token(other,"Survivor Token","1","1","Survivor",[])],"token")
                    elif "flip a coin" in effect:_queue_triggers(state,"coin_win" if random.SystemRandom().randrange(2) else "coin_lose",permanent,player)
                _log(state,f"{player['name']} performed {cost['effect']} {age} time(s).")
            _log(state,f"{player['name']} paid {permanent['name']}'s cumulative upkeep for {age} age counter(s).")
        elif permanent:_queue_triggers(state,"cumulative_unpaid",permanent,player);_leave_battlefield(state,player,permanent,"graveyard");_log(state,f"{permanent['name']} was sacrificed to cumulative upkeep.")
        state["pending_cumulative_upkeep"].pop(0)
        if not state["pending_cumulative_upkeep"]:state["pending_cumulative_upkeep"]=None
        state["priority_player_id"]=(state.get("pending_cumulative_upkeep") or [{"player_id":state["active_player_id"]}])[0]["player_id"]
    elif action_type in {"pay_echo","sacrifice_echo"}:
        pending=(state.get("pending_echo") or [None])[0]
        if not pending or pending["player_id"]!=player_id:raise RuleViolation("There is no echo payment due")
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending["card_id"]),None);cost=_echo_cost(permanent or {})
        if action_type=="pay_echo":
            selected=action.get("cost_card_ids") or []
            if not cost:raise RuleViolation("That echo cost is no longer available")
            if cost["kind"]=="mana":_pay_mana(state,player,{"mana_cost":cost["mana_cost"]})
            elif cost["kind"]=="discard":
                cards=[card for card in player["hand"] if card["instance_id"] in set(selected)]
                if len(selected)!=cost["amount"] or len(cards)!=cost["amount"]:raise RuleViolation("Choose the required Echo discard")
                _discard_cards(state,player,cards)
            else:
                lands=[card for card in player["battlefield"] if card["instance_id"] in set(selected) and "Land" in card.get("type_line","")]
                if len(selected)!=cost["amount"] or len(lands)!=cost["amount"]:raise RuleViolation("Choose the required lands for Echo")
                _sacrifice_permanents(state,player,lands)
            _log(state,f"{player['name']} paid {permanent['name']}'s echo cost.")
        elif permanent:_leave_battlefield(state,player,permanent,"graveyard");_log(state,f"{permanent['name']} was sacrificed to echo.")
        state["pending_echo"].pop(0)
        if not state["pending_echo"]:state["pending_echo"]=None
        state["priority_player_id"]=(state.get("pending_echo") or [{"player_id":state["active_player_id"]}])[0]["player_id"]
    elif action_type == "keep":
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
    elif action_type=="choose_escape_counter":
        pending=state.get("pending_escape_counter") or {};permanent=next((card for card in player["battlefield"] if card["instance_id"]==pending.get("card_id")),None);counter_name=action.get("counter_name")
        if pending.get("player_id")!=player_id or not permanent or counter_name not in {"+1/+1","flying"}:raise RuleViolation("That escape counter choice is no longer available")
        state["pending_escape_counter"]=None;_add_counters(state,permanent,counter_name,1,player_id,"escape");state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} put a {counter_name} counter on {permanent['name']} as it escaped.")
    elif action_type=="decline_rebound":
        pending=state.get("pending_rebound") or {};card=next((candidate for candidate in player["exile"] if candidate["instance_id"]==pending.get("card_id")),None)
        if pending.get("player_id")!=player_id or not card:raise RuleViolation("That Rebound choice is no longer available")
        state["pending_rebound"]=None;card.pop("rebound_triggered",None);card.pop("rebound_after_turn",None);state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} declined to cast {card['name']} from Rebound.")
    elif action_type in {"cast_madness","decline_madness"}:
        pending=state.get("pending_madness") or {};candidate=next((card for card in player["exile"] if card["instance_id"]==pending.get("card_id")),None);ability=_madness_ability(candidate or {})
        if pending.get("player_id")!=player_id or not candidate or not ability:raise RuleViolation("That madness choice is no longer available")
        state["pending_madness"]=None
        if action_type=="decline_madness":
            _leave_exile(state,player,[candidate]);player["graveyard"].append(candidate);state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} declined madness and put {candidate['name']} into their graveyard.")
        else:
            cost_card={**candidate,"mana_cost":ability["mana_cost"]};x_value=int(action.get("x_value") or 0);has_x=_has_x_cost(cost_card);maximum=_maximum_x(player,cost_card)
            if (has_x and not 0<=x_value<=maximum) or (not has_x and action.get("x_value") is not None):raise RuleViolation("Choose a legal value for X")
            target_id=action.get("target_id");targeting_card=_spell_targeting_card(candidate);targets=_targets(state,player_id,targeting_card)
            if _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target for the madness spell")
            if player["life"]<ability["life_cost"] or not _can_pay(player,cost_card,x_value=x_value):raise RuleViolation("That madness cost can no longer be paid")
            _pay_mana(state,player,cost_card,x_value=x_value);player["life"]-=ability["life_cost"];_leave_exile(state,player,[candidate]);stack_item={"id":_id(),"kind":"spell","card":candidate,"controller_id":player_id,"target_id":target_id,"target_ids":[],"mode_indices":[],"mode_targets":[],"x_value":x_value,"cast_source_zone":"exile","madness_cast":True};state["stack"].append(stack_item);_record_spell_cast(state,player);candidate["cast_source_zone"]="exile";_queue_triggers(state,"cast",candidate,player);_queue_cascade_triggers(state,player,candidate);_queue_storm_trigger(state,player,candidate,stack_item);candidate.pop("cast_source_zone",None);_queue_ward(state,player,target_id,stack_item);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
            if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"]
            x_label=f" with X={x_value}" if has_x else "";life_label=f" and paid {ability['life_cost']} life" if ability["life_cost"] else "";_log(state,f"{player['name']} cast {candidate['name']} for its madness cost {ability['mana_cost']}{x_label}{life_label}.")
    elif action_type in {"cast_discovered","hand_discovered","decline_discovery"}:
        pending=state.get("pending_discovery") or {};candidate=next((card for card in player["exile"] if card["instance_id"]==pending.get("candidate_id")),None)
        if pending.get("player_id")!=player_id or not candidate:raise RuleViolation("That discovered card is no longer available")
        target_id=action.get("target_id");targeting_card=_spell_targeting_card(candidate);targets=_targets(state,player_id,targeting_card)
        if action_type=="cast_discovered" and _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal target for the discovered spell")
        remaining=[card_id for card_id in pending["revealed_ids"] if card_id!=candidate["instance_id"]];state["pending_discovery"]=None
        if action_type=="cast_discovered":
            _leave_exile(state,player,[candidate]);stack_item={"id":_id(),"card":candidate,"controller_id":player_id,"target_id":target_id,"target_ids":[],"mode_indices":[],"mode_targets":[],"x_value":0,"free_cast":True,"cast_source_zone":"exile"};state["stack"].append(stack_item);_record_spell_cast(state,player);candidate["cast_source_zone"]="exile";_queue_triggers(state,"cast",candidate,player);_queue_cascade_triggers(state,player,candidate);_queue_storm_trigger(state,player,candidate,stack_item);candidate.pop("cast_source_zone",None);_log(state,f"{player['name']} cast {candidate['name']} without paying its mana cost.")
        elif action_type=="hand_discovered":
            _leave_exile(state,player,[candidate]);player["hand"].append(candidate);_log(state,f"{player['name']} put {candidate['name']} into their hand.")
        else:
            remaining.append(candidate["instance_id"]);_log(state,f"{player['name']} declined to cast {candidate['name']} with cascade.")
        _bottom_randomized_exiled(state,player,remaining);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if pending["mode"]=="discover":player["discover_event_value"]=pending["value"];_queue_triggers(state,"discover",None,player);player.pop("discover_event_value",None)
        if not state.get("pending_trigger_targets"):state["priority_player_id"]=opponent(state,player_id)["id"] if (_multiplayer(state) or not allow_direct_resolution) and action_type=="cast_discovered" else state["active_player_id"]
    elif action_type=="choose_manifest_dread":
        pending=state.get("pending_manifest") or {};card_id=action.get("card_id")
        if pending.get("player_id")!=player_id or card_id not in pending.get("card_ids",[]):raise RuleViolation("Choose one of the cards seen while manifesting dread")
        chosen=next((card for card in player["library"] if card["instance_id"]==card_id),None);other=next((card for card in player["library"] if card["instance_id"] in pending["card_ids"] and card["instance_id"]!=card_id),None)
        if not chosen or not other:raise RuleViolation("The top of the library changed before manifest dread finished")
        player["library"].remove(chosen);player["library"].remove(other);player["graveyard"].append(other);state["pending_manifest"]=None;_manifest_card(state,player,chosen,False,"library");_log(state,f"{player['name']} put one of the cards seen with manifest dread into their graveyard.")
        if pending.get("repeats",1)>1:_start_manifest_dread(state,player,pending["source_name"],pending["repeats"]-1)
        elif not state.get("pending_trigger_targets"):state["priority_player_id"]=state["active_player_id"]
    elif action_type=="turn_face_up":
        permanent=next((card for card in player["battlefield"] if card["instance_id"]==action.get("card_id") and card.get("face_down")),None);available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="turn_face_up" and entry["card_id"]==action.get("card_id")),None)
        if not permanent or not available:raise RuleViolation("That card cannot be turned face up right now")
        kind=available.get("turn_cost_kind");selected_ids=action.get("cost_card_ids") or [];amount=int(available.get("cost_amount") or 0);allowed=set(available.get("cost_options",[]))
        if kind in {"reveal","discard","return","sacrifice"} and (len(selected_ids)!=amount or len(set(selected_ids))!=amount or not set(selected_ids).issubset(allowed)):raise RuleViolation(f"Choose exactly {amount} legal card(s) for the turn-up cost")
        if kind=="life":player["life"]-=int(available.get("life_cost") or 0)
        elif kind=="reveal":
            revealed=[card for card in player["hand"] if card["instance_id"] in set(selected_ids)];_log(state,f"{player['name']} revealed {', '.join(card['name'] for card in revealed)}.")
        elif kind=="discard":_discard_cards(state,player,[card for card in player["hand"] if card["instance_id"] in set(selected_ids)])
        elif kind=="return":
            for selected in list(player["battlefield"]):
                if selected["instance_id"] in set(selected_ids):_leave_battlefield(state,player,selected,"hand")
        elif kind=="sacrifice":_sacrifice_permanents(state,player,[card for card in player["battlefield"] if card["instance_id"] in set(selected_ids)])
        else:_pay_mana(state,player,{"mana_cost":available["mana_cost"]})
        _turn_face_up(state,player,permanent)
    elif action_type=="choose_dungeon_room":
        pending=state.get("pending_dungeon") or {};room=action.get("room")
        if pending.get("player_id")!=player_id or pending.get("kind")!="room" or room not in pending.get("options",[]):raise RuleViolation("Choose a connected Undercity room")
        state["pending_dungeon"]=None;_enter_undercity_room(state,player,room)
    elif action_type=="choose_dungeon_target":
        pending=state.get("pending_dungeon") or {};target_id=action.get("target_id");allowed={target["id"] for target in pending.get("targets",[])}
        if pending.get("player_id")!=player_id or pending.get("kind")!="target" or target_id not in allowed:raise RuleViolation("Choose a legal creature for that Undercity room")
        target=next(card for owner in state["players"] for card in owner["battlefield"] if card["instance_id"]==target_id);room=pending["room"];state["pending_dungeon"]=None
        if room=="Forge":_add_counters(state,target,"+1/+1",2,player_id,"dungeon")
        else:target["goaded_until_turn"]=state["turn"]+1;target["goaded_by"]=player_id;_log(state,f"{target['name']} was goaded by {player['name']}.")
        state["priority_player_id"]=state["active_player_id"]
    elif action_type in {"choose_dungeon_card","skip_dungeon_card"}:
        pending=state.get("pending_dungeon") or {};card_id=action.get("card_id")
        if pending.get("player_id")!=player_id or pending.get("kind")!="throne" or (action_type=="choose_dungeon_card" and card_id not in pending.get("card_ids",[])):raise RuleViolation("Choose a creature revealed by the Throne")
        top_ids=pending["top_ids"];top=[card for card in player["library"] if card["instance_id"] in set(top_ids)];state["pending_dungeon"]=None
        if action_type=="choose_dungeon_card":
            chosen=next(card for card in top if card["instance_id"]==card_id);player["library"].remove(chosen);top.remove(chosen);chosen["controller_id"]=player_id;chosen["summoning_sick"]=True;chosen["hexproof_until_turn"]=state["turn"]+1;_add_counters(state,chosen,"+1/+1",3,player_id,"dungeon");_enter_battlefield(state,player,[chosen],"library")
        for card in top:player["library"].remove(card)
        random.SystemRandom().shuffle(top);player["library"][0:0]=top;state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} completed the Undercity.")
    elif action_type in {"keep_explored","graveyard_explored"}:
        pending=state.get("pending_explore") or {};card=next((card for card in player["library"] if card["instance_id"]==pending.get("card_id")),None)
        if pending.get("player_id")!=player_id or not card or player["library"][-1] is not card:raise RuleViolation("That explored card is no longer on top of the library")
        state["pending_explore"]=None
        if action_type=="graveyard_explored":player["library"].pop();player["graveyard"].append(card);_log(state,f"{player['name']} put {card['name']} into their graveyard after exploring.")
        else:_log(state,f"{player['name']} kept {card['name']} on top of their library after exploring.")
        _continue_explore(state,player)
        if not state.get("pending_explore"):state["priority_player_id"]=(state.get("pending_trigger_targets") or [{"controller_id":state["active_player_id"]}])[0]["controller_id"]
    elif action_type=="discard_connive":
        pending=state.get("pending_connive") or {};requested=action.get("card_ids") or [];required=pending.get("amount",0)
        if pending.get("player_id")!=player_id or len(requested)!=required or len(set(requested))!=required:raise RuleViolation(f"Choose exactly {required} cards to discard for connive")
        chosen=[card for card in player["hand"] if card["instance_id"] in set(requested)]
        if len(chosen)!=required:raise RuleViolation("One or more selected cards are not in your hand")
        nonlands=sum("Land" not in card.get("type_line","") for card in chosen);creature=next((card for card in player["battlefield"] if card["instance_id"]==pending.get("creature_id")),None);state["pending_connive"]=None;_discard_cards(state,player,chosen)
        if creature and nonlands:_add_counters(state,creature,"+1/+1",nonlands,player_id,"connive")
        if creature:_queue_triggers(state,"connive",creature,player);_log(state,f"{creature['name']} received {nonlands} +1/+1 counter{'s' if nonlands!=1 else ''} from conniving.")
        _continue_connive(state)
        if not state.get("pending_connive"):state["priority_player_id"]=(state.get("pending_trigger_targets") or [{"controller_id":state["active_player_id"]}])[0]["controller_id"]
    elif action_type=="cast_face_down":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);ability=_face_down_ability(card or {})
        if not card or not ability or not (state["active_player_id"]==player_id and state["phase"] in {"precombat_main","postcombat_main"} and not state["stack"]):raise RuleViolation("That card cannot be cast face down now")
        _pay_mana(state,player,{"mana_cost":"{3}"});player["hand"].remove(card);_make_face_down(card,player,False,ability);state["stack"].append({"id":_id(),"kind":"spell","card":card,"controller_id":player_id,"target_id":None,"target_ids":[],"mode_indices":[],"mode_targets":[],"face_down_cast":True,"cast_source_zone":"hand"});_record_spell_cast(state,player);_queue_triggers(state,"cast",card,player);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if _multiplayer(state) or not allow_direct_resolution:state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} cast a creature spell face down for {{3}}.")
    elif action_type == "play_land":
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id") and "Land" in card.get("type_line", "")), None)
        if not card: raise RuleViolation("That land is not in your hand")
        player["hand"].remove(card);card["summoning_sick"]=True;_enter_battlefield(state,player,[card],"hand",played=True);player["lands_played_this_turn"]=player.get("lands_played_this_turn",0)+1;player["land_plays_remaining"]-=1;_log(state,f"{player['name']} played {card['name']}.")
    elif action_type=="channel":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);abilities=_channel_abilities(card or {});ability_index=int(action.get("ability_index") or 0);ability=abilities[ability_index] if 0<=ability_index<len(abilities) else None;requested_target_count=len(action.get("target_ids") or []);available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="channel" and entry["card_id"]==action.get("card_id") and entry.get("ability_index")==ability_index and (entry.get("channel_target_count") is None or entry.get("channel_target_count")==requested_target_count)),None)
        if not card or not ability or not available:raise RuleViolation("That Channel ability cannot be activated now")
        cost_card={**card,"mana_cost":ability["mana_cost"]};x_value=int(action.get("x_value") or 0);has_x=_has_x_cost(cost_card)
        if (has_x and not available.get("x_min",0)<=x_value<=available.get("x_max",0)) or (not has_x and action.get("x_value") is not None):raise RuleViolation("Choose a legal Channel X value")
        target_id=action.get("target_id");targets=_targets(state,player_id,_spell_targeting_card(ability["card"]));target_steps=(available.get("target_steps_by_x") or {}).get(x_value,available.get("target_steps",[]));target_ids=action.get("target_ids") or []
        if target_steps:
            if len(target_ids)!=len(target_steps) or any(target_id_value not in {target["id"] for target in target_steps[index]["targets"]} for index,target_id_value in enumerate(target_ids)) or len(target_ids)!=len(set(target_ids)):raise RuleViolation("Choose the required number of distinct Channel targets")
        elif target_ids:raise RuleViolation("That Channel ability does not use multiple targets")
        elif _target_kind(_spell_targeting_card(ability["card"])) and target_id not in {target["id"] for target in targets}:raise RuleViolation("Choose a legal Channel target")
        stack_before_cost=len(state["stack"]);_pay_mana(state,player,cost_card,-_channel_reduction(player,ability),x_value=x_value);_discard_cards(state,player,[card]);cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        stack_item={"id":_id(),"kind":"channel_ability","card":ability["card"],"controller_id":player_id,"source_id":card["instance_id"],"target_id":target_id,"target_ids":target_ids,"x_value":x_value};state["stack"].append(stack_item);state["stack"].extend(cost_triggers);state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        for ward_target in [target for target in [target_id,*target_ids] if target]:_queue_ward(state,player,ward_target,stack_item)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward"):state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} channeled {card['name']}{f' with X={x_value}' if has_x else ''}.")
    elif action_type == "cast":
        requested_source=action.get("source");zone_name="graveyard" if requested_source in {"flashback","escape","mutate_graveyard"} else "exile" if requested_source in {"airbend","suspend","foretell","plot","rebound"} else "hand" if requested_source in {"mutate_hand","evoke","dash_hand"} else "command" if requested_source in {"mutate_command","dash_command"} else requested_source if requested_source in {"hand","command"} else next((zone for zone in ("hand","command") if any(card["instance_id"]==action.get("card_id") for card in player.get(zone,[]))),None)
        source=requested_source if requested_source in {"flashback","escape","mutate_graveyard","airbend","suspend","foretell","plot","rebound","mutate_hand","mutate_command","evoke","dash_hand","dash_command"} else zone_name;card=next((card for card in player.get(zone_name or "hand",[]) if card["instance_id"]==action.get("card_id")),None);flashback=_flashback_ability(card or {}) if source=="flashback" else None;escape=_escape_ability(card or {}) if source=="escape" else None
        requested_kicked=bool(action.get("kicked"));requested_entwined=bool(action.get("entwined"));requested_buyback=bool(action.get("buyback"));requested_convoke=bool(action.get("convoke"));requested_waterbend=bool(action.get("waterbend"));requested_overloaded=bool(action.get("overloaded"));requested_evoked=requested_source=="evoke";requested_dashed=requested_source in {"dash_hand","dash_command"};requested_delve=bool(action.get("delve"));requested_mutating=bool(action.get("mutating") or requested_source in {"mutate_hand","mutate_graveyard","mutate_command"});requested_blight=bool(action.get("blighted") or (action.get("cost_card_ids") and _optional_blight_cost(card or {})));requested_blessing_top=bool(action.get("blessing_top"));available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="cast" and entry["card_id"]==action.get("card_id") and entry.get("source")==source and bool(entry.get("entwined"))==requested_entwined and bool(entry.get("overloaded"))==requested_overloaded and bool(entry.get("evoked"))==requested_evoked and bool(entry.get("dashed"))==requested_dashed and bool(entry.get("delve"))==requested_delve and (not requested_delve or entry.get("cost_amount")==len(action.get("cost_card_ids") or [])) and bool(entry.get("mutating"))==requested_mutating and entry.get("mutate_position")==action.get("mutate_position") and bool(entry.get("kicked"))==requested_kicked and bool(entry.get("buyback"))==requested_buyback and bool(entry.get("convoke"))==requested_convoke and bool(entry.get("waterbend"))==requested_waterbend and bool(entry.get("blighted"))==requested_blight and bool(entry.get("blessing_top"))==requested_blessing_top),None)
        tax = _commander_tax(player, card) if card and source in {"command","mutate_command","dash_command"} else 0;affinity_reduction=_affinity_reduction(player,card or {});buyback=_buyback_ability(card or {}) if requested_buyback else None;generic_adjustment=tax-affinity_reduction-(_dash_reduction(player) if requested_dashed else 0)-int(available.get("buyback_reduction",0) if available else 0)
        if not card:raise RuleViolation("That spell cannot be cast")
        if not available:raise RuleViolation("That spell cannot be cast from that zone")
        cost_card={**card,"mana_cost":_overload_cost(card) or ""} if requested_overloaded else {**card,"mana_cost":_dash_cost(card)} if requested_dashed else {**card,"mana_cost":(_evoke_ability(card) or {}).get("mana_cost","")} if requested_evoked else {**card,"mana_cost":_mutate_cost(card)} if requested_mutating else {**card,"mana_cost":_foretell_cost(card) or ""} if source=="foretell" else {**card,"mana_cost":"{0}"} if source in {"suspend","plot","rebound"} else {**card,"mana_cost":"{2}"} if source=="airbend" else {**card,"mana_cost":flashback["mana_cost"]} if flashback else {**card,"mana_cost":escape["mana_cost"]} if escape else card
        if requested_kicked:cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{_kicker_cost(card) or ''}"}
        entwine=_entwine_ability(card) if requested_entwined else None
        if entwine:cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{entwine['mana_cost']}"}
        if buyback:cost_card={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{buyback['mana_cost']}"}
        waterbend_symbol=_spell_waterbend_symbol(card);x_value=int(action.get("x_value") or 0);has_x=_has_x_cost(cost_card) or waterbend_symbol=="X";x_max=available.get("x_max",_maximum_x(player,cost_card,generic_adjustment))
        if (has_x and not available.get("x_min",0)<=x_value<=x_max) or (not has_x and action.get("x_value") is not None): raise RuleViolation("That spell cannot be cast with the chosen X value")
        selected_cost_ids=action.get("cost_card_ids") or [];required_cost=available.get("cost_amount",0);cost_options=set(available.get("cost_options",[]))
        stack_before_cost=len(state["stack"])
        if escape:
            if len(selected_cost_ids)!=len(set(selected_cost_ids)) or not set(selected_cost_ids).issubset(cost_options):raise RuleViolation("Choose only legal cards for the escape cost")
            selected_grave=[candidate for candidate in player["graveyard"] if candidate is not card and candidate["instance_id"] in set(selected_cost_ids)];selected_lands=[candidate for candidate in player["battlefield"] if "Land" in candidate.get("type_line","") and candidate["instance_id"] in set(selected_cost_ids)]
            if len(selected_lands)!=escape["land_count"] or (escape["card_types_required"] and len(_graveyard_card_types(selected_grave))<escape["card_types_required"]) or (not escape["card_types_required"] and len(selected_grave)!=escape["exile_count"]):raise RuleViolation("Choose cards that satisfy every part of the escape cost")
        elif requested_waterbend:
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
        convoke_residual=_convoke_residual(player,cost_card,selected_cost_ids,generic_adjustment,x_value) if requested_convoke else None;waterbend_amount=x_value if waterbend_symbol=="X" else int(waterbend_symbol or 0);waterbend_base={**cost_card,"mana_cost":f"{cost_card.get('mana_cost') or ''}{f'{{{generic_adjustment}}}' if generic_adjustment>0 else ''}"};waterbend_residual=_waterbend_residual(player,waterbend_base,waterbend_amount,selected_cost_ids,x_value=x_value if _has_x_cost(cost_card) else 0) if requested_waterbend else None
        if (requested_waterbend and waterbend_residual is None) or (requested_convoke and convoke_residual is None) or (not requested_waterbend and not requested_convoke and not _can_pay(player,cost_card,generic_adjustment-(len(selected_cost_ids) if requested_delve else 0),x_value=x_value)):raise RuleViolation("That spell cannot be cast with the chosen payment")
        modal_spec=_modal_spec(card);modal_options=(modal_spec or {}).get("options",[]);chosen_modes=action.get("chosen_modes") or [];mode_targets=action.get("mode_targets") or []
        if requested_entwined and modal_spec:modal_spec={**modal_spec,"min_modes":len(modal_options),"max_modes":len(modal_options)}
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
        if requested_mutating:
            target_id=action.get("target_id")
            if target_id not in {target["id"] for target in available.get("targets",[])}:raise RuleViolation("Choose a non-Human creature you own to mutate")
        elif requested_overloaded:
            if target_id is not None:raise RuleViolation("An overloaded spell does not target")
        elif not modal_spec and _target_kind(targeting_card) and target_id not in {target["id"] for target in targets}: raise RuleViolation("Choose a legal target")
        if requested_waterbend:
            _pay_mana(state,player,waterbend_residual or {"mana_cost":""},excluded_ids=set(selected_cost_ids),x_value=x_value if _has_x_cost(cost_card) else 0)
            _set_tapped(state,[permanent for permanent in player["battlefield"] if permanent["instance_id"] in selected_cost_ids],True,player_id,"waterbend")
        elif requested_convoke:
            _pay_mana(state,player,convoke_residual or {"mana_cost":""},excluded_ids=set(selected_cost_ids))
            _set_tapped(state,[creature for creature in player["battlefield"] if creature["instance_id"] in selected_cost_ids],True,player_id,"convoke")
        else:_pay_mana(state,player,cost_card,generic_adjustment-(len(selected_cost_ids) if requested_delve else 0),x_value=x_value)
        if requested_evoked and available.get("cost_kind")=="evoke_exile":
            pitch=next((candidate for candidate in player["hand"] if candidate["instance_id"] in set(selected_cost_ids) and candidate is not card),None)
            if not pitch:raise RuleViolation("Choose the required colored card to exile for evoke")
            player["hand"].remove(pitch);_put_into_exile(state,player,[pitch],"evoke",player_id)
        delved_cards=[]
        if requested_delve:
            delved_cards=[candidate for candidate in player["graveyard"] if candidate["instance_id"] in set(selected_cost_ids)]
            if len(delved_cards)!=len(selected_cost_ids):raise RuleViolation("Choose only cards currently in your graveyard for delve")
            _leave_graveyard(state,player,delved_cards);_put_into_exile(state,player,delved_cards,"delve",player_id)
        if buyback:
            if buyback["life_cost"]>=player["life"]:raise RuleViolation("You cannot pay the buyback life cost")
            player["life"]-=buyback["life_cost"]
            if buyback["discard_count"]:
                candidates=[candidate for candidate in player["hand"] if candidate is not card]
                discard_cards=random.SystemRandom().sample(candidates,buyback["discard_count"]) if buyback["discard_random"] else [candidate for candidate in candidates if candidate["instance_id"] in set(selected_cost_ids)]
                if len(discard_cards)!=buyback["discard_count"]:raise RuleViolation("Choose the required cards to discard for buyback")
                _discard_cards(state,player,discard_cards)
            if buyback["sacrifice_count"]:
                sacrifice_cards=[candidate for candidate in player["battlefield"] if candidate["instance_id"] in set(selected_cost_ids)]
                if len(sacrifice_cards)!=buyback["sacrifice_count"]:raise RuleViolation("Choose the required lands to sacrifice for buyback")
                _sacrifice_permanents(state,player,sacrifice_cards)
        if escape:
            exile_cards=[candidate for candidate in player["graveyard"] if candidate["instance_id"] in set(selected_cost_ids) and candidate is not card]
            _leave_graveyard(state,player,exile_cards);_put_into_exile(state,player,exile_cards,"escape",player_id)
            for land in [candidate for candidate in list(player["battlefield"]) if candidate["instance_id"] in set(selected_cost_ids) and "Land" in candidate.get("type_line","")]:_leave_battlefield(state,player,land,"exile",exile_actor_id=player_id)
        if requested_blight:
            blight_target=next((creature for creature in player["battlefield"] if creature["instance_id"] in set(selected_cost_ids) and "Creature" in creature.get("type_line","")),None)
            if not blight_target:raise RuleViolation("Choose one creature you control to blight")
            _apply_blight(state,player,blight_target,int(available.get("blight_amount",0)))
        if entwine and entwine["sacrifice_lands"]:
            lands=[candidate for candidate in list(player["battlefield"]) if candidate["instance_id"] in set(selected_cost_ids) and "Land" in candidate.get("type_line","")]
            if len(lands)!=entwine["sacrifice_lands"]:raise RuleViolation("Choose the required lands for entwine")
            _sacrifice_permanents(state,player,lands)
        if zone_name=="graveyard":_leave_graveyard(state,player,[card])
        elif zone_name=="exile":_leave_exile(state,player,[card])
        else:player[zone_name].remove(card)
        cost_triggers=state["stack"][stack_before_cost:];del state["stack"][stack_before_cost:]
        if source=="rebound":state["pending_rebound"]=None
        card.pop("rebound_triggered",None);card.pop("rebound_after_turn",None);card.pop("airbent",None);card.pop("suspended_ready",None);card.pop("suspended",None);card.pop("foretold",None);card.pop("foretold_turn",None);card.pop("plotted",None);card.pop("plotted_turn",None)
        if card.get("commander"): player["commander_casts"] = player.get("commander_casts", 0) + 1
        effective_target=target_id or (mode_targets[0] if len(mode_targets)==1 else None);stack_item={"id": _id(), "card": card, "controller_id": player_id, "target_id": effective_target,"target_ids":target_ids,"mode_indices":chosen_modes,"mode_targets":mode_targets,"x_value":x_value,"flashback":bool(flashback),"buyback":requested_buyback,"rebound_cast":source=="rebound","escaped":bool(escape),"escape_counters":escape["counters"] if escape else 0,"escape_counter_choice":escape["counter_choice"] if escape else False,"suspended_cast":source=="suspend","kicked":requested_kicked,"entwined":requested_entwined,"overloaded":requested_overloaded,"blighted":requested_blight,"blessing_top":requested_blessing_top,"evoked":requested_evoked,"dashed":requested_dashed,"delved_cards":delved_cards if requested_delve else [],"mutating":requested_mutating,"mutate_position":action.get("mutate_position"),"cast_source_zone":"graveyard" if source in {"flashback","escape","mutate_graveyard"} else "exile" if source in {"airbend","suspend","foretell","plot","rebound"} else "hand" if source in {"mutate_hand","evoke","dash_hand"} else "command" if source in {"mutate_command","dash_command"} else source};state["stack"].append(stack_item);state["stack"].extend(cost_triggers); state["consecutive_passes"] = 0; state["pending_phase_advance"] = False
        if requested_waterbend:_queue_triggers(state,"waterbend",card,player)
        _record_spell_cast(state,player);card["cast_source_zone"]="graveyard" if source in {"flashback","escape","mutate_graveyard"} else "exile" if source in {"airbend","suspend","foretell","plot"} else "hand" if source=="mutate_hand" else "command" if source=="mutate_command" else source
        _queue_triggers(state,"cast",card,player);_queue_cascade_triggers(state,player,card);_queue_storm_trigger(state,player,card,stack_item);card.pop("cast_source_zone",None)
        ward_targets=[effective_target] if effective_target else []
        ward_targets.extend(target for target in mode_targets if target and target not in ward_targets)
        ward_targets.extend(target for target in target_ids if target not in ward_targets)
        for ward_target in ward_targets:_queue_ward(state,player,ward_target,stack_item)
        if (_multiplayer(state) or not allow_direct_resolution) and not state.get("pending_ward") and not state.get("pending_trigger_targets"): state["priority_player_id"] = opponent(state, player_id)["id"]
        mode_label="; ".join(next(mode["label"] for mode in modal_options if mode["index"]==index) for index in chosen_modes)
        behold_names=[next(candidate["name"] for zone in (player["hand"],player["battlefield"]) for candidate in zone if candidate["instance_id"]==card_id) for card_id in selected_cost_ids] if available.get("cost_kind")=="behold" else []
        _log(state, f"{player['name']} cast {card['name']}{' from foretell' if source=='foretell' else ' from suspend without paying its mana cost' if source=='suspend' else ' using flashback' if flashback else ' using airbend' if source=='airbend' else ''}{' with kicker' if requested_kicked else ''}{' using waterbend' if requested_waterbend else ''}{' using convoke' if requested_convoke else ''}{' after blighting' if requested_blight else ''}{f' with X={x_value}' if has_x else ''}{f' choosing {mode_label}' if mode_label else ''}{f' with {tax} commander tax' if tax else ''}{f' and {affinity_reduction} affinity reduction' if affinity_reduction else ''}{f' by beholding {', '.join(behold_names)}' if behold_names else ''}{' targeting '+next((target['name'] for target in targets if target['id']==target_id),'') if target_id else ''}.")
    elif action_type == "suspend":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);ability=_suspend_ability(card or {});available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="suspend" and entry["card_id"]==action.get("card_id")),None)
        if not card or not ability or not available:raise RuleViolation("That card cannot be suspended now")
        count=int(action.get("x_value") or 0) if ability["count"]=="X" else int(ability["count"])
        if ability["count"]=="X" and not available.get("x_min",1)<=count<=available.get("x_max",0):raise RuleViolation("Choose a legal number of time counters")
        _pay_mana(state,player,{"mana_cost":ability["mana_cost"]},x_value=count);player["hand"].remove(card);card["suspended"]=True;card.setdefault("counters",{})["time"]=count;_put_into_exile(state,player,[card],"hand",player_id);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_log(state,f"{player['name']} suspended {card['name']} with {count} time counter{'s' if count!=1 else ''}.")
    elif action_type == "unearth":
        card=next((card for card in player["graveyard"] if card["instance_id"]==action.get("card_id")),None);ability=_unearth_ability(card or {});available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="unearth" and entry["card_id"]==action.get("card_id")),None)
        if not card or not ability or not available:raise RuleViolation("That card cannot be unearthed now")
        if ability["mana_cost"]:_pay_mana(state,player,{"mana_cost":ability["mana_cost"]})
        if ability["energy_cost"]:_pay_energy(state,player,ability["energy_cost"])
        ability_card={**card,"name":f"{card['name']} — Unearth","type_line":"Ability","mana_cost":"","oracle_text":f"Return {card['name']} from your graveyard to the battlefield. It gains haste. Exile it at the beginning of the next end step or if it would leave the battlefield."};state["stack"].append({"id":_id(),"kind":"unearth_ability","card":ability_card,"controller_id":player_id,"source_id":card["instance_id"]});state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if _multiplayer(state) or not allow_direct_resolution:state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} activated {card['name']}'s unearth ability.")
    elif action_type == "foretell":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="foretell" and entry["card_id"]==action.get("card_id")),None)
        if not card or not _foretell_cost(card) or not available:raise RuleViolation("That card cannot be foretold now")
        _pay_mana(state,player,{"mana_cost":"{2}"});player["hand"].remove(card);card["foretold"]=True;card["foretold_turn"]=state["turn"];_put_into_exile(state,player,[card],"hand",player_id);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;_log(state,f"{player['name']} foretold a card face down.")
    elif action_type == "plot":
        card=next((card for card in player["hand"] if card["instance_id"]==action.get("card_id")),None);cost=_plot_cost(card or {});available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="plot" and entry["card_id"]==action.get("card_id")),None)
        if not card or not cost or not available:raise RuleViolation("That card cannot be plotted now")
        reduction=_plot_reduction(player);_pay_mana(state,player,{"mana_cost":cost},-reduction);player["hand"].remove(card);card["plotted"]=True;card["plotted_turn"]=state["turn"];_put_into_exile(state,player,[card],"hand",player_id);state["consecutive_passes"]=0;state["pending_phase_advance"]=False;reduction_label=f" with {reduction} cost reduction" if reduction else "";_log(state,f"{player['name']} plotted {card['name']} face up for {cost}{reduction_label}.")
    elif action_type == "ninjutsu":
        available=next((entry for entry in legal_actions(state,player_id,allow_direct_resolution) if entry["type"]=="ninjutsu" and entry["card_id"]==action.get("card_id") and entry["source"]==action.get("source")),None);attacker_id=action.get("target_id")
        if not available or attacker_id not in {target["id"] for target in available["targets"]}:raise RuleViolation("Choose an unblocked attacker to return for ninjutsu")
        ninja=next((card for card in player[available["source"]] if card["instance_id"]==available["card_id"]),None);attacker=next((card for card in player["battlefield"] if card["instance_id"]==attacker_id),None)
        if not ninja or not attacker:raise RuleViolation("The Ninja or returning attacker is no longer available")
        defender_id=state["combat"]["attack_targets"].get(attacker_id,opponent(state,player_id)["id"]);_pay_mana(state,player,{"mana_cost":available["mana_cost"]});_leave_battlefield(state,player,attacker,"hand")
        ability_card={**ninja,"name":f"{ninja['name']} — {'Commander ' if available['source']=='command' else ''}Ninjutsu","type_line":"Ability","mana_cost":""}
        state["stack"].append({"id":_id(),"kind":"ninjutsu_ability","card":ability_card,"controller_id":player_id,"source_id":ninja["instance_id"],"source_zone":available["source"],"defender_id":defender_id});state["consecutive_passes"]=0;state["pending_phase_advance"]=False
        if _multiplayer(state) or not allow_direct_resolution:state["priority_player_id"]=opponent(state,player_id)["id"]
        _log(state,f"{player['name']} returned {attacker['name']} and activated {ninja['name']}'s ninjutsu ability.")
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
        waterbend_symbol=ability.get("waterbend_symbol");energy_cost=ability.get("energy_cost",0);x_value=int(action.get("x_value") or 0);x_card={"mana_cost":ability["mana_cost"]};has_x=_has_x_cost(x_card) or waterbend_symbol=="X" or energy_cost=="X" or bool(available.get("selection_x"));x_max=available.get("x_max",_maximum_x(player,x_card,excluded_id=permanent["instance_id"] if ability["taps"] else None))
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
        elif ability["mana_cost"]:_pay_mana(state,player,{"mana_cost":ability["mana_cost"]},-int(available.get("generic_reduction") or 0),permanent["instance_id"] if ability["taps"] else None,x_value=x_value)
        _pay_energy(state,player,x_value if energy_cost=="X" else int(energy_cost or 0))
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
        if ability.get("self_bottom") and permanent in player["battlefield"]:
            _leave_battlefield(state,player,permanent,"library");owner=_player(state,permanent.get("owner_id",player["id"]));owner["library"].remove(permanent);owner["library"].insert(0,permanent)
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
        goaded={card["instance_id"] for card in player["battlefield"] if card["instance_id"] in eligible and card.get("goaded_until_turn",0)>=state["turn"]}
        if not goaded.issubset(requested):raise RuleViolation("Goaded creatures must attack if able")
        if len(requested)==1:
            lone=next(card for card in player["battlefield"] if card["instance_id"] in requested)
            if "can't attack or block alone" in _effective_rules_text(state,lone):raise RuleViolation(f"{lone['name']} can't attack alone")
        attack_action=next(entry for entry in legal_actions(state,player_id) if entry["type"]=="declare_attackers");defender_ids={target["id"] for target in attack_action.get("defenders",[])};requested_targets=action.get("attack_targets") or {};default_target=opponent(state,player_id)["id"]
        if any(requested_targets.get(attacker_id,default_target) not in defender_ids for attacker_id in requested):raise RuleViolation("Choose a legal defender for every attacker")
        state["combat"]["attackers"] = list(requested);state["combat"]["attackers_declared"]=True
        state["combat"]["attack_targets"]={attacker_id:requested_targets.get(attacker_id,default_target) for attacker_id in requested}
        _set_tapped(state,[card for card in player["battlefield"] if card["instance_id"] in requested and not _has_keyword(card,"Vigilance")],True,player_id,"attack")
        if len(requested)==1:_queue_exalted_triggers(state,player,next(card for card in player["battlefield"] if card["instance_id"] in requested))
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
            _queue_block_keyword_triggers(state,attacking_owner,player)
            _queue_triggers(state,"blockers_declared",None,player);state["combat"]["damage_pending"]=True
            if not state.get("pending_trigger_targets"):state["priority_player_id"] = state["active_player_id"]
            _log(state,"Blockers were finalized. Players may respond before combat damage.")
    elif action_type == "order_blockers":
        pending=state.get("pending_damage_order") or {};orders=action.get("block_orders") or {};expected=pending.get("groups",{})
        if pending.get("player_id")!=player_id or set(orders)!=set(expected) or any(len(order)!=len(expected[attacker_id]) or len(set(order))!=len(order) or set(order)!=set(expected[attacker_id]) for attacker_id,order in orders.items()):raise RuleViolation("Order every creature blocking each attacker exactly once")
        state["combat"]["block_orders"]=orders;state["pending_damage_order"]=None;state["combat"]["damage_pending"]=True
        if state["combat"].pop("block_triggers_pending",False):
            defender=opponent(state,state["active_player_id"]);_queue_block_keyword_triggers(state,_player(state,state["active_player_id"]),defender);_queue_triggers(state,"blockers_declared",None,defender)
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
    elif action_type in {"choose_populate_token","skip_populate"}:
        pending=state.get("pending_populate") or {};target_id=action.get("target_id")
        if pending.get("player_id")!=player_id:raise RuleViolation("There is no populate choice for this player")
        source=next((card for card in player["battlefield"] if card["instance_id"]==target_id and card.get("token") and "Creature" in card.get("type_line","")),None)
        if action_type=="choose_populate_token" and (not source or target_id not in pending.get("card_ids",[])):raise RuleViolation("Choose one of your creature tokens to populate")
        state["pending_populate"]=None
        if source:_finish_populate(state,player,source,pending)
        else:_log(state,f"{player['name']} could not finish populating because no eligible token remained.")
        if not state.get("pending_populate"):state["priority_player_id"]=state["active_player_id"]
    elif action_type=="choose_bolster_creature":
        pending=state.get("pending_bolster") or {};target_id=action.get("target_id")
        if pending.get("player_id")!=player_id or target_id not in pending.get("card_ids",[]):raise RuleViolation("Choose a creature tied for least toughness")
        target=next((card for card in player["battlefield"] if card["instance_id"]==target_id and "Creature" in card.get("type_line","")),None)
        if not target:raise RuleViolation("That bolster creature is no longer on the battlefield")
        current=[card for card in player["battlefield"] if "Creature" in card.get("type_line","")];minimum=min((_parse_stats(card,state)[1] for card in current),default=None)
        if minimum is None or _parse_stats(target,state)[1]!=minimum:raise RuleViolation("That creature no longer has the least toughness")
        state["pending_bolster"]=None;_add_counters(state,target,"+1/+1",pending["amount"],player_id,"bolster")
        if pending.get("grant_trample"):target["temporary_keywords"]=sorted(set(target.get("temporary_keywords",[]))|{"Trample"})
        state["priority_player_id"]=state["active_player_id"];_log(state,f"{player['name']} bolstered {target['name']} {pending['amount']}.")
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
        elif targets and not pending.get("optional"):raise RuleViolation("This triggered ability still has legal targets")
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
