from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Deck, DeckEntry, InventoryItem


def assign_to_deck(
    db: Session, deck_id: str, inventory: InventoryItem, quantity: int = 1
) -> DeckEntry:
    deck = db.get(Deck, deck_id)
    if not deck:
        raise ValueError("Selected deck no longer exists")
    assigned = db.scalar(
        select(func.coalesce(func.sum(DeckEntry.quantity), 0)).where(
            DeckEntry.inventory_id == inventory.id
        )
    )
    if quantity > inventory.quantity - assigned:
        raise ValueError(f"No unassigned copies of {inventory.card_name} remain")
    entry = db.scalar(
        select(DeckEntry).where(
            DeckEntry.deck_id == deck_id, DeckEntry.inventory_id == inventory.id
        )
    )
    if entry:
        entry.quantity += quantity
    else:
        entry = DeckEntry(deck_id=deck_id, inventory_id=inventory.id, quantity=quantity)
        db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
