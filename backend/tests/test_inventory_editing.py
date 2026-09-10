import asyncio
import time
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mtglogger.api import inventory
from mtglogger.database import Base
from mtglogger.models import InventoryItem
from mtglogger.providers import scryfall
from mtglogger.schemas import InventoryCopyMove, InventoryUpdate
from mtglogger.services import inventory_prices

CARD = dict(
    card_name="Lightning Bolt",
    set_code="m11",
    set_name="Magic 2011",
    collector_number="146",
    scryfall_id="00000000-0000-0000-0000-000000000001",
    quantity=5,
    foil=False,
    market_price=Decimal("0.42"),
)


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/inventory.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(inventory_prices, "SessionLocal", factory)
    with factory() as db:
        item = InventoryItem(**CARD)
        db.add(item)
        db.commit()
        yield db, item, factory
    engine.dispose()


@pytest.mark.asyncio
async def test_foil_edit_commits_before_a_stalled_price_request(database, monkeypatch):
    db, item, factory = database
    calls = []

    async def stalled(_self, _id):
        calls.append(_id)
        await asyncio.Event().wait()

    monkeypatch.setattr(inventory_prices.ScryfallProvider, "get_card", stalled)
    monkeypatch.setattr(inventory_prices, "PRICE_REFRESH_TIMEOUT", 0.02)
    background = BackgroundTasks()
    start = time.monotonic()
    target = inventory.move_inventory_copies(
        item.id,
        InventoryCopyMove(
            quantity=2,
            foil=True,
            condition="near_mint",
            expected_updated_at=item.updated_at,
        ),
        db,
        background,
    )
    assert time.monotonic() - start < 0.5
    assert not calls
    assert target.market_price is None  # Never pass the nonfoil price off as foil.
    with factory() as reader:
        assert {(row.foil, row.quantity) for row in reader.scalars(select(InventoryItem))} == {
            (False, 3),
            (True, 2),
        }
    await background()
    assert calls == [item.scryfall_id]
    assert db.get(InventoryItem, target.id).quantity == 2


def test_repeating_a_copy_move_cannot_move_the_same_selection_twice(database):
    db, item, _ = database
    change = InventoryCopyMove(
        quantity=2, foil=True, condition="near_mint", expected_updated_at=item.updated_at
    )
    target = inventory.move_inventory_copies(item.id, change, db)
    with pytest.raises(HTTPException) as error:
        inventory.move_inventory_copies(item.id, change, db)
    assert error.value.status_code == 409
    assert item.quantity == 3
    assert target.quantity == 2


def test_stale_metadata_edit_is_rejected_without_overwriting_a_newer_save(database):
    db, item, _ = database
    before = item.updated_at
    inventory.update_inventory(item.id, InventoryUpdate(notes="New notes"), db)
    with pytest.raises(HTTPException) as error:
        inventory.update_inventory(
            item.id,
            InventoryUpdate(
                notes="Old editor",
                expected_updated_at=before,
            ),
            db,
        )
    assert error.value.status_code == 409
    assert item.notes == "New notes"


def test_condition_only_move_keeps_price_and_does_not_queue_network_work(database):
    db, item, _ = database
    background = BackgroundTasks()
    target = inventory.move_inventory_copies(
        item.id,
        InventoryCopyMove(
            quantity=1,
            foil=False,
            condition="lightly_played",
        ),
        db,
        background,
    )
    assert target.market_price == Decimal("0.42")
    assert not background.tasks


def test_price_refresh_does_not_overwrite_a_later_manual_price(database):
    db, item, factory = database
    before = item.updated_at
    item.market_price = Decimal("9.99")
    item.updated_at = before + timedelta(seconds=1)
    db.commit()
    inventory_prices._store_price(item.id, item.scryfall_id, item.foil, before, Decimal("1.00"))
    with factory() as reader:
        assert reader.get(InventoryItem, item.id).market_price == Decimal("9.99")


@pytest.mark.asyncio
async def test_background_refresh_uses_the_existing_euro_fallback(database, monkeypatch):
    db, item, factory = database

    async def card(_self, _id):
        return {"prices": {"usd": None, "eur": "2.00"}}

    async def exchange_rate():
        return Decimal("1.10")

    monkeypatch.setattr(inventory_prices.ScryfallProvider, "get_card", card)
    monkeypatch.setattr(inventory_prices, "_eur_usd_rate", exchange_rate)
    await inventory_prices.refresh_inventory_price(
        item.id,
        item.scryfall_id,
        item.foil,
        item.updated_at,
    )
    with factory() as reader:
        assert reader.get(InventoryItem, item.id).market_price == Decimal("2.20")


@pytest.mark.asyncio
async def test_slow_provider_response_does_not_block_unrelated_requests(monkeypatch):
    started = asyncio.Event()
    starts = []

    class Client:
        async def request(self, _method, url, **_kwargs):
            starts.append(time.monotonic())
            if url.endswith("slow"):
                started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(scryfall, "scryfall_client", lambda: Client())
    monkeypatch.setattr(scryfall, "_api_request_lock", asyncio.Lock())
    monkeypatch.setattr(scryfall, "_api_last_request_at", 0)
    slow = asyncio.create_task(scryfall.scryfall_api_get("https://example.test/slow"))
    try:
        await started.wait()
        response = await asyncio.wait_for(
            scryfall.scryfall_api_get("https://example.test/fast"),
            timeout=0.5,
        )
        assert response.status_code == 200
        assert not slow.done()
        assert starts[1] - starts[0] >= 0.09
    finally:
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
