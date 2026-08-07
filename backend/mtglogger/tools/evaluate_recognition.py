"""Measure exact-printing recognition on user-confirmed camera captures."""

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from sqlalchemy import select

from ..config import get_settings
from ..database import SessionLocal
from ..models import CardReference, InventoryItem, ReviewItem, ReviewStatus
from ..services.recognition import CardRecognizer
from ..services.references import artwork_hash


def stress_variants(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Return deterministic, camera-like perturbations of a real capture."""
    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    rotation = cv2.getRotationMatrix2D(center, 2.5, 1.0)
    rotated = cv2.warpAffine(
        image,
        rotation,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )

    inset_x = max(2, int(width * 0.025))
    inset_y = max(2, int(height * 0.02))
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    target = np.float32(
        [
            [inset_x, inset_y],
            [width - 1, 0],
            [width - 1 - inset_x, height - 1 - inset_y],
            [0, height - 1],
        ]
    )
    perspective = cv2.warpPerspective(
        image,
        cv2.getPerspectiveTransform(source, target),
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    darker = cv2.convertScaleAbs(image, alpha=0.72, beta=-8)
    blurred = cv2.GaussianBlur(image, (3, 3), 0.8)
    return [
        ("original", image),
        ("rotation_2_5deg", rotated),
        ("perspective_mild", perspective),
        ("exposure_dark", darker),
        ("blur_mild", blurred),
    ]


def metric_summary(records: list[dict]) -> dict:
    """Summarize accuracy, intervention, and latency for a result slice."""
    total = len(records)
    latencies = [item["processing_ms"] for item in records]
    return {
        "evaluated_records": total,
        "exact_printing_top1_accuracy": (
            sum(item["top1_correct"] for item in records) / total if total else None
        ),
        "exact_printing_top5_accuracy": (
            sum(item["top5_correct"] for item in records) / total if total else None
        ),
        "exact_card_top1_accuracy": (
            sum(item["card_top1_correct"] for item in records) / total
            if total
            else None
        ),
        "exact_card_top5_accuracy": (
            sum(item["card_top5_correct"] for item in records) / total
            if total
            else None
        ),
        "auto_add_rate": (
            sum(item["auto_add"] for item in records) / total if total else None
        ),
        "false_auto_add_rate": (
            sum(item["false_auto_add"] for item in records) / total if total else None
        ),
        "uncertainty_rate": (
            sum(not item["auto_add"] for item in records) / total if total else None
        ),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": (
            sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
            if latencies
            else None
        ),
    }


async def evaluate(
    limit: int | None,
    manifest: Path | None = None,
    stress: bool = False,
) -> dict:
    # Confirmations are copied into durable evaluation storage at resolution
    # time. Prefer that manifest so deleting or merging an inventory row later
    # cannot silently remove a real camera capture from the benchmark.
    if manifest is None:
        preserved_manifest = get_settings().evaluation_dir / "manifest.json"
        if preserved_manifest.is_file():
            manifest = preserved_manifest
    with SessionLocal() as db:
        if manifest:
            labels = json.loads(manifest.read_text())
            if limit:
                labels = labels[-limit:]
            labeled = []
            for label in labels:
                review_id = label.get("review_id")
                review = db.get(ReviewItem, review_id) if review_id else None
                if label.get("image_path"):
                    review = SimpleNamespace(
                        id=label.get("review_id") or Path(label["image_path"]).stem,
                        image_path=label["image_path"],
                    )
                expected = (
                    db.get(CardReference, label["scryfall_id"])
                    if label.get("scryfall_id")
                    else db.scalar(
                        select(CardReference).where(
                            CardReference.name == label["name"],
                            CardReference.set_code == label["set_code"],
                            CardReference.collector_number == label["collector_number"],
                        )
                    )
                )
                if review and expected:
                    labeled.append((review, expected, label.get("language", "en")))
        else:
            statement = (
                select(ReviewItem, InventoryItem)
                .join(InventoryItem, ReviewItem.resolved_inventory_id == InventoryItem.id)
                .where(ReviewItem.status == ReviewStatus.resolved)
                .order_by(ReviewItem.created_at.desc())
            )
            if limit:
                statement = statement.limit(limit)
            labeled = [(*row, "en") for row in db.execute(statement)]

    recognizer = CardRecognizer()
    # Match the API lifespan: model execution and gallery hydration happen
    # before health checks admit live scanner traffic.
    await asyncio.gather(
        asyncio.to_thread(recognizer._neural.warm),
        asyncio.to_thread(recognizer._neural.warm_model),
        asyncio.to_thread(recognizer._get_visual_catalog),
    )
    results = []
    available_sources = 0
    for review, expected, language in labeled:
        path = Path(review.image_path)
        if not path.is_file():
            continue
        raw = path.read_bytes()
        decoded = recognizer.decode(raw)
        available_sources += 1
        original_hash = artwork_hash(CardRecognizer.rectify(decoded))
        variants = stress_variants(decoded) if stress else [("original", decoded)]
        for variant_name, variant_image in variants:
            if variant_name == "original":
                # Preserve the exact camera payload. Re-encoding the control
                # sample at quality 92 erased tiny collector/set footer text
                # and made the evaluator report reviews that production does
                # not produce on the same bytes. Only synthetic stress
                # variants should introduce another JPEG generation.
                variant_raw = raw
            else:
                encoded, buffer = cv2.imencode(
                    ".jpg", variant_image, [cv2.IMWRITE_JPEG_QUALITY, 92]
                )
                if not encoded:
                    continue
                variant_raw = buffer.tobytes()
            variant_hash = artwork_hash(CardRecognizer.rectify(variant_image))
            result = await recognizer.recognize(
                variant_raw,
                language=language,
                ignored_visual_hashes={original_hash, variant_hash},
                ignored_example_review_ids={review.id},
            )
            ids = [candidate.scryfall_id for candidate in result.candidates]
            names = [candidate.name.casefold() for candidate in result.candidates]
            expected_name = getattr(expected, "card_name", None) or getattr(
                expected, "name"
            )
            ocr_title, ocr_number, ocr_set_code, ocr_year = recognizer.hints(
                result.ocr_text
            )
            top_id = ids[0] if ids else None
            expected_rank = (
                ids.index(expected.scryfall_id) + 1
                if expected.scryfall_id in ids
                else None
            )
            # Mirror the production gate. Confidence alone is intentionally
            # insufficient: an automatic add also needs independent exact-
            # printing proof from the recognizer.
            auto_add = result.confidence >= 98.5 and result.auto_add_safe
            results.append(
                {
                    "review_id": review.id,
                    "variant": variant_name,
                    "expected": {
                        "name": expected_name,
                        "set_code": expected.set_code,
                        "collector_number": expected.collector_number,
                        "scryfall_id": expected.scryfall_id,
                    },
                    "predicted": (
                        {
                            "name": result.candidates[0].name,
                            "set_code": result.candidates[0].set_code,
                            "collector_number": result.candidates[0].collector_number,
                            "scryfall_id": top_id,
                        }
                        if result.candidates
                        else None
                    ),
                    "candidates": [
                        {
                            "rank": rank,
                            "name": candidate.name,
                            "set_code": candidate.set_code,
                            "collector_number": candidate.collector_number,
                            "scryfall_id": candidate.scryfall_id,
                            "confidence": candidate.confidence,
                        }
                        for rank, candidate in enumerate(result.candidates, start=1)
                    ],
                    "confidence": result.confidence,
                    "ocr": {
                        "title": ocr_title,
                        "collector_number": ocr_number,
                        "set_code": ocr_set_code,
                        "copyright_year": ocr_year,
                        "text": result.ocr_text,
                    },
                    "expected_printing_rank": expected_rank,
                    "card_top1_correct": bool(
                        names and names[0] == expected_name.casefold()
                    ),
                    "card_top5_correct": expected_name.casefold() in names,
                    "top1_correct": top_id == expected.scryfall_id,
                    "top5_correct": expected.scryfall_id in ids,
                    "auto_add": auto_add,
                    "auto_add_safe": result.auto_add_safe,
                    "false_auto_add": auto_add and top_id != expected.scryfall_id,
                    "processing_ms": result.processing_ms,
                    "timings_ms": result.timings_ms,
                    "neural": {
                        "top1_correct": bool(
                            result.neural_candidates
                            and result.neural_candidates[0]["scryfall_id"]
                            == expected.scryfall_id
                        ),
                        "top5_correct": expected.scryfall_id
                        in {
                            item["scryfall_id"]
                            for item in (result.neural_candidates or [])[:5]
                        },
                        "candidates": result.neural_candidates or [],
                    },
                }
            )

    totals = metric_summary(results)
    timing_stages = sorted(
        {
            stage
            for item in results
            for stage in (item.get("timings_ms") or {})
        }
    )
    summary = {
        "labeled_records": len(labeled),
        "available_source_records": available_sources,
        "stress_enabled": stress,
        "variants_per_source": 5 if stress else 1,
        "missing_images": len(labeled) - available_sources,
        **totals,
        "by_variant": {
            variant: metric_summary(
                [item for item in results if item["variant"] == variant]
            )
            for variant in dict.fromkeys(item["variant"] for item in results)
        },
        "latency_stage_ms_p50": {
            stage: statistics.median(
                item["timings_ms"][stage]
                for item in results
                if item.get("timings_ms") and stage in item["timings_ms"]
            )
            for stage in timing_stages
        },
        "neural_exact_printing_top1_accuracy": (
            sum(item["neural"]["top1_correct"] for item in results) / len(results)
            if results
            else None
        ),
        "neural_exact_printing_top5_accuracy": (
            sum(item["neural"]["top5_correct"] for item in results) / len(results)
            if results
            else None
        ),
        "samples": [
            {
                "review_id": item["review_id"],
                "variant": item["variant"],
                "expected": item["expected"],
                "confidence": item["confidence"],
                "top1_correct": item["top1_correct"],
                "auto_add": item["auto_add"],
                "processing_ms": item["processing_ms"],
                "timings_ms": item["timings_ms"],
                "ocr": {
                    "title": item["ocr"]["title"],
                    "collector_number": item["ocr"]["collector_number"],
                    "set_code": item["ocr"]["set_code"],
                    "copyright_year": item["ocr"]["copyright_year"],
                },
            }
            for item in results
        ],
        "failures": [item for item in results if not item["top1_correct"]],
        "uncertain": [item for item in results if not item["auto_add"]],
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--stress",
        action="store_true",
        help="Evaluate deterministic camera-like variants of every real capture.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        evaluate(
            args.limit if not args.limit or args.limit > 0 else None,
            args.manifest,
            args.stress,
        )
    )
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
