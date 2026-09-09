"""Retry-safe scan submissions without reserving IDs across OCR or network awaits."""

import hashlib

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ScanReceipt
from ..schemas import ScanDefaults, ScanResult


def payload_hash(raw: bytes, defaults: ScanDefaults, *, full_photo: bool = False) -> str:
    digest = hashlib.sha256(raw)
    digest.update(defaults.model_dump_json().encode())
    if full_photo:
        digest.update(b"\x00full_photo")
    return digest.hexdigest()


def cached_result(db: Session, capture_id: str, fingerprint: str) -> ScanResult | None:
    receipt = db.get(ScanReceipt, capture_id)
    if receipt is None:
        return None
    if receipt.payload_hash != fingerprint:
        raise HTTPException(409, "This capture ID was already used for a different scan")
    return ScanResult.model_validate_json(receipt.response_json)


def reserve_result(
    db: Session, capture_id: str, fingerprint: str
) -> tuple[ScanReceipt | None, ScanResult | None]:
    """Call AFTER recognition; finish the mutation and commit without any await.

    The unique insert serializes only the short write transaction. A process
    crash rolls back both the receipt and inventory, so retries remain safe.
    Concurrent retries may compute twice but can never add two physical copies.
    """
    receipt = ScanReceipt(id=capture_id, payload_hash=fingerprint, response_json="")
    db.add(receipt)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        cached = cached_result(db, capture_id, fingerprint)
        if cached is None:
            raise
        return None, cached
    return receipt, None


def finish_result(db: Session, receipt: ScanReceipt | None, result: ScanResult) -> ScanResult:
    if receipt is not None:
        receipt.response_json = result.model_dump_json()
    db.commit()
    return result
