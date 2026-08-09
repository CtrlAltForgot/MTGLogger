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


def _parse_stats(card: dict) -> tuple[int, int]:
    try:
        return int(card.get("power") or 0), int(card.get("toughness") or 0)
    except ValueError:
        return 0, 0


def _mana_symbols(card: dict) -> list[str]:
    return re.findall(r"\{([^}]+)\}", card.get("mana_cost") or "")


def _land_colors(card: dict) -> set[str]:
    text = f"{card.get('name', '')} {card.get('type_line', '')} {card.get('oracle_text', '')}"
    colors = {color for land, color in BASIC_COLORS.items() if land.casefold() in text.casefold()}
    colors.update(re.findall(r"Add \{([WUBRG])\}", text, re.IGNORECASE))
    if "mana of any color" in text.casefold():
        colors.update("WUBRG")
    return {color.upper() for color in colors}


def _can_pay(player: dict, card: dict, extra_generic: int = 0) -> bool:
    available = []
    for permanent in player["battlefield"]:
        if not permanent.get("tapped") and "Land" in permanent.get("type_line", ""):
            available.append(_land_colors(permanent) or {"C"})
    symbols = _mana_symbols(card)
    colored = [symbol for symbol in symbols if symbol in "WUBRG"]
    generic = sum(int(symbol) for symbol in symbols if symbol.isdigit()) + extra_generic
    for symbol in colored:
        match = next((colors for colors in available if symbol in colors), None)
        if not match:
            return False
        available.remove(match)
    return len(available) >= generic


def _pay_mana(player: dict, card: dict, extra_generic: int = 0) -> None:
    symbols = _mana_symbols(card)
    colored = [symbol for symbol in symbols if symbol in "WUBRG"]
    generic = sum(int(symbol) for symbol in symbols if symbol.isdigit()) + extra_generic
    lands = [permanent for permanent in player["battlefield"] if not permanent.get("tapped") and "Land" in permanent.get("type_line", "")]
    chosen = []
    for symbol in colored:
        land = next((item for item in lands if symbol in _land_colors(item)), None)
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
    return {"id": player_id, "name": name, "is_bot": is_bot, "format": format_name, "life": 40 if is_commander else 20, "library": library, "hand": [], "battlefield": [], "graveyard": [], "exile": [], "command": command, "commander_casts": 0, "commander_damage": {}, "land_plays_remaining": 1, "kept_hand": False, "lost": False}


def new_game(player_deck: list[dict], opponent_deck: list[dict], play_first: bool = True, opponent_is_bot: bool = True, player_format: str = "", opponent_format: str = "") -> dict:
    human_id, bot_id = "player", "bot"
    players = [_new_player(human_id, "You", player_deck, False, player_format), _new_player(bot_id, "Bot" if opponent_is_bot else "Guest", opponent_deck, opponent_is_bot, opponent_format)]
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id, "players": players, "stack": [], "combat": {"attackers": [], "blocks": {}}, "consecutive_passes": 0, "pending_phase_advance": False, "log": []}
    for player in players:
        _draw(state, player, 7)
    _log(state, "Opening hands drawn. Choose whether to keep or mulligan.")
    return state


def public_state(state: dict, viewer_id: str = "player") -> dict:
    visible = deepcopy(state)
    for player in visible["players"]:
        player["library_count"] = len(player.pop("library"))
        if player["id"] != viewer_id:
            player["hand_count"] = len(player["hand"])
            player["hand"] = []
    return visible


def _target_kind(card: dict) -> str | None:
    text = (card.get("oracle_text") or "").casefold()
    if re.search(r"(?:destroy|exile) target (?:artifact, creature, enchantment, planeswalker|nonland permanent|permanent)", text): return "permanent"
    if re.search(r"(?:destroy|exile) target creature", text) or re.search(r"deals \d+ damage to target creature", text): return "creature"
    if re.search(r"deals \d+ damage to any target", text): return "any"
    return None


