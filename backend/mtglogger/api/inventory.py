import csv
import io
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import DeckEntry, InventoryItem, PriceSnapshot, ReviewItem, utc_now
from ..providers import ScryfallProvider
from ..schemas import (
    InventoryCopyMove,
    InventoryCreate,
    InventoryFinishMove,
    InventoryRead,
    InventoryUpdate,
    Page,
)
from ..services.inventory import upsert_inventory
from ..services.prices import apply_price, record_collection_value

router = APIRouter(prefix="/inventory", tags=["inventory"])
prices = ScryfallProvider()


async def finish_price(scryfall_id: str, foil: bool):
    try:
        card = await prices.get_card(scryfall_id)
        return prices.market_price(card, foil=foil)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Could not refresh the current card price") from exc


def delete_item_preserving_reviews(db: Session, item: InventoryItem) -> None:
    db.execute(
        update(ReviewItem)
        .where(ReviewItem.resolved_inventory_id == item.id)
        .values(resolved_inventory_id=None)
    )
    db.delete(item)
    db.commit()


@router.get("", response_model=Page)
def list_inventory(
    q: str | None = None,
    set_code: str | None = None,
    status: str | None = None,
    collection_name: str | None = None,
    storage_location: str | None = None,
    sort: str = "date_added",
    descending: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=250),
    db: Session = Depends(get_db),
):
    filters = []
    if q:
        filters.append(
            or_(
                InventoryItem.card_name.ilike(f"%{q}%"),
                InventoryItem.collector_number.ilike(f"%{q}%"),
            )
        )
    if set_code:
        filters.append(InventoryItem.set_code == set_code.lower())
    if status:
        filters.append(InventoryItem.status == status)
    if collection_name:
        filters.append(InventoryItem.collection_name == collection_name)
    if storage_location:
        filters.append(InventoryItem.storage_location == storage_location)
    statement = select(InventoryItem).options(
        selectinload(InventoryItem.deck_entries).selectinload(DeckEntry.deck)
    ).where(*filters)
    column = getattr(InventoryItem, sort, InventoryItem.date_added)
    statement = statement.order_by(column.desc() if descending else column.asc())
    total = db.scalar(select(func.count()).select_from(InventoryItem).where(*filters)) or 0
    total_cards = db.scalar(select(func.coalesce(func.sum(InventoryItem.quantity), 0))) or 0
    collection_value = db.scalar(
        select(func.coalesce(func.sum(InventoryItem.market_price * InventoryItem.quantity), 0))
    ) or 0
    items = list(db.scalars(statement.offset((page - 1) * page_size).limit(page_size)))
    previous: dict[str, object] = {}
    if items:
        snapshots = db.execute(
            select(PriceSnapshot.inventory_id, PriceSnapshot.market_price)
            .where(PriceSnapshot.inventory_id.in_([item.id for item in items]))
            .order_by(PriceSnapshot.inventory_id, PriceSnapshot.recorded_at.desc())
        )
        for inventory_id, market_price in snapshots:
            previous.setdefault(inventory_id, market_price)
    serialized = [
        InventoryRead.model_validate(item).model_copy(
            update={"previous_market_price": previous.get(item.id)}
        )
        for item in items
    ]
    return Page(
        items=serialized,
        total=total,
        total_cards=total_cards,
        collection_value=collection_value,
        page=page,
        page_size=page_size,
    )


@router.get("/facets")
def inventory_facets(db: Session = Depends(get_db)):
    return {
        "collections": list(
            db.scalars(
                select(InventoryItem.collection_name)
                .distinct()
                .order_by(InventoryItem.collection_name.asc())
            )
        ),
        "storage_locations": list(
            db.scalars(
                select(InventoryItem.storage_location)
                .distinct()
                .order_by(InventoryItem.storage_location.asc())
            )
        ),
    }


@router.post("", response_model=InventoryRead, status_code=201)
def create_inventory(data: InventoryCreate, db: Session = Depends(get_db)):
    return upsert_inventory(db, data)


@router.get("/{item_id}/price")
async def inventory_finish_price(
    item_id: str, foil: bool = False, db: Session = Depends(get_db)
):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    return {"market_price": await finish_price(item.scryfall_id, foil)}


@router.post("/{item_id}/move-finish", response_model=InventoryRead)
async def move_inventory_finish(
    item_id: str, data: InventoryFinishMove, db: Session = Depends(get_db)
):
    """Move unassigned copies into a distinct foil/nonfoil inventory variant."""
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    if data.foil == item.foil:
        raise HTTPException(422, "Copies already use that finish")
    assigned = db.scalar(
        select(func.coalesce(func.sum(DeckEntry.quantity), 0)).where(
            DeckEntry.inventory_id == item.id
        )
    ) or 0
    available = item.quantity - assigned
    if data.quantity > available:
        raise HTTPException(
            409,
            f"Only {available} unassigned copies can change finish",
        )

    target = db.scalar(
        select(InventoryItem).where(
            InventoryItem.scryfall_id == item.scryfall_id,
            InventoryItem.foil == data.foil,
            InventoryItem.language == item.language,
            InventoryItem.condition == item.condition,
            InventoryItem.collection_name == item.collection_name,
            InventoryItem.storage_location == item.storage_location,
            InventoryItem.status == item.status,
        )
    )
    price = await finish_price(item.scryfall_id, data.foil)
    activity_time = utc_now()
    if target:
        target.quantity += data.quantity
        target.market_price = price
        target.updated_at = activity_time
    else:
        target = InventoryItem(
            card_name=item.card_name,
            set_code=item.set_code,
            set_name=item.set_name,
            collector_number=item.collector_number,
            scryfall_id=item.scryfall_id,
            oracle_id=item.oracle_id,
            quantity=data.quantity,
            foil=data.foil,
            language=item.language,
            condition=item.condition,
            purchase_price=item.purchase_price,
            market_price=price,
            storage_location=item.storage_location,
            collection_name=item.collection_name,
            image_url=item.image_url,
            notes=item.notes,
            color_identity=item.color_identity,
            rarity=item.rarity,
            type_line=item.type_line,
            status=item.status,
            updated_at=activity_time,
        )
        db.add(target)
        db.flush()

    item.quantity -= data.quantity
    if item.quantity == 0:
        db.execute(
            update(ReviewItem)
            .where(ReviewItem.resolved_inventory_id == item.id)
            .values(resolved_inventory_id=target.id)
        )
        db.delete(item)
    record_collection_value(db)
    db.commit()
    db.refresh(target)
    return target


