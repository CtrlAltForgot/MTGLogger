import json
import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Deck, ReviewItem
from ..schemas import InventoryCreate, InventoryRead, ScanDefaults, ScanResult
from ..services.decks import assign_to_deck
from ..services.evaluation import preserve_review_scan
from ..services.inventory import upsert_inventory
from ..services.recognition import CardRecognizer, save_scan

router = APIRouter(prefix="/scanner", tags=["scanner"])
logger = logging.getLogger(__name__)
recognizer = CardRecognizer()
MAX_IMAGE_BYTES = 15_000_000


async def read_bounded_upload(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image is larger than 15 MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/capabilities")
def capabilities():
    return {"ocr": recognizer.ocr_available, "artwork_matching": True}


@router.post("/upload-check")
async def upload_check(image: UploadFile = File(...)):
    """Consume a camera-sized multipart upload without recognition or persistence."""
    raw = await read_bounded_upload(image)
    return {"status": "ok", "bytes": len(raw)}


@router.post("/recognize", response_model=ScanResult)
async def recognize_card(
    image: UploadFile = File(...), defaults_json: str = Form("{}"), db: Session = Depends(get_db)
):
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Upload a JPEG, PNG, or WebP image")
    try:
        defaults = ScanDefaults.model_validate_json(defaults_json)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid scan defaults: {exc}") from exc
    if defaults.deck_id and not db.get(Deck, defaults.deck_id):
        raise HTTPException(422, "Selected deck no longer exists")
    raw = await read_bounded_upload(image)
    try:
        result = await recognizer.recognize(raw, defaults.box_set_code, defaults.language)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Wood/cloth boundaries can themselves resemble a card contour, and the
    # empty table occasionally produces one or two garbage OCR glyphs. A frame
    # with no candidate and no meaningful text is not actionable review data,
    # regardless of that weak contour signal. Artwork-only cards are retained
    # when the visual Art Series matcher supplies a candidate.
    meaningful_ocr = re.findall(r"[A-Za-z0-9]{2,}", result.ocr_text)
    if not result.candidates and not result.card_structure and sum(map(len, meaningful_ocr)) < 4:
        return ScanResult(
            disposition="empty",
            confidence=0,
            candidates=[],
            message="No card detected",
            processing_ms=result.processing_ms,
        )

    # Automatic inventory writes require near-certain agreement. Scores below
    # this remain one-key confirmations, even when automatic mode is enabled.
    if (
        result.confidence >= 98.5
        and result.auto_add_safe
        and result.candidates
        and defaults.auto_add
    ):
        top = result.candidates[0]
        foil = defaults.foil or top.is_foil_only()
        item = upsert_inventory(
            db,
            InventoryCreate(
                card_name=top.name,
                set_code=top.set_code,
                set_name=top.set_name,
                collector_number=top.collector_number,
                scryfall_id=top.scryfall_id,
                oracle_id=top.oracle_id,
                foil=foil,
                language=defaults.language,
                condition=defaults.condition,
                market_price=(
                    (top.foil_market_price or top.market_price) if foil else top.market_price
                ),
                storage_location=defaults.storage_location,
                collection_name=defaults.collection_name,
                image_url=top.image_url,
                color_identity=top.color_identity,
                rarity=top.rarity,
                type_line=top.type_line,
                status=defaults.status,
            ),
        )
        if defaults.deck_id:
            assign_to_deck(db, defaults.deck_id, item)
        return ScanResult(
            disposition="added",
            confidence=result.confidence,
            inventory=InventoryRead.model_validate(item),
            candidates=result.candidates,
            message=f"Added {top.name}{' · foil' if foil else ''}",
            processing_ms=result.processing_ms,
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    path = get_settings().image_dir / f"{timestamp}.jpg"
    save_scan(result.corrected, path)
    review = ReviewItem(
        image_path=str(path),
        confidence=result.confidence,
        ocr_text=result.ocr_text,
        candidates_json=json.dumps(
            {
                "candidates": [
                    candidate.model_dump(mode="json") for candidate in result.candidates
                ],
                "defaults": defaults.model_dump(mode="json"),
            }
        ),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    try:
        preserve_review_scan(path, review.id)
    except (OSError, ValueError, json.JSONDecodeError):
        logger.exception("Could not archive queued review %s", review.id)
    disposition = (
        "confirmation"
        if result.confidence > 95
        else ("suggestions" if result.confidence >= 70 else "queued")
    )
    return ScanResult(
        disposition=disposition,
        confidence=result.confidence,
        candidates=result.candidates,
        review_id=review.id,
        message=(
            "Confirm this match"
            if disposition == "confirmation"
            else ("Choose a match" if disposition == "suggestions" else "Saved to review queue")
        ),
        processing_ms=result.processing_ms,
    )
