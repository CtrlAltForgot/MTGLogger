import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ReviewItem, ReviewStatus
from ..schemas import InventoryCreate, ReviewRead, ReviewResolve
from ..services.inventory import upsert_inventory

router = APIRouter(prefix="/reviews", tags=["reviews"])


def serialize(item: ReviewItem) -> ReviewRead:
    return ReviewRead(
        **{
            key: getattr(item, key)
            for key in ("id", "image_path", "confidence", "ocr_text", "status", "created_at")
        },
        candidates=json.loads(item.candidates_json),
    )


@router.get("", response_model=list[ReviewRead])
def list_reviews(status: ReviewStatus = ReviewStatus.pending, db: Session = Depends(get_db)):
    return [
        serialize(item)
        for item in db.scalars(
            select(ReviewItem)
            .where(ReviewItem.status == status)
            .order_by(ReviewItem.created_at.desc())
        )
    ]


@router.post("/{review_id}/resolve")
def resolve_review(review_id: str, payload: ReviewResolve, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if not review:
        raise HTTPException(404, "Review item not found")
    card = payload.candidate
    defaults = payload.defaults
    item = upsert_inventory(
        db,
        InventoryCreate(
            card_name=card.name,
            set_code=card.set_code,
            set_name=card.set_name,
            collector_number=card.collector_number,
            scryfall_id=card.scryfall_id,
            foil=defaults.foil,
            language=defaults.language,
            condition=defaults.condition,
            market_price=card.market_price,
            storage_location=defaults.storage_location,
            collection_name=defaults.collection_name,
            image_url=card.image_url,
            status=defaults.status,
        ),
    )
    review.status = ReviewStatus.resolved
    review.resolved_inventory_id = item.id
    db.commit()
    return {"inventory_id": item.id}


@router.post("/{review_id}/ignore", status_code=204)
def ignore_review(review_id: str, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if not review:
        raise HTTPException(404, "Review item not found")
    review.status = ReviewStatus.ignored
    db.commit()


@router.delete("/{review_id}", status_code=204)
def delete_review(review_id: str, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if not review:
        raise HTTPException(404, "Review item not found")
    db.delete(review)
    db.commit()
