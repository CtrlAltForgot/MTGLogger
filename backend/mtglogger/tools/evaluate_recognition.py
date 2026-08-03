"""Measure exact-printing recognition on user-confirmed camera captures."""

import argparse
import asyncio
import json
import statistics
from pathlib import Path

from sqlalchemy import select

from ..database import SessionLocal
from ..models import InventoryItem, ReviewItem, ReviewStatus
from ..services.recognition import CardRecognizer
from ..services.references import artwork_hash


async def evaluate(limit: int | None) -> dict:
    with SessionLocal() as db:
        statement = (
            select(ReviewItem, InventoryItem)
            .join(InventoryItem, ReviewItem.resolved_inventory_id == InventoryItem.id)
            .where(ReviewItem.status == ReviewStatus.resolved)
            .order_by(ReviewItem.created_at.desc())
        )
        if limit:
            statement = statement.limit(limit)
        labeled = list(db.execute(statement))

    recognizer = CardRecognizer()
    results = []
    for review, expected in labeled:
        path = Path(review.image_path)
        if not path.is_file():
            continue
        raw = path.read_bytes()
        decoded = recognizer.decode(raw)
        held_out_hash = artwork_hash(CardRecognizer.rectify(decoded))
        result = await recognizer.recognize(
            raw,
            language=expected.language,
            ignored_visual_hashes={held_out_hash},
        )
        ids = [candidate.scryfall_id for candidate in result.candidates]
        top_id = ids[0] if ids else None
        auto_add = result.confidence >= 98.5
        results.append(
            {
                "review_id": review.id,
                "expected": {
                    "name": expected.card_name,
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
                "confidence": result.confidence,
                "top1_correct": top_id == expected.scryfall_id,
                "top5_correct": expected.scryfall_id in ids,
                "auto_add": auto_add,
                "false_auto_add": auto_add and top_id != expected.scryfall_id,
                "processing_ms": result.processing_ms,
            }
        )

    total = len(results)
    latencies = [item["processing_ms"] for item in results]
    summary = {
        "labeled_records": len(labeled),
        "evaluated_records": total,
        "missing_images": len(labeled) - total,
        "exact_printing_top1_accuracy": (
            sum(item["top1_correct"] for item in results) / total if total else None
        ),
        "exact_printing_top5_accuracy": (
            sum(item["top5_correct"] for item in results) / total if total else None
        ),
        "auto_add_rate": sum(item["auto_add"] for item in results) / total if total else None,
        "false_auto_add_rate": (
            sum(item["false_auto_add"] for item in results) / total if total else None
        ),
        "uncertainty_rate": (
            sum(not item["auto_add"] for item in results) / total if total else None
        ),
        "latency_ms_p50": statistics.median(latencies) if latencies else None,
        "latency_ms_p95": (
            sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None
        ),
        "failures": [item for item in results if not item["top1_correct"]],
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.limit if not args.limit or args.limit > 0 else None))
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
