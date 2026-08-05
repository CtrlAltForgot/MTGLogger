from decimal import Decimal

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import Deck, DeckEntry, InventoryItem, InventoryStatus
from ..providers import ScryfallProvider
from ..schemas import (
    AvailableCard,
    AvailablePage,
    DeckAllocations,
    DeckCreate,
    DeckEntryRead,
    DeckFormatSuggestion,
    DeckFormatSuggestions,
    DeckRead,
    DeckUpdate,
    InventoryRead,
)

router = APIRouter(prefix="/decks", tags=["decks"])

FORMAT_LABELS = {
    "standard": "Standard",
    "pioneer": "Pioneer",
    "modern": "Modern",
    "pauper": "Pauper",
    "legacy": "Legacy",
    "vintage": "Vintage",
    "commander": "Commander",
    "brawl": "Brawl",
    "oathbreaker": "Oathbreaker",
    "duel": "Duel Commander",
    "paupercommander": "Pauper Commander",
}


def deck_query():
    return (
        select(Deck)
        .options(selectinload(Deck.entries).selectinload(DeckEntry.inventory))
        .execution_options(populate_existing=True)
    )


def serialize(deck: Deck) -> DeckRead:
    entries = [
        DeckEntryRead(
            id=entry.id,
            quantity=entry.quantity,
            inventory=InventoryRead.model_validate(entry.inventory),
        )
        for entry in deck.entries
    ]
    return DeckRead(
        id=deck.id,
        name=deck.name,
        format=deck.format,
        description=deck.description,
        image_url=deck.image_url,
        created_at=deck.created_at,
        updated_at=deck.updated_at,
        total_cards=sum(entry.quantity for entry in deck.entries),
        unique_cards=len(deck.entries),
        total_value=sum(
            (entry.inventory.market_price or Decimal(0)) * entry.quantity for entry in deck.entries
        ),
        entries=entries,
    )


def get_deck(db: Session, deck_id: str) -> Deck:
    deck = db.scalar(deck_query().where(Deck.id == deck_id))
    if not deck:
        raise HTTPException(404, "Deck not found")
    return deck


@router.get("", response_model=list[DeckRead])
def list_decks(db: Session = Depends(get_db)):
    return [serialize(deck) for deck in db.scalars(deck_query().order_by(Deck.updated_at.desc()))]


@router.post("", response_model=DeckRead, status_code=201)
def create_deck(payload: DeckCreate, db: Session = Depends(get_db)):
    deck = Deck(**payload.model_dump())
    db.add(deck)
    db.commit()
    return serialize(get_deck(db, deck.id))


