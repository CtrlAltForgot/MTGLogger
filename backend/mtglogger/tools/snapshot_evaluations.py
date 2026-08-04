"""Backfill durable benchmark samples from resolved Review records."""

import json
from pathlib import Path

from sqlalchemy import select

from ..database import SessionLocal
from ..models import InventoryItem, ReviewItem, ReviewStatus
from ..schemas import Candidate
from ..services.evaluation import preserve_confirmed_scan


def main() -> None:
    preserved = 0
    missing = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(ReviewItem, InventoryItem)
            .join(InventoryItem, ReviewItem.resolved_inventory_id == InventoryItem.id)
            .where(ReviewItem.status == ReviewStatus.resolved)
        )
        for review, item in rows:
            source = Path(review.image_path)
            if not source.is_file():
                missing += 1
                continue
            preserve_confirmed_scan(
                source,
                review.id,
                Candidate(
                    scryfall_id=item.scryfall_id,
                    oracle_id=item.oracle_id,
                    name=item.card_name,
                    set_code=item.set_code,
                    set_name=item.set_name,
                    collector_number=item.collector_number,
                    image_url=item.image_url,
                    market_price=item.market_price,
                    language=item.language,
                    confidence=100,
                ),
                item.language,
            )
            preserved += 1
    print(json.dumps({"preserved": preserved, "missing": missing}))


if __name__ == "__main__":
    main()
