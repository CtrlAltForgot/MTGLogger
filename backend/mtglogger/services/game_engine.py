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


def _can_pay(player: dict, card: dict) -> bool:
    available = []
    for permanent in player["battlefield"]:
        if not permanent.get("tapped") and "Land" in permanent.get("type_line", ""):
            available.append(_land_colors(permanent) or {"C"})
    symbols = _mana_symbols(card)
    colored = [symbol for symbol in symbols if symbol in "WUBRG"]
    generic = sum(int(symbol) for symbol in symbols if symbol.isdigit())
    for symbol in colored:
        match = next((colors for colors in available if symbol in colors), None)
        if not match:
            return False
        available.remove(match)
    return len(available) >= generic


def _pay_mana(player: dict, card: dict) -> None:
    symbols = _mana_symbols(card)
    colored = [symbol for symbol in symbols if symbol in "WUBRG"]
    generic = sum(int(symbol) for symbol in symbols if symbol.isdigit())
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


def _new_player(player_id: str, name: str, deck: list[dict], is_bot: bool) -> dict:
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
    random.SystemRandom().shuffle(library)
    return {"id": player_id, "name": name, "is_bot": is_bot, "life": 20, "library": library, "hand": [], "battlefield": [], "graveyard": [], "exile": [], "command": [], "land_plays_remaining": 1, "kept_hand": False, "lost": False}


def new_game(player_deck: list[dict], opponent_deck: list[dict], play_first: bool = True) -> dict:
    human_id, bot_id = "player", "bot"
    players = [_new_player(human_id, "You", player_deck, False), _new_player(bot_id, "Bot", opponent_deck, True)]
    state = {"version": 1, "status": "mulligan", "winner_id": None, "turn": 1, "phase": "beginning", "active_player_id": human_id if play_first else bot_id, "priority_player_id": human_id, "players": players, "stack": [], "combat": {"attackers": [], "blocks": {}}, "log": []}
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
        actions.extend({"type": "cast", "card_id": card["instance_id"]} for card in player["hand"] if "Land" not in card.get("type_line", "") and _can_pay(player, card))
    if state["stack"]:
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
    if any(kind in card.get("type_line", "") for kind in ("Creature", "Artifact", "Enchantment", "Planeswalker", "Battle")):
        card["summoning_sick"] = "Creature" in card.get("type_line", "")
        caster["battlefield"].append(card)
    else:
        caster["graveyard"].append(card)
    _log(state, f"{card['name']} resolved.")


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
    for blocker_id, attacker_id in state["combat"]["blocks"].items():
        blocker, attacking = battlefield.get(blocker_id), battlefield.get(attacker_id)
        if not blocker or not attacking:
            continue
        attacking_power, attacking_toughness = _parse_stats(attacking)
        blocker_power, blocker_toughness = _parse_stats(blocker)
        attacking["damage"] += blocker_power
        blocker["damage"] += attacking_power
        if attacking["damage"] >= attacking_toughness:
            attacker["battlefield"].remove(attacking); attacker["graveyard"].append(attacking)
        if blocker["damage"] >= blocker_toughness:
            defender["battlefield"].remove(blocker); defender["graveyard"].append(blocker)
    _log(state, "Combat damage resolved.")
    state["combat"] = {"attackers": [], "blocks": {}}


def _check_winner(state: dict) -> None:
    losers = [player for player in state["players"] if player["life"] <= 0 or player.get("lost")]
    if losers:
        state["status"] = "complete"
        state["winner_id"] = opponent(state, losers[0]["id"])["id"]
        _log(state, f"{opponent(state, losers[0]['id'])['name']} wins the game.")


def perform_action(state: dict, player_id: str, action: dict) -> dict:
    state = deepcopy(state)
    player = _player(state, player_id)
    action_type = action.get("type")
    allowed = {entry["type"] for entry in legal_actions(state, player_id)}
    if action_type not in allowed:
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
        card = next((card for card in player["hand"] if card["instance_id"] == action.get("card_id")), None)
        if not card or not _can_pay(player, card): raise RuleViolation("That spell cannot be cast")
        _pay_mana(player, card); player["hand"].remove(card); state["stack"].append({"id": _id(), "card": card, "controller_id": player_id, "target_id": action.get("target_id")}); _log(state, f"{player['name']} cast {card['name']}.")
    elif action_type == "resolve":
        _resolve_spell(state)
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
    elif action_type == "concede":
        state["status"] = "complete"; state["winner_id"] = opponent(state, player_id)["id"]; _log(state, f"{player['name']} conceded.")
    _check_winner(state)
    state["version"] += 1
    return state
