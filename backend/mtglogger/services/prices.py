import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock

from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import InventoryItem
from ..providers import ScryfallProvider

logger = logging.getLogger(__name__)


@dataclass
class PriceRefreshState:
    state: str = "idle"
    completed: int = 0
    total: int = 0
    updated_items: int = 0
    errors: int = 0
    started_at: str | None = None
    finished_at: str | None = None


_state = PriceRefreshState()
_state_lock = Lock()


def refresh_status() -> dict:
    with _state_lock:
        result = asdict(_state)
    with SessionLocal() as db:
        result["inventory_items"] = db.scalar(select(func.count()).select_from(InventoryItem)) or 0
    return result


def _price(card: dict, foil: bool) -> Decimal | None:
    return ScryfallProvider.market_price(card, foil)


async def refresh_prices() -> None:
    with _state_lock:
        if _state.state == "running":
            return
        _state.state = "running"
        _state.completed = _state.updated_items = _state.errors = 0
        _state.started_at = datetime.now(UTC).isoformat()
        _state.finished_at = None
    provider = ScryfallProvider()
    with SessionLocal() as db:
        scryfall_ids = list(db.scalars(select(InventoryItem.scryfall_id).distinct()))
    with _state_lock:
        _state.total = len(scryfall_ids)
    for scryfall_id in scryfall_ids:
        try:
            card = await provider.get_card(scryfall_id)
            with SessionLocal() as db:
                items = list(
                    db.scalars(
                        select(InventoryItem).where(InventoryItem.scryfall_id == scryfall_id)
                    )
                )
                for item in items:
                    value = _price(card, item.foil)
                    if value is not None:
                        item.market_price = value
                        with _state_lock:
                            _state.updated_items += 1
                db.commit()
        except Exception:
            logger.exception("Price refresh failed for Scryfall card %s", scryfall_id)
            with _state_lock:
                _state.errors += 1
        finally:
            with _state_lock:
                _state.completed += 1
        # Scryfall asks clients to remain below ten requests per second.
        await asyncio.sleep(0.12)
    with _state_lock:
        _state.state = "complete"
        _state.finished_at = datetime.now(UTC).isoformat()


async def price_refresh_loop(interval_hours: int) -> None:
    # Let startup and scanner OCR initialization settle before background traffic.
    await asyncio.sleep(60)
    while True:
        await refresh_prices()
        await asyncio.sleep(max(1, interval_hours) * 3600)
