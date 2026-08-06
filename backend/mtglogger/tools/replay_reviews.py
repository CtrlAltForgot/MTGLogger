"""Replay saved Review captures through recognition without changing inventory."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from ..database import SessionLocal
from ..models import ReviewItem, ReviewStatus
from ..services.recognition import CardRecognizer


async def replay(limit: int) -> None:
    with SessionLocal() as db:
        reviews = list(
            db.scalars(
                select(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.pending)
                .order_by(ReviewItem.created_at.desc())
                .limit(limit)
            )
        )
    recognizer = CardRecognizer()
    for review in reviews:
        path = Path(review.image_path)
        if not path.is_file():
            print(json.dumps({"review_id": review.id, "error": "image missing"}), flush=True)
            continue
        result = await recognizer.recognize(path.read_bytes())
        print(
            json.dumps(
                {
                    "review_id": review.id,
                    "old_confidence": review.confidence,
                    "confidence": result.confidence,
                    "ocr_text": result.ocr_text,
                    "processing_ms": result.processing_ms,
                    "auto_add_safe": result.auto_add_safe,
                    "timings_ms": result.timings_ms,
                    "neural_candidates": result.neural_candidates or [],
                    "candidates": [
                        {
                            "name": candidate.name,
                            "set_code": candidate.set_code,
                            "collector_number": candidate.collector_number,
                            "confidence": candidate.confidence,
                        }
                        for candidate in result.candidates
                    ],
                }
            ),
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(replay(max(1, args.limit)))


if __name__ == "__main__":
    main()