def _targets(state: dict, caster_id: str, card: dict) -> list[dict]:
    kind = _target_kind(card)
    if not kind: return []
    targets = []
    for player in state["players"]:
        if kind == "any": targets.append({"id": player["id"], "name": player["name"], "kind": "player", "controller_id": player["id"]})
        for permanent in player["battlefield"]:
            if kind in {"any", "permanent"} or (kind == "creature" and "Creature" in permanent.get("type_line", "")):
                targets.append({"id": permanent["instance_id"], "name": permanent["name"], "kind": "permanent", "controller_id": player["id"]})
    return targets


def _multiplayer(state: dict) -> bool:
    return not any(player.get("is_bot") for player in state["players"])


def _commander_tax(player: dict, card: dict) -> int:
    return player.get("commander_casts", 0) * 2 if card.get("commander") else 0


def legal_actions(state: dict, player_id: str) -> list[dict]:
    if state["status"] == "complete":
        return []
    player = _player(state, player_id)
    if state["status"] == "mulligan":
        if player["kept_hand"]:
            return []
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
            instant_speed = "Instant" in card.get("type_line", "") or "Flash" in (card.get("oracle_text") or "")
            if "Land" in card.get("type_line", "") or not ((active and main and not state["stack"]) or instant_speed) or not _can_pay(player, card, _commander_tax(player, card)): continue
            targets = _targets(state, player_id, card)
            if _target_kind(card) and not targets: continue
            action = {"type": "cast", "card_id": card["instance_id"], "source": source, "commander_tax": _commander_tax(player, card)}
            if targets: action["targets"] = targets
            actions.append(action)
    if _multiplayer(state):
        actions.append({"type": "pass_priority"})
        if active and not state["stack"] and not state.get("pending_phase_advance"):
            actions.append({"type": "advance_phase"})
    elif state["stack"]:
        actions.append({"type": "resolve"})
    else:
        actions.append({"type": "advance_phase"})
    if active and state["phase"] == "combat" and not state["combat"]["attackers"]:
        eligible = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped") and not card.get("summoning_sick")]
        if eligible:
            actions.append({"type": "declare_attackers", "card_ids": eligible})
    elif not active and state["phase"] == "combat" and state["combat"]["attackers"]:
        blockers = [card["instance_id"] for card in player["battlefield"] if "Creature" in card.get("type_line", "") and not card.get("tapped")]
        if blockers:
            actions.append({"type": "declare_blockers", "card_ids": blockers})
    return actions


def _resolve_spell(state: dict) -> None:
    item = state["stack"].pop()
    card, caster = item["card"], _player(state, item["controller_id"])
    text = (card.get("oracle_text") or "").casefold()
    other = opponent(state, caster["id"])
    target_id = item.get("target_id")
    target_player = next((player for player in state["players"] if player["id"] == target_id), None)
    target_owner = next((player for player in state["players"] if any(permanent["instance_id"] == target_id for permanent in player["battlefield"])), None)
    target = next((permanent for player in state["players"] for permanent in player["battlefield"] if permanent["instance_id"] == target_id), None)
    draw_match = re.search(r"draw (?:a|one|two|three|four) cards?", text)
    if draw_match:
        word = draw_match.group(0).split()[1]
        _draw(state, caster, {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}[word])
    life_match = re.search(r"you gain (\d+) life", text)
    if life_match:
        caster["life"] += int(life_match.group(1))
    damage_match = re.search(r"deals (\d+) damage to (?:target opponent|each opponent)", text)
    if damage_match:
        other["life"] -= int(damage_match.group(1))
    targeted_damage = re.search(r"deals (\d+) damage to (?:any target|target creature)", text)
    if targeted_damage and (target_player or target):
        amount = int(targeted_damage.group(1))
        if target_player: target_player["life"] -= amount
        elif target: target["damage"] += amount
    if target and target_owner and re.search(r"destroy target (?:creature|permanent|nonland permanent)", text):
        _leave_battlefield(target_owner, target, "graveyard"); _log(state, f"{target['name']} was destroyed.")
    if target and target_owner and re.search(r"exile target (?:creature|permanent|nonland permanent)", text):
        _leave_battlefield(target_owner, target, "exile"); _log(state, f"{target['name']} was exiled.")
    token_match = re.search(r"create (a|one|two|three|four) (\d+)/(\d+) ([^.]*?) creature tokens?", text)
    if token_match:
        amount = {"a":1,"one":1,"two":2,"three":3,"four":4}[token_match.group(1)]
        for _ in range(amount):
            caster["battlefield"].append({"instance_id":_id(),"scryfall_id":"token","name":f"{token_match.group(4).title()} Token","image_url":None,"type_line":f"Token Creature — {token_match.group(4).title()}","oracle_text":"","mana_cost":"","mana_value":0,"power":token_match.group(2),"toughness":token_match.group(3),"owner_id":caster["id"],"controller_id":caster["id"],"tapped":False,"damage":0,"counters":{},"summoning_sick":True,"token":True})
        _log(state, f"{caster['name']} created {amount} token(s).")
    if any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle")):
        card["summoning_sick"] = "Creature" in card.get("type_line", "")
        caster["battlefield"].append(card)
    else:
        caster["graveyard"].append(card)
    for owner in state["players"]:
        for permanent in list(owner["battlefield"]):
            _, toughness = _parse_stats(permanent)
            plus = permanent.get("counters", {}).get("+1/+1", 0); minus = permanent.get("counters", {}).get("-1/-1", 0)
            if toughness + plus - minus > 0 and permanent.get("damage", 0) >= toughness + plus - minus:
                _leave_battlefield(owner, permanent, "graveyard")
    _log(state, f"{card['name']} resolved.")


