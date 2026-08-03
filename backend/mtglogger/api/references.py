import asyncio
import re

from fastapi import APIRouter, HTTPException

from ..services.references import sync_all, sync_set, sync_status

router = APIRouter(prefix="/references", tags=["recognition references"])


@router.get("/status")
def status():
    return sync_status()


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
