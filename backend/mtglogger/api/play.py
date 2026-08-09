import json
import hashlib
import secrets
import random
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import CardReference, Deck, DeckEntry, GameSession, InventoryItem
from ..providers import ScryfallProvider
from ..schemas import AutoDeckBuildRequest, GameAction, GameCreate, GameRead, GameUndo
from .decks import _auto_deck_proposal
from ..services.game_bot import run_bot
from ..services.game_engine import RuleViolation, legal_actions, new_game, perform_action, public_state
from ..services.references import _reference_metadata

router = APIRouter(prefix="/play", tags=["play"])


def _reference_faces(reference:CardReference|None)->list[dict]:
    if not reference or not reference.card_faces:return []
    try:return json.loads(reference.card_faces)
    except (TypeError,json.JSONDecodeError):return []


def _apply_front_face(card:dict)->dict:
    faces=card.get("card_faces") or []
    if not faces:return card
    face=faces[0]
    for key in ("name","oracle_text","mana_cost","type_line","power","toughness","loyalty","image_url","keywords"):
        if key in face:card[key]=face[key]
    card["current_face"]=0
    return card


def _deck(db: Session, deck_id: str) -> Deck:
    deck = db.scalar(select(Deck).options(selectinload(Deck.entries).selectinload(DeckEntry.inventory)).where(Deck.id == deck_id))
    if not deck:
        raise HTTPException(404, "Deck not found")
    return deck


def _deck_cards(db: Session, deck: Deck) -> list[dict]:
    ids = [entry.inventory.scryfall_id for entry in deck.entries]
    references = {card.scryfall_id: card for card in db.scalars(select(CardReference).where(CardReference.scryfall_id.in_(ids)))} if ids else {}
    cards = []
    for entry in deck.entries:
        inventory = entry.inventory
        reference = references.get(inventory.scryfall_id)
        cards.append(_apply_front_face({
            "scryfall_id": inventory.scryfall_id,
            "name": (reference.flavor_name or reference.printed_name or reference.name) if reference else inventory.card_name,
            "rules_name": reference.name if reference else inventory.card_name,
            "image_url": (reference.image_url if reference else inventory.image_url),
            "type_line": (reference.type_line if reference else inventory.type_line) or "",
            "oracle_text": (reference.oracle_text if reference else "") or "",
            "mana_cost": (reference.mana_cost if reference else "") or "",
            "mana_value": float((reference.mana_value if reference else 0) or 0),
            "keywords": json.loads(reference.keywords or "[]") if reference and reference.keywords else [],
            "power": getattr(reference, "power", None),
            "toughness": getattr(reference, "toughness", None),
            "loyalty": getattr(reference, "loyalty", None),
            "card_faces": _reference_faces(reference),
            "quantity": entry.quantity,
        }))
    return cards


def _generated_deck_cards(db: Session, proposal: dict) -> list[dict]:
    quantities = {card["inventory_id"]: card["quantity"] for card in proposal["cards"]}
    inventory = {item.id: item for item in db.scalars(select(InventoryItem).where(InventoryItem.id.in_(quantities)))}
    references = {card.scryfall_id: card for card in db.scalars(select(CardReference).where(CardReference.scryfall_id.in_([item.scryfall_id for item in inventory.values()])))}
    cards = []
    for inventory_id, quantity in quantities.items():
        item, reference = inventory[inventory_id], references.get(inventory[inventory_id].scryfall_id)
        cards.append(_apply_front_face({
            "scryfall_id": item.scryfall_id,
            "name": (reference.flavor_name or reference.printed_name or reference.name) if reference else item.card_name,
            "rules_name": reference.name if reference else item.card_name,
            "image_url": reference.image_url if reference else item.image_url,
            "type_line": (reference.type_line if reference else item.type_line) or "",
            "oracle_text": (reference.oracle_text if reference else "") or "",
            "mana_cost": (reference.mana_cost if reference else "") or "",
            "mana_value": float((reference.mana_value if reference else 0) or 0),
            "keywords": json.loads(reference.keywords or "[]") if reference and reference.keywords else [],
            "power": getattr(reference, "power", None), "toughness": getattr(reference, "toughness", None), "loyalty": getattr(reference, "loyalty", None),
            "card_faces": _reference_faces(reference),
            "quantity": quantity,
        }))
    return cards


