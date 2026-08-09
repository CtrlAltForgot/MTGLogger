import json
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import CardReference, Deck, DeckEntry, GameSession
from ..providers import ScryfallProvider
from ..schemas import GameAction, GameCreate, GameRead
from ..services.game_bot import run_bot
from ..services.game_engine import RuleViolation, legal_actions, new_game, perform_action, public_state
from ..services.references import _reference_metadata

router = APIRouter(prefix="/play", tags=["play"])


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
        cards.append({
            "scryfall_id": inventory.scryfall_id,
            "name": (reference.flavor_name or reference.printed_name or reference.name) if reference else inventory.card_name,
            "image_url": (reference.image_url if reference else inventory.image_url),
            "type_line": (reference.type_line if reference else inventory.type_line) or "",
            "oracle_text": (reference.oracle_text if reference else "") or "",
            "mana_cost": (reference.mana_cost if reference else "") or "",
            "mana_value": float((reference.mana_value if reference else 0) or 0),
            "power": getattr(reference, "power", None),
            "toughness": getattr(reference, "toughness", None),
            "quantity": entry.quantity,
        })
    return cards


def _serialize(game: GameSession, viewer_id: str = "player", invite_token: str | None = None) -> GameRead:
    state = json.loads(game.state_json)
    return GameRead(id=game.id, name=game.name, status=game.status, player_deck_id=game.player_deck_id, opponent_deck_id=game.opponent_deck_id, bot_difficulty=game.bot_difficulty, opponent_type=game.opponent_type or "bot", invite_code=game.invite_code, invite_token=invite_token, state=public_state(state, viewer_id), legal_actions=legal_actions(state, viewer_id), created_at=game.created_at, updated_at=game.updated_at)


@router.get("", response_model=list[GameRead])
def list_games(db: Session = Depends(get_db)):
    return [_serialize(game) for game in db.scalars(select(GameSession).order_by(GameSession.updated_at.desc()).limit(25))]


@router.post("", response_model=GameRead, status_code=201)
async def create_game(payload: GameCreate, db: Session = Depends(get_db)):
    player_deck, opponent_deck = _deck(db, payload.player_deck_id), _deck(db, payload.opponent_deck_id)
    reference_ids = {entry.inventory.scryfall_id for deck in (player_deck, opponent_deck) for entry in deck.entries}
    incomplete = list(db.scalars(select(CardReference).where(CardReference.scryfall_id.in_(reference_ids), CardReference.type_line.contains("Creature"), CardReference.power.is_(None))))
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
    player_cards, opponent_cards = _deck_cards(db, player_deck), _deck_cards(db, opponent_deck)
    if len(player_cards) == 0 or len(opponent_cards) == 0:
        raise HTTPException(422, "Both decks need cards before starting a game")
    invite_token = secrets.token_urlsafe(24) if payload.opponent_type == "human" else None
    state = new_game(player_cards, opponent_cards, payload.play_first, payload.opponent_type == "bot")
    game = GameSession(name=payload.name, player_deck_id=player_deck.id, opponent_deck_id=opponent_deck.id, bot_difficulty=payload.bot_difficulty, opponent_type=payload.opponent_type, invite_code=secrets.token_urlsafe(9) if invite_token else None, guest_token_hash=hashlib.sha256(invite_token.encode()).hexdigest() if invite_token else None, state_json=json.dumps(state), history_json="[]", status=state["status"])
    db.add(game); db.commit(); db.refresh(game)
    return _serialize(game, invite_token=invite_token)


def _get_game(db: Session, game_id: str) -> GameSession:
    game = db.get(GameSession, game_id)
    if not game: raise HTTPException(404, "Game not found")
    return game


@router.get("/{game_id}", response_model=GameRead)
def get_game(game_id: str, db: Session = Depends(get_db)):
    return _serialize(_get_game(db, game_id))


def _guest_game(db: Session, invite_code: str, token: str) -> GameSession:
    game = db.scalar(select(GameSession).where(GameSession.invite_code == invite_code))
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not game or not game.guest_token_hash or not secrets.compare_digest(digest, game.guest_token_hash):
        raise HTTPException(404, "Invite not found or expired")
    return game


@router.get("/invite/{invite_code}/state", response_model=GameRead)
def get_invited_game(invite_code: str, token: str = Query(min_length=20), db: Session = Depends(get_db)):
    return _serialize(_guest_game(db, invite_code, token), "bot")


def _save_action(game: GameSession, state: dict, player_id: str, action: dict) -> dict:
    history = json.loads(game.history_json or "[]"); history.append(state); game.history_json = json.dumps(history[-20:])
    state = perform_action(state, player_id, action)
    if game.opponent_type == "bot":
        if state["status"] == "mulligan": state = run_bot(state, game.bot_difficulty, 1)
        elif state["status"] == "active" and (state["active_player_id"] == "bot" or state["priority_player_id"] == "bot"): state = run_bot(state, game.bot_difficulty)
    game.state_json = json.dumps(state); game.status = state["status"]
    return state


@router.post("/{game_id}/actions", response_model=GameRead)
def act(game_id: str, payload: GameAction, db: Session = Depends(get_db)):
    game = _get_game(db, game_id); state = json.loads(game.state_json)
    try:
        _save_action(game, state, "player", payload.model_dump())
    except RuleViolation as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit(); db.refresh(game)
    return _serialize(game)


@router.post("/invite/{invite_code}/actions", response_model=GameRead)
def invited_act(invite_code: str, payload: GameAction, token: str = Query(min_length=20), db: Session = Depends(get_db)):
    game = _guest_game(db, invite_code, token); state = json.loads(game.state_json)
    try: _save_action(game, state, "bot", payload.model_dump())
    except RuleViolation as exc: raise HTTPException(422, str(exc)) from exc
    db.commit(); db.refresh(game)
    return _serialize(game, "bot")


@router.post("/{game_id}/undo", response_model=GameRead)
def undo(game_id: str, db: Session = Depends(get_db)):
    game = _get_game(db, game_id); history = json.loads(game.history_json or "[]")
    if not history: raise HTTPException(422, "Nothing to undo")
    state = history.pop(); game.state_json = json.dumps(state); game.history_json = json.dumps(history); game.status = state["status"]; db.commit(); db.refresh(game)
    return _serialize(game)


@router.delete("/{game_id}", status_code=204)
def delete_game(game_id: str, db: Session = Depends(get_db)):
    game = _get_game(db, game_id); db.delete(game); db.commit()
