import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Lock

import cv2
import imagehash
from PIL import Image
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import CardReference
from ..providers import ScryfallProvider


@dataclass
class SyncState:
    state: str = "idle"
    set_code: str | None = None
    completed: int = 0
    total: int = 0
    error: str | None = None
    updated_at: str | None = None


_state = SyncState()
_state_lock = Lock()


def artwork_hash(image) -> str:
    height, width = image.shape[:2]
    # The art box is stable across normal frames; excluding title/rules text makes
    # the hash resilient to language, glare, and small OCR-region differences.
    crop = image[int(height * 0.12) : int(height * 0.58), int(width * 0.055) : int(width * 0.945)]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return str(imagehash.phash(Image.fromarray(rgb), hash_size=8))


def hash_distance(left: str, right: str) -> int:
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def sync_status() -> dict:
    with _state_lock:
        result = asdict(_state)
    with SessionLocal() as db:
        result["indexed_cards"] = db.scalar(select(func.count()).select_from(CardReference)) or 0
        result["indexed_sets"] = (
            db.scalar(select(func.count(func.distinct(CardReference.set_code)))) or 0
        )
    return result


async def sync_set(set_code: str) -> None:
    provider = ScryfallProvider()
    code = set_code.strip().lower()
    with _state_lock:
        if _state.state == "running":
            return
        _state.state, _state.set_code, _state.completed, _state.total, _state.error = (
            "running",
            code,
            0,
            0,
            None,
        )
    try:
        cards = await provider.cards_for_set(code)
        with _state_lock:
            _state.total = len(cards)
        with SessionLocal() as db:
            for card in cards:
                image_url = provider.image_url(card)
                if not image_url:
                    continue
                existing = db.get(CardReference, card["id"])
                if existing:
                    with _state_lock:
                        _state.completed += 1
                    continue
                raw = await provider.download_image(image_url)
                image = cv2.imdecode(
                    __import__("numpy").frombuffer(raw, dtype="uint8"), cv2.IMREAD_COLOR
                )
                if image is None:
                    continue
                db.add(
                    CardReference(
                        scryfall_id=card["id"],
                        name=card["name"],
                        set_code=card["set"],
                        set_name=card["set_name"],
                        collector_number=card["collector_number"],
                        image_url=image_url,
                        art_hash=artwork_hash(image),
                        market_price=provider.market_price(card),
                    )
                )
                db.commit()
                with _state_lock:
                    _state.completed += 1
                await asyncio.sleep(0.1)
        with _state_lock:
            _state.state = "complete"
    except Exception as exc:
        with _state_lock:
            _state.state, _state.error = "failed", str(exc)
    finally:
        with _state_lock:
            _state.updated_at = datetime.now(UTC).isoformat()
