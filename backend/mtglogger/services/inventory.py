from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import InventoryItem, utc_now
from ..schemas import InventoryCreate


def upsert_inventory(db: Session, data: InventoryCreate) -> InventoryItem:
    match = db.scalar(
        select(InventoryItem).where(
            and_(
                InventoryItem.scryfall_id == data.scryfall_id,
                InventoryItem.foil == data.foil,
                InventoryItem.language == data.language,
                InventoryItem.condition == data.condition,
                InventoryItem.collection_name == data.collection_name,
                InventoryItem.storage_location == data.storage_location,
                InventoryItem.status == data.status,
            )
        )
    )
    if match:
        match.quantity += data.quantity
        # Adding another physical copy is a new collection activity even though
        # it intentionally reuses the same inventory row.
        match.updated_at = utc_now()
        if data.market_price is not None:
            match.market_price = data.market_price
        db.commit()
        db.refresh(match)
        return match
    item = InventoryItem(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
