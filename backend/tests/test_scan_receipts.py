import asyncio
import io
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from mtglogger.api import scanner
from mtglogger.database import Base
from mtglogger.models import InventoryItem, ReviewItem, ScanReceipt
from mtglogger.schemas import Candidate
from mtglogger.services.recognition import Recognition


@pytest.fixture
def scan_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/scans.db")
    Base.metadata.create_all(engine)
    candidate = Candidate(
        scryfall_id=str(uuid4()), name="Lightning Bolt", set_code="m10",
        set_name="Magic 2010", collector_number="146", confidence=99,
    )
    picture = np.zeros((40, 30, 3), dtype=np.uint8)
    recognition = Recognition(99, "Lightning Bolt", [candidate], picture, picture, 10, True,
                              auto_add_safe=True)
    calls = []

    async def recognize(*args, **kwargs):
        calls.append((args, kwargs))
        return recognition

    monkeypatch.setattr(scanner.recognizer, "recognize", recognize)
    monkeypatch.setattr(scanner, "get_settings", lambda: SimpleNamespace(image_dir=tmp_path))
    monkeypatch.setattr(scanner, "preserve_auto_added_scan", lambda *args: None)
    monkeypatch.setattr(scanner, "preserve_review_scan", lambda *args: None)
    yield engine, recognition, calls
    engine.dispose()


def submit(engine, capture_id, defaults="{}", raw=b"same physical capture", full_photo=False):
    upload = UploadFile(io.BytesIO(raw), filename="capture.jpg",
                        headers=Headers({"content-type": "image/jpeg"}))
    with Session(engine, expire_on_commit=False) as db:
        return asyncio.run(scanner.recognize_card(upload, defaults, capture_id, db, full_photo))


def count(engine, model):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(model))


def test_retry_returns_original_receipt_without_incrementing_inventory(scan_db):
    engine, _, calls = scan_db
    capture_id = uuid4()
    first = submit(engine, capture_id)
    second = submit(engine, capture_id)
    assert first.model_dump() == second.model_dump()
    assert len(calls) == 1
    assert first.inventory.quantity == 1
    assert count(engine, ScanReceipt) == 1
    # A NEW physical capture of the identical printing still counts as another copy.
    assert submit(engine, uuid4()).inventory.quantity == 2


def test_reusing_capture_id_with_changed_image_or_defaults_is_rejected(scan_db):
    engine, _, _ = scan_db
    capture_id = uuid4()
    submit(engine, capture_id)
    for defaults, raw in [('{}', b"different"), ('{"foil":true}', b"same physical capture")]:
        with pytest.raises(HTTPException) as error:
            submit(engine, capture_id, defaults, raw)
        assert error.value.status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(InventoryItem)).quantity == 1


def test_review_retry_creates_one_saved_review(scan_db):
    engine, recognition, _ = scan_db
    recognition.confidence = 80
    recognition.auto_add_safe = False
    capture_id = uuid4()
    first = submit(engine, capture_id)
    assert submit(engine, capture_id).review_id == first.review_id
    assert count(engine, ReviewItem) == 1
    assert count(engine, InventoryItem) == 0


def test_native_photo_geometry_is_forwarded_and_part_of_capture_identity(scan_db):
    engine, _, calls = scan_db
    capture_id = uuid4()
    result = submit(engine, capture_id, full_photo=True)
    assert calls[-1][1] == {"full_photo": True}
    assert submit(engine, capture_id, full_photo=True).model_dump() == result.model_dump()
    with pytest.raises(HTTPException) as error:
        submit(engine, capture_id, full_photo=False)
    assert error.value.status_code == 409


def test_iphone_multipart_contract_and_retry_through_http(scan_db, monkeypatch):
    from fastapi.testclient import TestClient

    from mtglogger.database import get_db
    from mtglogger.main import app

    engine, _, calls = scan_db

    def database():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    client = TestClient(app)
    try:
        fields = {"capture_id": str(uuid4()), "full_photo": "true", "defaults_json": "{}"}
        files = {"image": ("capture.jpg", b"saved camera bytes", "image/jpeg")}
        first = client.post("/api/scanner/recognize", data=fields, files=files)
        second = client.post("/api/scanner/recognize", data=fields, files=files)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["inventory"]["quantity"] == 1
        assert calls == [((b"saved camera bytes", None, "en"), {"full_photo": True})]
        assert client.get("/api/scanner/capabilities").json()["full_photo"] is True
    finally:
        client.close()


def test_inventory_and_receipt_roll_back_together_on_failure(scan_db, monkeypatch):
    engine, _, _ = scan_db
    real_finish = scanner.finish_result

    def interrupted(*args):
        raise RuntimeError("Simulated interruption after inventory flush")

    monkeypatch.setattr(scanner, "finish_result", interrupted)
    capture_id = uuid4()
    with pytest.raises(RuntimeError):
        submit(engine, capture_id)
    assert count(engine, InventoryItem) == count(engine, ScanReceipt) == 0
    monkeypatch.setattr(scanner, "finish_result", real_finish)
    assert submit(engine, capture_id).inventory.quantity == 1


def test_racing_completion_uses_the_already_committed_result(scan_db):
    from mtglogger.schemas import ScanDefaults
    from mtglogger.services.scan_receipts import payload_hash, reserve_result

    engine, _, _ = scan_db
    capture_id = uuid4()
    result = submit(engine, capture_id)
    with Session(engine) as db:
        receipt, cached = reserve_result(
            db, str(capture_id), payload_hash(b"same physical capture", ScanDefaults())
        )
        assert receipt is None
        assert cached.model_dump() == result.model_dump()


def test_legacy_client_can_submit_without_a_capture_id(scan_db):
    engine, _, _ = scan_db
    assert submit(engine, None).disposition == "added"
    assert count(engine, ScanReceipt) == 0
