import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CardReference, CardVisualFingerprint
from ..services.references import sync_all, sync_set, sync_status

router = APIRouter(prefix="/references", tags=["recognition references"])


@router.get("/status")
def status():
    return sync_status()


@router.get("/sets")
def indexed_sets(db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            CardReference.set_code,
            CardReference.set_name,
            func.count(CardReference.scryfall_id),
            func.count(CardVisualFingerprint.scryfall_id),
            func.max(CardVisualFingerprint.updated_at),
        )
        .outerjoin(
            CardVisualFingerprint,
            CardVisualFingerprint.scryfall_id == CardReference.scryfall_id,
        )
        .group_by(CardReference.set_code, CardReference.set_name)
        .order_by(CardReference.set_name)
    )
    return [
        {
            "set_code": code,
            "set_name": name,
            "indexed_printings": indexed,
            "ready_printings": ready,
            "updated_at": updated_at,
        }
        for code, name, indexed, ready, updated_at in rows
    ]


@router.get("/cards")
def indexed_cards(
    set_code: str = Query(min_length=2, max_length=8),
    search: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
):
    statement = (
        select(CardReference, CardVisualFingerprint)
        .join(
            CardVisualFingerprint,
            CardVisualFingerprint.scryfall_id == CardReference.scryfall_id,
        )
        .where(CardReference.set_code == set_code.lower())
    )
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        statement = statement.where(
            or_(CardReference.name.ilike(pattern), CardReference.collector_number.ilike(pattern))
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(
        statement.order_by(CardReference.name, CardReference.collector_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "scryfall_id": card.scryfall_id,
                "name": card.name,
                "set_code": card.set_code,
                "set_name": card.set_name,
                "collector_number": card.collector_number,
                "image_url": card.image_url,
                "language": fingerprint.language,
                "layout": fingerprint.layout,
                "updated_at": fingerprint.updated_at,
            }
            for card, fingerprint in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/sync/{set_code}", status_code=202)
async def start_sync(set_code: str):
    if not re.fullmatch(r"[A-Za-z0-9]{2,8}", set_code):
        raise HTTPException(422, "Set code must contain 2 to 8 letters or numbers")
    current = sync_status()
    if current["state"] == "running":
        raise HTTPException(409, f"Already indexing {current['set_code']}")
    asyncio.create_task(sync_set(set_code))
    return {"message": f"Indexing {set_code.upper()} in the background"}


@router.post("/sync-all", status_code=202)
async def start_full_sync():
    current = sync_status()
    if current["state"] == "running":
        raise HTTPException(409, f"Already indexing {current['set_code']}")
    asyncio.create_task(sync_all())
    return {"message": "Indexing every Scryfall paper printing in the background"}
