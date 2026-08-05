"""Backfill retained corrections and canonical references into the neural index."""

import argparse
import asyncio

from ..database import Base, engine, migrate_schema
from ..services.neural_maintenance import (
    backfill_confirmed_embeddings,
    backfill_reference_embeddings,
    benchmark_confirmed_embeddings,
    run_neural_maintenance,
    train_metric_adapter,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--references", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--reindex-corrections", action="store_true")
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    if args.reindex_corrections:
        print(backfill_confirmed_embeddings(force=True))
    else:
        print(run_neural_maintenance())
    if args.references:
        print(
            asyncio.run(backfill_reference_embeddings(limit=args.limit, batch_size=args.batch_size))
        )
    if args.benchmark:
        print(benchmark_confirmed_embeddings())
    if args.train:
        print(train_metric_adapter())


if __name__ == "__main__":
    main()