@router.patch("/{deck_id}", response_model=DeckRead)
def update_deck(deck_id: str, payload: DeckUpdate, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(deck, key, value)
    db.commit()
    return serialize(get_deck(db, deck.id))


@router.post("/{deck_id}/image", response_model=DeckRead)
async def upload_deck_image(
    deck_id: str, image: UploadFile = File(...), db: Session = Depends(get_db)
):
    deck = get_deck(db, deck_id)
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Upload a JPEG, PNG, or WebP image")
    raw = await image.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "Deck image must be 8 MB or smaller")
    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(422, "Deck image is unreadable")
    height, width = decoded.shape[:2]
    scale = min(1.0, 1600 / max(height, width))
    if scale < 1:
        decoded = cv2.resize(decoded, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    path = get_settings().deck_image_dir / f"{deck.id}.jpg"
    if not cv2.imwrite(str(path), decoded, [cv2.IMWRITE_JPEG_QUALITY, 90]):
        raise HTTPException(500, "Could not save deck image")
    deck.image_url = f"/api/decks/{deck.id}/image"
    db.commit()
    return serialize(get_deck(db, deck.id))


@router.get("/{deck_id}/image", response_class=FileResponse)
def deck_image(deck_id: str, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    path = get_settings().deck_image_dir / f"{deck.id}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Deck image not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{deck_id}/format-suggestions", response_model=DeckFormatSuggestions)
async def format_suggestions(deck_id: str, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    total = sum(entry.quantity for entry in deck.entries)
    if not deck.entries:
        return DeckFormatSuggestions(complete_deck=False, card_count=0, suggestions=[])
    requested_ids = list(dict.fromkeys(entry.inventory.scryfall_id for entry in deck.entries))
    cards = await ScryfallProvider().get_cards(requested_ids)
    if len(cards) != len(requested_ids):
        raise HTTPException(503, "Could not verify every exact card in this deck")
    by_id = {card["id"]: card for card in cards}
    suggestions: list[DeckFormatSuggestion] = []
    constructed_size = total >= 60
    singleton = all(
        entry.quantity == 1
        or "Basic Land" in (by_id.get(entry.inventory.scryfall_id, {}).get("type_line") or "")
        for entry in deck.entries
    )
    four_copy = all(
        entry.quantity <= 4
        or "Basic Land" in (by_id.get(entry.inventory.scryfall_id, {}).get("type_line") or "")
        for entry in deck.entries
    )
    deck_colors = set().union(*(set(card.get("color_identity", [])) for card in cards))
    commander_candidates = [
        card
        for card in cards
        if "Legendary" in (card.get("type_line") or "")
        and "Creature" in (card.get("type_line") or "")
        and deck_colors.issubset(set(card.get("color_identity", [])))
    ]
    oathbreaker_candidates = [
        card
        for card in cards
        if "Planeswalker" in (card.get("type_line") or "")
        and deck_colors.issubset(set(card.get("color_identity", [])))
    ]
    for key, label in FORMAT_LABELS.items():
        statuses = [card.get("legalities", {}).get(key, "not_legal") for card in cards]
        if not statuses or any(status in {"not_legal", "banned"} for status in statuses):
            continue
        commander_style = key in {"commander", "duel", "paupercommander"}
        exact_size = total == (60 if key in {"brawl", "oathbreaker"} else 100)
        structure_ok = (
            singleton and exact_size
            if commander_style or key in {"brawl", "oathbreaker"}
            else constructed_size and four_copy
        )
        if key in {"commander", "duel", "paupercommander", "brawl"}:
            structure_ok = structure_ok and bool(commander_candidates)
        elif key == "oathbreaker":
            structure_ok = structure_ok and bool(oathbreaker_candidates)
        reasons = [f"All {len(cards)} unique cards are currently {label}-legal."]
        if structure_ok:
            reasons.append("Stored card count and copy limits match the format.")
            confidence = "high"
        else:
            reasons.append(
                f"The stored deck has {total} cards; it may be incomplete "
                "or use casual construction."
            )
            confidence = "possible"
        suggestions.append(
            DeckFormatSuggestion(format=label, confidence=confidence, reasons=reasons)
        )
    suggestions.append(
        DeckFormatSuggestion(
            format="Casual / Kitchen Table",
            confidence="possible",
            reasons=["Casual play allows a custom card pool and house rules."],
        )
    )
    suggestions.sort(
        key=lambda item: (
            item.confidence != "high",
            {value: index for index, value in enumerate(FORMAT_LABELS.values())}.get(
                item.format, 999
            ),
        )
    )
    return DeckFormatSuggestions(
        complete_deck=any(item.confidence == "high" for item in suggestions),
        card_count=total,
        suggestions=suggestions,
    )


@router.delete("/{deck_id}", status_code=204)
def delete_deck(deck_id: str, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    image_path = get_settings().deck_image_dir / f"{deck.id}.jpg"
    db.delete(deck)
    db.commit()
    image_path.unlink(missing_ok=True)


@router.get("/{deck_id}/available", response_model=AvailablePage)
def available_cards(
    deck_id: str,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    db: Session = Depends(get_db),
):
    get_deck(db, deck_id)
    assigned = (
        select(DeckEntry.inventory_id, func.sum(DeckEntry.quantity).label("assigned"))
        .group_by(DeckEntry.inventory_id)
        .subquery()
    )
    filters = [
        InventoryItem.status == InventoryStatus.owned,
        InventoryItem.quantity > func.coalesce(assigned.c.assigned, 0),
    ]
    if q:
        filters.append(
            or_(
                InventoryItem.card_name.ilike(f"%{q}%"),
                InventoryItem.set_code.ilike(f"%{q}%"),
                InventoryItem.collector_number.ilike(f"%{q}%"),
            )
        )
    base = (
        select(InventoryItem, func.coalesce(assigned.c.assigned, 0))
        .outerjoin(assigned, assigned.c.inventory_id == InventoryItem.id)
        .where(*filters)
    )
    statement = (
        base.order_by(InventoryItem.card_name.asc()).offset((page - 1) * page_size).limit(page_size)
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .outerjoin(assigned, assigned.c.inventory_id == InventoryItem.id)
            .where(*filters)
        )
        or 0
    )
    items = [
        AvailableCard(
            inventory=InventoryRead.model_validate(item),
            assigned_quantity=assigned_quantity,
            available_quantity=item.quantity - assigned_quantity,
        )
        for item, assigned_quantity in db.execute(statement)
    ]
    return AvailablePage(items=items, total=total, page=page, page_size=page_size)


@router.post("/{deck_id}/entries", response_model=DeckRead)
def add_entries(deck_id: str, payload: DeckAllocations, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    requested: dict[str, int] = {}
    for allocation in payload.entries:
        requested[allocation.inventory_id] = (
            requested.get(allocation.inventory_id, 0) + allocation.quantity
        )
    inventory = {
        item.id: item
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.id.in_(requested)).with_for_update()
        )
    }
    if len(inventory) != len(requested):
        raise HTTPException(404, "One or more collection entries no longer exist")
    assigned = dict(
        db.execute(
            select(DeckEntry.inventory_id, func.sum(DeckEntry.quantity))
            .where(DeckEntry.inventory_id.in_(requested))
            .group_by(DeckEntry.inventory_id)
        ).all()
    )
    for inventory_id, quantity in requested.items():
        available = inventory[inventory_id].quantity - int(assigned.get(inventory_id, 0))
        if quantity > available:
            raise HTTPException(
                409,
                f"Only {available} unassigned copies of {inventory[inventory_id].card_name} remain",
            )
    existing = {entry.inventory_id: entry for entry in deck.entries}
    for inventory_id, quantity in requested.items():
        if inventory_id in existing:
            existing[inventory_id].quantity += quantity
        else:
            db.add(DeckEntry(deck_id=deck.id, inventory_id=inventory_id, quantity=quantity))
    db.commit()
    return serialize(get_deck(db, deck.id))


@router.patch("/{deck_id}/entries/{entry_id}", response_model=DeckRead)
def update_entry(
    deck_id: str,
    entry_id: str,
    quantity: int = Query(ge=0),
    db: Session = Depends(get_db),
):
    deck = get_deck(db, deck_id)
    entry = next((item for item in deck.entries if item.id == entry_id), None)
    if not entry:
        raise HTTPException(404, "Deck entry not found")
    assigned_elsewhere = db.scalar(
        select(func.coalesce(func.sum(DeckEntry.quantity), 0)).where(
            DeckEntry.inventory_id == entry.inventory_id, DeckEntry.id != entry.id
        )
    )
    if quantity > entry.inventory.quantity - assigned_elsewhere:
        raise HTTPException(409, "Not enough unassigned copies remain")
    if quantity == 0:
        db.delete(entry)
    else:
        entry.quantity = quantity
    db.commit()
    return serialize(get_deck(db, deck.id))
