"""Compare local-feature evidence across normalized card regions."""

import argparse
from pathlib import Path

import cv2
import numpy as np

from ..services.recognition import CardRecognizer

REGIONS = {
    "full": (0.0, 1.0, 0.0, 1.0),
    "title": (0.03, 0.16, 0.02, 0.98),
    "symbol": (0.50, 0.68, 0.65, 0.99),
    "footer": (0.80, 1.0, 0.01, 0.99),
    "bottom": (0.68, 1.0, 0.01, 0.99),
}


def descriptors(image: np.ndarray, region: tuple[float, ...]) -> np.ndarray | None:
    top, bottom, left, right = region
    height, width = image.shape[:2]
    crop = image[
        int(height * top) : int(height * bottom),
        int(width * left) : int(width * right),
    ]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (600, 300))
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.ORB_create(nfeatures=1000).detectAndCompute(gray, None)[1]


def score(left: np.ndarray | None, right: np.ndarray | None) -> tuple[int, float]:
    if left is None or right is None:
        return 0, 999.0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(left, right, k=2)
    good = [
        pair[0]
        for pair in pairs
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
    ]
    median = round(float(np.median([match.distance for match in good])), 1) if good else 999.0
    return len(good), median


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", type=Path)
    parser.add_argument("references", nargs="+", type=Path)
    args = parser.parse_args()
    scan = CardRecognizer.rectify(cv2.imread(str(args.scan)))
    for reference_path in args.references:
        reference = cv2.imread(str(reference_path))
        print(reference_path.name)
        for name, region in REGIONS.items():
            print(f"  {name}: {score(descriptors(scan, region), descriptors(reference, region))}")


if __name__ == "__main__":
    main()
