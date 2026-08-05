"""Replay pending review captures without mutating the review queue."""

import asyncio
import argparse
import json
from pathlib import Path

from sqlalchemy import select

from ..database import SessionLocal
from ..models import ReviewItem, ReviewStatus
from ..services.recognition import CardRecognizer


async def audit(offset: int = 0, limit: int | None = None) -> None:
    with SessionLocal() as db:
        reviews = list(
            db.scalars(
                select(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.pending)
                .order_by(ReviewItem.created_at)
            )
        )[offset : offset + limit if limit else None]
    recognizer = CardRecognizer()
    for index, review in enumerate(reviews, start=1):
        path = Path(review.image_path)
        if not path.is_file():
            continue
        result = await recognizer.recognize(path.read_bytes(), language="en")
        top = result.candidates[0] if result.candidates else None
        print(
            json.dumps(
                {
                    "progress": f"{offset + index}/{offset + len(reviews)}",
                    "review_id": review.id,
                    "previous_confidence": review.confidence,
                    "predicted": (
                        f"{top.name} · {top.set_code.upper()} #{top.collector_number}"
                        if top
                        else None
                    ),
                    "confidence": result.confidence,
                    "processing_ms": result.processing_ms,
                    "ocr": result.ocr_text,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    asyncio.run(audit(arguments.offset, arguments.limit))
