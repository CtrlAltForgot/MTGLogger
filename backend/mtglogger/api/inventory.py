import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import InventoryItem, ReviewItem
from ..schemas import InventoryCreate, InventoryRead, InventoryUpdate, Page
from ..services.inventory import upsert_inventory

router = APIRouter(prefix="/inventory", tags=["inventory"])


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
    statement = select(InventoryItem).where(*filters)
    column = getattr(InventoryItem, sort, InventoryItem.date_added)
    statement = statement.order_by(column.desc() if descending else column.asc())
    total = db.scalar(select(func.count()).select_from(InventoryItem).where(*filters)) or 0
    items = list(db.scalars(statement.offset((page - 1) * page_size).limit(page_size)))
    return Page(items=items, total=total, page=page, page_size=page_size)


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


@router.patch("/{item_id}", response_model=InventoryRead)
def update_inventory(item_id: str, data: InventoryUpdate, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(404, "Inventory item not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    if item.quantity == 0:
        delete_item_preserving_reviews(db, item)
        raise HTTPException(204)
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
        for item in db.scalars(select(InventoryItem))
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
