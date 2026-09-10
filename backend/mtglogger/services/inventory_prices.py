"""Keep external price requests outside inventory write transactions."""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import CardReference, InventoryItem
from ..providers import ScryfallProvider
from .prices import _eur_usd_rate, _price, apply_price, record_collection_value

logger = logging.getLogger(__name__)
PRICE_REFRESH_TIMEOUT = 6.0


def cached_finish_price(db: Session, scryfall_id: str, foil: bool) -> Decimal | None:
    """Use a known price for this exact printing and finish; never swap finishes."""
    value = db.scalar(
        select(InventoryItem.market_price)
        .where(
            InventoryItem.scryfall_id == scryfall_id,
            InventoryItem.foil == foil,
            InventoryItem.market_price.is_not(None),
        )
        .order_by(InventoryItem.updated_at.desc())
        .limit(1)
    )
    if value is not None or foil:
        return value
    # The reference catalog's existing price column contains USD nonfoil only.
    return db.scalar(
        select(CardReference.market_price).where(
            CardReference.scryfall_id == scryfall_id,
        )
    )


async def refresh_inventory_price(
    item_id: str,
    scryfall_id: str,
    foil: bool,
    expected_updated_at: datetime,
) -> None:
    """Fetch after the save response, then update only the version we scheduled."""
    try:
        async with asyncio.timeout(PRICE_REFRESH_TIMEOUT):
            card = await ScryfallProvider().get_card(scryfall_id)
            value = _price(card, foil)
            if value is None and card.get("prices", {}).get("eur_foil" if foil else "eur"):
                value = _price(card, foil, await _eur_usd_rate())
        if value is None:
            return
        # No database transaction or row lock is held during the network wait.
        await asyncio.to_thread(
            _store_price,
            item_id,
            scryfall_id,
            foil,
            expected_updated_at,
            value,
        )
    except Exception:
        # A price lookup failure must never turn a committed edit into an error.
        # The normal periodic price refresh will try again later.
        logger.warning("Price refresh deferred for inventory item %s", item_id, exc_info=True)


def _store_price(item_id, scryfall_id, foil, expected_updated_at, value):
    with SessionLocal() as db:
        item = db.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.id == item_id,
                InventoryItem.scryfall_id == scryfall_id,
                InventoryItem.foil == foil,
                InventoryItem.updated_at == expected_updated_at,
            )
            .with_for_update()
        )
        if item is not None and apply_price(db, item, value):
            record_collection_value(db)
            db.commit()


def schedule_price_refresh(background_tasks, item: InventoryItem) -> None:
    if background_tasks is not None:
        background_tasks.add_task(
            refresh_inventory_price,
            item.id,
            item.scryfall_id,
            item.foil,
            item.updated_at,
        )
