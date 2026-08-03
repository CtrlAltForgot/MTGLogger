import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import ReviewItem
from ..schemas import InventoryCreate, InventoryRead, ScanDefaults, ScanResult
from ..services.inventory import upsert_inventory
from ..services.recognition import CardRecognizer, save_scan

router = APIRouter(prefix="/scanner", tags=["scanner"])
recognizer = CardRecognizer()


@router.get("/capabilities")
def capabilities():
    return {"ocr": recognizer.ocr_available, "artwork_matching": True}


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
    raw = await image.read()
    if len(raw) > 15_000_000:
        raise HTTPException(413, "Image is larger than 15 MB")
    try:
        result = await recognizer.recognize(raw, defaults.box_set_code)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if result.confidence > 95 and result.candidates:
        top = result.candidates[0]
        item = upsert_inventory(
            db,
            InventoryCreate(
                card_name=top.name,
                set_code=top.set_code,
                set_name=top.set_name,
                collector_number=top.collector_number,
                scryfall_id=top.scryfall_id,
                foil=defaults.foil,
                language=defaults.language,
                condition=defaults.condition,
                market_price=top.market_price,
                storage_location=defaults.storage_location,
                collection_name=defaults.collection_name,
                image_url=top.image_url,
                status=defaults.status,
            ),
        )
        return ScanResult(
            disposition="added",
            confidence=result.confidence,
            inventory=InventoryRead.model_validate(item),
            candidates=result.candidates,
            message=f"Added {top.name}",
        )

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    path = get_settings().image_dir / f"{timestamp}.jpg"
    save_scan(result.corrected, path)
    review = ReviewItem(
        image_path=str(path),
        confidence=result.confidence,
        ocr_text=result.ocr_text,
        candidates_json=json.dumps(
            [candidate.model_dump(mode="json") for candidate in result.candidates]
        ),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    disposition = "suggestions" if result.confidence >= 70 else "queued"
    return ScanResult(
        disposition=disposition,
        confidence=result.confidence,
        candidates=result.candidates,
        review_id=review.id,
        message="Choose a match" if disposition == "suggestions" else "Saved to review queue",
    )
