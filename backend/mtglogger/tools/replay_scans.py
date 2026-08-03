"""Replay saved camera frames without changing inventory or Review state."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from ..database import SessionLocal
from ..models import ReviewItem, ReviewStatus
from ..services.recognition import CardRecognizer


async def replay(limit: int, status: ReviewStatus) -> dict:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(ReviewItem)
                .where(ReviewItem.status == status)
                .order_by(ReviewItem.created_at.desc())
                .limit(limit)
            )
        )

    recognizer = CardRecognizer()
    scans = []
    for review in rows:
        path = Path(review.image_path)
        if not path.is_file():
            scans.append({"review_id": review.id, "missing_image": True})
            continue
        result = await recognizer.recognize(path.read_bytes())
        scans.append(
            {
                "review_id": review.id,
                "captured_at": review.created_at.isoformat(),
                "previous_confidence": review.confidence,
                "confidence": result.confidence,
                "processing_ms": result.processing_ms,
                "ocr_text": result.ocr_text,
                "candidates": [
                    {
                        "name": candidate.name,
                        "set_code": candidate.set_code,
                        "collector_number": candidate.collector_number,
                        "scryfall_id": candidate.scryfall_id,
                        "confidence": candidate.confidence,
                    }
                    for candidate in result.candidates
                ],
            }
        )
    return {"status": status.value, "requested": limit, "replayed": len(scans), "scans": scans}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--status", choices=[status.value for status in ReviewStatus], default="pending"
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(replay(max(1, args.limit), ReviewStatus(args.status)))
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
