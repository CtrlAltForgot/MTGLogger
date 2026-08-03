import asyncio

from fastapi import APIRouter, HTTPException

from ..services.prices import refresh_prices, refresh_status

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/status")
def status():
    return refresh_status()


@router.post("/refresh", status_code=202)
async def start_refresh():
    if refresh_status()["state"] == "running":
        raise HTTPException(409, "A price refresh is already running")
    asyncio.create_task(refresh_prices())
    return {"message": "Price refresh started in the background"}
