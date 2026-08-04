import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CollectionValueSnapshot, InventoryItem
from ..services.prices import refresh_prices, refresh_status

router = APIRouter(prefix="/prices", tags=["prices"])


WINDOWS = {
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
    "6m": timedelta(days=183),
    "1y": timedelta(days=365),
}


@router.get("/history")
def history(
    window: str = Query("1d", alias="range", pattern="^(1d|1w|1m|6m|1y)$"),
    db: Session = Depends(get_db),
):
    window_end = datetime.now(UTC)
    cutoff = window_end - WINDOWS[window]
    snapshots = list(
        db.scalars(
            select(CollectionValueSnapshot)
            .where(CollectionValueSnapshot.recorded_at >= cutoff)
            .order_by(CollectionValueSnapshot.recorded_at)
        )
    )
    current = db.scalar(
        select(func.coalesce(func.sum(InventoryItem.market_price * InventoryItem.quantity), 0))
    ) or 0
    points = [
        {"recorded_at": item.recorded_at, "total_value": item.total_value}
        for item in snapshots
    ]
    if not points:
        # Record the first genuine observation once. It appears at its actual
        # timestamp; the chart still leaves the earlier part of the range blank.
        observed = CollectionValueSnapshot(total_value=current, recorded_at=window_end)
        db.add(observed)
        db.commit()
        db.refresh(observed)
        points = [{"recorded_at": observed.recorded_at, "total_value": current}]
    baseline = db.scalar(
        select(CollectionValueSnapshot)
        .where(CollectionValueSnapshot.recorded_at <= cutoff)
        .order_by(CollectionValueSnapshot.recorded_at.desc())
        .limit(1)
    )
    # Inventory additions and edits change the live total between scheduled
    # market refreshes. Include that observed value in the response so the chart
    # and headline always describe the same collection state.
    latest = points[-1]["total_value"] if points else None
    if latest != current:
        points.append({"recorded_at": window_end, "total_value": current})
    # Keep long ranges responsive while retaining the real first/last points.
    if len(points) > 500:
        step = (len(points) - 1) / 499
        indexes = sorted({round(index * step) for index in range(500)})
        points = [points[index] for index in indexes]
    previous = baseline.total_value if baseline is not None else None
    change = current - previous if previous is not None else None
    percentage = (
        change / previous * 100 if change is not None and previous not in (None, 0) else None
    )
    return {
        "current_value": current,
        "previous_value": previous,
        "change": change,
        "change_percentage": percentage,
        "range": window,
        "window_start": cutoff,
        "window_end": window_end,
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
