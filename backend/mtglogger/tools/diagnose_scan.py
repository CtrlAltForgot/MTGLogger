"""Explain the OCR and visual evidence produced by one saved physical scan."""

import argparse
import asyncio
import json
from pathlib import Path

import cv2

from ..database import SessionLocal
from ..models import ReviewItem
from ..services.recognition import CardRecognizer
from ..services.references import visual_fingerprints


def region_sharpness(image, top: float, bottom: float) -> float:
    height = image.shape[0]
    region = image[int(height * top) : int(height * bottom)]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)


async def diagnose(review_id: str) -> dict:
    with SessionLocal() as db:
        review = db.get(ReviewItem, review_id)
    if not review:
        raise ValueError(f"Review {review_id} does not exist")
    path = Path(review.image_path)
    if not path.is_file():
        raise ValueError(f"Captured image is missing: {path}")

    recognizer = CardRecognizer()
    raw = path.read_bytes()
    decoded = recognizer.decode(raw)
    corrected = recognizer.rectify(decoded)
    result = await recognizer.recognize(raw)
    fingerprints = visual_fingerprints(corrected)
    visual = recognizer._visual_matches(fingerprints, None)
    names = {candidate.name for candidate in result.candidates}
    descriptors = recognizer._descriptor_matches(
        corrected, names, ignored_example_review_ids={review_id}
    )
    title, number, set_code, year = recognizer.hints(result.ocr_text)
    candidate_ids = {candidate.scryfall_id for candidate in result.candidates}

    return {
        "review_id": review_id,
        "image_path": str(path),
        "camera_dimensions": [decoded.shape[1], decoded.shape[0]],
        "rectified_dimensions": [corrected.shape[1], corrected.shape[0]],
        "card_structure": result.card_structure,
        "sharpness": {
            "title": region_sharpness(corrected, 0.045, 0.22),
            "art": region_sharpness(corrected, 0.12, 0.58),
            "footer": region_sharpness(corrected, 0.80, 0.995),
        },
        "ocr": {
            "title": title,
            "collector_number": number,
            "set_code": set_code,
            "copyright_year": year,
            "text": result.ocr_text,
        },
        "result": {
            "confidence": result.confidence,
            "processing_ms": result.processing_ms,
            "candidates": [
                {
                    "name": candidate.name,
                    "set_code": candidate.set_code,
                    "collector_number": candidate.collector_number,
                    "confidence": candidate.confidence,
                }
                for candidate in result.candidates
            ],
        },
        "visual_fingerprint_matches": [
            {
                "name": reference.name,
                "set_code": reference.set_code,
                "collector_number": reference.collector_number,
                "score": score,
                "is_result_candidate": reference.scryfall_id in candidate_ids,
            }
            for reference, score in visual[:12]
        ],
        "artwork_descriptor_matches": [
            {
                "name": reference.name,
                "set_code": reference.set_code,
                "collector_number": reference.collector_number,
                "score": score,
            }
            for reference, score in descriptors[:12]
        ],
        "candidate_descriptor_catalog_complete": (
            recognizer._descriptor_catalog_complete(candidate_ids)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_id")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(diagnose(args.review_id))
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
