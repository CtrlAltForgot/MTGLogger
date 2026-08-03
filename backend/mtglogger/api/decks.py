from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Deck, DeckEntry, InventoryItem, InventoryStatus
from ..schemas import (
    AvailableCard,
    DeckAllocations,
    DeckCreate,
    DeckEntryRead,
    DeckRead,
    DeckUpdate,
    InventoryRead,
)

router = APIRouter(prefix="/decks", tags=["decks"])


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


@router.delete("/{deck_id}", status_code=204)
def delete_deck(deck_id: str, db: Session = Depends(get_db)):
    deck = get_deck(db, deck_id)
    db.delete(deck)
    db.commit()


@router.get("/{deck_id}/available", response_model=list[AvailableCard])
def available_cards(
    deck_id: str,
    q: str | None = None,
    page_size: int = Query(250, ge=1, le=250),
    db: Session = Depends(get_db),
):
    get_deck(db, deck_id)
    assigned = (
        select(DeckEntry.inventory_id, func.sum(DeckEntry.quantity).label("assigned"))
        .group_by(DeckEntry.inventory_id)
        .subquery()
    )
    statement = (
        select(InventoryItem, func.coalesce(assigned.c.assigned, 0))
        .outerjoin(assigned, assigned.c.inventory_id == InventoryItem.id)
        .where(
            InventoryItem.status == InventoryStatus.owned,
            InventoryItem.quantity > func.coalesce(assigned.c.assigned, 0),
        )
        .order_by(InventoryItem.card_name.asc())
        .limit(page_size)
    )
    if q:
        statement = statement.where(
            or_(
                InventoryItem.card_name.ilike(f"%{q}%"),
                InventoryItem.set_code.ilike(f"%{q}%"),
                InventoryItem.collector_number.ilike(f"%{q}%"),
            )
        )
    return [
        AvailableCard(
            inventory=InventoryRead.model_validate(item),
            assigned_quantity=assigned_quantity,
            available_quantity=item.quantity - assigned_quantity,
        )
        for item, assigned_quantity in db.execute(statement)
    ]


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