@router.post("/{item_id}/move-copies", response_model=InventoryRead)
async def move_inventory_copies(
    item_id: str, data: InventoryCopyMove, db: Session = Depends(get_db)
):
    """Move selected unassigned physical copies into a finish/condition variant."""
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    if data.foil == item.foil and data.condition == item.condition:
        raise HTTPException(422, "Selected copies already use those properties")
    assigned = db.scalar(
        select(func.coalesce(func.sum(DeckEntry.quantity), 0)).where(
            DeckEntry.inventory_id == item.id
        )
    ) or 0
    available = item.quantity - assigned
    if data.quantity > available:
        raise HTTPException(409, f"Only {available} unassigned copies can be changed")

    target = db.scalar(
        select(InventoryItem).where(
            InventoryItem.scryfall_id == item.scryfall_id,
            InventoryItem.foil == data.foil,
            InventoryItem.language == item.language,
            InventoryItem.condition == data.condition,
            InventoryItem.collection_name == item.collection_name,
            InventoryItem.storage_location == item.storage_location,
            InventoryItem.status == item.status,
        )
    )
    price = (
        await finish_price(item.scryfall_id, data.foil)
        if data.foil != item.foil
        else item.market_price
    )
    activity_time = utc_now()
    if target:
        target.quantity += data.quantity
        target.market_price = price
        target.updated_at = activity_time
    else:
        target = InventoryItem(
            card_name=item.card_name,
            set_code=item.set_code,
            set_name=item.set_name,
            collector_number=item.collector_number,
            scryfall_id=item.scryfall_id,
            oracle_id=item.oracle_id,
            quantity=data.quantity,
            foil=data.foil,
            language=item.language,
            condition=data.condition,
            purchase_price=item.purchase_price,
            market_price=price,
            storage_location=item.storage_location,
            collection_name=item.collection_name,
            image_url=item.image_url,
            notes=item.notes,
            color_identity=item.color_identity,
            rarity=item.rarity,
            type_line=item.type_line,
            status=item.status,
            updated_at=activity_time,
        )
        db.add(target)
        db.flush()

    item.quantity -= data.quantity
    if item.quantity == 0:
        db.execute(
            update(ReviewItem)
            .where(ReviewItem.resolved_inventory_id == item.id)
            .values(resolved_inventory_id=target.id)
        )
        db.delete(item)
    record_collection_value(db)
    db.commit()
    db.refresh(target)
    return target


@router.patch("/{item_id}", response_model=InventoryRead)
async def update_inventory(item_id: str, data: InventoryUpdate, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    changes = data.model_dump(exclude_unset=True)
    if "foil" in changes and changes["foil"] != item.foil:
        changes["market_price"] = await finish_price(item.scryfall_id, changes["foil"])
    if "quantity" in changes:
        assigned = db.scalar(
            select(func.coalesce(func.sum(DeckEntry.quantity), 0)).where(
                DeckEntry.inventory_id == item.id
            )
        ) or 0
        if changes["quantity"] < assigned:
            raise HTTPException(
                409,
                f"Quantity cannot be lower than the {assigned} copies assigned to decks",
            )
    market_price = (
        changes.pop("market_price", None) if "market_price" in changes else item.market_price
    )
    for key, value in changes.items():
        setattr(item, key, value)
    price_changed = apply_price(db, item, market_price)
    if item.quantity == 0:
        delete_item_preserving_reviews(db, item)
        raise HTTPException(204)
    if price_changed or "quantity" in changes:
        record_collection_value(db)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_inventory(item_id: str, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    delete_item_preserving_reviews(db, item)


@router.get("/export/{format}")
def export_inventory(format: str, db: Session = Depends(get_db)):
    records = [
        InventoryRead.model_validate(item).model_dump(mode="json")
        for item in db.scalars(
            select(InventoryItem).options(
                selectinload(InventoryItem.deck_entries).selectinload(DeckEntry.deck)
            )
        )
    ]
    if format == "json":
        return Response(
            json.dumps(records),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=mtglogger.json"},
        )
    if format != "csv":
        raise HTTPException(400, "Format must be csv or json")
    output = io.StringIO()
    if records:
        writer = csv.DictWriter(output, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mtglogger.csv"},
    )
