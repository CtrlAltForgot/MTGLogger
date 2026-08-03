from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import InventoryItem, ReviewItem, ReviewStatus
from ..schemas import DashboardSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db)):
    total_cards = db.scalar(select(func.coalesce(func.sum(InventoryItem.quantity), 0))) or 0
    total_value = db.scalar(
        select(func.coalesce(func.sum(InventoryItem.market_price * InventoryItem.quantity), 0))
    ) or Decimal(0)
    unique = db.scalar(select(func.count()).select_from(InventoryItem)) or 0
    review_count = (
        db.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.status == ReviewStatus.pending)
        )
        or 0
    )

    def group(column):
        return [
            {"label": label or "Unknown", "count": count}
            for label, count in db.execute(
                select(column, func.sum(InventoryItem.quantity))
                .group_by(column)
                .order_by(desc(func.sum(InventoryItem.quantity)))
                .limit(20)
            )
        ]

    valuable = list(
        db.scalars(
            select(InventoryItem)
            .where(InventoryItem.market_price.is_not(None))
            .order_by(InventoryItem.market_price.desc())
            .limit(20)
        )
    )
    newest = list(
        db.scalars(select(InventoryItem).order_by(InventoryItem.date_added.desc()).limit(8))
    )
    duplicates = list(
        db.scalars(
            select(InventoryItem)
            .where(InventoryItem.quantity > 1)
            .order_by(InventoryItem.quantity.desc(), InventoryItem.card_name.asc())
            .limit(8)
        )
    )
    return DashboardSummary(
        total_value=total_value,
        total_cards=total_cards,
        unique_printings=unique,
        review_count=review_count,
        by_set=group(InventoryItem.set_name),
        by_color=group(InventoryItem.color_identity),
        by_rarity=group(InventoryItem.rarity),
        by_type=group(InventoryItem.type_line),
        most_valuable=valuable,
        newest=newest,
        duplicate_cards=duplicates,
    )
