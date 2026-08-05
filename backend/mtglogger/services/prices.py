import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock

from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import CardReference, CollectionValueSnapshot, InventoryItem, PriceSnapshot
from ..providers import ScryfallProvider
from ..providers.scryfall import scryfall_api_get

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


def _price(card: dict, foil: bool, eur_usd_rate: Decimal | None = None) -> Decimal | None:
    native = ScryfallProvider.market_price(card, foil)
    if native is not None or eur_usd_rate is None:
        return native
    value = card.get("prices", {}).get("eur_foil" if foil else "eur")
    try:
        return (Decimal(value) * eur_usd_rate).quantize(Decimal("0.01")) if value else None
    except Exception:
        return None


async def _eur_usd_rate() -> Decimal | None:
    """Fetch the ECB-backed daily rate used only when Scryfall has no USD price."""
    try:
        response = await scryfall_api_get(
            "https://api.frankfurter.dev/v1/latest", params={"from": "EUR", "to": "USD"}
        )
        response.raise_for_status()
        return Decimal(str(response.json()["rates"]["USD"]))
    except Exception:
        logger.warning("EUR price fallback unavailable", exc_info=True)
        return None


def apply_price(db, item: InventoryItem, value: Decimal | None) -> bool:
    """Apply a genuine price change while retaining the previous observed value."""
    if value is None or item.market_price == value:
        return False
    if item.market_price is not None:
        db.add(PriceSnapshot(inventory_id=item.id, market_price=item.market_price))
    item.market_price = value
    return True


def record_collection_value(db) -> CollectionValueSnapshot:
    total = db.scalar(
        select(func.coalesce(func.sum(InventoryItem.market_price * InventoryItem.quantity), 0))
    ) or Decimal("0")
    snapshot = CollectionValueSnapshot(total_value=total)
    db.add(snapshot)
    return snapshot


async def refresh_prices() -> None:
    with _state_lock:
        if _state.state == "running":
            return
        _state.state = "running"
        _state.completed = _state.updated_items = _state.errors = 0
        _state.started_at = datetime.now(UTC).isoformat()
        _state.finished_at = None
    provider = ScryfallProvider()
    eur_usd_rate = await _eur_usd_rate()
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
                    item.color_identity = "".join(card.get("color_identity") or [])
                    item.rarity = card.get("rarity")
                    item.type_line = card.get("type_line")
                    # Some catalog entries (notably The List variants) only
                    # exist in foil. Older local reference rows did not retain
                    # finishes, so those copies could be stored as nonfoil and
                    # would forever request Scryfall's null USD nonfoil field.
                    finishes = set(card.get("finishes") or [])
                    if finishes == {"foil"} and not item.foil:
                        item.foil = True
                    value = _price(card, item.foil, eur_usd_rate)
                    if apply_price(db, item, value):
                        with _state_lock:
                            _state.updated_items += 1
                reference = db.get(CardReference, scryfall_id)
                if reference:
                    reference.color_identity = "".join(card.get("color_identity") or [])
                    reference.rarity = card.get("rarity")
                    reference.type_line = card.get("type_line")
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
    with SessionLocal() as db:
        record_collection_value(db)
        db.commit()
    with _state_lock:
        _state.state = "complete"
        _state.finished_at = datetime.now(UTC).isoformat()


async def price_refresh_loop(interval_hours: int) -> None:
    # Let startup and scanner OCR initialization settle before background traffic.
    await asyncio.sleep(60)
    while True:
        await refresh_prices()
        await asyncio.sleep(max(1, interval_hours) * 3600)
