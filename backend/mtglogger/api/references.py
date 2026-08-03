import asyncio
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CardReference, CardVisualFingerprint
from ..providers import ScryfallProvider
from ..services.references import sync_all, sync_set, sync_status

router = APIRouter(prefix="/references", tags=["recognition references"])
provider = ScryfallProvider()
_detail_cache: dict[str, tuple[float, dict]] = {}


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


def serialize_card_details(card: dict) -> dict:
    faces = card.get("card_faces") or []
    image_url = provider.image_url(card)
    return {
        "scryfall_id": card["id"],
        "oracle_id": card.get("oracle_id"),
        "name": card["name"],
        "set_code": card.get("set", ""),
        "set_name": card.get("set_name", ""),
        "collector_number": card.get("collector_number", ""),
        "image_url": image_url,
        "mana_cost": card.get("mana_cost") or (faces[0].get("mana_cost") if faces else None),
        "type_line": card.get("type_line") or (faces[0].get("type_line") if faces else None),
        "oracle_text": card.get("oracle_text") or "\n\n".join(
            face.get("oracle_text", "") for face in faces if face.get("oracle_text")
        ),
        "flavor_text": card.get("flavor_text") or (faces[0].get("flavor_text") if faces else None),
        "power": card.get("power") or (faces[0].get("power") if faces else None),
        "toughness": card.get("toughness") or (faces[0].get("toughness") if faces else None),
        "loyalty": card.get("loyalty") or (faces[0].get("loyalty") if faces else None),
        "rarity": card.get("rarity"),
        "artist": card.get("artist"),
        "language": card.get("lang", "en"),
        "released_at": card.get("released_at"),
        "finishes": card.get("finishes", []),
        "prices": card.get("prices", {}),
        "legalities": card.get("legalities", {}),
        "scryfall_uri": card.get("scryfall_uri"),
    }


@router.get("/card/{scryfall_id}")
async def card_details(scryfall_id: str, db: Session = Depends(get_db)):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", scryfall_id):
        raise HTTPException(422, "Invalid Scryfall ID")
    cached = _detail_cache.get(scryfall_id)
    if cached and time.monotonic() - cached[0] < 86_400:
        return cached[1]
    try:
        details = serialize_card_details(await provider.get_card(scryfall_id))
    except Exception as exc:
        reference = db.get(CardReference, scryfall_id)
        if not reference:
            raise HTTPException(404, "Card printing not found") from exc
        details = {
            "scryfall_id": reference.scryfall_id,
            "name": reference.name,
            "set_code": reference.set_code,
            "set_name": reference.set_name,
            "collector_number": reference.collector_number,
            "image_url": reference.image_url,
            "prices": {"usd": str(reference.market_price) if reference.market_price else None},
        }
    if len(_detail_cache) >= 512:
        oldest = min(_detail_cache, key=lambda key: _detail_cache[key][0])
        _detail_cache.pop(oldest, None)
    _detail_cache[scryfall_id] = (time.monotonic(), details)
    return details


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