def _spectator_state(state:dict)->dict:
    visible=public_state(state,"spectator")
    for player in visible.get("players",[]):
        for zone in ("hand","battlefield","graveyard","exile","command"):player[zone]=[]
        player["commander_damage"]={};player["bent_this_turn"]=[]
    visible["stack"]=[];visible["combat"]={"attackers":[],"blocks":{},"attack_targets":{},"block_orders":{},"damage_pending":False,"damage_step":None,"first_strike_damage_ids":[]};visible["log"]=[]
    for key in ("pending_discard","pending_mulligan_bottom","pending_sacrifice","pending_legendary","pending_library_search","pending_scry","pending_damage_order","pending_ward","pending_blight","pending_proliferate","pending_transform"):visible[key]=None
    visible["pending_commander_zone"]=[];visible["pending_trigger_targets"]=[]
    return visible


async def _build_random_bot_deck(db: Session, format_name: str) -> tuple[list[dict], str]:
    colors = list("WUBRG")
    strategies = ["balanced", "aggro", "control", "spells", "tokens", "creatures"]
    attempts = []
    for _ in range(6):
        selected = random.sample(colors, random.randint(1, 3))
        payload = AutoDeckBuildRequest(name="Bot generated deck", format=format_name or "Modern", colors=selected, strategy=random.choice(strategies))
        proposal = await _auto_deck_proposal(payload, db)
        attempts.append(proposal)
        if proposal["complete"] and proposal["quality_score"] >= 70:
            break
    proposal = max(attempts, key=lambda item: (item["complete"], item["quality_score"], item["total_cards"]))
    if not proposal["cards"]:
        raise HTTPException(422, "There are not enough unassigned owned cards to generate a bot deck")
    return _generated_deck_cards(db, proposal), f"Auto-built {proposal['theme']} · {'/'.join(proposal['colors'])} · quality {proposal['quality_score']:.0f}"


def _history_state(entry:dict)->dict:
    return entry.get("state",entry) if isinstance(entry,dict) else {}


def _public_history(game:GameSession)->list[dict]:
    history=[]
    for entry in json.loads(game.history_json or "[]"):
        state=_history_state(entry)
        if "state" in entry:
            history.append({key:entry.get(key) for key in ("version","turn","actor_id","action_type","message","created_at")})
        else:
            latest=(state.get("log") or [{}])[-1]
            history.append({"version":state.get("version",1),"turn":state.get("turn",1),"actor_id":"unknown","action_type":"legacy","message":latest.get("message","Saved game action"),"created_at":None})
    return history


def _serialize(game: GameSession, viewer_id: str = "player", invite_token: str | None = None, host_token: str | None = None) -> GameRead:
    state = json.loads(game.state_json)
    player_ids={player["id"] for player in state["players"]};spectator=viewer_id not in player_ids;actions=legal_actions(state,viewer_id,allow_direct_resolution=False) if not spectator else []
    return GameRead(id=game.id, name=game.name, status=game.status, player_deck_id=game.player_deck_id, opponent_deck_id=game.opponent_deck_id, bot_difficulty=game.bot_difficulty, opponent_type=game.opponent_type or "bot", invite_code=None if spectator else game.invite_code, invite_token=invite_token, host_token=host_token, invite_expires_at=None if spectator else game.invite_expires_at, state=_spectator_state(state) if spectator else public_state(state, viewer_id), legal_actions=actions, action_history=[] if spectator else _public_history(game), created_at=game.created_at, updated_at=game.updated_at)


@router.get("", response_model=list[GameRead])
def list_games(db: Session = Depends(get_db)):
    # Private sessions are discoverable only by possession of their host or guest
    # capability. The host UI restores its own sessions from locally held host keys.
    games=db.scalars(select(GameSession).where(or_(GameSession.opponent_type.is_(None),GameSession.opponent_type!="human")).order_by(GameSession.updated_at.desc()).limit(25))
    return [_serialize(game,"player") for game in games]