def _leave_battlefield(owner: dict, card: dict, destination: str) -> None:
    if card in owner["battlefield"]: owner["battlefield"].remove(card)
    card["damage"] = 0; card["tapped"] = False
    if card.get("token"): return
    if card.get("commander"):
        owner["command"].append(card)
    else:
        owner[destination].append(card)


def _combat_damage(state: dict) -> None:
    attacker = _player(state, state["active_player_id"])
    defender = opponent(state, attacker["id"])
    battlefield = {card["instance_id"]: card for player in state["players"] for card in player["battlefield"]}
    blocked_attackers = set(state["combat"]["blocks"].values())
    for attacker_id in state["combat"]["attackers"]:
        creature = battlefield.get(attacker_id)
        if not creature:
            continue
        power, _ = _parse_stats(creature)
        if attacker_id not in blocked_attackers:
            defender["life"] -= power
            if creature.get("commander"):
                source = creature.get("owner_id", attacker["id"]); defender.setdefault("commander_damage", {})[source] = defender.setdefault("commander_damage", {}).get(source, 0) + power
    for blocker_id, attacker_id in state["combat"]["blocks"].items():
        blocker, attacking = battlefield.get(blocker_id), battlefield.get(attacker_id)
        if not blocker or not attacking:
            continue
        attacking_power, attacking_toughness = _parse_stats(attacking)
        blocker_power, blocker_toughness = _parse_stats(blocker)
        attacking["damage"] += blocker_power
        blocker["damage"] += attacking_power
        if attacking["damage"] >= attacking_toughness:
            _leave_battlefield(attacker, attacking, "graveyard")
        if blocker["damage"] >= blocker_toughness:
            _leave_battlefield(defender, blocker, "graveyard")
    _log(state, "Combat damage resolved.")
    state["combat"] = {"attackers": [], "blocks": {}}


def _check_winner(state: dict) -> None:
    losers = [player for player in state["players"] if player["life"] <= 0 or player.get("lost") or any(amount >= 21 for amount in player.get("commander_damage", {}).values())]
    if losers:
        state["status"] = "complete"
        state["winner_id"] = opponent(state, losers[0]["id"])["id"]
        _log(state, f"{opponent(state, losers[0]['id'])['name']} wins the game.")


