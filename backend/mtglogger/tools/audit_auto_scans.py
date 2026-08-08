"""Replay archived automatic scans without changing inventory or training data."""

import argparse
import asyncio
import json
import statistics
from pathlib import Path

from ..config import get_settings
from ..services.recognition import CardRecognizer


async def audit(
    since: str | None,
    limit: int | None,
    scan_ids: set[str] | None = None,
) -> dict:
    manifest = get_settings().evaluation_dir / "auto_added" / "manifest.json"
    records = json.loads(manifest.read_text()) if manifest.is_file() else []
    if since:
        records = [record for record in records if record["scan_id"] >= since]
    if scan_ids:
        records = [record for record in records if record["scan_id"] in scan_ids]
    if limit:
        records = records[-limit:]

    recognizer = CardRecognizer()
    await asyncio.gather(
        asyncio.to_thread(recognizer._neural.warm),
        asyncio.to_thread(recognizer._neural.warm_model),
        asyncio.to_thread(recognizer._get_visual_catalog),
    )
    results = []
    for record in records:
        path = Path(record["image_path"])
        if not path.is_file():
            results.append({"scan_id": record["scan_id"], "missing_image": True})
            continue
        # Archives created before untouched-source preservation contain the
        # recognizer's already-rectified output. New records declare their
        # source format explicitly in the manifest.
        result = await recognizer.recognize(
            path.read_bytes(),
            language=record["language"],
            already_rectified=record.get("image_kind") != "camera_source",
        )
        top = result.candidates[0] if result.candidates else None
        auto_add = bool(top and result.confidence >= 98.5 and result.auto_add_safe)
        results.append(
            {
                "scan_id": record["scan_id"],
                "expected_prediction": record["predicted_scryfall_id"],
                "prediction": top.scryfall_id if top else None,
                "name": top.name if top else None,
                "set_code": top.set_code if top else None,
                "collector_number": top.collector_number if top else None,
                "prediction_stable": bool(
                    top and top.scryfall_id == record["predicted_scryfall_id"]
                ),
                "confidence": result.confidence,
                "auto_add": auto_add,
                "processing_ms": result.processing_ms,
            }
        )

    available = [item for item in results if not item.get("missing_image")]
    latencies = [item["processing_ms"] for item in available]
    recorded_latencies = [
        record["processing_ms"] for record in records if record.get("processing_ms") is not None
    ]
    return {
        "requested_records": len(records),
        "available_records": len(available),
        "stable_predictions": sum(item["prediction_stable"] for item in available),
        "automatic_adds": sum(item["auto_add"] for item in available),
        "review_rate": (
            1 - sum(item["auto_add"] for item in available) / len(available)
            if available
            else None
        ),
        "latency_ms_mean": round(statistics.mean(latencies), 1) if latencies else None,
        "latency_ms_median": round(statistics.median(latencies), 1) if latencies else None,
        "recorded_live_latency_count": len(recorded_latencies),
        "recorded_live_latency_ms_mean": (
            round(statistics.mean(recorded_latencies), 1) if recorded_latencies else None
        ),
        "recorded_live_latency_ms_median": (
            round(statistics.median(recorded_latencies), 1) if recorded_latencies else None
        ),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Minimum sortable scan id, e.g. 20260807-185200")
    parser.add_argument(
        "--scan-id",
        action="append",
        dest="scan_ids",
        help="Replay one exact scan id; repeat to select more than one",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(audit(args.since, args.limit, set(args.scan_ids or [])))
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