@router.post("", response_model=GameRead, status_code=201)
async def create_game(payload: GameCreate, db: Session = Depends(get_db)):
    player_deck = _deck(db, payload.player_deck_id)
    generated_bot = payload.opponent_type == "bot" and payload.bot_deck_mode == "generated"
    opponent_deck = player_deck if generated_bot else _deck(db, payload.opponent_deck_id)
    reference_ids = {entry.inventory.scryfall_id for deck in (player_deck, opponent_deck) for entry in deck.entries}
    incomplete = list(db.scalars(select(CardReference).where(CardReference.scryfall_id.in_(reference_ids), or_(
        (CardReference.type_line.contains("Creature")) & CardReference.power.is_(None),
        (CardReference.type_line.contains("Planeswalker")) & CardReference.loyalty.is_(None),
    ))))
    if incomplete:
        try:
            provider = ScryfallProvider()
            by_id = {reference.scryfall_id: reference for reference in incomplete}
            for card in await provider.get_cards(list(by_id)):
                reference = by_id.get(card.get("id"))
                if reference:
                    for field, value in _reference_metadata(provider, card).items(): setattr(reference, field, value)
            db.commit()
        except Exception:
            db.rollback()
    player_cards = _deck_cards(db, player_deck)
    if generated_bot:
        opponent_cards, bot_description = await _build_random_bot_deck(db, player_deck.format or "Modern")
    else:
        opponent_cards, bot_description = _deck_cards(db, opponent_deck), ""
    if len(player_cards) == 0 or len(opponent_cards) == 0:
        raise HTTPException(422, "Both decks need cards before starting a game")
    invite_token = secrets.token_urlsafe(32) if payload.opponent_type == "human" else None
    host_token = secrets.token_urlsafe(32) if payload.opponent_type == "human" else None
    state = new_game(player_cards, opponent_cards, payload.play_first, payload.opponent_type == "bot", player_deck.format or "", opponent_deck.format or "")
    if generated_bot:
        state["players"][1]["name"] = f"Bot · {bot_description}"
    game = GameSession(name=payload.name, player_deck_id=player_deck.id, opponent_deck_id=opponent_deck.id, bot_difficulty=payload.bot_difficulty, opponent_type=payload.opponent_type, invite_code=secrets.token_urlsafe(12) if invite_token else None, host_token_hash=hashlib.sha256(host_token.encode()).hexdigest() if host_token else None, guest_token_hash=hashlib.sha256(invite_token.encode()).hexdigest() if invite_token else None, invite_expires_at=datetime.now(UTC)+timedelta(days=30) if invite_token else None, state_json=json.dumps(state), history_json="[]", status=state["status"])
    db.add(game); db.commit(); db.refresh(game)
    return _serialize(game, invite_token=invite_token, host_token=host_token)


def _get_game(db: Session, game_id: str, lock: bool = False) -> GameSession:
    game = db.scalar(select(GameSession).where(GameSession.id==game_id).with_for_update()) if lock else db.get(GameSession, game_id)
    if not game: raise HTTPException(404, "Game not found")
    return game


def _verify_token(candidate:str|None,stored_hash:str|None)->bool:
    return bool(candidate and stored_hash and secrets.compare_digest(hashlib.sha256(candidate.encode()).hexdigest(),stored_hash))


def _host_game(db:Session,game_id:str,token:str|None,lock:bool=False)->GameSession:
    game=_get_game(db,game_id,lock)
    if game.opponent_type=="human" and not _verify_token(token,game.host_token_hash):raise HTTPException(404,"Game not found or access expired")
    return game


