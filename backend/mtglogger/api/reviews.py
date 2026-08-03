import json
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CardReference, CardVisualExample, Deck, ReviewItem, ReviewStatus
from ..providers import ScryfallProvider
from ..schemas import (
    Candidate,
    InventoryCreate,
    InventoryRead,
    ReviewRead,
    ReviewResolve,
    ScanDefaults,
)
from ..services.decks import assign_to_deck
from ..services.inventory import upsert_inventory
from ..services.recognition import CardRecognizer
from ..services.references import artwork_hash

router = APIRouter(prefix="/reviews", tags=["reviews"])
provider = ScryfallProvider()


def serialize(item: ReviewItem) -> ReviewRead:
    stored = json.loads(item.candidates_json)
    candidates = stored if isinstance(stored, list) else stored.get("candidates", [])
    defaults = ScanDefaults.model_validate(
        stored.get("defaults", {}) if isinstance(stored, dict) else {}
    )
    return ReviewRead(
        **{
            key: getattr(item, key)
            for key in ("id", "image_path", "confidence", "ocr_text", "status", "created_at")
        },
        candidates=candidates,
        defaults=defaults,
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


@router.get("/search", response_model=list[Candidate])
async def search_cards(
    q: str = Query(min_length=2, max_length=100),
    lang: str = Query("en", pattern=r"^[a-z]{2,3}$"),
):
    escaped = q.strip().replace('"', "")
    cards = await provider.search(f'name:"{escaped}"', language=lang)
    return [
        Candidate(
            scryfall_id=card["id"],
            name=card["name"],
            set_code=card["set"],
            set_name=card["set_name"],
            collector_number=card["collector_number"],
            image_url=provider.image_url(card),
            market_price=provider.market_price(card),
            foil_market_price=provider.market_price(card, foil=True),
            finishes=card.get("finishes", []),
            language=card.get("lang", "en"),
            confidence=0,
            oracle_id=card.get("oracle_id"),
            color_identity="".join(card.get("color_identity", [])),
            rarity=card.get("rarity"),
            type_line=card.get("type_line"),
        )
        for card in cards
    ]


@router.get("/{review_id}/image", response_class=FileResponse)
def review_image(review_id: str, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if not review:
        raise HTTPException(404, "Review item not found")
    return FileResponse(review.image_path, media_type="image/jpeg")


@router.post("/{review_id}/resolve")
def resolve_review(review_id: str, payload: ReviewResolve, db: Session = Depends(get_db)):
    review = db.get(ReviewItem, review_id)
    if not review:
        raise HTTPException(404, "Review item not found")
    card = payload.candidate
    stored = serialize(review)
    defaults = payload.defaults or stored.defaults
    if defaults.deck_id and not db.get(Deck, defaults.deck_id):
        if payload.defaults is None:
            defaults = defaults.model_copy(update={"deck_id": None})
        else:
            raise HTTPException(422, "Selected deck no longer exists")
    item = upsert_inventory(
        db,
        InventoryCreate(
            card_name=card.name,
            set_code=card.set_code,
            set_name=card.set_name,
            collector_number=card.collector_number,
            scryfall_id=card.scryfall_id,
            oracle_id=card.oracle_id,
            foil=(defaults.foil or card.is_foil_only()),
            language=defaults.language,
            condition=defaults.condition,
            market_price=(
                (card.foil_market_price or card.market_price)
                if (defaults.foil or card.is_foil_only())
                else card.market_price
            ),
            storage_location=defaults.storage_location,
            collection_name=defaults.collection_name,
            image_url=card.image_url,
            color_identity=card.color_identity,
            rarity=card.rarity,
            type_line=card.type_line,
            status=defaults.status,
        ),
    )
    if defaults.deck_id:
        assign_to_deck(db, defaults.deck_id, item)
    review.status = ReviewStatus.resolved
    review.resolved_inventory_id = item.id
    # A user-confirmed correction becomes a local visual example. This makes
    # repeated cards/printings faster to recognize without training a cloud model.
    image = cv2.imread(review.image_path)
    if image is not None and card.image_url:
        reference = db.get(CardReference, card.scryfall_id)
        learned_hash = artwork_hash(CardRecognizer.rectify(image))
        if reference:
            existing_example = db.scalar(
                select(CardVisualExample).where(
                    CardVisualExample.scryfall_id == card.scryfall_id,
                    CardVisualExample.art_hash == learned_hash,
                )
            )
            if not existing_example:
                db.add(
                    CardVisualExample(
                        scryfall_id=card.scryfall_id,
                        art_hash=learned_hash,
                    )
                )
        else:
            reference = CardReference(
                scryfall_id=card.scryfall_id,
                name=card.name,
                set_code=card.set_code,
                set_name=card.set_name,
                collector_number=card.collector_number,
                image_url=card.image_url,
                art_hash=learned_hash,
                market_price=card.market_price,
            )
            db.add(reference)
            db.flush()
            db.add(CardVisualExample(scryfall_id=card.scryfall_id, art_hash=learned_hash))
    db.commit()
    # Keep the confirmed camera frame as labeled ground truth. These examples
    # are what let us benchmark recognition and improve it from real hardware
    # instead of tuning confidence against synthetic fixtures.
    return InventoryRead.model_validate(item)


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
    image_path = review.image_path
    db.delete(review)
    db.commit()
    Path(image_path).unlink(missing_ok=True)