def _advance_turn_phase(state: dict) -> None:
    if state["phase"] == "combat" and state["combat"]["attackers"]: _combat_damage(state)
    index = PHASES.index(state["phase"])
    if index == len(PHASES) - 1:
        state["turn"] += 1; state["phase"] = PHASES[0]; state["active_player_id"] = opponent(state, state["active_player_id"])["id"]
        active = _player(state, state["active_player_id"]); active["land_plays_remaining"] = 1
        for permanent in active["battlefield"]: permanent["tapped"] = False; permanent["damage"] = 0; permanent["summoning_sick"] = False
        _draw(state, active); _log(state, f"Turn {state['turn']} began for {active['name']}.")
    else:
        state["phase"] = PHASES[index + 1]
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
        player["kept_hand"] = True; _log(state, f"{player['name']} kept seven cards.")
        if all(item["kept_hand"] for item in state["players"]):
            state["status"] = "active"; state["priority_player_id"] = state["active_player_id"]
    elif action_type == "mulligan":
        size = max(1, len(player["hand"]) - 1); player["library"].extend(player["hand"]); player["hand"] = []
        random.SystemRandom().shuffle(player["library"]); _draw(state, player, size); _log(state, f"{player['name']} mulliganed to {size}.")
    elif action_type == "play_land":
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id") and "Land" in card.get("type_line", "")), None)
        if not card: raise RuleViolation("That land is not in your hand")
        player["hand"].remove(card); player["battlefield"].append(card); player["land_plays_remaining"] -= 1; _log(state, f"{player['name']} played {card['name']}.")
    elif action_type == "cast":
        source = next((zone for zone in ("hand", "command") if any(card["instance_id"] == action.get("card_id") for card in player.get(zone, []))), None)
        card = next((card for card in player.get(source or "hand", []) if card["instance_id"] == action.get("card_id")), None)
        tax = _commander_tax(player, card) if card else 0
        if not card or not _can_pay(player, card, tax): raise RuleViolation("That spell cannot be cast")
        targets = _targets(state, player_id, card); target_id = action.get("target_id")
        if _target_kind(card) and target_id not in {target["id"] for target in targets}: raise RuleViolation("Choose a legal target")
        _pay_mana(player, card, tax); player[source].remove(card)
        if card.get("commander"): player["commander_casts"] = player.get("commander_casts", 0) + 1
        state["stack"].append({"id": _id(), "card": card, "controller_id": player_id, "target_id": target_id}); state["consecutive_passes"] = 0; state["pending_phase_advance"] = False
        if _multiplayer(state): state["priority_player_id"] = opponent(state, player_id)["id"]
        _log(state, f"{player['name']} cast {card['name']}{f' with {tax} commander tax' if tax else ''}{' targeting '+next((target['name'] for target in targets if target['id']==target_id),'') if target_id else ''}.")
    elif action_type == "resolve":
        _resolve_spell(state)
    elif action_type == "pass_priority":
        state["consecutive_passes"] = state.get("consecutive_passes", 0) + 1
        if state["consecutive_passes"] >= 2:
            state["consecutive_passes"] = 0
            if state["stack"]: _resolve_spell(state)
            elif state.get("pending_phase_advance"): _advance_turn_phase(state)
            state["priority_player_id"] = state["active_player_id"]
        else:
            state["priority_player_id"] = opponent(state, player_id)["id"]
    elif action_type == "declare_attackers":
        requested = set(action.get("attacker_ids") or [])
        eligible = {card_id for entry in legal_actions(state, player_id) if entry["type"] == "declare_attackers" for card_id in entry.get("card_ids", [])}
        if not requested.issubset(eligible): raise RuleViolation("One or more attackers are not eligible")
        state["combat"]["attackers"] = list(requested)
        for card in player["battlefield"]:
            if card["instance_id"] in requested: card["tapped"] = True
        state["priority_player_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} attacked with {len(requested)} creature(s).")
    elif action_type == "declare_blockers":
        available = {entry_id for entry in legal_actions(state, player_id) if entry["type"] == "declare_blockers" for entry_id in entry.get("card_ids", [])}
        blocks = action.get("blocks") or {}
        if not set(blocks).issubset(available) or not set(blocks.values()).issubset(set(state["combat"]["attackers"])): raise RuleViolation("One or more blocks are illegal")
        state["combat"]["blocks"] = blocks; _combat_damage(state); state["priority_player_id"] = state["active_player_id"]
    elif action_type == "advance_phase":
        if _multiplayer(state):
            state["pending_phase_advance"] = True; state["consecutive_passes"] = 1; state["priority_player_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} is ready to leave {state['phase'].replace('_', ' ')}.")
        else:
            _advance_turn_phase(state)
    elif action_type == "concede":
        state["status"] = "complete"; state["winner_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} conceded.")
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
    _check_winner(state)
    state["version"] += 1
    return state