@router.get("/{game_id}", response_model=GameRead)
def get_game(game_id: str, game_token:str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    return _serialize(_host_game(db,game_id,game_token))


def _guest_game(db: Session, invite_code: str, token: str, lock:bool=False) -> GameSession:
    statement=select(GameSession).where(GameSession.invite_code==invite_code)
    game = db.scalar(statement.with_for_update() if lock else statement)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expired=bool(game and game.invite_expires_at and game.invite_expires_at.replace(tzinfo=game.invite_expires_at.tzinfo or UTC)<datetime.now(UTC))
    if not game or expired or not game.guest_token_hash or not secrets.compare_digest(digest, game.guest_token_hash):
        raise HTTPException(404, "Invite not found or expired")
    return game


@router.get("/invite/{invite_code}/state", response_model=GameRead)
def get_invited_game(invite_code: str, game_token: str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    return _serialize(_guest_game(db, invite_code, game_token or ""), "bot")


def _save_action(game: GameSession, state: dict, player_id: str, action: dict) -> dict:
    expected=action.pop("expected_version",None)
    if game.opponent_type=="human" and expected!=state.get("version"):raise HTTPException(409,"Game changed in the other browser. Refresh and try again.")
    previous_state=state;action_type=action.get("type","action");previous_log_size=len(state.get("log",[]))
    state = perform_action(state, player_id, action,allow_direct_resolution=False)
    if game.opponent_type == "bot":
        if state["status"] == "mulligan": state = run_bot(state, game.bot_difficulty, 20)
        elif state["status"] == "active" and (state["active_player_id"] == "bot" or state["priority_player_id"] == "bot"): state = run_bot(state, game.bot_difficulty)
    new_messages=state.get("log",[])[previous_log_size:]
    message=new_messages[0]["message"] if new_messages else action_type.replace("_"," ").capitalize()
    history=json.loads(game.history_json or "[]");history.append({"state":previous_state,"version":previous_state.get("version",1),"turn":previous_state.get("turn",1),"actor_id":player_id,"action_type":action_type,"message":message,"created_at":datetime.now(UTC).isoformat()});game.history_json=json.dumps(history[-50:])
    game.state_json = json.dumps(state); game.status = state["status"]
    return state


@router.post("/{game_id}/actions", response_model=GameRead)
def act(game_id: str, payload: GameAction, game_token:str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    game = _host_game(db, game_id,game_token,True); state = json.loads(game.state_json)
    try:
        _save_action(game, state, "player", payload.model_dump())
    except RuleViolation as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit(); db.refresh(game)
    return _serialize(game)


@router.post("/invite/{invite_code}/actions", response_model=GameRead)
def invited_act(invite_code: str, payload: GameAction, game_token:str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    game = _guest_game(db, invite_code, game_token or "",True);state = json.loads(game.state_json)
    try: _save_action(game, state, "bot", payload.model_dump())
    except RuleViolation as exc: raise HTTPException(422, str(exc)) from exc
    db.commit(); db.refresh(game)
    return _serialize(game, "bot")


@router.post("/{game_id}/undo", response_model=GameRead)
def undo(game_id: str, payload:GameUndo, game_token:str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    game = _host_game(db,game_id,game_token,True); history = json.loads(game.history_json or "[]")
    if game.opponent_type=="human":raise HTTPException(422,"Undo is disabled in private multiplayer games")
    current=json.loads(game.state_json)
    if payload.expected_version!=current.get("version"):raise HTTPException(409,"Game changed. Refresh and try again.")
    if not history: raise HTTPException(422, "Nothing to undo")
    state = _history_state(history.pop()); game.state_json = json.dumps(state); game.history_json = json.dumps(history); game.status = state["status"]; db.commit(); db.refresh(game)
    return _serialize(game)


@router.post("/{game_id}/invite/rotate",response_model=GameRead)
def rotate_invite(game_id:str,game_token:str|None=Header(None,alias="X-Game-Token"),db:Session=Depends(get_db)):
    game=_host_game(db,game_id,game_token,True)
    if game.opponent_type!="human":raise HTTPException(422,"Bot games do not have invite links")
    invite_token=secrets.token_urlsafe(32);game.invite_code=secrets.token_urlsafe(12);game.guest_token_hash=hashlib.sha256(invite_token.encode()).hexdigest();game.invite_expires_at=datetime.now(UTC)+timedelta(days=30);db.commit();db.refresh(game)
    return _serialize(game,invite_token=invite_token)


@router.delete("/{game_id}", status_code=204)
def delete_game(game_id: str, game_token:str|None=Header(None,alias="X-Game-Token"), db: Session = Depends(get_db)):
    game = _host_game(db,game_id,game_token,True); db.delete(game); db.commit()
