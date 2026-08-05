"""Correction backfill and scheduled neural-index maintenance."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import httpx
import numpy as np
from sqlalchemy import func, select

from ..config import get_settings
from ..database import SessionLocal
from ..models import CardNeuralEmbedding, CardReference
from .neural import NeuralEmbedder, NeuralRetriever, store_embedding

logger = logging.getLogger(__name__)


def backfill_confirmed_embeddings() -> dict[str, int]:
    """Embed every retained, labeled camera correction not already indexed."""
    settings = get_settings()
    manifest_path = settings.evaluation_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"labeled": 0, "indexed": 0, "missing": 0, "failed": 0}
    records = json.loads(manifest_path.read_text())
    embedder = NeuralEmbedder()
    if not embedder.available:
        raise RuntimeError("Cannot index corrections until the neural model is installed")
    result = {"labeled": len(records), "indexed": 0, "missing": 0, "failed": 0}
    with SessionLocal() as db:
        for record in records:
            review_id = record.get("review_id")
            card_id = record.get("scryfall_id")
            image_path = Path(record.get("image_path", ""))
            if not review_id or not card_id or not image_path.is_file():
                result["missing"] += 1
                continue
            if not db.get(CardReference, card_id):
                result["missing"] += 1
                continue
            exists = db.scalar(
                select(func.count())
                .select_from(CardNeuralEmbedding)
                .where(
                    CardNeuralEmbedding.model_version == settings.neural_model_version,
                    CardNeuralEmbedding.source_kind == "correction",
                    CardNeuralEmbedding.source_id == review_id,
                )
            )
            if exists:
                continue
            try:
                vector = embedder.embed_path(image_path)
                store_embedding(
                    db,
                    scryfall_id=card_id,
                    source_kind="correction",
                    source_id=review_id,
                    vector=vector,
                )
                db.commit()
                result["indexed"] += 1
            except Exception:
                db.rollback()
                result["failed"] += 1
                logger.exception("Could not index confirmed neural example %s", review_id)
    return result


def write_maintenance_state(result: dict[str, int]) -> None:
    settings = get_settings()
    settings.neural_index_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.neural_index_dir / "maintenance.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "model_version": settings.neural_model_version,
                "completed_at": datetime.now(ZoneInfo(settings.neural_maintenance_timezone)).isoformat(),
                **result,
            },
            indent=2,
        )
        + "\n"
    )
    temporary.replace(destination)


def run_neural_maintenance() -> dict[str, int]:
    result = backfill_confirmed_embeddings()
    NeuralRetriever.invalidate()
    write_maintenance_state(result)
    return result


async def backfill_reference_embeddings(
    *, limit: int | None = None, batch_size: int = 8
) -> dict[str, int]:
    """Download and embed canonical artwork missing from the active model index."""
    settings = get_settings()
    embedder = NeuralEmbedder()
    if not embedder.available:
        raise RuntimeError("Cannot index references until the neural model is installed")
    with SessionLocal() as db:
        embedded_ids = select(CardNeuralEmbedding.source_id).where(
            CardNeuralEmbedding.model_version == settings.neural_model_version,
            CardNeuralEmbedding.source_kind == "canonical",
        )
        query = (
            select(CardReference.scryfall_id, CardReference.image_url)
            .where(CardReference.scryfall_id.not_in(embedded_ids))
            .order_by(CardReference.released_at.desc().nullslast(), CardReference.scryfall_id)
        )
        if limit is not None:
            query = query.limit(limit)
        pending = list(db.execute(query).all())
    result = {"total": len(pending), "indexed": 0, "failed": 0}
    settings.neural_index_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.neural_index_dir / "reference-backfill.json"
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {"User-Agent": settings.scryfall_user_agent}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset : offset + max(1, batch_size)]
            responses = await asyncio.gather(
                *(client.get(image_url) for _, image_url in batch), return_exceptions=True
            )
            decoded: list[tuple[str, np.ndarray]] = []
            for (card_id, _), response in zip(batch, responses, strict=True):
                if isinstance(response, Exception) or response.status_code >= 400:
                    result["failed"] += 1
                    continue
                image = cv2.imdecode(np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    result["failed"] += 1
                    continue
                decoded.append((card_id, image))
            if decoded:
                try:
                    vectors = await asyncio.to_thread(
                        embedder.embed_many, [image for _, image in decoded]
                    )
                    with SessionLocal() as db:
                        for (card_id, _), vector in zip(decoded, vectors, strict=True):
                            store_embedding(
                                db,
                                scryfall_id=card_id,
                                source_kind="canonical",
                                source_id=card_id,
                                vector=vector,
                            )
                        db.commit()
                    result["indexed"] += len(decoded)
                except Exception:
                    result["failed"] += len(decoded)
                    logger.exception("Could not embed canonical batch at offset %s", offset)
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        **result,
                        "remaining": max(0, result["total"] - result["indexed"] - result["failed"]),
                        "batches": math.ceil(result["total"] / max(1, batch_size)),
                        "updated_at": datetime.now(ZoneInfo(settings.neural_maintenance_timezone)).isoformat(),
                    },
                    indent=2,
                )
                + "\n"
            )
            temporary.replace(state_path)
            await asyncio.sleep(0.05)
    NeuralRetriever.invalidate()
    return result


def _seconds_until_maintenance() -> float:
    settings = get_settings()
    timezone = ZoneInfo(settings.neural_maintenance_timezone)
    now = datetime.now(timezone)
    target = now.replace(
        hour=max(0, min(23, settings.neural_maintenance_hour)),
        minute=0,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def neural_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_maintenance())
        try:
            await asyncio.to_thread(run_neural_maintenance)
        except Exception:
            logger.exception("Scheduled neural maintenance failed")
