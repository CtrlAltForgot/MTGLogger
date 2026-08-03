import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import cv2
import imagehash
from PIL import Image
from sqlalchemy import func, select

from ..config import get_settings
from ..database import SessionLocal
from ..models import CardReference, CardVisualFingerprint
from ..providers import ScryfallProvider


@dataclass
class SyncState:
    state: str = "idle"
    set_code: str | None = None
    completed: int = 0
    total: int = 0
    error: str | None = None
    errors: int = 0
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


def _region_hash(image, top: float, bottom: float, left: float = 0, right: float = 1) -> str:
    height, width = image.shape[:2]
    crop = image[
        int(height * top) : max(int(height * bottom), int(height * top) + 1),
        int(width * left) : max(int(width * right), int(width * left) + 1),
    ]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return str(imagehash.phash(Image.fromarray(rgb), hash_size=8))


def visual_fingerprints(image) -> dict[str, str]:
    """Fingerprint complementary regions of one normalized physical printing."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    full = Image.fromarray(rgb)
    return {
        "full_hash": str(imagehash.phash(full, hash_size=8)),
        "art_hash": artwork_hash(image),
        "title_hash": _region_hash(image, 0.045, 0.145, 0.04, 0.96),
        "footer_hash": _region_hash(image, 0.865, 0.99, 0.035, 0.965),
        "symbol_hash": _region_hash(image, 0.545, 0.66, 0.72, 0.965),
        "frame_hash": str(imagehash.dhash(full, hash_size=8)),
    }


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
        result["fingerprinted_cards"] = (
            db.scalar(select(func.count()).select_from(CardVisualFingerprint)) or 0
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
        _state.errors = 0
    try:
        cards = await provider.cards_for_set(code)
        with _state_lock:
            _state.total = len(cards)
        with SessionLocal() as db:
            for card in cards:
                try:
                    await _index_card(db, provider, card)
                except Exception:
                    db.rollback()
                    with _state_lock:
                        _state.errors += 1
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


async def sync_all() -> None:
    """Resumably fingerprint every Scryfall paper printing."""
    provider = ScryfallProvider()
    with _state_lock:
        if _state.state == "running":
            return
        _state.state = "running"
        _state.set_code = "all-paper"
        _state.completed = _state.total = _state.errors = 0
        _state.error = None
    try:
        with SessionLocal() as db:
            async for cards in provider.paper_printing_pages():
                with _state_lock:
                    _state.total += len(cards)
                for card in cards:
                    try:
                        await _index_card(db, provider, card)
                    except Exception:
                        db.rollback()
                        with _state_lock:
                            _state.errors += 1
                    finally:
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


async def reference_refresh_loop(interval_hours: int) -> None:
    await asyncio.sleep(300)
    while True:
        await sync_all()
        await asyncio.sleep(max(1, interval_hours) * 3600)


async def _index_card(db, provider: ScryfallProvider, card: dict) -> None:
    image_url = provider.image_url(card)
    if not image_url:
        return
    existing = db.get(CardReference, card["id"])
    fingerprint = db.get(CardVisualFingerprint, card["id"])
    cached_image_exists = bool(
        fingerprint
        and fingerprint.cached_image_path
        and Path(fingerprint.cached_image_path).is_file()
    )
    image_unchanged = bool(existing and existing.image_url == image_url)
    cache_satisfied = not get_settings().cache_reference_images or cached_image_exists
    if (
        existing
        and fingerprint
        and fingerprint.symbol_hash
        and image_unchanged
        and cache_satisfied
    ):
        # Metadata and prices can change without changing the canonical image.
        existing.name = card["name"]
        existing.set_code = card["set"]
        existing.set_name = card["set_name"]
        existing.collector_number = card["collector_number"]
        existing.image_url = image_url
        existing.market_price = provider.market_price(card)
        db.commit()
        return
    raw = await provider.download_image(image_url)
    image = cv2.imdecode(__import__("numpy").frombuffer(raw, dtype="uint8"), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unreadable reference image for {card['id']}")
    if not existing:
        existing = CardReference(
            scryfall_id=card["id"],
            name=card["name"],
            set_code=card["set"],
            set_name=card["set_name"],
            collector_number=card["collector_number"],
            image_url=image_url,
            art_hash=artwork_hash(image),
            market_price=provider.market_price(card),
        )
        db.add(existing)
        db.flush()
    cache_path = _cache_image(card["id"], raw)
    db.merge(
        CardVisualFingerprint(
            scryfall_id=card["id"],
            **visual_fingerprints(image),
            language=card.get("lang", "en"),
            layout=card.get("layout", "normal"),
            cached_image_path=str(cache_path) if cache_path else None,
        )
    )
    db.commit()


def _cache_image(scryfall_id: str, raw: bytes) -> Path | None:
    settings = get_settings()
    if not settings.cache_reference_images:
        return None
    path = settings.reference_image_dir / f"{scryfall_id}.jpg"
    if not path.exists():
        path.write_bytes(raw)
    return path
