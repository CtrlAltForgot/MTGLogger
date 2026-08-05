"""Correction backfill and scheduled neural-index maintenance."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
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
from .neural import (
    NeuralEmbedder,
    NeuralRetriever,
    decode_vector,
    store_embedding,
)

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
                "completed_at": datetime.now(
                    ZoneInfo(settings.neural_maintenance_timezone)
                ).isoformat(),
                **result,
            },
            indent=2,
        )
        + "\n"
    )
    temporary.replace(destination)


def run_neural_maintenance() -> dict[str, object]:
    result = backfill_confirmed_embeddings()
    result["training"] = train_metric_adapter()
    NeuralRetriever.refresh_in_background()
    write_maintenance_state(result)
    return result


def benchmark_confirmed_embeddings() -> dict[str, object]:
    """Evaluate camera corrections against all other indexed visual evidence."""
    settings = get_settings()
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(CardNeuralEmbedding).where(
                    CardNeuralEmbedding.model_version == settings.neural_model_version
                )
            )
        )
        names = dict(db.execute(select(CardReference.scryfall_id, CardReference.name)).all())
    if not rows:
        return {"queries": 0}
    matrix = np.stack([decode_vector(row.vector, row.dimensions) for row in rows])
    corrections = [index for index, row in enumerate(rows) if row.source_kind == "correction"]
    exact_top1 = exact_top5 = name_top1 = 0
    observations: list[tuple[float, float, bool]] = []
    for query_index in corrections:
        scores = matrix @ matrix[query_index]
        scores[query_index] = -1
        order = np.argsort(scores)[::-1]
        target = rows[query_index].scryfall_id
        top = rows[int(order[0])]
        correct = top.scryfall_id == target
        exact_top1 += int(correct)
        exact_top5 += int(any(rows[int(index)].scryfall_id == target for index in order[:5]))
        name_top1 += int(names.get(top.scryfall_id) == names.get(target))
        observations.append(
            (float(scores[order[0]]), float(scores[order[0]] - scores[order[1]]), correct)
        )
    query_count = len(corrections)
    policies: list[dict[str, float | int]] = []
    for threshold in np.arange(0.70, 0.991, 0.01):
        for margin in np.arange(0.0, 0.201, 0.02):
            accepted = [item for item in observations if item[0] >= threshold and item[1] >= margin]
            if not accepted:
                continue
            correct_count = sum(item[2] for item in accepted)
            policies.append(
                {
                    "threshold": round(float(threshold), 2),
                    "margin": round(float(margin), 2),
                    "accepted": len(accepted),
                    "precision": round(correct_count / len(accepted), 4),
                    "coverage": round(len(accepted) / max(1, query_count), 4),
                }
            )
    safe = [policy for policy in policies if policy["precision"] >= 0.995]
    safe.sort(key=lambda item: (item["coverage"], item["accepted"]), reverse=True)
    return {
        "queries": query_count,
        "gallery": len(rows) - query_count,
        "exact_top1": round(exact_top1 / max(1, query_count), 4),
        "exact_top5": round(exact_top5 / max(1, query_count), 4),
        "name_top1": round(name_top1 / max(1, query_count), 4),
        "best_zero_error_policy": safe[0] if safe else None,
    }


def _retrieval_metrics(
    queries: np.ndarray,
    query_labels: list[str],
    gallery: np.ndarray,
    gallery_labels: list[str],
    scale: np.ndarray,
) -> dict[str, float]:
    queries = queries * scale
    gallery = gallery * scale
    queries /= np.maximum(np.linalg.norm(queries, axis=1, keepdims=True), 1e-12)
    gallery /= np.maximum(np.linalg.norm(gallery, axis=1, keepdims=True), 1e-12)
    scores = queries @ gallery.T
    order = np.argsort(scores, axis=1)[:, ::-1]
    correct = np.array(
        [gallery_labels[int(order[index, 0])] == label for index, label in enumerate(query_labels)]
    )
    top5 = np.array(
        [
            label in {gallery_labels[int(item)] for item in order[index, :5]}
            for index, label in enumerate(query_labels)
        ]
    )
    margins = (
        scores[np.arange(len(scores)), order[:, 0]]
        - scores[np.arange(len(scores)), order[:, 1]]
    )
    return {
        "top1": float(correct.mean()),
        "top5": float(top5.mean()),
        "mean_margin": float(margins.mean()),
    }


def train_metric_adapter(epochs: int = 120) -> dict[str, object]:
    """Train and conservatively promote a sleeve/camera-aware feature metric."""
    settings = get_settings()
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(CardNeuralEmbedding).where(
                    CardNeuralEmbedding.model_version == settings.neural_model_version
                )
            )
        )
    canonical = {row.scryfall_id: row for row in rows if row.source_kind == "canonical"}
    corrections = [
        row
        for row in rows
        if row.source_kind == "correction" and row.scryfall_id in canonical
    ]
    labels = sorted({row.scryfall_id for row in corrections})
    if len(corrections) < 24 or len(labels) < 8:
        return {
            "state": "insufficient-data",
            "corrections": len(corrections),
            "labels": len(labels),
        }
    validation_labels = set(labels[::5])
    train_rows = [row for row in corrections if row.scryfall_id not in validation_labels]
    validation_rows = [row for row in corrections if row.scryfall_id in validation_labels]
    evaluation_gallery_rows = list(canonical.values())
    dimensions = corrections[0].dimensions
    train_query = np.stack([decode_vector(row.vector, dimensions) for row in train_rows])
    train_positive = np.stack(
        [decode_vector(canonical[row.scryfall_id].vector, dimensions) for row in train_rows]
    )
    evaluation_gallery = np.stack(
        [decode_vector(row.vector, dimensions) for row in evaluation_gallery_rows]
    )
    evaluation_gallery_labels = [row.scryfall_id for row in evaluation_gallery_rows]
    # Mining a bounded hard-negative gallery preserves the useful training
    # signal without multiplying every epoch by the entire 99k-card catalog.
    probe_scores = train_query @ evaluation_gallery.T
    hard_count = min(2048, len(evaluation_gallery_rows))
    hard_indices = set(np.argsort(np.max(probe_scores, axis=0))[-hard_count:].tolist())
    hard_indices.update(
        evaluation_gallery_labels.index(row.scryfall_id) for row in corrections
    )
    selected = sorted(hard_indices)
    gallery_rows = [evaluation_gallery_rows[index] for index in selected]
    gallery = evaluation_gallery[selected]
    gallery_labels = [row.scryfall_id for row in gallery_rows]
    validation_query = np.stack([decode_vector(row.vector, dimensions) for row in validation_rows])
    validation_query_labels = [row.scryfall_id for row in validation_rows]
    identity = np.ones(dimensions, dtype=np.float32)
    baseline = _retrieval_metrics(
        validation_query,
        validation_query_labels,
        evaluation_gallery,
        evaluation_gallery_labels,
        identity,
    )
    try:
        import paddle
        import paddle.nn.functional as functional
    except ImportError as exc:
        raise RuntimeError("Paddle training runtime is unavailable") from exc
    paddle.seed(20260805)
    log_scale = paddle.create_parameter(
        shape=[dimensions], dtype="float32", default_initializer=paddle.nn.initializer.Constant(0.0)
    )
    optimizer = paddle.optimizer.Adam(learning_rate=0.01, parameters=[log_scale])
    query_tensor = paddle.to_tensor(train_query)
    positive_tensor = paddle.to_tensor(train_positive)
    gallery_tensor = paddle.to_tensor(gallery)
    label_indices = paddle.to_tensor(
        np.array([gallery_labels.index(row.scryfall_id) for row in train_rows], dtype=np.int64)
    )
    for _ in range(max(1, epochs)):
        scale_tensor = paddle.exp(paddle.clip(log_scale, -1.5, 1.5))
        query_features = functional.normalize(query_tensor * scale_tensor, axis=1)
        positive_features = functional.normalize(positive_tensor * scale_tensor, axis=1)
        gallery_features = functional.normalize(gallery_tensor * scale_tensor, axis=1)
        similarities = query_features @ gallery_features.T
        mask = functional.one_hot(label_indices, num_classes=len(gallery_rows)).astype("bool")
        negative = paddle.max(
            paddle.where(mask, paddle.full_like(similarities, -2.0), similarities), axis=1
        )
        positive = paddle.sum(query_features * positive_features, axis=1)
        loss = paddle.mean(functional.relu(negative - positive + 0.12))
        loss += 0.002 * paddle.mean(log_scale * log_scale)
        loss.backward()
        optimizer.step()
        optimizer.clear_grad()
    scale = np.exp(np.clip(log_scale.numpy(), -1.5, 1.5)).astype(np.float32)
    candidate = _retrieval_metrics(
        validation_query,
        validation_query_labels,
        evaluation_gallery,
        evaluation_gallery_labels,
        scale,
    )
    promoted = bool(
        candidate["top1"] >= baseline["top1"]
        and candidate["top5"] >= baseline["top5"]
        and candidate["mean_margin"] >= baseline["mean_margin"] - 0.005
    )
    if promoted:
        # Validation chooses whether this training recipe is safe. The promoted
        # artifact is then fit once more with every retained correction, so no
        # confirmed review is discarded from the production model.
        all_query = paddle.to_tensor(
            np.stack([decode_vector(row.vector, dimensions) for row in corrections])
        )
        all_positive = paddle.to_tensor(
            np.stack(
                [
                    decode_vector(canonical[row.scryfall_id].vector, dimensions)
                    for row in corrections
                ]
            )
        )
        all_indices = paddle.to_tensor(
            np.array([gallery_labels.index(row.scryfall_id) for row in corrections], dtype=np.int64)
        )
        for _ in range(40):
            scale_tensor = paddle.exp(paddle.clip(log_scale, -1.5, 1.5))
            query_features = functional.normalize(all_query * scale_tensor, axis=1)
            positive_features = functional.normalize(all_positive * scale_tensor, axis=1)
            gallery_features = functional.normalize(gallery_tensor * scale_tensor, axis=1)
            similarities = query_features @ gallery_features.T
            mask = functional.one_hot(all_indices, num_classes=len(gallery_rows)).astype("bool")
            negative = paddle.max(
                paddle.where(mask, paddle.full_like(similarities, -2.0), similarities), axis=1
            )
            positive = paddle.sum(query_features * positive_features, axis=1)
            loss = paddle.mean(functional.relu(negative - positive + 0.12))
            loss += 0.002 * paddle.mean(log_scale * log_scale)
            loss.backward()
            optimizer.step()
            optimizer.clear_grad()
        scale = np.exp(np.clip(log_scale.numpy(), -1.5, 1.5)).astype(np.float32)
    timestamp = datetime.now(ZoneInfo(settings.neural_maintenance_timezone)).strftime(
        "%Y%m%d-%H%M%S"
    )
    version = f"metric-{timestamp}-{uuid.uuid4().hex[:6]}"
    root = settings.neural_index_dir
    root.mkdir(parents=True, exist_ok=True)
    candidate_path = root / f"{version}.npz"
    np.savez_compressed(candidate_path, scale=scale)
    report = {
        "state": "promoted" if promoted else "rejected",
        "version": version,
        "training_examples": len(train_rows),
        "validation_examples": len(validation_rows),
        "baseline": baseline,
        "candidate": candidate,
    }
    (root / f"{version}.json").write_text(json.dumps(report, indent=2) + "\n")
    if promoted:
        manifest = root / "active-adapter.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": version, "weights": candidate_path.name}, indent=2) + "\n"
        )
        temporary.replace(manifest)
        NeuralRetriever.refresh_in_background()
    return report


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
                image = cv2.imdecode(
                    np.frombuffer(response.content, dtype=np.uint8), cv2.IMREAD_COLOR
                )
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
                        "updated_at": datetime.now(
                            ZoneInfo(settings.neural_maintenance_timezone)
                        ).isoformat(),
                    },
                    indent=2,
                )
                + "\n"
            )
            temporary.replace(state_path)
            await asyncio.sleep(0.05)
    NeuralRetriever.refresh_in_background()
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
