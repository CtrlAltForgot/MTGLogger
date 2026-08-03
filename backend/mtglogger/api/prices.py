import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CollectionValueSnapshot, InventoryItem
from ..services.prices import refresh_prices, refresh_status

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/history")
def history(days: int = Query(365, ge=1, le=3650), db: Session = Depends(get_db)):
    snapshots = list(
        db.scalars(
            select(CollectionValueSnapshot)
            .order_by(CollectionValueSnapshot.recorded_at.desc())
            .limit(days * 4)
        )
    )
    current = db.scalar(
        select(func.coalesce(func.sum(InventoryItem.market_price * InventoryItem.quantity), 0))
    ) or 0
    points = [
        {"recorded_at": item.recorded_at, "total_value": item.total_value}
        for item in reversed(snapshots)
    ]
    if not points:
        baseline = CollectionValueSnapshot(total_value=current)
        db.add(baseline)
        db.commit()
        db.refresh(baseline)
        points = [{"recorded_at": baseline.recorded_at, "total_value": current}]
    # Inventory additions and edits change the live total between scheduled
    # market refreshes. Include that observed value in the response so the chart
    # and headline always describe the same collection state.
    latest = points[-1]["total_value"] if points else None
    if latest != current:
        previous = latest
        points.append({"recorded_at": datetime.now(UTC), "total_value": current})
    else:
        previous = points[-2]["total_value"] if len(points) > 1 else None
    change = current - previous if previous is not None else None
    percentage = (
        change / previous * 100 if change is not None and previous not in (None, 0) else None
    )
    return {
        "current_value": current,
        "previous_value": previous,
        "change": change,
        "change_percentage": percentage,
        "history": points,
    }


@router.get("/status")
def status():
    return refresh_status()


@router.post("/refresh", status_code=202)
async def start_refresh():
    if refresh_status()["state"] == "running":
        raise HTTPException(409, "A price refresh is already running")
    asyncio.create_task(refresh_prices())
    return {"message": "Price refresh started in the background"}
