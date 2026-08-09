import random

from .game_engine import legal_actions, perform_action


def _card(state: dict, player_id: str, instance_id: str) -> dict:
    player = next(player for player in state["players"] if player["id"] == player_id)
    return next(card for zone in (player["hand"], player["battlefield"]) for card in zone if card["instance_id"] == instance_id)


def choose_bot_action(state: dict, difficulty: str = "standard") -> dict | None:
    actions = legal_actions(state, "bot")
    if not actions:
        return None
    by_type = {kind: [action for action in actions if action["type"] == kind] for kind in {action["type"] for action in actions}}
    if "keep" in by_type:
        return by_type["keep"][0]
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
        attackers = state["combat"]["attackers"]
        return {"type": "declare_blockers", "blocks": dict(zip(action["card_ids"], attackers))}
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
