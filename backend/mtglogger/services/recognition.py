import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from threading import Lock

import cv2
import httpx
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..database import SessionLocal
from ..models import CardReference, CardVisualExample, CardVisualFingerprint
from ..providers import ScryfallProvider
from ..schemas import Candidate
from .neural import NeuralRetriever
from .references import (
    ensure_reference_profiles,
    hash_distance,
    visual_descriptor_bundle,
    visual_fingerprints,
)

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class _VisualCatalog:
    loaded_at: float
    rows: tuple[tuple[CardReference, CardVisualFingerprint | None], ...]
    rows_by_set: dict[
        str, tuple[tuple[CardReference, CardVisualFingerprint | None], ...]
    ]
    references_by_name: dict[str, tuple[CardReference, ...]]
    names: tuple[str, ...]
    names_by_prefix: dict[str, tuple[str, ...]]
    examples: dict[str, tuple[str, ...]]
    global_hashes: np.ndarray
    global_row_indices: np.ndarray
    global_hash_is_example: np.ndarray


_visual_catalog: _VisualCatalog | None = None
_visual_catalog_lock = Lock()
# Reference writes explicitly invalidate this cache. Reloading nearly 100,000
# ORM rows every minute created a multi-second scan spike without making the
# immutable catalog any fresher, especially during a long Card Slinger batch.
_VISUAL_CATALOG_TTL_SECONDS = 60 * 60

_local_catalog_ready_cache: tuple[float, bool] | None = None
_local_catalog_ready_lock = Lock()
_LOCAL_CATALOG_READY_TTL_SECONDS = 60
_LOCAL_CATALOG_READY_MINIMUM = 90_000


@lru_cache(maxsize=4096)
def _load_descriptor_bundle(
    descriptor_path: str, modified_ns: int, size: int
) -> dict[str, np.ndarray] | None:
    """Load one immutable profile once per process and file revision."""
    del modified_ns, size  # These values deliberately participate in the cache key.
    try:
        loaded = np.load(descriptor_path, allow_pickle=False)
    except (OSError, ValueError, TypeError):
        return None
    if isinstance(loaded, np.lib.npyio.NpzFile):
        known = {key: loaded[key] for key in loaded.files}
        loaded.close()
        return known
    return {"art": loaded}


def _descriptor_bundle(descriptor_path: str) -> dict[str, np.ndarray] | None:
    try:
        stat = Path(descriptor_path).stat()
    except OSError:
        return None
    return _load_descriptor_bundle(descriptor_path, stat.st_mtime_ns, stat.st_size)


@dataclass
class Recognition:
    confidence: float
    ocr_text: str
    candidates: list[Candidate]
    source: np.ndarray
    corrected: np.ndarray
    processing_ms: int
    card_structure: bool
    timings_ms: dict[str, int] | None = None
    neural_candidates: list[dict[str, str | float]] | None = None
    auto_add_safe: bool = False


class CardRecognizer:
    """Hybrid recognizer. PaddleOCR is optional so the API remains lightweight."""

    @staticmethod
    def _local_catalog_is_ready() -> bool:
        """Return whether local recognition can replace remote broad search.

        Once the server has the normal full catalog, a failed fuzzy-title query
        should fall through to local artwork/descriptor retrieval instead of
        waiting several seconds for the same broad Scryfall searches. Keep a
        short TTL so a first-run database can cross the threshold while syncing.
        """
        global _local_catalog_ready_cache
        now = time.monotonic()
        cached = _local_catalog_ready_cache
        if cached and now - cached[0] < _LOCAL_CATALOG_READY_TTL_SECONDS:
            return cached[1]
        with _local_catalog_ready_lock:
            cached = _local_catalog_ready_cache
            if cached and now - cached[0] < _LOCAL_CATALOG_READY_TTL_SECONDS:
                return cached[1]
            try:
                with SessionLocal() as db:
                    total = int(
                        db.scalar(select(func.count()).select_from(CardReference)) or 0
                    )
            except SQLAlchemyError:
                total = 0
            ready = total >= _LOCAL_CATALOG_READY_MINIMUM
            _local_catalog_ready_cache = (now, ready)
            return ready

    def __init__(self) -> None:
        self.provider = ScryfallProvider()
        self._recognition_lock = asyncio.Lock()
        self._neural = NeuralRetriever()
        self._ocr = None
        self._footer_ocr = None
        try:
            from paddleocr import PaddleOCR
            from paddlex import create_model

            self._ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                # Paddle 3.3's oneDNN runner cannot execute the OCRv6 model's
                # ArrayAttribute on common slim Linux images.
                enable_mkldnn=False,
                text_detection_model_name="PP-OCRv4_mobile_det",
                text_recognition_model_name="PP-OCRv4_mobile_rec",
                # The default detector enlarges the shortest side to 736px.
                # Cards are already normalized to 600x840, so cap the longest
                # side instead of paying to upscale every scan.
                text_det_limit_side_len=840,
                text_det_limit_type="max",
            )
            # Rectification puts the two footer rows at stable coordinates, so
            # they do not need the comparatively expensive general text
            # detector. The recognition-only model reads each complete row in
            # tens of milliseconds and is also less likely to discard tiny
            # collector digits as background.
            self._footer_ocr = create_model("PP-OCRv4_mobile_rec")
        except (ImportError, RuntimeError):
            pass

    @property
    def ocr_available(self) -> bool:
        return self._ocr is not None

    @staticmethod
    def decode(raw: bytes) -> np.ndarray:
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("The uploaded file is not a readable image")
        return image

    @staticmethod
    def expand_quad(
        ordered: np.ndarray, image_shape: tuple[int, ...], scale: float = 1.06
    ) -> np.ndarray:
        expanded = ordered.mean(axis=0) + (ordered - ordered.mean(axis=0)) * scale
        expanded[:, 0] = np.clip(expanded[:, 0], 0, image_shape[1] - 1)
        expanded[:, 1] = np.clip(expanded[:, 1], 0, image_shape[0] - 1)
        return expanded.astype("float32")

    @staticmethod
    def rectify(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 140)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        minimum_area = image.shape[0] * image.shape[1] * 0.06
        inspected = sorted(contours, key=cv2.contourArea, reverse=True)[:20]
        candidates: list[tuple[float, np.ndarray]] = []
        for contour in inspected:
            perimeter = cv2.arcLength(contour, True)
            polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if len(polygon) != 4 or cv2.contourArea(polygon) < minimum_area:
                continue
            points = polygon.reshape(4, 2).astype("float32")
            warped = CardRecognizer.warp_card(image, points)
            if warped is not None and CardRecognizer.has_card_structure(warped):
                candidates.append((cv2.contourArea(polygon), warped))
        # Sleeves, worn borders, and virtual-camera sharpening often leave four
        # strong card edges as separate contours. Join nearby edge fragments and
        # retry the outer silhouette before falling back to a centered crop. The
        # slightly wider ratio allowance is intentional: OBS/virtual webcams can
        # stretch one axis, and the perspective warp restores the MTG card ratio.
        kernel_size = max(9, int(round(min(image.shape[:2]) * 0.03)))
        connected = cv2.morphologyEx(
            edges,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size)),
        )
        connected_contours, _ = cv2.findContours(
            connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for contour in sorted(connected_contours, key=cv2.contourArea, reverse=True)[:12]:
            rectangle = cv2.minAreaRect(contour)
            width, height = rectangle[1]
            if width * height < minimum_area or min(width, height) <= 0:
                continue
            ratio = min(width, height) / max(width, height)
            if not 0.42 <= ratio <= 1.0:
                continue
            warped = CardRecognizer.warp_card(image, cv2.boxPoints(rectangle))
            if warped is not None and CardRecognizer.has_card_structure(warped):
                candidates.append((width * height, warped))
        # Glare, sleeves, and worn borders can break one card edge into several
        # contours. A rotated bounding rectangle still localizes an off-center
        # card while its aspect-ratio check rejects ordinary widescreen regions.
        for contour in inspected:
            rectangle = cv2.minAreaRect(contour)
            width, height = rectangle[1]
            if width * height < minimum_area or min(width, height) <= 0:
                continue
            ratio = min(width, height) / max(width, height)
            if not 0.48 <= ratio <= 0.9:
                continue
            warped = CardRecognizer.warp_card(image, cv2.boxPoints(rectangle))
            if warped is not None and CardRecognizer.has_card_structure(warped):
                candidates.append((width * height, warped))
        if candidates:
            # Rules boxes and basic-land mana panels can look card-shaped. The
            # physical card/sleeve encloses them and is the largest structured
            # silhouette, so never accept the first internal rectangle found.
            return max(candidates, key=lambda item: item[0])[1]
        # If no plausible card boundary exists, retain the old centered fallback
        # for very low-contrast sleeves.
        height, width = image.shape[:2]
        crop_height = int(height * 0.98)
        crop_width = min(int(width * 0.52), int(crop_height * 63 / 88))
        x1, y1 = (width - crop_width) // 2, (height - crop_height) // 2
        guide = image[y1 : y1 + crop_height, x1 : x1 + crop_width]
        return cv2.resize(guide, (600, 840), interpolation=cv2.INTER_AREA)

    @staticmethod
    def warp_card(image: np.ndarray, points: np.ndarray) -> np.ndarray | None:
        points = points.astype("float32")
        widths = [
            np.linalg.norm(points[0] - points[1]),
            np.linalg.norm(points[2] - points[3]),
        ]
        heights = [
            np.linalg.norm(points[1] - points[2]),
            np.linalg.norm(points[3] - points[0]),
        ]
        ratio = min(max(widths), max(heights)) / max(max(widths), max(heights))
        if not 0.42 <= ratio <= 1.0:
            return None
        sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
        ordered = np.array(
            [
                points[np.argmin(sums)],
                points[np.argmin(diffs)],
                points[np.argmax(sums)],
                points[np.argmax(diffs)],
            ]
        )
        # Edge detection frequently locks onto the printed black border instead
        # of the physical card edge. Preserve the identifying footer margin.
        ordered = CardRecognizer.expand_quad(ordered, image.shape)
        target = np.array([[0, 0], [599, 0], [599, 839], [0, 839]], dtype="float32")
        return cv2.warpPerspective(image, cv2.getPerspectiveTransform(ordered, target), (600, 840))

    def extract_text(self, image: np.ndarray) -> str:
        if self._ocr is None:
            return ""
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        try:
            result = self._ocr.predict(rgb)
            texts: list[str] = []
            for page in result:
                data = page.json if hasattr(page, "json") else {}
                if callable(data):
                    data = data()
                texts.extend((data.get("res") or {}).get("rec_texts", []))
            return "\n".join(texts)
        except Exception:
            logger.exception("PaddleOCR inference failed")
            return ""

    def extract_focused_identification_text(self, image: np.ndarray) -> str:
        """Read the large title and enlarged printing footer in one OCR pass."""
        height, width = image.shape[:2]
        title = image[int(height * 0.045) : int(height * 0.22)]
        footer = image[int(height * 0.80) : int(height * 0.995)]
        # Printing identifiers live in the left portion of modern MTG footers.
        # Upscale that crop uniformly instead of stretching only its height; the
        # old distortion made collector digits no wider and harder to detect.
        footer_left = footer[:, : int(width * 0.72)]
        title = self.scale_to_width(title, 840)
        footer_left = self.scale_to_width(footer_left, 840)
        separator = np.zeros((18, 840, 3), dtype=np.uint8)
        focused_image = np.vstack((title, separator, footer_left))
        return self.extract_text(focused_image)

    def extract_identification_text(self, image: np.ndarray) -> str:
        """Read focused identity fields, with a standalone full-card fallback."""
        focused = self.extract_focused_identification_text(image)
        focused_title, focused_number, focused_set, focused_year = self.hints(focused)
        # Basic lands contain very little text. When glare hides their title,
        # the cleanest alphabetic line in this combined crop is commonly the
        # all-caps artist credit from the footer (for example ``JUNG PARK``).
        # That is not a credible title observation and must not suppress the
        # full-card pass which can recover ``Basic Land - Swamp`` from the type
        # line. Keep genuine all-caps titles on the fast path when another
        # printing field was not what caused the footer to dominate the crop.
        focused_title_is_footer_credit = bool(
            focused_title
            and re.fullmatch(r"[A-Z][A-Z'\-.]+(?:\s+[A-Z][A-Z'\-.]+){1,3}", focused_title)
            and (focused_number or focused_set or focused_year)
        )
        focused_title_letters = sum(character.isalpha() for character in (focused_title or ""))
        if (
            focused_title
            and not focused_title_is_footer_credit
            and focused_title_letters > 3
        ):
            return focused
        # The detector can skip a perfectly sharp title on dark/showcase art
        # while still finding the much smaller footer. Read the stable title
        # row directly with the lightweight recognition model before paying for
        # another full-card detection pass.
        fixed_title = self.extract_fixed_title_text(image)
        if self.hints(fixed_title)[0]:
            return "\n".join(part for part in (fixed_title, focused) if part.strip())
        # Showcase frames and older layouts occasionally place the title outside
        # the normal band. Preserve reliability with a full-card fallback only
        # when the fast title pass produced no usable text.
        full_text = self.extract_text(image)
        # The full-card pass can recover a title from a type line while losing
        # the tiny collector footer. Preserve both independent observations.
        return "\n".join(part for part in (focused, full_text) if part.strip())

    def extract_fixed_title_text(self, image: np.ndarray) -> str:
        """Recognize the rectified title row without running text detection."""
        if getattr(self, "_footer_ocr", None) is None:
            return ""
        height, width = image.shape[:2]
        # Exclude the expansion/frame icon at left and mana cost at right. The
        # remaining band is shared by conventional and showcase portrait cards.
        title = image[
            int(height * 0.01) : int(height * 0.09),
            int(width * 0.04) : int(width * 0.90),
        ]
        title = self.scale_to_width(title, 720)
        try:
            result = self._footer_ocr.predict([title])
            texts = []
            for item in result:
                data = item.json if hasattr(item, "json") else {}
                if callable(data):
                    data = data()
                value = (data.get("res") or {}).get("rec_text", "")
                if value.strip():
                    texts.append(value.strip())
            return "\n".join(texts)
        except Exception:
            logger.exception("Fixed title OCR inference failed")
            return ""

    def extract_fixed_identity_text(self, image: np.ndarray) -> str:
        """Read the title and printing footer in one recognition-only batch.

        Normal portrait cards put these fields in stable rows.  Paddle's text
        detector is by far the most expensive part of a live scan, so use the
        lightweight recognizer directly before falling back to layout-agnostic
        detection for showcase, token, helper, and other unusual frames.
        """
        if getattr(self, "_footer_ocr", None) is None:
            return ""
        height, width = image.shape[:2]
        rows = [
            image[
                int(height * 0.01) : int(height * 0.09),
                int(width * 0.04) : int(width * 0.90),
            ],
            image[int(height * 0.86) : int(height * 0.92), : int(width * 0.55)],
            image[int(height * 0.90) : int(height * 0.95), : int(width * 0.45)],
            image[int(height * 0.94) : int(height * 0.995), : int(width * 0.45)],
        ]
        rows[0] = self.scale_to_width(rows[0], 720)
        try:
            texts = []
            for result in self._footer_ocr.predict(rows):
                data = result.json if hasattr(result, "json") else {}
                if callable(data):
                    data = data()
                value = (data.get("res") or {}).get("rec_text", "")
                if value.strip():
                    texts.append(value.strip())
            return "\n".join(texts)
        except Exception:
            logger.exception("Fixed identity OCR inference failed")
            return ""

    def warm_fixed_ocr(self) -> None:
        """Pay Paddle's recognition-only initialization cost before traffic."""
        if getattr(self, "_footer_ocr", None) is None:
            return
        try:
            # Exercise the exact four-crop batch, including its mixed row
            # shapes. Paddle initializes different execution paths for a
            # singleton input and a batched input, so warming one title row did
            # not remove the first live card's batch preparation cost.
            self.extract_fixed_identity_text(
                np.zeros((840, 600, 3), dtype=np.uint8)
            )
        except Exception:
            logger.exception("Fixed OCR warmup failed")

    def extract_recovery_footer_text(
        self,
        image: np.ndarray,
        expected_numbers: set[str] | None = None,
        expected_sets: set[str] | None = None,
    ) -> str:
        """Read a geometry-preserving lower card band after identity is known."""
        fixed_lines = self.extract_fixed_footer_lines(image)
        if fixed_lines:
            matching_lines = []
            for line in fixed_lines:
                _title, number, set_code, _year = self.hints(line)
                number_matches = bool(
                    number
                    and expected_numbers
                    and any(
                        self.collector_score(number, expected) == 1.0
                        for expected in expected_numbers
                    )
                )
                set_matches = bool(
                    set_code
                    and expected_sets
                    and any(
                        self.exact_set_code_match(set_code, expected)
                        for expected in expected_sets
                    )
                )
                if number_matches or set_matches:
                    matching_lines.append(line)
            if matching_lines:
                return "\n".join(matching_lines)
            fixed_text = "\n".join(fixed_lines)
            if self.hints(fixed_text)[3]:
                # A complete copyright year is useful printing evidence even
                # when this pass misses collector/set glyphs. Downstream safety
                # still requires a unique family year plus corroborating art.
                return fixed_text
        # The general detector costs multiple seconds and, after the first two
        # OCR passes already failed, mostly repeats noisy evidence. Preserve the
        # initial observation and let exhaustive visual/neural ranking decide;
        # uncertain exact printings remain review items instead of stalling the
        # live scanner for a low-yield third OCR pass.
        return ""

    def extract_fixed_footer_text(self, image: np.ndarray) -> str:
        """Read normalized collector and set rows without text detection."""
        return "\n".join(self.extract_fixed_footer_lines(image))

    def extract_raw_footer_band_text(self, image: np.ndarray) -> str:
        """Detect text only in the untouched physical footer band."""
        height = image.shape[0]
        footer = image[int(height * 0.78) : int(height * 0.995)]
        # The detector itself is capped at 840px. Enlarging beyond that is
        # immediately downscaled again and costs ~300ms on difficult lands
        # without improving collector/set transcription.
        return self.extract_text(self.scale_to_width(footer, 840))

    def extract_set_symbol_text(self, image: np.ndarray) -> str:
        """Read an alphanumeric expansion/core-set logo from the type-line corner."""
        height, width = image.shape[:2]
        symbol = image[
            int(height * 0.53) : int(height * 0.64),
            int(width * 0.72) : int(width * 0.99),
        ]
        symbol = cv2.resize(symbol, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        return self.extract_text(symbol)

    def extract_fixed_footer_lines(self, image: np.ndarray) -> list[str]:
        """Read modern-left and legacy-right footer rows independently."""
        if getattr(self, "_footer_ocr", None) is None:
            return []
        height, width = image.shape[:2]
        # Modern frames put collector/set fields at lower left. Older frames
        # often put the collector pair after the copyright at lower right.
        # Read the layouts independently so the recognizer is never forced to
        # transcribe an entire tiny copyright line just to recover three digits.
        rows = (
            # Current frames place the rarity/land marker and zero-padded
            # collector number above the language/artist row.
            image[int(height * 0.86) : int(height * 0.92), : int(width * 0.55)],
            image[int(height * 0.90) : int(height * 0.95), : int(width * 0.45)],
            image[int(height * 0.94) : int(height * 0.995), : int(width * 0.45)],
            image[int(height * 0.93) : int(height * 0.98), int(width * 0.55) :],
            image[int(height * 0.94) : int(height * 0.995), int(width * 0.55) :],
        )
        try:
            texts = []
            for result in self._footer_ocr.predict(list(rows)):
                data = result.json if hasattr(result, "json") else {}
                if callable(data):
                    data = data()
                value = (data.get("res") or {}).get("rec_text", "")
                if value.strip():
                    texts.append(value)
            return texts
        except Exception:
            logger.exception("Footer OCR inference failed")
            return []

    @staticmethod
    def low_light_score(image: np.ndarray) -> float:
        """Return a robust brightness estimate that ignores small foil glare.

        A mean is badly skewed by a phone flash reflected from one corner of a
        sleeve.  The 65th percentile describes the normally exposed card area
        while still distinguishing a genuinely dark capture from black borders.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(np.percentile(gray, 65))

    @classmethod
    def normalize_low_light(cls, image: np.ndarray) -> tuple[np.ndarray, bool]:
        """Brighten a dark card once for every recognition signal.

        OCR, ORB descriptors, perceptual hashes, and the neural embedding must
        all see the same normalized image.  Applying a bounded luminance-only
        correction avoids an additional inference pass and preserves hue/set
        symbol information. Clean captures are returned byte-for-byte unchanged.
        """
        brightness = cls.low_light_score(image)
        if brightness >= 105:
            return image, False

        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        luminance, a_channel, b_channel = cv2.split(lab)
        luminance = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(luminance)
        # Bring the middle tones toward a readable level without blowing out a
        # reflective sleeve. Gamma is deliberately capped for noisy webcams.
        normalized = max(1.0 / 255.0, brightness / 255.0)
        gamma = float(np.clip(np.log(118 / 255.0) / np.log(normalized), 0.58, 0.90))
        lookup = np.array(
            [min(255, round(((value / 255.0) ** gamma) * 255)) for value in range(256)],
            dtype=np.uint8,
        )
        luminance = cv2.LUT(luminance, lookup)
        enhanced = cv2.cvtColor(
            cv2.merge((luminance, a_channel, b_channel)), cv2.COLOR_LAB2BGR
        )
        return enhanced, True

    @staticmethod
    def scale_to_width(image: np.ndarray, target_width: int) -> np.ndarray:
        scale = target_width / max(1, image.shape[1])
        target_height = max(1, round(image.shape[0] * scale))
        interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        return cv2.resize(image, (target_width, target_height), interpolation=interpolation)

    @staticmethod
    def enhance_footer(footer: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(footer, cv2.COLOR_BGR2GRAY)
        contrasted = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
        blurred = cv2.GaussianBlur(contrasted, (0, 0), 1.2)
        sharpened = cv2.addWeighted(contrasted, 1.8, blurred, -0.8, 0)
        return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

    @staticmethod
    def has_card_structure(image: np.ndarray) -> bool:
        """Detect a portrait card frame, not merely horizontal table boundaries."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 140)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=45,
            minLineLength=int(image.shape[1] * 0.38),
            maxLineGap=18,
        )
        if lines is None:
            return False
        horizontal = 0
        vertical = 0
        for [[x1, y1, x2, y2]] in lines:
            width = abs(x2 - x1)
            height = abs(y2 - y1)
            if abs(y2 - y1) <= max(4, width * 0.08):
                horizontal += 1
            if abs(x2 - x1) <= max(4, height * 0.08) and height >= image.shape[0] * 0.28:
                vertical += 1
        return horizontal >= 3 and vertical >= 2

    @staticmethod
    def hints(text: str) -> tuple[str | None, str | None, str | None, int | None]:
        # OCR frequently emits full-width punctuation from tiny collector
        # footers (for example ``267／272``). Compatibility normalization turns
        # those glyphs into their ASCII equivalents before the deliberately
        # strict footer patterns run.
        text = unicodedata.normalize("NFKC", text)
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 1]
        # Copyright footers commonly contain a range (for example
        # "© 1993-2011 Wizards").  The final/latest year identifies the
        # physical printing; taking the first year incorrectly labels every
        # such card as a 1993 printing.
        years = [int(value) for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)]
        copyright_year = max(years) if years else None
        if copyright_year is None:
            # Decorative copyright type is particularly hostile to OCR: the
            # common 1996 footer is often read as ``Ol9g6`` (©/1/g confusion).
            # Normalize only footer-like lines containing an artist/copyright
            # marker so ordinary rules text cannot manufacture a release year.
            footer = "\n".join(lines[-8:])
            for raw_line in footer.splitlines():
                if not re.search(
                    r"(?:illus|wizard|w[ui]ards|coa[st]|©|rights?)", raw_line, re.I
                ):
                    continue
                compact = re.sub(r"[^A-Za-z0-9]", "", raw_line)
                normalized = compact.translate(
                    str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "g": "9"})
                )
                fuzzy_years = [
                    int(value)
                    for value in re.findall(r"(?<!\d)0?((?:19|20)\d{2})(?!\d)", normalized)
                ]
                if fuzzy_years:
                    copyright_year = max(fuzzy_years)
                    break
        if copyright_year is None:
            # Tiny copyright text often loses "20" while retaining a marker
            # and the final two digits (for example ©2013 -> "co13").
            footer = "\n".join(lines[-5:])
            short_year = re.search(
                r"(?:©|&|co|c|o)[^0-9\n]{0,2}([0-2]\d)(?!\d)"
                r"(?=[^0-9\n]*(?:w(?:izards?)?|rds|coa?st|cat))",
                footer,
                re.I,
            )
            if short_year:
                inferred = 2000 + int(short_year.group(1))
                if 1993 <= inferred <= 2030:
                    copyright_year = inferred
        if copyright_year is None:
            # Footer OCR can concatenate the collector denominator and the
            # copyright year (for example ``094092012W``).  The normal digit
            # boundaries intentionally reject that shape, so recover it only
            # from the final footer lines and only when the year is immediately
            # followed by the beginning of the Wizards credit.  This avoids
            # treating four digits in rules text or a collector number as a
            # release year.
            footer = "\n".join(lines[-3:])
            joined_years = [
                int(value)
                for value in re.findall(r"((?:19|20)\d{2})(?=\s*W)", footer, re.I)
            ]
            if joined_years:
                copyright_year = max(joined_years)
        set_code = None
        languages = "EN|ES|FR|DE|IT|PT|JA|KO|RU|ZHS|ZHT|HE|LA|GRC|AR|SA|PHY"
        # A standalone modern core-set token is substantially less ambiguous
        # than the permissive joined footer parser below. Artist fragments such
        # as ``LONAS DE RO`` can otherwise become a fabricated ``LONAS · DE``
        # pair even when OCR also read ``M21`` cleanly. Prefer only the closed
        # set of real M10-M21 codes here; arbitrary tokens remain conservative.
        core_set_match = re.search(
            r"(?<![A-Z0-9])M(?:1[0-9]|2[01])(?![A-Z0-9])",
            "\n".join(lines),
            re.I,
        )
        if core_set_match:
            set_code = core_set_match.group(0).lower()
        # Core-set footers frequently lose their separator to a letter-like
        # glyph (``M21 · EN`` -> ``M21AEN`` or ``M21^EN``). Prefer the real
        # core-set token before the general joined parser can manufacture the
        # nonexistent set code ``M21A``.
        core_language_match = re.search(
            rf"(?<![A-Z0-9])(M(?:1[0-9]|2[01]))(?:A|[^A-Z0-9])*(?:{languages})(?=\s|$|[A-Z])",
            "\n".join(lines),
            re.I,
        )
        if not set_code and core_language_match:
            set_code = core_language_match.group(1).lower()
        # Prefer a footer with a visible separator. A permissive joined-footer
        # match is useful for tiny text, but can hallucinate a language inside
        # an artist fragment (for example NGPARK -> NGP + AR) and must not
        # override a clean ``ORI-EN`` elsewhere in the OCR passes.
        for line in reversed(lines):
            if set_code:
                break
            match = re.match(
                rf"^\s*([A-Z2][A-Z0-9]{{1,5}}?)[\s·•.+\-:]+"
                rf"(?:A[\s·•.+\-:]*)?(?:{languages})(?=\s|$|[A-Z])",
                line,
            )
            if match:
                set_code = match.group(1).lower()
                break
        if not set_code:
            for line in reversed(lines):
                match = re.match(
                    rf"^\s*([A-Z2][A-Z0-9]{{1,5}}?)(?:{languages})(?=[^A-Z0-9]|$|[A-Z])",
                    line,
                )
                if match:
                    set_code = match.group(1).lower()
                    break
        if not set_code:
            # Low-resolution footer OCR often separates the set code from the
            # adjacent language ("M15 · EN" -> standalone "MIS"). Only accept
            # short uppercase/alphanumeric footer tokens, never ordinary title
            # or rules text.
            language_tokens = set(languages.split("|"))
            for line in reversed(lines):
                token = line.strip()
                # Standalone set codes can appear near the title/type text in
                # Paddle's reading order even though they are physically in the
                # footer. Requiring an all-caps short token keeps rules text out;
                # a digit or the usual three-character code shape excludes mana
                # and rarity glyphs.
                if (
                    re.fullmatch(r"[A-Z2][A-Z0-9]{1,4}", token)
                    and token not in language_tokens
                    # Current frames place the one-letter rarity before a
                    # zero-padded collector number (for example ``C0049``).
                    # That is printing evidence, not a five-character set.
                    and not (
                        re.fullmatch(r"[LCMRU][0-9O]{3,4}", token)
                        and any(character.isdigit() for character in token[1:])
                    )
                    and any(character.isalpha() for character in token)
                    and (any(character.isdigit() for character in token) or len(token) == 3)
                ):
                    set_code = token.lower()
                    break
        # Physical token footers use the parent expansion code (for example
        # M21), while Scryfall stores those printings in the corresponding
        # token set (TM21). The printed type line is independent evidence that
        # this is a token, so translate the set only when OCR actually read a
        # token layout. Without this, a perfectly legible Goblin Wizard token
        # is looked up among ordinary M21 cards and can never reach its exact
        # TM21 printing.
        if set_code and re.search(
            r"\btoken\s+(?:artifact|creature|enchantment|land)\b",
            "\n".join(lines),
            re.I,
        ):
            if not set_code.casefold().startswith("t"):
                set_code = f"t{set_code}"
        number = None
        # Collector numbers often share the copyright line. Prefer an explicit
        # numerator/denominator pair before filtering copyright years.
        # Paddle sometimes returns the slash and denominator as a separate text
        # line (``011/`` followed by ``269``). Search the joined footer first so
        # that physical layout still becomes one collector pair.
        number_sources = ["\n".join(lines[-8:]), *reversed(lines)]
        for line in number_sources:
            match = re.search(
                # Tiny legacy slashes are commonly transcribed as ``L``
                # (``172/175`` -> ``172L75``). Requiring digits on both sides
                # keeps this repair out of ordinary words and artist credits.
                r"(?<!\d)(\d{1,4}[a-z]?)\s*[/|\\Ll.]+\s*(\d{1,4})(?!\d)", line, re.I
            )
            # Power/toughness (for example 5/4) is read far more reliably
            # than a tiny footer. A real collector denominator represents a
            # set/card-sheet total and is not a single digit.
            if match and int(match.group(2)) >= 10:
                number = match.group(1)
                # The tiny footer's final ``1`` is frequently read as a
                # lowercase L or uppercase I (``251/274`` -> ``25l/274``).
                # Normalize only that terminal glyph in an otherwise numeric
                # explicit numerator/denominator pair. Legitimate collector
                # suffixes such as ``123a`` remain untouched.
                if re.fullmatch(r"\d{2,3}[lI]", number):
                    number = f"{number[:-1]}1"
                break
        # Low-resolution OCR commonly drops the slash ("062/249" -> "02 249").
        # On a copyright line, the last two non-year numbers are still a strong
        # collector/total pair; retain the leading zero as useful OCR evidence.
        if not number:
            # Paddle can also remove every separator (``246/249`` ->
            # ``246249``). Recover a plausible numerator plus three-digit set
            # total only from the footer. Copyright ranges are eight digits and
            # deliberately do not fit this pattern.
            footer = "\n".join(lines[-6:])
            for joined in reversed(re.findall(r"(?<!\d)(\d{5,7})(?!\d)", footer)):
                numerator, denominator = joined[:-3], joined[-3:]
                # Showcase and bonus-sheet collector numbers may legitimately
                # exceed the printed denominator, and tiny denominator digits
                # are often wrong (``236/249`` -> ``236219``). The numerator is
                # still useful when title/year/artist independently agree.
                # Bonus sheets can exceed the printed set total, but a
                # numerator dozens of times larger than the denominator is
                # concatenated copyright/artist noise, not a collector pair
                # (for example ``4093122`` -> bogus 4093/122).
                if (
                    1 <= int(numerator) <= 9999
                    and int(denominator) >= 100
                    and int(numerator) <= int(denominator) * 5
                ):
                    number = numerator
                    break
        if not number:
            # Some current frames reverse the older joined layout above and
            # print rarity before the zero-padded number (``C0049``). Keep the
            # zero padding here; collector comparison normalizes it safely.
            footer = "\n".join(lines[-6:])
            rarity_prefixed = re.search(
                # Current-frame OCR frequently concatenates the artist credit
                # immediately after this rigid rarity+zero-padded field
                # (``L0282SLAWEK``). A following letter is therefore allowed;
                # the leading rarity and 3-4 digit shape keep the match narrow.
                r"(?<![A-Z0-9])[LCMRU]([0-9O]{3,4})(?![0-9O])", footer
            )
            if rarity_prefixed:
                observed = rarity_prefixed.group(1)
                # Round O back to zero only inside this rigid current-frame
                # field. Requiring a real digit keeps words and set codes out.
                if any(character.isdigit() for character in observed):
                    number = observed.replace("O", "0")
        if not number:
            # Modern footers print a zero-padded collector number immediately
            # beside a one-letter rarity. OCR commonly joins them ("005 R" ->
            # "005R"). The rarity is not a collector suffix.
            footer = "\n".join(lines[-6:])
            rarity_joined = re.search(r"(?<!\d)(\d{3})[CMRU](?=\s|$)", footer)
            if rarity_joined:
                number = rarity_joined.group(1)
        if not number:
            for line in reversed(lines[-5:]):
                if "©" not in line and "Wizards" not in line:
                    continue
                values = re.findall(r"(?<!\d)(\d{1,4}[a-z]?)(?!\d)", line, re.I)
                values = [
                    value
                    for value in values
                    if not 1900 <= int(re.match(r"\d+", value).group()) <= 2100
                ]
                if len(values) >= 2:
                    number = values[-2]
                    break
        # A bare digit is weak evidence and is only trustworthy in the footer.
        # Looking across the whole card lets mana costs such as "3B" become a
        # bogus collector number when OCR separates the symbols.
        for line in reversed(lines[-5:]):
            if number:
                break
            matches = re.findall(r"(?:^|\s)(\d{1,4}[a-z]?)(?:/\d{1,4})?(?=\s|$)", line, re.I)
            for value in matches:
                numeric = int(re.match(r"\d+", value).group())
                # Copyright years commonly appear below the collector number.
                # Single-digit bare values are much more likely to be a mana
                # cost or power/toughness than a collector number. Exact
                # single-digit collectors still work through the slash form.
                if numeric < 10 or 1900 <= numeric <= 2100 or "©" in line or "Wizards" in line:
                    continue
                number = value
                break
            if number:
                break
        # The title is physically above the type line. Under foil glare Paddle
        # may lose the title entirely while reading "Sorcery" or "Creature"
        # perfectly; treating that type word as a fuzzy card name can anchor the
        # whole pipeline to an unrelated card (for example Sorcery -> Sorry).
        # Stop title search at the first recognizable type line so rules text
        # below it cannot become a fabricated identity either.
        type_prefixes = (
            "artifact",
            "battle",
            "basic land",
            "conspiracy",
            "creature",
            "enchantment",
            "instant",
            "kindred",
            "land",
            "phenomenon",
            "plane",
            "planeswalker",
            "scheme",
            "sorcery",
            "token",
            "tribal",
            "vanguard",
        )
        type_line_index = next(
            (
                index
                for index, line in enumerate(lines[:6])
                if line.casefold().startswith(type_prefixes)
            ),
            None,
        )
        title_lines = lines[:type_line_index] if type_line_index is not None else lines[:5]

        def plausible_title(line: str) -> bool:
            # A footer-only pass can be exceptionally clear while the title is
            # lost to glare.  Do not reinterpret "ORI · EN DANFRAZIER" as a
            # card name; keep its set/collector evidence available for fusion
            # with the full-frame title pass below.
            if re.match(
                rf"^\s*[A-Z][A-Z0-9]{{1,5}}?[\s·•.+\-:]*(?:{languages})(?=[^A-Z0-9]|$|[A-Z])",
                line,
            ):
                return False
            # A clean standalone set code (for example ``ORI``) is common in
            # the focused footer pass. It is evidence for the printing, never
            # a card title; accepting it suppresses the full-card title pass.
            if re.fullmatch(r"[A-Z2][A-Z0-9]{1,5}", line):
                return False
            return (
                not re.search(r"\d{3,}", line)
                and len(line) <= 60
                and sum(character.isalpha() for character in line) >= 3
            )

        basic_names = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
        title = next(
            (line.title() for line in lines if line.casefold() in basic_names),
            None,
        ) or next((line for line in title_lines if plausible_title(line)), None)
        if re.search(
            r"use\s+this\s+card\s+to\s+represent\s+a\s+double[- ]faced\s*card",
            " ".join(lines),
            re.I,
        ):
            title = "Double-Faced Substitute Card"
            if not number:
                insert_number = re.search(
                    r"(?<!\d)(0{2,3}\d)\s*[A-Z](?=\s|$)", text, re.I
                )
                if insert_number:
                    number = f"{insert_number.group(1)}d"
        # A basic land's type line contains its actual card name after the dash.
        # This is the one safe case where a type line can recover identity when
        # glare or a dark frame hides the title. Keeping the allow-list narrow
        # avoids turning ordinary subtype text into a fabricated card name.
        if type_line_index is not None:
            type_line = lines[type_line_index]
            basic_land = re.match(
                r"^basic\s+land\s*[-—–:]\s*(plains|island|swamp|mountain|forest|wastes)\b",
                type_line,
                re.I,
            )
            if basic_land:
                # This closed vocabulary is more authoritative than any other
                # alphabetic line in the focused footer. In particular, merged
                # recovery text may place an artist credit before the full-card
                # type line; keeping that earlier guess made ``JUNG PARK`` win
                # over the explicitly printed ``Basic Land - Swamp``.
                title = basic_land.group(1).title()
        return title, number, set_code, copyright_year

    @staticmethod
    def collector_score(ocr_number: str | None, printed_number: str) -> float:
        if not ocr_number:
            return 0.45
        left = ocr_number.lower().lstrip("0") or "0"
        right = printed_number.lower().lstrip("0") or "0"
        if left == right:
            return 1.0
        direct = SequenceMatcher(None, left, right).ratio()
        # Common tiny-footer confusions. This only affects ranking; an inferred
        # number is deliberately capped below exact-match confidence.
        variants = {ocr_number.lower(), ocr_number.lower().translate(str.maketrans("08", "68"))}
        inferred = max(
            SequenceMatcher(None, value.lstrip("0") or "0", right).ratio() for value in variants
        )
        return min(0.85, max(direct, inferred))

    @classmethod
    def observed_footer_contradicts_printing(
        cls,
        number: str | None,
        printed_set_code: str | None,
        candidate_number: str,
        candidate_set_code: str,
    ) -> bool:
        """Return whether readable footer evidence rules out a candidate.

        Promos and reprints frequently reuse artwork. A visual winner cannot be
        the exact physical printing when its catalog set or collector number
        strongly disagrees with a field that was read from the card itself.
        """
        number_mismatch = bool(
            number and cls.collector_score(number, candidate_number) < 0.78
        )
        set_mismatch = bool(
            printed_set_code
            and cls.set_code_score(printed_set_code, candidate_set_code) < 0.78
        )
        return number_mismatch or set_mismatch

    @staticmethod
    def observed_footer_is_reliable(
        number: str | None,
        printed_set_code: str | None,
        observed_text: str,
    ) -> bool:
        """Require evidence that a parsed number actually came from the footer.

        Short standalone numbers are common in mana costs and rules text. Keep
        the final contradiction veto for an observed set code, a printed
        numerator/denominator, or a collector number long enough to be unlikely
        incidental OCR noise.
        """
        if printed_set_code:
            return True
        if not number:
            return False
        if re.search(rf"(?<!\d)0*{re.escape(number)}\s*/\s*\d{{2,4}}(?!\d)", observed_text):
            return True
        return len(re.sub(r"\D", "", number)) >= 3

    @classmethod
    def repair_family_set_code(
        cls,
        observed_set: str | None,
        observed_number: str | None,
        cards: list[dict],
    ) -> str | None:
        """Recover a real set suffix from a slightly damaged footer token."""
        if not observed_set or not observed_number:
            return None
        observed = observed_set.casefold()
        matches = {
            card["set"].casefold()
            for card in cards
            if 0 <= len(observed) - len(card["set"]) <= 2
            and observed.endswith(card["set"].casefold())
            and cls.collector_score(observed_number, card["collector_number"]) == 1.0
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def neural_source_can_recover_identity(source_kind: str | None) -> bool:
        """Allow reference artwork, but not learned corrections, to name a card.

        Correction vectors are valuable once OCR has constrained the card
        family. Before that point a visually similar prior scan can inject an
        unrelated identity and prevent rules/title OCR recovery entirely.
        """
        return source_kind in {"canonical", "alternate"}

    @classmethod
    def neural_name_consensus(cls, matches: list, minimum_similarity: float = 0.44) -> str | None:
        """Recover only a card name when leading artwork references agree."""
        if not matches:
            return None
        leader = matches[0]
        if (
            not cls.neural_source_can_recover_identity(leader.source_kind)
            or leader.similarity < minimum_similarity
        ):
            return None
        leader_name = cls.normalized_name(leader.reference.name)
        corroborating = [
            match
            for match in matches[1:10]
            if cls.neural_source_can_recover_identity(match.source_kind)
            and match.similarity >= minimum_similarity
            and cls.normalized_name(match.reference.name) == leader_name
        ]
        return leader.reference.name if corroborating else None

    @classmethod
    def neural_title_fragment_identity(cls, title: str | None, matches: list) -> str | None:
        """Fuse a damaged title fragment with one canonical artwork identity."""
        if not title or len(cls.normalized_name(title)) < 6:
            return None
        qualifying = [
            match
            for match in matches[:10]
            if cls.neural_source_can_recover_identity(match.source_kind)
            and match.similarity >= 0.45
            and cls.card_name_similarity(title, match.reference.name) >= 0.62
        ]
        names = {cls.normalized_name(match.reference.name) for match in qualifying}
        return qualifying[0].reference.name if len(names) == 1 else None

    @staticmethod
    def partial_family_set_code(observed_text: str, cards: list[dict]) -> str | None:
        """Resolve a clipped two-character set logo only inside one card family."""
        tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]+", observed_text)
            if 2 <= len(token) <= 4 and any(character.isdigit() for character in token)
        }
        matches = {
            card["set"].casefold()
            for card in cards
            for token in tokens
            if card["set"].casefold().startswith(token)
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @classmethod
    def family_set_code_from_footer_text(
        cls,
        observed_text: str,
        cards: list[dict],
        observed_number: str | None = None,
    ) -> str | None:
        """Recover a family set code immediately preceding the printed EN marker."""
        observed_codes = {
            match[-3:].casefold()
            for match in re.findall(
                r"[A-Za-z0-9]{3,5}(?=[\-·•.]?EN(?:[A-Z]|\b))",
                observed_text,
                re.I,
            )
        }
        if not observed_codes:
            return None
        number_scoped_cards = [
            card
            for card in cards
            if observed_number
            and (
                cls.collector_score(observed_number, card["collector_number"]) >= 0.78
                or card["collector_number"].casefold().endswith(
                    f"-{observed_number.casefold()}"
                )
            )
        ]
        scores: dict[str, float] = {}
        for card in number_scoped_cards or cards:
            code = card["set"].casefold()
            if code == "plst" or len(code) != 3:
                continue
            scores[code] = max(
                [scores.get(code, 0)]
                + [SequenceMatcher(None, observed, code).ratio() for observed in observed_codes]
            )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < 2 / 3:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.2:
            return None
        return ranked[0][0]

    @staticmethod
    def exact_family_set_code_from_footer_text(
        observed_text: str, cards: list[dict]
    ) -> str | None:
        """Return one literally printed family code before a language marker.

        This deliberately performs no fuzzy repair. Untouched camera footers
        can prefix a clean token with garbage (``WAORI-EN``), while a damaged
        token such as ``WAORT-EN`` must remain unusable rather than being
        guessed into an exact-printing authorization.
        """
        compact = unicodedata.normalize("NFKC", observed_text).upper()
        languages = "EN|ES|FR|DE|IT|PT|JA|KO|RU|ZHS|ZHT|HE|LA|GRC|AR|SA|PHY"
        matches = {
            str(card.get("set") or "").casefold()
            for card in cards
            if str(card.get("set") or "").casefold() != "plst"
            and re.search(
                rf"{re.escape(str(card.get('set') or '').upper())}"
                rf"[\s·•.+\-:]+(?:{languages})(?=\s|$|[A-Z])",
                compact,
            )
        }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def normalized_name(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        return "".join(
            character
            for character in decomposed
            if character.isalnum() and not unicodedata.combining(character)
        )

    @classmethod
    def card_name_similarity(cls, observed: str | None, catalog_name: str) -> float:
        """Compare OCR against either face of a multi-faced card."""
        source = cls.normalized_name(observed or "")
        if not source:
            return 0.0
        names = [catalog_name, *catalog_name.split(" // ")]
        return max(
            SequenceMatcher(None, source, cls.normalized_name(name)).ratio() for name in names
        )

    @classmethod
    def artist_text_score(cls, text: str, artist: str | None) -> float:
        """Find a printed artist credit without trusting a nearby OCR digit.

        Artist names are exact-art evidence: they can disprove a collector-number
        OCR error, but reprints can share an artist, so this signal deliberately
        remains below the automatic-add threshold on its own.
        """
        source = cls.normalized_name(text)
        target = cls.normalized_name(artist or "")
        if not source or not target:
            return 0.0
        if target in source:
            return 1.0
        tokens = [
            cls.normalized_name(token)
            for token in re.findall(r"[^\W\d_]+", artist or "", re.UNICODE)
            if len(cls.normalized_name(token)) >= 3
        ]
        if len(tokens) >= 2 and all(token in source for token in tokens):
            return 0.95
        if tokens and len(tokens[-1]) >= 5 and tokens[-1] in source:
            return 0.78
        # Footer OCR commonly inserts, drops, or confuses one character in an
        # otherwise complete artist credit (for example ``NKEVWALRER`` for
        # ``Kev Walker``). Treat a close full-name window as strong evidence;
        # callers still require independent printing evidence before this can
        # authorize an automatic add.
        if cls.fuzzy_contains(text, artist or "", threshold=0.80):
            return 0.9
        observed_tokens = [
            cls.normalized_name(token)
            for token in re.findall(r"[^\W\d_]+", text or "", re.UNICODE)
            if len(cls.normalized_name(token)) >= 3
        ]
        if len(tokens) >= 2 and observed_tokens and all(
            max(
                SequenceMatcher(None, expected, observed).ratio()
                for observed in observed_tokens
            )
            >= 0.75
            for expected in tokens
        ):
            return 0.9
        return 0.0

    @staticmethod
    def has_exact_footer_artist_proof(
        *,
        is_basic_land: bool,
        number_score: float,
        artist_score: float,
        strong_artist_count: int,
    ) -> bool:
        """Require two independent footer signals before trusting damaged set OCR."""
        return bool(
            not is_basic_land
            and number_score == 1.0
            and artist_score >= 0.9
            and strong_artist_count == 1
        )

    @classmethod
    def fuzzy_contains(cls, text: str, phrase: str, threshold: float = 0.78) -> bool:
        source, target = cls.normalized_name(text), cls.normalized_name(phrase)
        if target in source:
            return True
        if len(target) < 5 or len(source) < len(target) - 2:
            return False
        for size in range(max(3, len(target) - 2), len(target) + 3):
            for start in range(0, len(source) - size + 1):
                if SequenceMatcher(None, source[start : start + size], target).ratio() >= threshold:
                    return True
        return False

    @classmethod
    def oracle_terms(cls, text: str) -> list[str]:
        # Ordered from distinctive phrases to broad vocabulary. Three agreeing
        # terms keep Scryfall results small enough for local OCR similarity ranking.
        # Printed stat modifiers are especially valuable when glare obscures a
        # foil card's title: unlike words such as "creature", values such as
        # -4/-4 sharply constrain the oracle-text search.
        terms: list[str] = []
        for match in re.findall(r"[+-]\s*\d+\s*/\s*[+-]\s*\d+", text):
            normalized = re.sub(r"\s+", "", match)
            if normalized not in terms:
                terms.append(normalized)
        vocabulary = [
            ("gain 2 life", 0.82),
            ("loses 2 life", 0.82),
            ("four or more", 0.82),
            ("deathtouch", 0.78),
            ("regenerate", 0.78),
            ("sacrifice", 0.78),
            ("graveyard", 0.78),
            ("flying", 0.7),
            ("enchant creature", 0.78),
            ("target creature", 0.78),
            ("damage", 0.78),
            ("all creatures", 0.78),
            ("until end of turn", 0.78),
        ]
        terms.extend(
            phrase
            for phrase, threshold in vocabulary
            if phrase not in terms and cls.fuzzy_contains(text, phrase, threshold)
        )
        return terms[:3]

    @classmethod
    def oracle_similarity(cls, ocr_text: str, oracle_text: str) -> float:
        left, right = cls.normalized_name(ocr_text), cls.normalized_name(oracle_text)
        if not left or not right:
            return 0
        left_grams = {left[index : index + 3] for index in range(max(1, len(left) - 2))}
        right_grams = {right[index : index + 3] for index in range(max(1, len(right) - 2))}
        containment = len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))
        return max(SequenceMatcher(None, left, right).ratio(), containment)

    @staticmethod
    def oracle_printing_cap(
        title_score: float,
        number: str | None,
        number_score: float,
        promo_type_hint: str | None,
        card: dict,
    ) -> float:
        """Rank exact-footer variants while always requiring human confirmation."""
        if title_score < 0.93 or not number or number_score < 1:
            return 89.0
        promo_types = set(card.get("promo_types") or [])
        if promo_type_hint:
            return 94.0 if promo_type_hint in promo_types else 89.0
        # When OCR sees no promo marker, put the ordinary set printing before
        # date-stamped/Game Day variants with the same collector number. The
        # small distinction improves the review order but remains below auto-add.
        promo_printing = bool(promo_types) or card.get("set", "").casefold().startswith("p")
        return 93.5 if promo_printing else 94.0

    @staticmethod
    def oracle_recovery_requires_cap(
        oracle_recovery: bool,
        exact_printed_identity: bool,
        printing_signal: bool,
        visual_printing_proof: bool,
        title_art_symbol_proof: bool,
    ) -> bool:
        """Cap rules-text identity only when no independent printing proof exists."""
        return bool(
            oracle_recovery
            and not exact_printed_identity
            and not printing_signal
            and not visual_printing_proof
            and not title_art_symbol_proof
        )

    async def _oracle_recovery(self, text: str, language: str) -> tuple[str | None, list[dict]]:
        if language != "en":
            return None, []
        terms = self.oracle_terms(text)
        if len(terms) < 2:
            return None, []
        matches = self._lookup_local_oracle_cards(terms, language)
        if not matches and hasattr(self.provider, "oracle_search"):
            try:
                async with asyncio.timeout(2.5):
                    matches = await self.provider.oracle_search(terms)
            except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError):
                return None, []
        ranked = sorted(
            ((card, self.oracle_similarity(text, card.get("oracle_text", ""))) for card in matches),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] < 0.48:
            return None, []
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.08:
            return None, []
        name = ranked[0][0]["name"]
        return name, await self._lookup_cards(name, None, None, None, language)

    @staticmethod
    def _lookup_local_oracle_cards(terms: list[str], language: str) -> list[dict]:
        """Find unique oracle cards locally; canonical metadata avoids network latency."""
        try:
            with SessionLocal() as db:
                statement = select(CardReference).where(
                    CardReference.language == language,
                    CardReference.oracle_text.is_not(None),
                )
                for term in terms[:3]:
                    statement = statement.where(CardReference.oracle_text.ilike(f"%{term}%"))
                rows = list(db.scalars(statement.limit(500)))
        except SQLAlchemyError:
            return []
        unique: dict[str, CardReference] = {}
        for row in rows:
            key = row.oracle_id or row.name.casefold()
            unique.setdefault(key, row)
        return [{"name": row.name, "oracle_text": row.oracle_text or ""} for row in unique.values()]

    @classmethod
    def promo_type_hint(cls, text: str) -> str | None:
        normalized = cls.normalized_name(text)
        # OCR commonly renders the tiny "Intro Pack" footer as InroOPack.
        if re.search(r"in(?:t)?r[o0]+pack", normalized):
            return "intropack"
        return None

    @classmethod
    def closest_catalog_names(
        cls, title: str, names: list[str], limit: int = 3
    ) -> list[tuple[str, float]]:
        query = cls.normalized_name(title)
        if len(query) < 4:
            return []
        ranked = sorted(
            (
                (name, cls.card_name_similarity(title, name))
                for name in names
                if any(
                    abs(len(cls.normalized_name(face)) - len(query)) <= max(5, len(query) // 2)
                    for face in [name, *name.split(" // ")]
                )
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return [item for item in ranked[:limit] if item[1] >= 0.72]

    @staticmethod
    def set_code_score(ocr_set: str | None, printed_set: str) -> float:
        if not ocr_set:
            return 0.45
        source = ocr_set.casefold()
        target = printed_set.casefold()
        # Core-set logos omit the tiny "1" visually; Paddle commonly reads
        # M13 as M3 even from an otherwise clean type-line pass.
        if source == "m3" and target == "m13":
            return 1.0
        variants = {
            source,
            source.translate(str.maketrans({"i": "1", "l": "1", "s": "5", "o": "0"})),
            source.translate(str.maketrans({"2": "z"})),
        }
        # Codes are tiny: ORI often reads ORL and M15 reads MIS. Normalize
        # visually indistinguishable glyphs on both sides before fuzzy scoring.
        visual_key = str.maketrans({"1": "i", "l": "i", "5": "s", "0": "o", "2": "z"})
        if target in variants or source.translate(visual_key) == target.translate(visual_key):
            return 1.0
        return min(
            0.85,
            max(SequenceMatcher(None, variant, target).ratio() for variant in variants),
        )

    @staticmethod
    def exact_set_code_match(ocr_set: str | None, printed_set: str) -> bool:
        """Match glyph-confusable codes without inventing a missing character."""
        if not ocr_set:
            return False
        visual_key = str.maketrans({"1": "i", "l": "i", "5": "s", "0": "o", "2": "z"})
        source = ocr_set.casefold().translate(visual_key)
        target = printed_set.casefold().translate(visual_key)
        return len(source) == len(target) and source == target

    @classmethod
    def has_strong_lookup_evidence(
        cls,
        title: str | None,
        number: str | None,
        printed_set_code: str | None,
        copyright_year: int | None,
        cards: list[dict],
    ) -> bool:
        # A Scryfall set code and collector number are the canonical identity of
        # a physical printing. Foil glare frequently hides the title while the
        # footer remains perfectly readable; once that pair resolves to one
        # catalog object, another full-frame OCR pass cannot add identity
        # evidence and only delays the scanner.
        if cls.unique_exact_footer_card(number, printed_set_code, cards):
            return True
        if not title or not cards:
            return False
        title_scores = [cls.card_name_similarity(title, card["name"]) for card in cards]
        best_index = max(range(len(cards)), key=title_scores.__getitem__)
        if title_scores[best_index] < 0.9:
            return False
        best = cards[best_index]
        exactish_footer = bool(
            number
            and printed_set_code
            and cls.collector_score(number, best["collector_number"]) >= 0.78
            and cls.set_code_score(printed_set_code, best["set"]) >= 0.78
        )
        if exactish_footer or len(cards) == 1:
            return True
        if copyright_year:
            matching_years = [
                card for card in cards if int(card.get("released_at", "0000")[:4]) == copyright_year
            ]
            return len(matching_years) == 1
        return False

    @classmethod
    def has_strong_fixed_identity_evidence(
        cls,
        title: str | None,
        number: str | None,
        printed_set_code: str | None,
        copyright_year: int | None,
        cards: list[dict],
    ) -> bool:
        """Accept a fixed-row near-title only with physical-frame corroboration.

        Recognition-only OCR occasionally damages the final few title glyphs
        (``Preacher`` -> ``Preacnet``).  Requiring an exact set code, a
        compatible copyright year, and a clearly separated catalog name keeps
        this shortcut conservative while avoiding a multi-second detector pass.
        Exact-printing auto-add authorization remains independent downstream.
        """
        if cls.has_strong_lookup_evidence(
            title, number, printed_set_code, copyright_year, cards
        ):
            return True
        if not title or not printed_set_code or not cards:
            return False
        scored = [
            (cls.card_name_similarity(title, card["name"]), card)
            for card in cards
            if cls.exact_set_code_match(printed_set_code, card["set"])
            and (
                not copyright_year
                or int(card.get("released_at", "0000")[:4]) == copyright_year
            )
        ]
        scored.sort(key=lambda item: item[0])
        if not scored or scored[-1][0] < 0.82:
            return False
        runner_up = scored[-2][0] if len(scored) > 1 else 0.0
        return scored[-1][0] - runner_up >= 0.08

    @classmethod
    def canonical_fixed_title_identity(
        cls, observed_title: str | None, cards: list[dict]
    ) -> str | None:
        """Return one clearly separated ordinary-card name from a title row."""
        if not observed_title or not cards:
            return None
        # A title names a card family, not a token/land printing. Those families
        # contain many visually distinct objects and must retain footer/layout
        # evidence before exact-print matching begins.
        if any(cls.is_basic_land(card) for card in cards) or any(
            "token" in str(card.get("type_line") or "").casefold() for card in cards
        ):
            return None
        names = sorted({str(card["name"]) for card in cards})
        ranked = sorted(
            (
                (cls.card_name_similarity(observed_title, name), name)
                for name in names
            ),
            reverse=True,
        )
        if not ranked or ranked[0][0] < 0.86:
            return None
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        return ranked[0][1] if ranked[0][0] - runner_up >= 0.10 else None

    @classmethod
    def unique_exact_footer_card(
        cls,
        number: str | None,
        printed_set_code: str | None,
        cards: list[dict],
    ) -> dict | None:
        if not number or not printed_set_code:
            return None
        matches = [
            card
            for card in cards
            if cls.collector_score(number, card["collector_number"]) == 1.0
            and cls.exact_set_code_match(printed_set_code, card["set"])
        ]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def has_strong_card_identity(cls, title: str | None, cards: list[dict]) -> bool:
        if not title or not cards:
            return False
        return any(cls.card_name_similarity(title, card["name"]) >= 0.90 for card in cards)

    @classmethod
    def has_exact_footer_match(
        cls,
        title: str | None,
        number: str | None,
        printed_set_code: str | None,
        cards: list[dict],
    ) -> bool:
        """Return true when OCR already identifies one exact physical printing."""
        if not title or not number or not printed_set_code:
            return False
        return any(
            cls.card_name_similarity(title, card["name"]) >= 0.93
            and cls.collector_score(number, card["collector_number"]) == 1.0
            and cls.set_code_score(printed_set_code, card["set"]) == 1.0
            for card in cards
        )

    @classmethod
    def has_exact_footer_title_fragment(
        cls,
        observed_title: str | None,
        card: dict,
        copyright_year: int | None,
    ) -> bool:
        """Corroborate one exact non-land footer with a real title fragment.

        The caller has already resolved an exact set-code/collector-number pair
        to one catalog printing. A four-character substring of that printing's
        title is independent corroboration; requiring copyright OCR as a third
        signal only forced another slow full-frame pass. Basic lands retain the
        stricter artwork path because their shared names cannot corroborate a
        particular illustration.
        """
        if cls.is_basic_land(card) or not observed_title:
            return False
        source = cls.normalized_name(observed_title)
        target = cls.normalized_name(str(card.get("name") or ""))
        if len(source) < 4 or source not in target:
            return False
        if not copyright_year:
            return True
        try:
            released_year = int(str(card.get("released_at") or "0000")[:4])
        except ValueError:
            return False
        return released_year == copyright_year

    @classmethod
    def has_constrained_visual_identity(
        cls, title: str | None, cards: list[dict], identity_names: set[str]
    ) -> bool:
        """Allow printing-level visual reranking only after one name is established."""
        return bool(
            len(identity_names) == 1 and cards and cls.has_strong_card_identity(title, cards)
        )

    @staticmethod
    def structured_confidence(
        confidence: float,
        title_score: float,
        number_score: float,
        set_score: float,
        year_score: float,
    ) -> float:
        if title_score >= 0.72 and number_score == 1.0 and set_score == 1.0:
            return max(confidence, 99.5)
        # Tiny footer OCR often drops one character from a three-letter set code
        # (KTK -> TK). An exact title and collector number plus that near-exact
        # independent set signal is still printing-specific evidence.
        if title_score >= 0.93 and number_score == 1.0 and set_score >= 0.78:
            return max(confidence, 98.5)
        if title_score >= 0.9 and number_score >= 0.78 and set_score >= 0.78 and year_score == 1.0:
            return max(confidence, 98.5)
        return confidence

    @staticmethod
    def has_exact_printing_identity(
        title_score: float,
        number: str | None,
        printed_set_code: str | None,
        number_score: float,
        set_score: float,
    ) -> bool:
        """Require recognizable identity plus the globally unique footer pair."""
        return bool(
            title_score >= 0.72
            and number_score == 1.0
            and set_score == 1.0
            and number
            and printed_set_code
        )

    @staticmethod
    def neural_printing_is_safe(
        *,
        shadow_mode: bool,
        candidate_id: str,
        neural_top_id: str | None,
        neural_top_score: float,
        neural_margin: float,
        independently_corroborated: bool,
    ) -> bool:
        """Keep artwork similarity from certifying a reused-art printing alone."""
        return bool(
            not shadow_mode
            and neural_top_id
            and candidate_id == neural_top_id
            and neural_top_score >= 0.70
            and neural_margin >= 0.06
            and independently_corroborated
        )

    @staticmethod
    def printing_verifiers_agree(
        *,
        identity_is_constrained: bool,
        family_complete: bool,
        title_score: float,
        candidate_id: str,
        neural_top_id: str | None,
        neural_margin: float,
        collector_number_exact: bool,
        is_basic_land: bool,
        has_observed_footer_identity: bool,
        footer_contradiction: bool,
    ) -> bool:
        """Promote a printing only when independent constrained verifiers agree."""
        return bool(
            identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and candidate_id == neural_top_id
            and collector_number_exact
            and neural_margin >= 0.01
            # Reused artwork can make both the full-card embedding and title
            # agree with the wrong physical printing. Consensus is auto-safe
            # only when the camera also supplied printing-specific footer data,
            # for every card type (not merely basic lands).
            and has_observed_footer_identity
            and not footer_contradiction
        )

    @classmethod
    def confirmed_camera_rerank_matches_footer(
        cls,
        *,
        source_kind: str | None,
        similarity: float,
        margin: float,
        observed_number: str | None,
        candidate_number: str,
        observed_set: str | None,
        candidate_set: str,
    ) -> bool:
        """Use a prior physical scan to rerank only when its footer agrees."""
        return bool(
            source_kind == "correction"
            and similarity >= 0.82
            and margin >= 0.08
            and observed_number
            and cls.collector_score(observed_number, candidate_number) >= 0.78
            and (
                not observed_set
                or cls.set_code_score(observed_set, candidate_set) >= 0.78
            )
        )

    @staticmethod
    def neural_rerank_without_footer(
        *,
        source_kind: str | None,
        similarity: float,
        margin: float,
        observed_number: str | None,
        observed_set: str | None,
    ) -> bool:
        """Rank a decisive neural artwork match first, without auto-adding it.

        A clear canonical or prior-camera win is better than arbitrary database
        order when OCR supplies only the exact card name. It still cannot prove
        an exact printing because artwork may be reused, so callers cap this
        path below the automatic-add threshold.
        """
        return bool(
            source_kind in {"canonical", "correction"}
            and similarity >= 0.82
            and margin >= 0.08
            and not observed_number
            and not observed_set
        )

    @staticmethod
    def confirmed_camera_printing_is_safe(
        *,
        source_kind: str | None,
        similarity: float,
        identity_is_constrained: bool,
        family_complete: bool,
        title_score: float,
        footer_contradiction: bool,
    ) -> bool:
        """Trust an exceptionally close prior labeled camera observation.

        Leave-one-out evaluation of confirmed physical scans found 34 accepted
        matches at this threshold with zero wrong printings.  Unlike canonical
        artwork, a correction also captures this camera, sleeve, and lighting;
        it can therefore prove a repeated printing when the tiny footer is
        unreadable.  Identity and complete-family gates keep unrelated nearest
        neighbors out, while any readable contradictory footer still vetoes it.
        """
        return bool(
            source_kind == "correction"
            and similarity >= 0.94
            and identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and not footer_contradiction
        )

    @staticmethod
    def confirmed_camera_visual_consensus_is_safe(
        *,
        source_kind: str | None,
        similarity: float,
        margin: float,
        identity_is_constrained: bool,
        family_complete: bool,
        title_score: float,
        candidate_id: str,
        neural_top_id: str | None,
        descriptor_top_id: str | None,
        descriptor_score: float,
        descriptor_margin: float,
        art_top_id: str | None,
        art_score: float,
        art_margin: float,
        footer_contradiction: bool,
    ) -> bool:
        """Accept a confirmed camera printing when two profile regions agree."""
        regional_consensus = bool(
            source_kind == "correction"
            and similarity >= 0.84
            and margin >= 0.06
            and identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and candidate_id == neural_top_id == descriptor_top_id == art_top_id
            and descriptor_score >= 98
            and descriptor_margin >= 1
            and art_score >= 98
            and art_margin >= 1
            and not footer_contradiction
        )
        frame_consensus = bool(
            source_kind == "correction"
            and similarity >= 0.90
            and margin >= 0.08
            and identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and candidate_id == neural_top_id == descriptor_top_id
            and descriptor_score >= 99
            and descriptor_margin >= 3
            and not footer_contradiction
        )
        return regional_consensus or frame_consensus

    @classmethod
    def has_unique_printing_signal(
        cls,
        title_score: float,
        number: str | None,
        number_score: float,
        competing_number_scores: list[float],
        printed_set_code: str | None,
        candidate_set_code: str,
        candidates_in_set: int,
    ) -> bool:
        unique_number = bool(
            number
            and number_score >= 0.78
            and (not competing_number_scores or number_score - max(competing_number_scores) >= 0.12)
        )
        unique_set = bool(
            printed_set_code
            and cls.set_code_score(printed_set_code, candidate_set_code) == 1.0
            and candidates_in_set == 1
        )
        return title_score >= (0.93 if unique_set else 0.95) and (unique_number or unique_set)

    @staticmethod
    def unique_number_year_artist_ids(
        cards: list[dict],
        collector_number: str | None,
        copyright_year: int | None,
        artist_scores: dict[str, float],
    ) -> set[str]:
        """Resolve a printing from three independent footer observations."""
        if not collector_number or not copyright_year:
            return set()
        return {
            card["id"]
            for card in cards
            if CardRecognizer.collector_score(
                collector_number, card["collector_number"]
            )
            == 1.0
            and int(card.get("released_at", "0000")[:4]) == copyright_year
            and artist_scores.get(card["id"], 0.0) >= 0.9
        }

    @staticmethod
    def visual_only_score(score: float) -> float:
        # Artwork is intentionally supporting evidence, never proof of an exact
        # printing. Wizards frequently reuses identical art across sets, promos,
        # collector numbers, and finishes. Keep visual-only candidates useful at
        # the top of Review without allowing them to cross the 98.5% auto-add gate.
        return min(94.0, score)

    @classmethod
    def should_admit_visual_candidate(
        cls,
        reference_name: str,
        cards: list[dict],
        identity_is_constrained: bool,
    ) -> bool:
        """Keep strong OCR identities closed while allowing visual rescue.

        A damaged title can fuzzy-match a real but unrelated card name. In that
        case the global artwork match must be allowed into Review or the wrong
        OCR identity becomes impossible to dislodge. Once OCR has established a
        single strong identity, retain the strict family filter so perceptual
        hash collisions cannot inject unrelated cards.
        """
        if not cards or not identity_is_constrained:
            return True
        normalized_reference = cls.normalized_name(reference_name)
        return any(
            SequenceMatcher(
                None,
                normalized_reference,
                cls.normalized_name(card["name"]),
            ).ratio()
            >= 0.90
            for card in cards
        )

    async def _lookup_cards(
        self,
        title: str | None,
        number: str | None,
        printed_set_code: str | None,
        box_set_code: str | None,
        language: str,
        promo_type: str | None = None,
    ) -> list[dict]:
        if not title and not number:
            return []
        preferred_set = printed_set_code or box_set_code

        # Wizards occasionally ships a physical substitute/helper card before
        # Scryfall publishes that exact set-specific insert. Its printed phrase
        # plus set, collector number, and year are still a complete physical
        # identity. Seed a deterministic local-only reference so the scanner
        # can inventory the item without relabeling it as an unrelated card.
        if (
            title == "Double-Faced Substitute Card"
            and number
            and printed_set_code
            and language.casefold() == "en"
        ):
            await asyncio.to_thread(
                self._ensure_pack_insert_reference,
                printed_set_code,
                number,
                None,
                language,
            )

        # The growing local reference catalog is authoritative enough for card
        # identity and avoids putting every physical scan behind Scryfall's
        # network latency. Remote lookup remains the fallback for new/unindexed
        # cards and supplies printing-family completeness below.
        if (
            number
            and printed_set_code
            and not promo_type
            and language.casefold() == "en"
        ):
            # Set + collector number identifies a physical printing even when
            # glare turns the title into plausible-looking nonsense.  Looking
            # up the damaged title first used to hide this exact local match
            # (for example ``Tiac GideonJura`` beside ``ORI 008/272``), leaving
            # the correct card in Review at a low confidence.  Require both
            # footer fields to match exactly so a partial/misread collector
            # number cannot bypass the normal title and artwork safeguards.
            footer_cards = await asyncio.to_thread(
                self._lookup_local_cards_by_number, number, printed_set_code
            )
            exact_footer_cards = [
                card
                for card in footer_cards
                if self.collector_score(number, card["collector_number"]) == 1.0
                and self.set_code_score(printed_set_code, card["set"]) == 1.0
            ]
            if exact_footer_cards:
                # A perfectly readable catalog title must veto a conflicting
                # exact-looking footer digit. Tiny numerators commonly lose a
                # leading digit (Plains 261/274 -> 26/274), which otherwise
                # becomes the unrelated M21 card #26. Damaged/non-catalog
                # titles retain the footer-first rescue used for glare cases.
                if title and all(
                    self.card_name_similarity(title, card["name"]) < 0.72
                    for card in exact_footer_cards
                ):
                    title_cards = await asyncio.to_thread(
                        self._lookup_local_cards, title, None, None
                    )
                    if title_cards and any(
                        self.card_name_similarity(title, card["name"]) >= 0.93
                        for card in title_cards
                    ):
                        return title_cards
                return exact_footer_cards
        if title and not promo_type and language.casefold() == "en":
            local_cards = await asyncio.to_thread(
                self._lookup_local_cards, title, number, preferred_set
            )
            if local_cards:
                return local_cards
        if not title and number and not promo_type and language.casefold() == "en":
            # A readable collector number remains useful when glare destroys the
            # title. Preserve every matching printing as a conservative pool;
            # global/descriptor evidence will rank it, but number-only evidence
            # can never cross the automatic-add threshold by itself.
            local_cards = await asyncio.to_thread(
                self._lookup_local_cards_by_number, number, preferred_set
            )
            if local_cards:
                return local_cards

        # A complete local catalog contains the same canonical identities used
        # by remote search. If OCR could not find one, the scan benefits more
        # from the already-scheduled local visual/neural recovery than from a
        # sequence of network fuzzy searches. Promo and localized lookups retain
        # their remote path because those variants may not exist locally yet.
        if (
            not promo_type
            and language.casefold() == "en"
            and await asyncio.to_thread(self._local_catalog_is_ready)
        ):
            return []

        async def search_variants(candidate_title: str, *, relaxed: bool = False) -> list[dict]:
            title_query = f'!"{candidate_title}"'
            if promo_type:
                title_query += " is:promo"
            variants: list[tuple[str, str | None]] = []
            if number:
                variants.append(
                    (f"{title_query} cn:{number}", None if promo_type else preferred_set)
                )
                if relaxed:
                    variants.append((f"{title_query} cn:{number}", None))
            if relaxed or not number:
                variants.append((title_query, preferred_set))
            if relaxed:
                variants.append((title_query, None))
            seen: set[tuple[str, str | None]] = set()
            for query, set_code in variants:
                key = (query, set_code)
                if key in seen:
                    continue
                seen.add(key)
                cards = await self.provider.search(query, set_code, language)
                if promo_type:
                    cards = [
                        card
                        for card in cards
                        if promo_type in card.get("promo_types", [])
                        and (
                            not number
                            or self.collector_score(number, card["collector_number"]) == 1.0
                        )
                    ]
                if cards:
                    return cards
            return []

        try:
            # Recognition must not sit behind a long external outage. All lookup
            # attempts share one short budget; the captured frame still proceeds
            # through local artwork matching and into Review if Scryfall is down.
            async with asyncio.timeout(3.5):
                cards = await search_variants(title) if title else []
                # Set code + collector number directly identifies a printing and
                # is more reliable than forcing a misspelled OCR title into the
                # initial query.
                if not cards and number and printed_set_code and not promo_type:
                    cards = await self.provider.search(f"cn:{number}", printed_set_code, language)
                # Localized title text is not consistently searchable through
                # Scryfall's canonical-name field. Set + collector number + chosen
                # language identifies the printing without guessing an English ID.
                if not cards and number and language != "en" and preferred_set:
                    cards = await self.provider.search(f"cn:{number}", preferred_set, language)
                if not cards and title and hasattr(self.provider, "fuzzy_name"):
                    canonical_name = await self.provider.fuzzy_name(title)
                    if canonical_name:
                        cards = await search_variants(canonical_name, relaxed=True)
                if not cards and title and hasattr(self.provider, "card_names"):
                    catalog = await self.provider.card_names()
                    closest = await asyncio.to_thread(self.closest_catalog_names, title, catalog)
                    for canonical_name, _similarity in closest:
                        cards = await search_variants(canonical_name, relaxed=True)
                        if cards:
                            break
                if not cards and title:
                    cards = await search_variants(title, relaxed=True)
                return cards
        except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Scryfall lookup unavailable; preserving scan for Review: %s", exc)
            return []

    @classmethod
    def _ensure_pack_insert_reference(
        cls,
        set_code: str,
        collector_number: str,
        released_year: int | None,
        language: str,
    ) -> None:
        normalized_set = set_code.casefold()
        normalized_number = collector_number.lstrip("0") or "0"
        reference_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"mtglogger:pack-insert:{normalized_set}:{normalized_number}:{language}",
            )
        )
        try:
            with SessionLocal() as db:
                existing = db.get(CardReference, reference_id)
                if existing:
                    # Early locally-seeded inserts used a descriptive sentinel
                    # here. Keep persisted rows compatible with the visual
                    # catalog's hexadecimal hash contract when they are seen
                    # again after an upgrade.
                    existing.art_hash = "0000000000000000"
                    db.commit()
                    return
                db.add(
                    CardReference(
                        scryfall_id=reference_id,
                        name="Double-Faced Substitute Card",
                        set_code=normalized_set,
                        set_name=f"{normalized_set.upper()} Pack Inserts",
                        collector_number=normalized_number,
                        language=language,
                        oracle_text="You can use this card to represent a double-faced card.",
                        promo_types="[]",
                        finishes='["nonfoil"]',
                        color_identity="",
                        rarity="common",
                        type_line="Card",
                        legalities="{}",
                        released_at=(date(released_year, 1, 1) if released_year else None),
                        image_url="",
                        # The insert has no canonical image yet, so this value
                        # only satisfies the storage contract. Blank-image
                        # references are excluded from visual retrieval below.
                        art_hash="0000000000000000",
                    )
                )
                db.commit()
        except SQLAlchemyError:
            logger.exception("Could not seed physical pack-insert reference")

    @classmethod
    def _lookup_local_cards(
        cls, title: str, number: str | None, preferred_set: str | None
    ) -> list[dict]:
        catalog = cls._get_visual_catalog()
        rows = list(catalog.references_by_name.get(title.casefold(), ()))
        if not rows:
            normalized_title = cls.normalized_name(title)
            prefix_names = catalog.names_by_prefix.get(normalized_title[:3], ())
            closest = cls.closest_catalog_names(
                title, prefix_names or catalog.names, limit=1
            )
            if not closest or closest[0][1] < 0.72:
                return []
            rows = list(
                catalog.references_by_name.get(closest[0][0].casefold(), ())
            )
        if not rows:
            return []
        exact = [
            reference
            for reference in rows
            if (not preferred_set or cls.set_code_score(preferred_set, reference.set_code) == 1.0)
            and (not number or cls.collector_score(number, reference.collector_number) == 1.0)
        ]
        selected = exact or rows
        return [cls._reference_card(reference) for reference in selected[:24]]

    @staticmethod
    def _reference_card(reference: CardReference) -> dict:
        """Serialize a local reference in the provider-compatible card shape."""
        return {
            "id": reference.scryfall_id,
            "name": reference.name,
            "set": reference.set_code,
            "set_name": reference.set_name,
            "collector_number": reference.collector_number,
            "released_at": (reference.released_at.isoformat() if reference.released_at else "0000"),
            "image_uris": {"normal": reference.image_url},
            "prices": {
                "usd": (str(reference.market_price) if reference.market_price is not None else None)
            },
            "lang": reference.language or "en",
            "oracle_id": reference.oracle_id,
            "oracle_text": reference.oracle_text or "",
            "artist": reference.artist,
            "promo_types": json.loads(reference.promo_types or "[]"),
            "finishes": json.loads(reference.finishes or "[]"),
            "color_identity": list(reference.color_identity or ""),
            "rarity": reference.rarity,
            "type_line": reference.type_line,
        }

    @classmethod
    def _lookup_local_printing_family(cls, name: str, language: str) -> tuple[list[dict], int]:
        """Return a printing family from the completed local catalog.

        Exact-print recognition must never wait behind Scryfall once the same
        canonical printings are present locally. Unlike interactive search,
        this path deliberately returns the *complete* family: truncating common
        names such as Swamp to 24 rows can remove the photographed artwork from
        consideration and turn a wrong footer reading into a false auto-add.
        """
        try:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(CardReference)
                        .where(
                            CardReference.name == name,
                            CardReference.language == language,
                        )
                        .order_by(
                            CardReference.released_at.desc(),
                            CardReference.set_code,
                            CardReference.collector_number,
                        )
                    )
                )
                if not rows:
                    rows = list(
                        db.scalars(
                            select(CardReference)
                            .where(
                                func.lower(CardReference.name) == name.casefold(),
                                CardReference.language == language,
                            )
                            .order_by(
                                CardReference.released_at.desc(),
                                CardReference.set_code,
                                CardReference.collector_number,
                            )
                        )
                    )
        except SQLAlchemyError:
            return [], 0
        return [cls._reference_card(reference) for reference in rows], len(rows)

    @classmethod
    def _lookup_local_cards_by_number(cls, number: str, preferred_set: str | None) -> list[dict]:
        normalized = number.casefold().lstrip("0") or "0"
        try:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(CardReference).where(
                            func.lower(func.ltrim(CardReference.collector_number, "0"))
                            == normalized
                        )
                    )
                )
        except SQLAlchemyError:
            return []
        if preferred_set:
            preferred = [row for row in rows if row.set_code.casefold() == preferred_set.casefold()]
            rows = preferred or rows
        return [cls._reference_card(reference) for reference in rows]

    async def recognize(
        self,
        raw: bytes,
        box_set_code: str | None = None,
        language: str = "en",
        ignored_visual_hashes: set[str] | None = None,
        ignored_example_review_ids: set[str] | None = None,
        already_rectified: bool = False,
    ) -> Recognition:
        async with self._recognition_lock:
            started = time.perf_counter()
            recovery_used = False
            oracle_recovery = False
            decoded = self.decode(raw)
            logger.info(
                "Scanner input frame: %dx%d (%d bytes)",
                decoded.shape[1],
                decoded.shape[0],
                len(raw),
            )
            corrected = (
                decoded
                if already_rectified
                else await asyncio.to_thread(lambda: self.rectify(decoded))
            )
            analysis_image, low_light_normalized = await asyncio.to_thread(
                self.normalize_low_light, corrected
            )
            neural = getattr(self, "_neural", None)
            neural_live = bool(neural is not None and not get_settings().neural_shadow_mode)
            neural_task = (
                asyncio.create_task(asyncio.to_thread(neural.embed, analysis_image))
                if neural_live
                else None
            )
            prepared = time.perf_counter()
            card_structure = await asyncio.to_thread(self.has_card_structure, analysis_image)
            neural_vector = await neural_task if neural_task is not None else None
            neural_matches = (
                await asyncio.to_thread(
                    neural.search_vector,
                    neural_vector,
                    10,
                    ignored_source_ids=ignored_example_review_ids,
                )
                if neural_live
                else []
            )
            preliminary_neural_margin = (
                neural_matches[0].similarity - neural_matches[1].similarity
                if len(neural_matches) > 1
                else (neural_matches[0].similarity if neural_matches else 0.0)
            )
            neural_fast_identity = bool(
                neural_matches
                and self.neural_source_can_recover_identity(
                    neural_matches[0].source_kind
                )
                and neural_matches[0].similarity >= 0.82
                and preliminary_neural_margin >= 0.06
            )
            consensus_name = self.neural_name_consensus(neural_matches)
            if (
                consensus_name
                and neural_matches
                and neural_matches[0].similarity >= 0.72
            ):
                neural_fast_identity = True
            fixed_title_identity = False
            if neural_fast_identity:
                # A clear canonical embedding establishes only the card name.
                # Exact-printing authorization remains downstream and still
                # requires complete-family descriptor/frame corroboration.
                title = consensus_name or neural_matches[0].reference.name
                promo_type = None
                text = await asyncio.to_thread(self.extract_fixed_footer_text, corrected)
                _, number, printed_set_code, copyright_year = self.hints(text)
                fixed_title_text = await asyncio.to_thread(
                    self.extract_fixed_title_text, analysis_image
                )
                fixed_title = self.hints(fixed_title_text)[0]
                if fixed_title:
                    fixed_title_cards = await asyncio.to_thread(
                        self._lookup_local_cards, fixed_title, None, None
                    )
                    exact_fixed_names = [
                        card["name"]
                        for card in fixed_title_cards
                        if self.card_name_similarity(fixed_title, card["name"]) >= 0.90
                    ]
                    if exact_fixed_names:
                        # A directly read printed title outranks agreement among
                        # weak visual neighbors. This prevents repeated-art or
                        # dark-frame embeddings from locking OCR onto the wrong
                        # family before footer evidence is considered.
                        title = exact_fixed_names[0]
                        text = "\n".join(
                            part for part in (fixed_title_text, text) if part.strip()
                        )
                cards = await self._lookup_cards(
                    title,
                    None,
                    None,
                    box_set_code,
                    language,
                    None,
                )
                footer_family_cards, _footer_family_total = await asyncio.to_thread(
                    self._lookup_local_printing_family, title, language
                )
                footer_family_cards = footer_family_cards or cards
                if printed_set_code and not any(
                    self.exact_set_code_match(printed_set_code, card["set"])
                    for card in footer_family_cards
                ):
                    printed_set_code = None
                if not printed_set_code:
                    # The rectified footer is already in memory and commonly
                    # retains enough set/language structure for a safe
                    # family-scoped repair. Exhaust it before invoking any raw
                    # camera OCR fallback.
                    printed_set_code = self.family_set_code_from_footer_text(
                        text, footer_family_cards, number
                    )
                if not printed_set_code:
                    decoded_height, decoded_width = decoded.shape[:2]
                    if (
                        decoded_height > decoded_width
                        and 0.68
                        <= decoded_width / max(1, decoded_height)
                        <= 0.75
                    ):
                        raw_footer_text = await asyncio.to_thread(
                            self.extract_fixed_footer_text, decoded
                        )
                        raw_exact_set = self.exact_family_set_code_from_footer_text(
                            raw_footer_text, footer_family_cards
                        )
                        if not raw_exact_set:
                            # Recognition-only OCR can confuse the final set
                            # glyph (ORI -> ORT). Repair it only inside the
                            # already established card family, and deliberately
                            # discard every collector-number guess from this
                            # raw pass. This prevents a damaged ``62`` from
                            # competing with the rectified physical evidence as
                            # the real 10E #162 printing.
                            raw_exact_set = self.family_set_code_from_footer_text(
                                raw_footer_text, footer_family_cards, None
                            )
                        if raw_exact_set:
                            printed_set_code = raw_exact_set
                        if not raw_exact_set:
                            detected_footer_text = await asyncio.to_thread(
                                self.extract_raw_footer_band_text, decoded
                            )
                            _, detected_number, detected_set, detected_year = self.hints(
                                detected_footer_text
                            )
                            if not detected_set or not any(
                                self.exact_set_code_match(detected_set, card["set"])
                                for card in footer_family_cards
                            ):
                                detected_set = self.family_set_code_from_footer_text(
                                    detected_footer_text,
                                    footer_family_cards,
                                    detected_number,
                                )
                            detected_matches = [
                                card
                                for card in footer_family_cards
                                if detected_number
                                and detected_set
                                and self.collector_score(
                                    detected_number, card["collector_number"]
                                )
                                == 1.0
                                and self.exact_set_code_match(
                                    detected_set, card["set"]
                                )
                                and (
                                    not detected_year
                                    or int(card.get("released_at", "0000")[:4])
                                    == detected_year
                                )
                            ]
                            if len(detected_matches) == 1:
                                number = detected_number
                                printed_set_code = detected_set
                                copyright_year = detected_year or copyright_year
                                text = "\n".join(
                                    part
                                    for part in (text, detected_footer_text)
                                    if part.strip()
                                )
                number_is_plausible = bool(
                    number
                    and any(
                        self.collector_score(number, card["collector_number"]) >= 0.78
                        for card in footer_family_cards
                    )
                )
                if printed_set_code or number_is_plausible:
                    if number and not any(
                        self.exact_set_code_match(printed_set_code, card["set"])
                        and self.collector_score(number, card["collector_number"]) >= 0.78
                        for card in footer_family_cards
                    ):
                        number = None
                    cards = await self._lookup_cards(
                        title,
                        number,
                        printed_set_code,
                        box_set_code,
                        language,
                        None,
                    )
                oracle_recovery = True
            else:
                # Conventional portrait cards can be identified from their
                # fixed title and footer rows without running Paddle's costly
                # full text detector.  Only accept this shortcut when catalog
                # lookup corroborates a unique printing or a strong title;
                # unusual layouts retain the existing broad-OCR fallback.
                fixed_title_text = await asyncio.to_thread(
                    self.extract_fixed_title_text, analysis_image
                )
                observed_fixed_title = self.hints(fixed_title_text)[0]
                fixed_title_cards = await self._lookup_cards(
                    observed_fixed_title, None, None, box_set_code, language, None
                )
                canonical_fixed_title = self.canonical_fixed_title_identity(
                    observed_fixed_title, fixed_title_cards
                )
                fixed_title_identity = bool(canonical_fixed_title)
                if canonical_fixed_title:
                    title = canonical_fixed_title
                    number = printed_set_code = copyright_year = None
                    promo_type = None
                    text = fixed_title_text
                    cards = await self._lookup_cards(
                        title, None, None, box_set_code, language, None
                    )
                else:
                    fixed_text = await asyncio.to_thread(
                        self.extract_fixed_identity_text, analysis_image
                    )
                    fixed_hints = self.hints(fixed_text)
                    fixed_promo = self.promo_type_hint(fixed_text)
                    fixed_cards = await self._lookup_cards(
                        *fixed_hints[:3], box_set_code, language, fixed_promo
                    )
                    if self.has_strong_fixed_identity_evidence(*fixed_hints, fixed_cards):
                        text = fixed_text
                        title, number, printed_set_code, copyright_year = fixed_hints
                        promo_type = fixed_promo
                        cards = fixed_cards
                    else:
                        text = await asyncio.to_thread(
                            self.extract_identification_text, analysis_image
                        )
                        title, number, printed_set_code, copyright_year = self.hints(text)
                        promo_type = self.promo_type_hint(text)
                        cards = await self._lookup_cards(
                            title,
                            number,
                            printed_set_code,
                            box_set_code,
                            language,
                            promo_type,
                        )
            ocr_complete = time.perf_counter()
            repaired_family_set_code = False
            # Keep the camera's original footer observation separate from the
            # family-scoped lookup hints. Later recovery deliberately discards
            # numbers/codes impossible for the assumed title family; that is
            # useful for ranking but must not erase physical evidence that
            # disproves a truncated-title candidate (Cunning Strike #150 was
            # once narrowed to singleton Cunning EXO #28 this way).
            raw_observed_number = number
            raw_observed_set_code = printed_set_code
            raw_observed_text = text
            exact_footer_card = self.unique_exact_footer_card(number, printed_set_code, cards)
            if exact_footer_card and (
                title is None
                or self.has_exact_footer_title_fragment(
                    title,
                    exact_footer_card,
                    copyright_year,
                )
            ):
                # Populate the title from the uniquely identified local
                # printing so downstream scoring follows the same path as a
                # readable title without paying for broad OCR recovery. A real
                # fragment plus matching copyright year may safely repair a
                # truncated non-land title; basic lands never use this shortcut.
                title = exact_footer_card["name"]
            # A set code plus collector number is normally the canonical
            # identifier for a paper printing. Basic lands are the exception in
            # practice: one mistaken footer digit silently selects a different
            # artwork from the same set. Never let footer OCR bypass independent
            # artwork verification for those cards.
            exact_land_needs_art = bool(exact_footer_card and self.is_basic_land(exact_footer_card))
            if exact_footer_card and not exact_land_needs_art:
                scan_fingerprints = {}
                visual_matches = []
            else:
                # Exact-print frame hashes must use the physical camera colors;
                # OCR contrast normalization can make one reused-art reprint's
                # border resemble another (RTR Ogre Jailbreaker vs MM3).
                scan_fingerprints = await asyncio.to_thread(visual_fingerprints, corrected)
                # Do not search all ~100k fingerprints before OCR recovery has
                # had a chance to establish the card name. Identity-scoped
                # matching below compares the complete printing family without
                # the global hash prefilter, so doing both only duplicated work.
                # Truly unidentified scans still receive the exhaustive global
                # rescue after recovery.
                visual_matches = []
            initial_match_complete = time.perf_counter()
            # Low-light normalization is tuned for OCR and embeddings, but its
            # local contrast amplification can deform the tiny set-symbol ORB
            # keypoints. Reference descriptor bundles were built from ordinary
            # card images, so compare them with the original rectified camera
            # pixels. This reuses an existing frame and adds no processing pass.
            descriptor_image = corrected
            neural_recovered_identity = neural_fast_identity or fixed_title_identity
            # The embedding is computed alongside OCR. Consult it before the
            # expensive full-frame OCR recovery so a decisive artwork identity
            # can fuse with an already-readable footer. This turns the common
            # "damaged title, clean set/number" case into a local exact lookup
            # instead of paying for broad OCR and catalog searches.
            if (
                neural_live
                and not self.has_strong_lookup_evidence(
                    title, number, printed_set_code, copyright_year, cards
                )
            ):
                if not neural_matches:
                    neural_vector = await neural_task if neural_task is not None else None
                    neural_matches = await asyncio.to_thread(
                        neural.search_vector,
                        neural_vector,
                        10,
                        ignored_source_ids=ignored_example_review_ids,
                    )
                neural_identity_margin = (
                    neural_matches[0].similarity - neural_matches[1].similarity
                    if len(neural_matches) > 1
                    else (neural_matches[0].similarity if neural_matches else 0.0)
                )
                if (
                    neural_matches
                    and self.neural_source_can_recover_identity(
                        neural_matches[0].source_kind
                    )
                    and neural_matches[0].similarity >= 0.80
                    and neural_identity_margin >= 0.03
                ):
                    recovered_name = neural_matches[0].reference.name
                    recovered_cards = await self._lookup_cards(
                        recovered_name,
                        number,
                        printed_set_code,
                        box_set_code,
                        language,
                        promo_type,
                    )
                    if recovered_cards:
                        title = recovered_name
                        cards = recovered_cards
                        neural_recovered_identity = True
                        # Artwork may recover identity, but only the independent
                        # footer can make the resulting exact printing auto-safe.
                        oracle_recovery = True
                if not neural_recovered_identity:
                    fragment_name = self.neural_title_fragment_identity(title, neural_matches)
                    if fragment_name:
                        recovered_cards = await self._lookup_cards(
                            fragment_name,
                            number,
                            printed_set_code,
                            box_set_code,
                            language,
                            promo_type,
                        )
                        if recovered_cards:
                            title = fragment_name
                            cards = recovered_cards
                            neural_recovered_identity = True
                            oracle_recovery = True
            if (
                not neural_recovered_identity
                and not self.has_strong_lookup_evidence(
                    title, number, printed_set_code, copyright_year, cards
                )
            ):
                recovery_used = True
                focused_identity_is_strong = self.has_strong_card_identity(title, cards)
                # A fast crop can occasionally lock onto an internal rules box,
                # and tiny footers may be incomplete. OCR the original frame only
                # for weak scans, then rerank before interrupting the user.
                recovery_text = (
                    await asyncio.to_thread(
                        self.extract_recovery_footer_text,
                        decoded,
                        {card["collector_number"] for card in cards},
                        {card["set"] for card in cards},
                    )
                    if focused_identity_is_strong
                    else await asyncio.to_thread(self.extract_text, decoded)
                )
                recovery_hints = self.hints(recovery_text)
                recovery_promo = self.promo_type_hint(recovery_text)
                recovery_cards = await self._lookup_cards(
                    *recovery_hints[:3], box_set_code, language, recovery_promo
                )
                if focused_identity_is_strong:
                    # Full-frame OCR sees table texture, sleeves, and rules text.
                    # It may therefore invent a plausible but unrelated title.
                    # Never replace an identity already corroborated by Scryfall;
                    # instead borrow only missing printing-specific footer fields
                    # and re-query that original card name with the fused evidence.
                    recovered_number, recovered_set, recovered_year = recovery_hints[1:]
                    fused_number = number or recovered_number
                    fused_set = printed_set_code or recovered_set
                    fused_year = copyright_year or recovered_year
                    if (
                        fused_number != number
                        or fused_set != printed_set_code
                        or fused_year != copyright_year
                    ):
                        fused_cards = await self._lookup_cards(
                            title,
                            fused_number,
                            fused_set,
                            box_set_code,
                            language,
                            promo_type,
                        )
                        if self.has_strong_card_identity(title, fused_cards):
                            cards = fused_cards
                            number = fused_number
                            printed_set_code = fused_set
                            copyright_year = fused_year
                    if recovery_text.strip():
                        text = "\n".join((text, recovery_text))
                elif recovery_cards:
                    # The focused crop is best at the tiny collector footer,
                    # while the noisier full-frame pass is often the only one
                    # that can read the title.  Fuse those complementary facts
                    # before accepting the broader recovery pool.  This is
                    # essential for basic lands whose names and set are shared
                    # by several distinct, potentially valuable artworks.
                    recovered_title, recovered_number, recovered_set, recovered_year = (
                        recovery_hints
                    )
                    # The focused crop has already failed to establish a card
                    # identity on this branch. Prefer complete fields from the
                    # successful full-frame identity pass over damaged-but-
                    # nonempty focused OCR (for example 116 read as 11).
                    fused_number = recovered_number or number
                    fused_set = recovered_set or printed_set_code
                    fused_year = recovered_year or copyright_year
                    fused_cards = await self._lookup_cards(
                        recovered_title,
                        fused_number,
                        fused_set,
                        box_set_code,
                        language,
                        recovery_promo,
                    )
                    text = "\n".join(part for part in (text, recovery_text) if part.strip())
                    title = recovered_title
                    number = fused_number
                    printed_set_code = fused_set
                    copyright_year = fused_year
                    cards = fused_cards or recovery_cards
                elif recovery_text.strip():
                    text = recovery_text
                if (
                    recovery_text.strip()
                    and not self.has_strong_lookup_evidence(
                        title, number, printed_set_code, copyright_year, cards
                    )
                    and not self.has_strong_card_identity(title, cards)
                ):
                    recovered_name, oracle_cards = await self._oracle_recovery(
                        recovery_text, language
                    )
                    if oracle_cards:
                        oracle_recovery = True
                        title = recovered_name
                        cards = oracle_cards
                # If this still needs Review, preserve what the camera saw rather
                # than a misleading enlarged internal rectangle.
                corrected = decoded
                card_structure = await asyncio.to_thread(self.has_card_structure, decoded)
                # Keep visual candidates from the best localized crop. Visual
                # evidence is capped below auto-add, so it can rescue a damaged
                # OCR title without silently accepting a bad contour.
            # Recovery may append a cleaner footer after the initial lookup.
            # Reconcile the merged observations once before printing-family
            # verification; otherwise ranking can display an exact footer while
            # still using stale set/collector variables from the first pass.
            merged_title, merged_number, merged_set, merged_year = self.hints(text)
            if merged_number and merged_set:
                exact_footer_year_cards = []
                if not merged_title and merged_year and not promo_type:
                    footer_cards = await asyncio.to_thread(
                        self._lookup_local_cards_by_number, merged_number, merged_set
                    )
                    exact_footer_year_cards = [
                        card
                        for card in footer_cards
                        if self.collector_score(
                            merged_number, card["collector_number"]
                        )
                        == 1.0
                        and self.exact_set_code_match(merged_set, card["set"])
                        and int(card.get("released_at", "0000")[:4]) == merged_year
                        and not self.is_basic_land(card)
                    ]
                # A weak visual recovery must not hide a unique physical
                # set/collector/year footer. This is especially important for
                # current frames whose title can vanish under foil treatment.
                # Basic lands remain excluded because their within-set artwork
                # still requires independent visual proof.
                merged_cards = (
                    exact_footer_year_cards
                    if len(exact_footer_year_cards) == 1
                    else await self._lookup_cards(
                        merged_title or title,
                        merged_number,
                        merged_set,
                        box_set_code,
                        language,
                        promo_type,
                    )
                )
                merged_exact = self.unique_exact_footer_card(
                    merged_number, merged_set, merged_cards
                )
                merged_identity = merged_title or title
                if merged_exact and (
                    len(exact_footer_year_cards) == 1
                    or
                    not merged_identity
                    or self.card_name_similarity(
                        merged_identity, merged_exact["name"]
                    )
                    >= 0.72
                ):
                    title = merged_exact["name"]
                    number = merged_number
                    printed_set_code = merged_set
                    copyright_year = merged_year or copyright_year
                    cards = merged_cards
            recovery_complete = time.perf_counter()
            identity_names = {card["name"] for card in cards} or ({title} if title else set())
            identity_is_constrained = self.has_constrained_visual_identity(
                title, cards, identity_names
            )
            if neural_live and not identity_is_constrained:
                if not neural_matches:
                    neural_vector = await neural_task if neural_task is not None else None
                    neural_matches = await asyncio.to_thread(
                        neural.search_vector,
                        neural_vector,
                        10,
                        ignored_source_ids=ignored_example_review_ids,
                    )
                neural_identity_margin = (
                    neural_matches[0].similarity - neural_matches[1].similarity
                    if len(neural_matches) > 1
                    else (neural_matches[0].similarity if neural_matches else 0.0)
                )
                if (
                    neural_matches
                    and self.neural_source_can_recover_identity(
                        neural_matches[0].source_kind
                    )
                    and neural_matches[0].similarity >= 0.80
                    and neural_identity_margin >= 0.03
                ):
                    # A decisive embedding can recover the card *name* cheaply
                    # when glare destroys title OCR. Treat it like oracle-text
                    # recovery: it narrows expensive local comparisons and
                    # improves Review ordering, but artwork-derived identity is
                    # capped below auto-add unless footer evidence independently
                    # proves the physical printing.
                    recovered = self._reference_card(neural_matches[0].reference)
                    title = recovered["name"]
                    cards = [recovered]
                    identity_names = {title}
                    identity_is_constrained = True
                    oracle_recovery = True
            if scan_fingerprints and not identity_is_constrained:
                visual_matches = await asyncio.to_thread(
                    self._visual_matches,
                    scan_fingerprints,
                    printed_set_code or box_set_code,
                    *([ignored_visual_hashes] if ignored_visual_hashes is not None else []),
                )
            family_complete = False
            if identity_is_constrained:
                # Even a syntactically exact set/collector pair can be an OCR
                # substitution for another real printing (055 -> 065 is common
                # in tiny footers). Always admit the complete local family so
                # independent artwork/neural evidence can overturn that error.
                # Local expansion is cheap and auto-add safety is decided later
                # by corroboration, never by this candidate-admission step.
                try:
                    family_name = next(iter(identity_names))
                    family_cards, family_total = await asyncio.to_thread(
                        self._lookup_local_printing_family,
                        family_name,
                        language,
                    )
                    if not family_cards and hasattr(self.provider, "printing_family"):
                        async with asyncio.timeout(3.0):
                            family_cards, family_total = await self.provider.printing_family(
                                family_name, language
                            )
                    if family_cards:
                        family_complete = bool(len(family_cards) == family_total)
                        known_ids = {card["id"] for card in cards}
                        cards.extend(card for card in family_cards if card["id"] not in known_ids)
                        if family_complete:
                            try:
                                await ensure_reference_profiles(self.provider, family_cards)
                            except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError):
                                # Reference-profile hydration is an optional
                                # visual-cache operation. The local database
                                # family is still exhaustive when hydration is
                                # unavailable, and marking it incomplete here
                                # disables safe footer/art proofs for common
                                # names such as Mountain.
                                logger.warning(
                                    "Reference profile hydration unavailable for %s",
                                    family_name,
                                )
                except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError):
                    # The normal conservative path remains valid while offline.
                    family_complete = False
            observed_digits = re.sub(r"\D", "", number or "")
            if (
                identity_is_constrained
                and any(self.is_basic_land(card) for card in cards)
                and len(observed_digits) < 3
            ):
                # A short land numerator is commonly a clipped leading/trailing
                # digit (240 -> 24, 261 -> 26). Rectification makes the physical
                # footer rows stable, so one recognition-only pass is cheaper
                # than comparing hundreds of land profiles and supplies exact
                # digits for the independent artwork safety gate.
                basic_footer_text = await asyncio.to_thread(
                    # Preserve the original rectified luminance here. The
                    # low-light normalization that helps broad OCR can erase
                    # thin white legacy-footer digits (the fresh M13 Swamp
                    # reads 240/249 twice before normalization).
                    self.extract_fixed_footer_text,
                    corrected,
                )
                if basic_footer_text.strip():
                    _, footer_number, footer_set, footer_year = self.hints(
                        basic_footer_text
                    )
                    if footer_number:
                        number = footer_number
                    if footer_set and any(
                        self.exact_set_code_match(footer_set, card["set"])
                        for card in cards
                    ):
                        printed_set_code = footer_set
                    copyright_year = footer_year or copyright_year
                    text = "\n".join(
                        part for part in (text, basic_footer_text) if part.strip()
                    )
            if (
                identity_is_constrained
                and any(self.is_basic_land(card) for card in cards)
                and len(re.sub(r"\D", "", number or "")) < 3
                and not printed_set_code
            ):
                # A short isolated number on a legacy land is overwhelmingly a
                # clipped collector numerator, not authoritative printing
                # evidence. Let exhaustive art/artist ranking decide while the
                # card remains reviewable if no independent proof is decisive.
                number = None
            if (
                identity_is_constrained
                and not printed_set_code
                and len({card["set"].casefold() for card in cards}) > 1
            ):
                # Alphanumeric set logos such as M13 remain large and crisp in
                # the type line when the collector footer is unreadable. The
                # tight crop prevents rules text from confusing the detector.
                # Only admit an exact set already present in this constrained
                # card family, so arbitrary OCR cannot inject a candidate.
                symbol_text = await asyncio.to_thread(
                    self.extract_set_symbol_text, corrected
                )
                _, _, symbol_set, _ = self.hints(symbol_text)
                if not symbol_set or not any(
                    self.exact_set_code_match(symbol_set, card["set"]) for card in cards
                ):
                    symbol_set = self.partial_family_set_code(symbol_text, cards)
                matching_sets = {
                    card["set"].casefold()
                    for card in cards
                    if symbol_set
                    and self.exact_set_code_match(symbol_set, card["set"])
                }
                if len(matching_sets) == 1:
                    printed_set_code = next(iter(matching_sets))
                    text = "\n".join(
                        part for part in (text, symbol_text) if part.strip()
                    )
            repeated_exact_footer_ids: set[str] = set()
            if identity_is_constrained and family_complete:
                # Treat OCR fields as contradictory only when they plausibly
                # describe at least one member of the complete card family.
                # Rules/flavor text and damaged logos otherwise manufacture
                # values such as Wild Guess #8993 or DTK -> OTK, which match
                # no real printing and used to veto strong independent proof.
                number_family = cards
                if printed_set_code:
                    exact_set_family = [
                        card
                        for card in cards
                        if self.exact_set_code_match(printed_set_code, card["set"])
                    ]
                    if exact_set_family:
                        number_family = exact_set_family
                if number and not any(
                    self.collector_score(number, card["collector_number"]) >= 0.78
                    for card in number_family
                ):
                    number = None
                current_set_is_plausible = bool(
                    printed_set_code
                    and any(
                        self.set_code_score(printed_set_code, card["set"]) >= 0.78
                        for card in cards
                    )
                )
                if not current_set_is_plausible:
                    # Broad recovery OCR can replace a useful damaged set
                    # suffix (``FOORI``) with adjacent collector digits
                    # (``267722``), or a later merge can discard it entirely.
                    # Reparse the already-collected OCR text so family repair
                    # sees every retained camera token without another OCR pass.
                    combined_observed_set_code = self.hints(text)[2]
                    repaired_set_code = None
                    for observed_set_code in (
                        printed_set_code,
                        raw_observed_set_code,
                        combined_observed_set_code,
                    ):
                        repaired_set_code = self.repair_family_set_code(
                            observed_set_code, number, cards
                        )
                        if repaired_set_code:
                            break
                    if repaired_set_code:
                        printed_set_code = repaired_set_code
                        repaired_family_set_code = True
                existing_footer_ids = {
                    card["id"]
                    for card in cards
                    if number
                    and self.collector_score(number, card["collector_number"]) == 1.0
                    and (
                        (
                            printed_set_code
                            and self.exact_set_code_match(
                                printed_set_code, card["set"]
                            )
                        )
                        or (
                            copyright_year
                            and int(card.get("released_at", "0000")[:4])
                            == copyright_year
                        )
                    )
                }
                needs_independent_land_footer = bool(
                    len(existing_footer_ids) == 1
                    and number
                    and printed_set_code
                    and any(
                        card["id"] in existing_footer_ids and self.is_basic_land(card)
                        for card in cards
                    )
                )
                decoded_height, decoded_width = decoded.shape[:2]
                if (
                    (len(existing_footer_ids) != 1 or needs_independent_land_footer)
                    and decoded_height > decoded_width
                    and 0.68
                    <= decoded_width / max(1, decoded_height)
                    <= 0.75
                ):
                    # The identity is complete but exact-print evidence is not.
                    # Read only the untouched physical footer and accept it
                    # only when collector+set or collector+year intersects at
                    # exactly one family printing. This resolves normal/List
                    # twins without making image similarity an auto-add proof.
                    detected_footer_text = await asyncio.to_thread(
                        self.extract_raw_footer_band_text, decoded
                    )
                    _, detected_number, detected_set, detected_year = self.hints(
                        detected_footer_text
                    )
                    if not detected_set or not any(
                        self.exact_set_code_match(detected_set, card["set"])
                        for card in cards
                    ):
                        detected_set = self.family_set_code_from_footer_text(
                            detected_footer_text, cards, detected_number
                        )
                    detected_ids = {
                        card["id"]
                        for card in cards
                        if detected_number
                        and self.collector_score(
                            detected_number, card["collector_number"]
                        )
                        == 1.0
                        and (
                            (
                                detected_set
                                and self.exact_set_code_match(
                                    detected_set, card["set"]
                                )
                            )
                            or (
                                detected_year
                                and int(card.get("released_at", "0000")[:4])
                                == detected_year
                            )
                        )
                    }
                    if len(detected_ids) == 1:
                        if (
                            needs_independent_land_footer
                            and detected_ids == existing_footer_ids
                        ):
                            repeated_exact_footer_ids = detected_ids
                        number = detected_number
                        printed_set_code = detected_set or printed_set_code
                        copyright_year = detected_year or copyright_year
                        text = "\n".join(
                            part
                            for part in (text, detected_footer_text)
                            if part.strip()
                        )
                        # This detector reads the untouched camera footer at a
                        # larger scale than the initial fixed-row pass. Once its
                        # collector plus set/year intersection is unique inside
                        # the complete family, it is the stronger raw physical
                        # observation and may safely replace a disproven tiny-row
                        # guess (Hornet Sting 181 was initially read as 132).
                        raw_observed_number = detected_number
                        raw_observed_set_code = detected_set or raw_observed_set_code
                        raw_observed_text = "\n".join(
                            part
                            for part in (raw_observed_text, detected_footer_text)
                            if part.strip()
                        )
            family_complete_at = time.perf_counter()
            neural_vector = (
                neural_vector
                if neural_vector is not None
                else (await neural_task if neural_task is not None else None)
            )
            neural_matches = (
                await asyncio.to_thread(
                    neural.search_vector,
                    neural_vector,
                    10,
                    allowed_names=identity_names if identity_is_constrained else None,
                    ignored_source_ids=ignored_example_review_ids,
                )
                if neural_live
                else []
            )
            neural_lookup_complete = time.perf_counter()
            pre_descriptor_neural_margin = (
                neural_matches[0].similarity - neural_matches[1].similarity
                if len(neural_matches) > 1
                else (neural_matches[0].similarity if neural_matches else 0.0)
            )
            confirmed_footer_neural_id = (
                neural_matches[0].reference.scryfall_id
                if neural_matches
                and self.confirmed_camera_rerank_matches_footer(
                    source_kind=neural_matches[0].source_kind,
                    similarity=neural_matches[0].similarity,
                    margin=pre_descriptor_neural_margin,
                    observed_number=number,
                    candidate_number=neural_matches[0].reference.collector_number,
                    observed_set=printed_set_code,
                    candidate_set=neural_matches[0].reference.set_code,
                )
                else None
            )
            descriptor_search_complete = True
            if (
                identity_is_constrained
                and not confirmed_footer_neural_id
                and not repeated_exact_footer_ids
            ):
                # A shortlist is safe and valuable for basic lands only when
                # the observed collector number names at least one printing in
                # the complete local family.  Cards without a trustworthy
                # footer must retain exhaustive descriptor comparison: the
                # broader corpus contains several reprints whose exact artwork
                # is absent from the neural/hash shortlist.  Likewise, reject
                # impossible OCR numerators (for example 172 -> 740) instead of
                # letting them hide the genuine printing.
                basic_number_is_catalogued = bool(
                    number
                    and any(self.is_basic_land(card) for card in cards)
                    and any(
                        self.collector_score(number, card["collector_number"]) == 1.0
                        for card in cards
                    )
                )
                basic_land_identity = any(self.is_basic_land(card) for card in cards)
                exact_set_shortlist = {
                    card["id"]
                    for card in cards
                    if printed_set_code
                    and self.exact_set_code_match(printed_set_code, card["set"])
                }
                neural_land_shortlist = {
                    match.reference.scryfall_id
                    for match in neural_matches
                    if basic_land_identity
                    and not number
                    and not printed_set_code
                    and match.reference.scryfall_id in {card["id"] for card in cards}
                }
                # Scoring every regional hash for a basic land means walking
                # hundreds of near-identical printings. Once an exact catalogued
                # numerator exists, the adaptive artwork pass below supplies the
                # stronger evidence and retains its exhaustive fallback.
                identity_visual_matches = (
                    []
                    if exact_set_shortlist
                    or neural_land_shortlist
                    else await asyncio.to_thread(
                        self._identity_visual_matches,
                        scan_fingerprints,
                        identity_names,
                        box_set_code,
                    )
                )
                descriptor_shortlist = {
                    card["id"]
                    for card in cards
                    if self.collector_score(number, card["collector_number"]) == 1.0
                    and (
                        not printed_set_code
                        or self.exact_set_code_match(printed_set_code, card["set"])
                    )
                }
                allowed_descriptor_ids = (
                    descriptor_shortlist
                    if family_complete and basic_number_is_catalogued
                    else (
                        exact_set_shortlist
                        if family_complete and exact_set_shortlist
                        else (neural_land_shortlist or None)
                    )
                )
                descriptor_search_complete = allowed_descriptor_ids is None
                descriptor_evidence = await asyncio.to_thread(
                    self._descriptor_matches_with_art,
                    descriptor_image,
                    identity_names,
                    # Only explicit Box Mode may constrain the set. A plausible
                    # but wrong land footer must still be contradicted by art
                    # from another set before any automatic add is considered.
                    box_set_code,
                    ignored_example_review_ids,
                    allowed_descriptor_ids,
                    allowed_descriptor_ids is not None,
                )
                # A correct land numerator normally leaves fewer than a dozen
                # plausible profiles. If none supplies strong artwork evidence,
                # assume the tiny footer was misread and immediately retry the
                # complete family. This keeps the common path fast without
                # allowing a bad OCR digit to remove the genuine printing.
                if (
                    family_complete
                    and basic_number_is_catalogued
                    and (
                        not descriptor_evidence[1]
                        or descriptor_evidence[1][0][1] < 88
                    )
                ):
                    descriptor_evidence = await asyncio.to_thread(
                        self._descriptor_matches_with_art,
                        descriptor_image,
                        identity_names,
                        box_set_code,
                        ignored_example_review_ids,
                        None,
                        False,
                    )
                    descriptor_search_complete = True
                elif (
                    neural_land_shortlist
                    and (
                        not descriptor_evidence[1]
                        or not neural_matches
                        or descriptor_evidence[1][0][0].scryfall_id
                        != neural_matches[0].reference.scryfall_id
                        or descriptor_evidence[1][0][1] < 65
                    )
                ):
                    descriptor_evidence = await asyncio.to_thread(
                        self._descriptor_matches_with_art,
                        descriptor_image,
                        identity_names,
                        box_set_code,
                        ignored_example_review_ids,
                        None,
                        False,
                    )
                    descriptor_search_complete = True
                (
                    descriptor_matches,
                    descriptor_art_matches,
                    descriptor_symbol_matches,
                ) = descriptor_evidence
            else:
                # Regional ORB descriptors are excellent for separating known
                # printings of one established card name, but deliberately broad
                # pools (for example every printing numbered 105) contain many
                # unrelated layouts that can saturate the ratio score. Do not let
                # that identity-scoped reranker manufacture an identity when foil
                # glare hid the title. The exhaustive global fingerprints remain
                # available here and can conservatively recover the exact artwork.
                descriptor_matches = []
                descriptor_art_matches = []
                descriptor_symbol_matches = []
                identity_visual_matches = []
            descriptor_complete = time.perf_counter()
            known_card_ids = {card["id"] for card in cards}
            # The remote title lookup is intentionally bounded and may return
            # only the newest page of a name with hundreds of printings (basic
            # lands are the important case). Local identity-scoped matching can
            # find an older exact artwork/footer, so promote those references
            # into the ranking pool instead of merely attaching a score to a
            # card the pool does not contain.
            local_matches = [
                *descriptor_matches,
                *descriptor_art_matches,
                *descriptor_symbol_matches,
                *identity_visual_matches,
            ]
            for reference, _score in local_matches:
                if reference.scryfall_id in known_card_ids:
                    continue
                local_card = self._reference_card(reference)
                local_card["lang"] = language
                cards.append(local_card)
                known_card_ids.add(reference.scryfall_id)
            candidate_expansion_complete = time.perf_counter()
            if neural_matches:
                neural_match_margin = (
                    neural_matches[0].similarity - neural_matches[1].similarity
                    if len(neural_matches) > 1
                    else neural_matches[0].similarity
                )
                neural_match_is_safe = bool(
                    neural_matches[0].similarity >= 0.70 and neural_match_margin >= 0.06
                )
                logger.info(
                    "Neural shadow top=%s similarity=%.4f source=%s candidates=%d",
                    neural_matches[0].reference.scryfall_id,
                    neural_matches[0].similarity,
                    neural_matches[0].source_kind,
                    len(neural_matches),
                )
                if identity_is_constrained or neural_match_is_safe:
                    admitted_matches = (
                        neural_matches if identity_is_constrained else neural_matches[:1]
                    )
                    for match in admitted_matches:
                        if match.reference.scryfall_id in known_card_ids:
                            continue
                        local_card = self._reference_card(match.reference)
                        local_card["lang"] = language
                        cards.append(local_card)
                        known_card_ids.add(match.reference.scryfall_id)
            matching_complete = time.perf_counter()
        fingerprint_visual_scores = {
            reference.scryfall_id: score for reference, score in visual_matches
        }
        for reference, score in identity_visual_matches:
            fingerprint_visual_scores[reference.scryfall_id] = max(
                score, fingerprint_visual_scores.get(reference.scryfall_id, 0)
            )
        visual_scores = dict(fingerprint_visual_scores)
        for reference, score in descriptor_matches:
            visual_scores[reference.scryfall_id] = max(
                score, visual_scores.get(reference.scryfall_id, 0)
            )
        ranked_visual_scores = sorted(
            fingerprint_visual_scores.items(), key=lambda item: item[1], reverse=True
        )
        visual_top_id = ranked_visual_scores[0][0] if ranked_visual_scores else None
        visual_top_score = ranked_visual_scores[0][1] if ranked_visual_scores else 0.0
        visual_margin = (
            ranked_visual_scores[0][1] - ranked_visual_scores[1][1]
            if len(ranked_visual_scores) > 1
            else visual_top_score
        )
        descriptor_scores = {
            reference.scryfall_id: score for reference, score in descriptor_matches
        }
        descriptor_art_scores = {
            reference.scryfall_id: score for reference, score in descriptor_art_matches
        }
        descriptor_art_top_id = (
            descriptor_art_matches[0][0].scryfall_id if descriptor_art_matches else None
        )
        descriptor_art_margin = (
            descriptor_art_matches[0][1] - descriptor_art_matches[1][1]
            if len(descriptor_art_matches) > 1
            else (descriptor_art_matches[0][1] if descriptor_art_matches else 0)
        )
        number_art_matches = [
            (reference.scryfall_id, score)
            for reference, score in descriptor_art_matches
            if number and self.collector_score(number, reference.collector_number) == 1.0
        ]
        number_art_top_id = number_art_matches[0][0] if number_art_matches else None
        number_art_score = number_art_matches[0][1] if number_art_matches else 0
        number_art_margin = (
            number_art_matches[0][1] - number_art_matches[1][1]
            if len(number_art_matches) > 1
            else number_art_score
        )
        descriptor_symbol_scores = {
            reference.scryfall_id: score for reference, score in descriptor_symbol_matches
        }
        descriptor_symbol_top_id = (
            descriptor_symbol_matches[0][0].scryfall_id if descriptor_symbol_matches else None
        )
        descriptor_symbol_margin = (
            descriptor_symbol_matches[0][1] - descriptor_symbol_matches[1][1]
            if len(descriptor_symbol_matches) > 1
            else (descriptor_symbol_matches[0][1] if descriptor_symbol_matches else 0)
        )
        symbol_scores_by_set: dict[str, float] = {}
        for reference, score in descriptor_symbol_matches:
            code = reference.set_code.casefold()
            symbol_scores_by_set[code] = max(score, symbol_scores_by_set.get(code, 0))
        ranked_symbol_sets = sorted(
            symbol_scores_by_set.items(), key=lambda item: item[1], reverse=True
        )
        descriptor_symbol_top_set = ranked_symbol_sets[0][0] if ranked_symbol_sets else None
        descriptor_symbol_set_score = ranked_symbol_sets[0][1] if ranked_symbol_sets else 0
        descriptor_symbol_set_margin = (
            ranked_symbol_sets[0][1] - ranked_symbol_sets[1][1]
            if len(ranked_symbol_sets) > 1
            else descriptor_symbol_set_score
        )
        descriptor_top_id = descriptor_matches[0][0].scryfall_id if descriptor_matches else None
        descriptor_margin = (
            descriptor_matches[0][1] - descriptor_matches[1][1]
            if len(descriptor_matches) > 1
            else (descriptor_matches[0][1] if descriptor_matches else 0)
        )
        candidate_descriptor_catalog_complete = self._descriptor_catalog_complete(
            {card["id"] for card in cards}
        )
        descriptor_catalog_complete = (
            family_complete
            and candidate_descriptor_catalog_complete
            and descriptor_search_complete
        )
        neural_scores = {match.reference.scryfall_id: match.similarity for match in neural_matches}
        neural_top_id = neural_matches[0].reference.scryfall_id if neural_matches else None
        neural_top_score = neural_matches[0].similarity if neural_matches else 0.0
        neural_margin = (
            neural_matches[0].similarity - neural_matches[1].similarity
            if len(neural_matches) > 1
            else neural_top_score
        )
        ranked: dict[str, Candidate] = {}
        safe_candidate_ids: set[str] = set()
        release_years = [int(card.get("released_at", "0000")[:4]) for card in cards]
        release_year_counts = Counter(release_years)
        number_scores = [self.collector_score(number, card["collector_number"]) for card in cards]
        # Ranking used to rebuild the complete competing-score list for every
        # candidate. Large printing families (especially basic lands) made that
        # O(n²) and could spend several seconds repeating the same comparisons.
        # The uniqueness rule only needs the best *other* score, so derive it
        # once while preserving the exact same margin decision below.
        ranked_number_scores = sorted(number_scores, reverse=True)
        best_number_score = ranked_number_scores[0] if ranked_number_scores else None
        best_number_score_count = (
            number_scores.count(best_number_score) if best_number_score is not None else 0
        )
        second_number_score = ranked_number_scores[1] if len(ranked_number_scores) > 1 else None
        exact_set_counts: dict[str, int] = {}
        set_art_evidence: dict[str, tuple[str | None, float, float]] = {}
        for card in cards:
            code = card["set"].casefold()
            if code in set_art_evidence:
                exact_set_counts[code] = exact_set_counts.get(code, 0) + 1
                continue
            same_set_art = [
                (reference.scryfall_id, score)
                for reference, score in descriptor_art_matches
                if reference.set_code.casefold() == code
            ]
            set_art_top_id = same_set_art[0][0] if same_set_art else None
            set_art_score = same_set_art[0][1] if same_set_art else 0
            set_art_margin = (
                same_set_art[0][1] - same_set_art[1][1] if len(same_set_art) > 1 else set_art_score
            )
            set_art_evidence[code] = (set_art_top_id, set_art_score, set_art_margin)
            exact_set_counts[code] = exact_set_counts.get(code, 0) + 1
        exact_number_year_ids = {
            candidate["id"]
            for candidate in cards
            if number
            and copyright_year
            and self.collector_score(number, candidate["collector_number"]) == 1.0
            and int(candidate.get("released_at", "0000")[:4]) == copyright_year
        }
        # Reprints frequently share the same artist. Fuzzy footer matching is
        # substantially more expensive than a dictionary lookup, so score each
        # distinct printed credit once instead of once per printing. When exact
        # collector and copyright-year evidence already isolates one member of
        # a complete family, artist OCR cannot change the proof or ordering and
        # scanning hundreds of land artists only adds several seconds.
        artist_score_by_name = (
            {artist: 0.0 for artist in {card.get("artist") for card in cards}}
            if identity_is_constrained
            and family_complete
            and len(exact_number_year_ids) == 1
            else {
                artist: self.artist_text_score(text, artist)
                for artist in {card.get("artist") for card in cards}
            }
        )
        artist_scores = {
            card["id"]: artist_score_by_name[card.get("artist")] for card in cards
        }
        strong_artist_ids = {card_id for card_id, score in artist_scores.items() if score >= 0.9}
        artist_supported_neural_id = (
            max(strong_artist_ids, key=lambda card_id: neural_scores.get(card_id, 0.0))
            if strong_artist_ids
            else None
        )
        structured_printing_ids = self.unique_number_year_artist_ids(
            cards, number, copyright_year, artist_scores
        )
        for card in cards:
            set_art_top_id, set_art_score, set_art_margin = set_art_evidence.get(
                card["set"].casefold(), (None, 0.0, 0.0)
            )
            title_score = self.card_name_similarity(title, card["name"]) if title else 0.55
            number_score = self.collector_score(number, card["collector_number"])
            set_score = self.set_code_score(printed_set_code, card["set"])
            footer_contradiction = self.observed_footer_contradicts_printing(
                number,
                printed_set_code,
                card["collector_number"],
                card["set"],
            )
            released_year = int(card.get("released_at", "0000")[:4])
            year_score = 1.0 if copyright_year and released_year == copyright_year else 0.0
            if printed_set_code:
                ocr_score = (
                    title_score * 0.45
                    + number_score * 0.25
                    + set_score * 0.2
                    + (year_score if copyright_year else 0.5) * 0.1
                ) * 100
            elif copyright_year:
                ocr_score = (title_score * 0.58 + number_score * 0.27 + year_score * 0.15) * 100
            else:
                ocr_score = (title_score * 0.66 + number_score * 0.34) * 100
            visual_score = visual_scores.get(card["id"])
            confidence = (
                ocr_score * 0.62 + visual_score * 0.38
                if visual_score is not None
                else ocr_score * (0.995 if printed_set_code else 0.92)
            )
            if (
                not printed_set_code
                and box_set_code
                and card["set"].lower() == box_set_code.lower()
            ):
                confidence += 3
            # Exact collector+set evidence identifies a printing even when OCR
            # misspells one title character. A one-character loss in both footer
            # fields is also near-certain when the copyright year independently
            # agrees. Neither path allows name/artwork similarity alone to add.
            confidence = self.structured_confidence(
                confidence, title_score, number_score, set_score, year_score
            )
            exact_printed_identity = self.has_exact_printing_identity(
                # Exact set code + collector number is globally printing
                # specific. Permit a partial but recognizable title (for
                # example OCR reading only "Whirler" from Whirler Rogue), while
                # still rejecting unrelated footer noise.
                title_score,
                number,
                printed_set_code,
                number_score,
                set_score,
            )
            # An exact title with one known printing is an exact-printing match.
            # A unique matching copyright year can distinguish reused artwork.
            only_printing = len(cards) == 1
            unique_release_year = bool(
                copyright_year
                and year_score == 1.0
                and release_year_counts[copyright_year] == 1
            )
            copyright_art_printing_proof = False
            single_printing_identity_proof = bool(
                only_printing
                and self.has_safe_single_printing_identity(
                    is_basic_land=self.is_basic_land(card),
                    identity_is_constrained=identity_is_constrained,
                    family_complete=family_complete,
                    observed_title=title,
                    candidate_name=card["name"],
                )
            )
            if single_printing_identity_proof:
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            if title_score >= 0.93 and (only_printing or unique_release_year):
                confidence = max(confidence, 98.5)
                if number_score == 1.0 and unique_release_year:
                    safe_candidate_ids.add(card["id"])
                copyright_art_printing_proof = bool(
                    unique_release_year
                    and card["id"] == descriptor_art_top_id
                    and descriptor_art_scores.get(card["id"], 0) >= 90
                    and artist_scores.get(card["id"], 0) >= 0.9
                )
                if copyright_art_printing_proof:
                    # Older frames frequently defeat collector-number OCR. An
                    # exact title plus a unique copyright/release year, printed
                    # artist credit, and the strongest high-quality artwork
                    # match are independent proof of the physical printing.
                    safe_candidate_ids.add(card["id"])
                if unique_release_year and artist_scores.get(card["id"], 0) >= 0.9:
                    # The copyright year is printing-specific when exactly one
                    # member of the complete card family was released that
                    # year. A matching printed artist credit anchors that tiny
                    # footer observation to this card instead of unrelated OCR
                    # noise, even when reused artwork defeats descriptor order.
                    safe_candidate_ids.add(card["id"])
            # Footer OCR sometimes loses a leading digit (123/272 -> 23/272)
            # while retaining enough evidence to distinguish every printing of
            # an exactly-read card name. Accept it only when one candidate has a
            # strong collector-number match with a clear margin over all others.
            if best_number_score is None:
                competing_number_scores = []
            elif number_score == best_number_score and best_number_score_count == 1:
                competing_number_scores = (
                    [second_number_score] if second_number_score is not None else []
                )
            else:
                competing_number_scores = [best_number_score]
            printing_signal = self.has_unique_printing_signal(
                title_score,
                number,
                number_score,
                competing_number_scores,
                printed_set_code,
                card["set"],
                exact_set_counts[card["set"].casefold()],
            )
            if printing_signal:
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            number_scoped_art_proof = bool(
                identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and number_score == 1.0
                and card["id"] == number_art_top_id
                and number_art_score
                >= (
                    65
                    if self.is_basic_land(card) and set_score == 1.0
                    else (84 if self.is_basic_land(card) else 88)
                )
                and number_art_margin >= 12
            )
            repaired_set_art_proof = bool(
                repaired_family_set_code
                and identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and number_score == 1.0
                and set_score == 1.0
                and card["id"] == set_art_top_id
                and set_art_score >= 65
                and set_art_margin >= 12
            )
            exact_set_art_proof = bool(
                identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and printed_set_code
                and self.exact_set_code_match(printed_set_code, card["set"])
                and card["id"] == set_art_top_id
                and set_art_score >= 65
                and set_art_margin >= 12
            )
            if number_scoped_art_proof:
                # Collector number and artwork are independent physical fields.
                # Restricting the comparison to the exact-number subset avoids
                # unrelated same-art reprints diluting a decisive printing win.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            if repaired_set_art_proof or exact_set_art_proof:
                # A damaged footer prefix (``FOORI``) can retain an exact real
                # set suffix.  For lands, accept that recovery only when the
                # exact collector number and the exhaustive within-set artwork
                # comparison independently select the same illustration.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            repeated_physical_footer_proof = bool(
                identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and card["id"] in repeated_exact_footer_ids
                and number_score == 1.0
                and set_score == 1.0
            )
            if repeated_physical_footer_proof:
                # Two independent OCR geometries—the rectified identity crop
                # and untouched camera footer—read the same globally unique
                # set/collector pair. This is safer than trusting a single land
                # digit and avoids an exhaustive artwork sweep when foil glare
                # makes canonical descriptors unreliable.
                confidence = max(confidence, 99.5)
                safe_candidate_ids.add(card["id"])
            repeated_unique_year_proof = bool(
                identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and unique_release_year
                and copyright_year
                and len(
                    re.findall(
                        rf"(?<!\d){copyright_year}(?!\d)",
                        unicodedata.normalize("NFKC", text),
                    )
                )
                >= 2
            )
            if repeated_unique_year_proof:
                # Recognition-only footer rows independently read the same full
                # copyright year twice; when only one family printing has that
                # year, this is printing-specific without relying on artwork.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            descriptor_score = descriptor_scores.get(card["id"], 0)
            descriptor_symbol_score = descriptor_symbol_scores.get(card["id"], 0)
            normal_list_twin_proof = False
            if len(neural_matches) >= 2 and card["id"] == neural_top_id:
                normal_match, list_match = neural_matches[:2]
                normal_number = normal_match.reference.collector_number.casefold()
                list_number = list_match.reference.collector_number.casefold()
                normal_list_twin_proof = bool(
                    normal_match.source_kind in {"canonical", "alternate"}
                    and list_match.source_kind in {"canonical", "alternate"}
                    and normal_match.reference.set_code.casefold() != "plst"
                    and list_match.reference.set_code.casefold() == "plst"
                    and self.normalized_name(normal_match.reference.name)
                    == self.normalized_name(list_match.reference.name)
                    and normal_match.similarity >= 0.78
                    and list_match.similarity >= 0.78
                    and normal_match.similarity - list_match.similarity >= 0.0005
                    and list_number.endswith(
                        f"{normal_match.reference.set_code.casefold()}-{normal_number}"
                    )
                    and descriptor_scores.get(normal_match.reference.scryfall_id, 0) >= 95
                    and descriptor_scores.get(list_match.reference.scryfall_id, 0) >= 95
                    and abs(
                        descriptor_scores.get(normal_match.reference.scryfall_id, 0)
                        - descriptor_scores.get(list_match.reference.scryfall_id, 0)
                    )
                    <= 1
                )
            frame_footer_visual_proof = bool(
                self.is_basic_land(card)
                and family_complete
                and card["id"] == descriptor_top_id == visual_top_id
                and descriptor_score >= 99
                and descriptor_margin >= 10
                and visual_scores.get(card["id"], 0) >= 75
            )
            if normal_list_twin_proof or frame_footer_visual_proof:
                # The List copy is distinguished by its physical lower-left
                # stamp; canonical neural images retain that mark even though
                # art/full ORB features otherwise tie. For lands, a decisive
                # full footer/frame match plus the independent frame hash may
                # veto one damaged OCR digit.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            visual_printing_proof = bool(
                not self.is_basic_land(card)
                and title_score >= 0.72
                and descriptor_catalog_complete
                and card["id"] == descriptor_top_id
                and descriptor_score >= 88
                and descriptor_margin >= 18
            )
            if visual_printing_proof:
                # The full-card descriptor contains artwork plus footer and set
                # symbol regions. A large exhaustive-family margin proves the
                # physical printing even when rules-text recovery found its name.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            title_art_symbol_proof = self.has_safe_title_art_symbol_match(
                card["id"],
                title_score,
                candidate_descriptor_catalog_complete,
                card["set"],
                descriptor_symbol_top_set,
                descriptor_symbol_set_score,
                descriptor_symbol_set_margin,
                set_art_top_id,
                set_art_score,
                set_art_margin,
            )
            if title_art_symbol_proof:
                # When the footer is outside the camera crop, an exact title,
                # a decisive set-symbol vote, and a unique artwork win within
                # that set are independent proof of the physical printing.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            if self.has_decisive_symbol_match(
                card["id"],
                descriptor_symbol_top_id,
                descriptor_symbol_score,
                descriptor_symbol_margin,
            ):
                # A clear set symbol can retrieve and prioritize the right set
                # even when glare leaves too few artwork keypoints. It remains
                # below auto-add unless decisive artwork independently agrees.
                confidence = max(confidence, 96.5)
            if (
                descriptor_symbol_top_set
                and card["set"].casefold() == descriptor_symbol_top_set
                and descriptor_symbol_set_score >= 65
                and descriptor_symbol_set_margin >= 8
            ):
                # A set symbol identifies the set, not one arbitrary card row
                # within it. Apply its reranking evidence to every candidate in
                # that set; exact artwork/collector evidence still chooses the
                # printing and the automatic-add gate remains unchanged.
                confidence = max(confidence, 96.5)
            if (
                card["id"] == descriptor_top_id
                and title_score >= 0.93
                and descriptor_score >= 75
                and descriptor_margin >= 10
            ):
                # Unique exact-art evidence is strong enough to put the right
                # printing first immediately. It remains below auto-add until
                # footer/set evidence agrees, because the exhaustive catalog
                # may still be syncing and identical art can be reprinted.
                confidence = max(confidence, 97.0)
                if (
                    descriptor_catalog_complete
                    and descriptor_score >= 80
                    and descriptor_margin >= 12
                ):
                    # Once every known printing of this exact card identity has
                    # a local descriptor, a decisive unique-art margin is
                    # printing-specific evidence. Reused art naturally fails
                    # the margin and remains an immediate user decision.
                    confidence = max(confidence, 98.5)
            artist_score = artist_scores.get(card["id"], 0.0)
            if artist_score >= 0.9:
                # Printed artist credit is stronger than one tiny, ambiguous
                # collector digit. It selects the correct artwork while staying
                # below auto-add unless other independent evidence agrees.
                confidence = max(confidence, 97.8 if len(strong_artist_ids) == 1 else 96.0)
            elif strong_artist_ids and card.get("artist"):
                confidence = min(confidence, 86.0)
            exact_footer_artist_proof = self.has_exact_footer_artist_proof(
                is_basic_land=self.is_basic_land(card),
                number_score=number_score,
                artist_score=artist_score,
                strong_artist_count=len(strong_artist_ids),
            )
            if exact_footer_artist_proof:
                # Candidate families have already been constrained by the card
                # identity. Within that family, an exact collector number plus
                # one uniquely matching printed artist proves a non-basic
                # printing even when glare damages the tiny set-code glyph.
                # A wrong collector number (for example the known Singing Bell
                # Strike #55 scan OCR'd as #65) cannot pass this gate, and basic
                # lands retain their stricter artwork-specific safeguards.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            structured_printing_proof = bool(
                identity_is_constrained
                and family_complete
                and title_score >= 0.93
                and structured_printing_ids == {card["id"]}
            )
            if structured_printing_proof:
                # Collector number, printed copyright/release year, and artist
                # are three independent physical fields. Requiring their
                # intersection to be unique across the complete printing
                # family safely resolves core-set lands even when the set logo
                # itself is not transcribed.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            exact_number_year_proof = bool(
                identity_is_constrained
                and family_complete
                and (
                    title_score >= 0.93
                    or (
                        consensus_name
                        and self.card_name_similarity(
                            consensus_name, card["name"]
                        )
                        >= 0.99
                    )
                )
                and exact_number_year_ids == {card["id"]}
            )
            if exact_number_year_proof:
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            neural_score = neural_scores.get(card["id"], 0.0)
            footer_contradiction = self.observed_footer_contradicts_printing(
                number,
                printed_set_code,
                card["collector_number"],
                card["set"],
            )
            confirmed_camera_is_safe = self.confirmed_camera_printing_is_safe(
                source_kind=(
                    neural_matches[0].source_kind
                    if neural_matches and card["id"] == neural_top_id
                    else None
                ),
                similarity=neural_score,
                identity_is_constrained=identity_is_constrained,
                family_complete=family_complete,
                title_score=title_score,
                footer_contradiction=footer_contradiction,
            )
            neural_is_safe = self.neural_printing_is_safe(
                shadow_mode=get_settings().neural_shadow_mode,
                candidate_id=card["id"],
                neural_top_id=neural_top_id,
                neural_top_score=neural_top_score,
                neural_margin=neural_margin,
                # Embeddings identify artwork very well, but reprints can reuse
                # that artwork. Exact-printing auto-add therefore also requires
                # independent footer or exhaustive descriptor corroboration.
                independently_corroborated=bool(
                    exact_printed_identity
                    or printing_signal
                    or visual_printing_proof
                    or copyright_art_printing_proof
                ),
            )
            recovered_exact_footer_proof = self.has_safe_recovered_exact_footer(
                is_basic_land=self.is_basic_land(card),
                identity_is_constrained=identity_is_constrained,
                family_complete=family_complete,
                number_score=number_score,
                set_score=set_score,
                candidate_name=card["name"],
                neural_top_name=(
                    neural_matches[0].reference.name if neural_matches else None
                ),
                neural_score=neural_top_score,
                neural_margin=neural_margin,
                footer_contradiction=footer_contradiction,
            )
            recovered_exact_footer_proof = recovered_exact_footer_proof or bool(
                not self.is_basic_land(card)
                and exact_footer_card
                and card["id"] == exact_footer_card["id"]
                and not footer_contradiction
            )
            recovered_exact_footer_proof = recovered_exact_footer_proof or bool(
                not self.is_basic_land(card)
                and title_score >= 0.93
                and number_score >= 0.78
                and year_score == 1.0
                and artist_score >= 0.9
                and card["id"] == neural_top_id
                and neural_top_score >= 0.80
                and neural_margin >= 0.04
                and not footer_contradiction
            )
            if recovered_exact_footer_proof:
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            neural_is_decisive_rerank = bool(
                identity_is_constrained
                and card["id"] == neural_top_id
                and neural_score >= 0.45
                and neural_margin >= 0.04
            )
            neural_visual_rank_agreement = bool(
                identity_is_constrained
                and card["id"] == neural_top_id
                and neural_score >= 0.45
                and (
                    (
                        card["id"] == descriptor_art_top_id
                        and descriptor_art_scores.get(card["id"], 0) >= 65
                    )
                    or (
                        card["id"] == visual_top_id
                        and visual_scores.get(card["id"], 0) >= 55
                    )
                )
            )
            if neural_visual_rank_agreement:
                # Two image comparators may rank an uncertain exact printing,
                # but they are not independent physical proof. Put their shared
                # winner first while retaining Review below the auto-add gate.
                confidence = max(confidence, 97.8)
            if card["id"] == neural_top_id and (neural_score >= 0.62 or neural_is_decisive_rerank):
                # Neural evidence is first used as an identity-scoped reranker.
                # It cannot auto-add unless it also clears the independently
                # benchmarked zero-error threshold below.
                confidence = max(confidence, 98.4 if neural_is_decisive_rerank else 97.8)
            if card["id"] == artist_supported_neural_id and artist_score >= 0.9:
                # When the footer clearly identifies an artist shared by a few
                # reprints, use neural evidence only to order those supported
                # printings. This remains below automatic-add confidence.
                confidence = max(confidence, 97.0)
            if neural_is_safe or confirmed_camera_is_safe:
                confidence = 99.5
                safe_candidate_ids.add(card["id"])
            elif (
                not (
                    recovered_exact_footer_proof
                    or number_scoped_art_proof
                    or repaired_set_art_proof
                    or exact_set_art_proof
                    or normal_list_twin_proof
                    or frame_footer_visual_proof
                    or exact_number_year_proof
                    or repeated_unique_year_proof
                )
                and not get_settings().neural_shadow_mode
                and neural_top_score >= 0.70
                and neural_margin >= 0.06
            ):
                # A benchmark-safe exact match must also win the candidate
                # ordering. OCR can still display its conflicting interpretation
                # in Review, but cannot outrank the verified image match.
                confidence = min(confidence, 98.4)
            # A readable title/footer is not enough to distinguish basic-land
            # artwork.  Sets routinely contain several Plains/Island/Swamp/
            # Mountain/Forest printings whose only meaningful difference is
            # the illustration and collector number.  A single OCR digit can
            # therefore produce a very confident, but wrong, exact printing.
            # Keep these below the automatic-add threshold unless the local
            # exhaustive visual catalogue independently agrees with a clear
            # margin.  They remain first-class suggestions for quick review.
            safe_land_match = False
            if self.is_basic_land(card):
                repeated_footer_number = self.has_repeated_footer_printing_evidence(
                    text,
                    card,
                    cards,
                    printed_set_code,
                )
                unique_set_artist = self.has_unique_set_artist_evidence(
                    text,
                    card,
                    cards,
                    printed_set_code,
                    artist_score,
                )
                safe_land_match = self.has_safe_basic_land_match(
                    card["id"],
                    descriptor_art_top_id,
                    # Safety depends on complete coverage of this card identity,
                    # not unrelated entries in the global visual catalog.
                    descriptor_catalog_complete,
                    descriptor_art_scores.get(card["id"], 0),
                    descriptor_art_margin,
                    number,
                    printed_set_code,
                    number_score,
                    set_score,
                    descriptor_symbol_top_id,
                    descriptor_symbol_scores.get(card["id"], 0),
                    descriptor_symbol_margin,
                    card["set"],
                    descriptor_symbol_top_set,
                    descriptor_symbol_set_score,
                    descriptor_symbol_set_margin,
                    set_art_top_id,
                    set_art_score,
                    set_art_margin,
                    artist_score,
                    descriptor_catalog_complete,
                    year_score == 1.0,
                )
                if (
                    safe_land_match
                    or repeated_footer_number
                    or unique_set_artist
                    or neural_is_safe
                    or structured_printing_proof
                    or number_scoped_art_proof
                    or repaired_set_art_proof
                    or exact_set_art_proof
                    or repeated_physical_footer_proof
                    or frame_footer_visual_proof
                    or exact_number_year_proof
                ):
                    # Exact set plus a decisive illustration match is safer
                    # than a tiny collector-number crop. This specifically
                    # prevents a misread 264 as 261 from selecting the wrong art.
                    confidence = max(confidence, 98.5)
                    safe_candidate_ids.add(card["id"])
                else:
                    confidence = min(confidence, 98.4)
            elif exact_printed_identity:
                # The footer pair identifies one physical printing globally.
                # This branch was already declared safe, but a partial title
                # could leave its displayed score at 97.8 and the API therefore
                # sent it to Review anyway. Keep basic lands on their stricter
                # artwork path above; for other cards make score and safety agree.
                confidence = max(confidence, 98.5)
                safe_candidate_ids.add(card["id"])
            if strong_artist_ids and artist_score < 0.9 and card.get("artist"):
                # Apply the physical artist contradiction after neural boosts;
                # otherwise an attractive but visibly different printing can
                # overwrite the earlier penalty and lead the Review list.
                confidence = min(confidence, 86.0)
                safe_candidate_ids.discard(card["id"])
            # Reused artwork can make a promo descriptor look nearly perfect.
            # The physical footer remains authoritative for exact-printing
            # identity, so a contradicted candidate must neither lead nor add.
            if footer_contradiction and not frame_footer_visual_proof:
                confidence = min(confidence, 90.0)
                safe_candidate_ids.discard(card["id"])
            confidence = min(99.5, confidence)
            if self.oracle_recovery_requires_cap(
                oracle_recovery,
                exact_printed_identity or recovered_exact_footer_proof,
                # The basic-land gate already requires complete-catalog,
                # printing-specific artwork plus independent footer, set,
                # symbol, or artist evidence. A later generic oracle cap must
                # not undo that stricter proof merely because OCR recovered the
                # identity from an artist/rules fragment instead of the title.
                printing_signal
                or safe_land_match
                or number_scoped_art_proof
                or repaired_set_art_proof
                or exact_set_art_proof
                or normal_list_twin_proof
                or frame_footer_visual_proof
                or exact_number_year_proof
                or repeated_unique_year_proof,
                visual_printing_proof,
                title_art_symbol_proof,
            ):
                # Rules text can identify a card, but it cannot prove which set,
                # collector number, or finish is physically present. Independent
                # collector/set footer evidence may still prove the printing.
                oracle_cap = self.oracle_printing_cap(
                    title_score,
                    number,
                    number_score,
                    promo_type,
                    card,
                )
                if oracle_cap > 89:
                    confidence = max(confidence, oracle_cap)
                confidence = min(oracle_cap, confidence)
            ranked[card["id"]] = Candidate(
                scryfall_id=card["id"],
                name=card["name"],
                set_code=card["set"],
                set_name=card["set_name"],
                collector_number=card["collector_number"],
                image_url=self.provider.image_url(card),
                market_price=self.provider.market_price(card),
                foil_market_price=self.provider.market_price(card, foil=True),
                finishes=card.get("finishes", []),
                language=card.get("lang", "en"),
                confidence=round(confidence, 1),
                oracle_id=card.get("oracle_id"),
                color_identity="".join(card.get("color_identity", [])),
                rarity=card.get("rarity"),
                type_line=card.get("type_line"),
            )
        for reference, score in visual_matches:
            existing = ranked.get(reference.scryfall_id)
            if existing:
                continue
            # When OCR already established a card identity, visual evidence may
            # distinguish its printings but must not inject unrelated cards that
            # happen to have a similar low-resolution perceptual hash.
            if not self.should_admit_visual_candidate(
                reference.name, cards, identity_is_constrained
            ):
                continue
            ranked[reference.scryfall_id] = Candidate(
                scryfall_id=reference.scryfall_id,
                name=reference.name,
                set_code=reference.set_code,
                set_name=reference.set_name,
                collector_number=reference.collector_number,
                image_url=reference.image_url,
                market_price=reference.market_price,
                confidence=round(self.visual_only_score(score), 1),
            )
        candidates = sorted(ranked.values(), key=lambda item: item.confidence, reverse=True)
        if neural_matches and neural_matches[0].source_kind == "correction":
            camera_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.scryfall_id == neural_top_id
                ),
                None,
            )
            if camera_candidate and self.confirmed_camera_rerank_matches_footer(
                source_kind=neural_matches[0].source_kind,
                similarity=neural_top_score,
                margin=neural_margin,
                observed_number=number,
                candidate_number=camera_candidate.collector_number,
                observed_set=printed_set_code,
                candidate_set=camera_candidate.set_code,
            ):
                camera_candidate.confidence = max(camera_candidate.confidence, 98.4)
                candidates.sort(key=lambda item: item.confidence, reverse=True)
        if neural_matches:
            neural_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.scryfall_id == neural_top_id
                ),
                None,
            )
            rank_only_visual_agreement = bool(
                neural_candidate
                and neural_top_score >= 0.45
                and (
                    (
                        neural_top_id == descriptor_art_top_id
                        and descriptor_art_scores.get(neural_top_id, 0) >= 65
                    )
                    or (
                        neural_top_id == visual_top_id
                        and visual_scores.get(neural_top_id, 0) >= 55
                    )
                )
            )
            if rank_only_visual_agreement and candidates[0] is not neural_candidate:
                # Oracle recovery caps intentionally flatten uncertain reprints.
                # Preserve that Review disposition while breaking the resulting
                # tie with the printing selected by two visual comparators.
                neural_candidate.confidence = min(
                    98.4, max(neural_candidate.confidence, candidates[0].confidence + 0.1)
                )
                candidates.sort(key=lambda item: item.confidence, reverse=True)
            if (
                neural_candidate
                and identity_is_constrained
                and family_complete
                and self.card_name_similarity(merged_title, neural_candidate.name) >= 0.93
                and self.neural_rerank_without_footer(
                    source_kind=neural_matches[0].source_kind,
                    similarity=neural_top_score,
                    margin=neural_margin,
                    observed_number=number,
                    observed_set=printed_set_code,
                )
            ):
                neural_candidate.confidence = max(neural_candidate.confidence, 98.4)
                candidates.sort(key=lambda item: item.confidence, reverse=True)
            elif (
                neural_candidate
                and identity_is_constrained
                and family_complete
                and self.card_name_similarity(merged_title, neural_candidate.name) >= 0.93
                and neural_top_score >= 0.70
                and neural_margin >= 0.02
                and number
                and self.collector_score(
                    number, neural_candidate.collector_number
                )
                == 1.0
                and not self.observed_footer_contradicts_printing(
                    number,
                    printed_set_code,
                    neural_candidate.collector_number,
                    neural_candidate.set_code,
                )
            ):
                # Put independent neural evidence ahead of database tie order;
                # the verifier below still decides whether footer consensus is
                # sufficient for an automatic add.
                neural_candidate.confidence = max(neural_candidate.confidence, 98.4)
                candidates.sort(key=lambda item: item.confidence, reverse=True)
        if candidates:
            top = candidates[0]
            top_card = next(
                (card for card in cards if card["id"] == top.scryfall_id), None
            )
            if top_card and self.printing_verifiers_agree(
                identity_is_constrained=identity_is_constrained,
                family_complete=family_complete,
                title_score=self.card_name_similarity(merged_title, top.name),
                candidate_id=top.scryfall_id,
                neural_top_id=neural_top_id,
                neural_margin=neural_margin,
                collector_number_exact=bool(
                    number
                    and self.collector_score(number, top.collector_number) == 1.0
                ),
                is_basic_land=self.is_basic_land(top_card),
                has_observed_footer_identity=bool(
                    number
                    or self.exact_set_code_match(printed_set_code, top.set_code)
                ),
                footer_contradiction=self.observed_footer_contradicts_printing(
                    number,
                    printed_set_code,
                    top.collector_number,
                    top.set_code,
                ),
            ):
                top.confidence = 99.5
                safe_candidate_ids.add(top.scryfall_id)
            runner_up_confidence = candidates[1].confidence if len(candidates) > 1 else 0.0
            descriptor_agrees = bool(
                descriptor_catalog_complete
                and top.scryfall_id == descriptor_top_id
                and descriptor_scores.get(top.scryfall_id, 0) >= 70
                and descriptor_margin >= 8
            )
            artwork_agrees = bool(
                descriptor_catalog_complete
                and top.scryfall_id == descriptor_art_top_id
                and descriptor_art_scores.get(top.scryfall_id, 0) >= 78
                and descriptor_art_margin >= 8
            )
            unique_artist_agrees = bool(
                top.scryfall_id in strong_artist_ids and len(strong_artist_ids) == 1
            )
            # Foil glare frequently costs the final tenth of a point even when
            # every useful signal agrees. Promote only a clearly separated
            # visual winner with an exactly read identity and a complete local
            # comparison family. Close/reused-art printings remain in Review.
            if self.has_decisive_candidate_lead(
                top.confidence,
                runner_up_confidence,
                self.card_name_similarity(title, top.name),
                descriptor_agrees or artwork_agrees or unique_artist_agrees,
            ) and not self.observed_footer_contradicts_printing(
                number,
                printed_set_code,
                top.collector_number,
                top.set_code,
            ):
                # A candidate lead can corroborate weak/missing footer text,
                # but it must never overrule a clearly different collector or
                # set observation. ``Cunning Strike`` split across OCR lines
                # once retrieved singleton ``Cunning``; its visible #150 had
                # already disproved EXO #28 before this shortcut re-added it.
                top.confidence = max(top.confidence, 98.5)
                safe_candidate_ids.add(top.scryfall_id)
            if self.has_safe_multimodal_printing_consensus(
                is_basic_land=self.is_basic_land(top_card),
                identity_is_constrained=identity_is_constrained,
                family_complete=family_complete,
                title_score=self.card_name_similarity(title, top.name),
                candidate_id=top.scryfall_id,
                candidate_lead=top.confidence - runner_up_confidence,
                neural_top_id=neural_top_id,
                neural_score=neural_top_score,
                neural_margin=neural_margin,
                visual_top_id=visual_top_id,
                visual_score=visual_top_score,
                visual_margin=visual_margin,
                art_top_id=descriptor_art_top_id,
                art_score=descriptor_art_scores.get(top.scryfall_id, 0),
                symbol_top_set=descriptor_symbol_top_set,
                candidate_set=top.set_code,
                symbol_score=descriptor_symbol_set_score,
                symbol_margin=descriptor_symbol_set_margin,
                footer_contradiction=self.observed_footer_contradicts_printing(
                    number,
                    printed_set_code,
                    top.collector_number,
                    top.set_code,
                ),
            ):
                # Independent embedding, regional fingerprint, and either
                # frame/art or set-symbol evidence can resolve a reused-art
                # printing even when every tiny footer OCR pass fails. The
                # complete-family and clear candidate-lead requirements keep
                # this path auditable; basic lands retain stricter safeguards.
                top.confidence = max(top.confidence, 98.5)
                safe_candidate_ids.add(top.scryfall_id)
            if self.confirmed_camera_visual_consensus_is_safe(
                source_kind=(neural_matches[0].source_kind if neural_matches else None),
                similarity=neural_top_score,
                margin=neural_margin,
                identity_is_constrained=identity_is_constrained,
                family_complete=family_complete,
                title_score=self.card_name_similarity(title, top.name),
                candidate_id=top.scryfall_id,
                neural_top_id=neural_top_id,
                descriptor_top_id=descriptor_top_id,
                descriptor_score=descriptor_scores.get(top.scryfall_id, 0),
                descriptor_margin=descriptor_margin,
                art_top_id=descriptor_art_top_id,
                art_score=descriptor_art_scores.get(top.scryfall_id, 0),
                art_margin=descriptor_art_margin,
                footer_contradiction=self.observed_footer_contradicts_printing(
                    number,
                    printed_set_code,
                    top.collector_number,
                    top.set_code,
                ),
            ):
                top.confidence = 99.5
                safe_candidate_ids.add(top.scryfall_id)
            if (
                not self.is_basic_land(top_card)
                and self.observed_footer_is_reliable(
                    raw_observed_number,
                    raw_observed_set_code,
                    raw_observed_text,
                )
                and self.observed_footer_contradicts_printing(
                    raw_observed_number,
                    raw_observed_set_code,
                    top.collector_number,
                    top.set_code,
                )
            ):
                # This is the final automatic-write invariant. Earlier gates
                # may accumulate safety evidence independently, but none may
                # authorize a non-land printing that a readable physical
                # collector/set footer disproves. Basic lands retain their
                # stricter set-scoped artwork rules because one noisy collector
                # digit is common and those rules explicitly resolve it.
                top.confidence = min(top.confidence, 98.4)
                safe_candidate_ids.discard(top.scryfall_id)
            # ``safe_candidate_ids`` is populated only by independent
            # exact-printing verifiers above and every footer contradiction
            # gets a final veto immediately before this point. A later generic
            # oracle/visual cap must not leave an otherwise authorized top
            # candidate below the API's auto-add confidence threshold.
            if top.scryfall_id in safe_candidate_ids:
                top.confidence = max(top.confidence, 98.5)
        finished = time.perf_counter()
        logger.info(
            "Recognition timings frame=%dx%d recovery=%s prep=%dms ocr=%dms "
            "lookup+visual=%dms rank=%dms total=%dms",
            decoded.shape[1],
            decoded.shape[0],
            recovery_used,
            (prepared - started) * 1000,
            (ocr_complete - prepared) * 1000,
            (matching_complete - ocr_complete) * 1000,
            (finished - matching_complete) * 1000,
            (finished - started) * 1000,
        )
        final_confidence = candidates[0].confidence if candidates else 0
        auto_add_safe = bool(candidates and candidates[0].scryfall_id in safe_candidate_ids)
        # Review is an audit trail. Always retain the untouched camera frame for
        # uncertain scans so a bad perspective contour can never masquerade as
        # the photographed card or discard identifying regions.
        review_image = corrected if final_confidence >= 98.5 else decoded
        return Recognition(
            confidence=final_confidence,
            ocr_text=text,
            candidates=candidates[:5],
            source=decoded,
            corrected=review_image,
            processing_ms=round((finished - started) * 1000),
            card_structure=card_structure,
            timings_ms={
                "low_light_normalized": int(low_light_normalized),
                "prepare": round((prepared - started) * 1000),
                "ocr": round((ocr_complete - prepared) * 1000),
                "initial_lookup_visual": round(
                    (initial_match_complete - ocr_complete) * 1000
                ),
                "recovery": round((recovery_complete - initial_match_complete) * 1000),
                "printing_family": round(
                    (family_complete_at - recovery_complete) * 1000
                ),
                "descriptors": round(
                    (descriptor_complete - neural_lookup_complete) * 1000
                ),
                "neural_search": round(
                    (neural_lookup_complete - family_complete_at) * 1000
                ),
                "candidate_merge": round(
                    (matching_complete - candidate_expansion_complete) * 1000
                ),
                "lookup_visual": round((matching_complete - ocr_complete) * 1000),
                "rank": round((finished - matching_complete) * 1000),
            },
            neural_candidates=[
                {
                    "scryfall_id": match.reference.scryfall_id,
                    "name": match.reference.name,
                    "similarity": round(match.similarity, 6),
                    "source_kind": match.source_kind,
                }
                for match in neural_matches
            ],
            auto_add_safe=auto_add_safe,
        )

    @staticmethod
    def _visual_matches(
        scan_hash: str | dict[str, str],
        box_set_code: str | None,
        ignored_example_hashes: set[str] | None = None,
    ) -> list[tuple[CardReference, float]]:
        ignored_example_hashes = ignored_example_hashes or set()
        scan_fingerprints = scan_hash if isinstance(scan_hash, dict) else {"art_hash": scan_hash}
        catalog = CardRecognizer._get_visual_catalog()
        set_code = box_set_code.lower() if box_set_code else None
        scan_art = int(scan_fingerprints["art_hash"], 16)
        matches = []
        rows = catalog.rows_by_set.get(set_code, ()) if set_code else catalog.rows
        prefiltered_distances: dict[int, int] | None = None
        if not set_code and len(catalog.global_hashes):
            active = np.ones(len(catalog.global_hashes), dtype=bool)
            if ignored_example_hashes:
                ignored = np.fromiter(
                    (int(value, 16) for value in ignored_example_hashes),
                    dtype=np.uint64,
                )
                active &= ~(
                    catalog.global_hash_is_example
                    & np.isin(catalog.global_hashes, ignored)
                )
            hashes = catalog.global_hashes[active]
            row_indices = catalog.global_row_indices[active]
            differences = np.bitwise_xor(hashes, np.uint64(scan_art))
            distances = np.unpackbits(
                differences.view(np.uint8).reshape(-1, 8), axis=1
            ).sum(axis=1)
            close = np.flatnonzero(distances <= 22)
            prefiltered_distances = {}
            for index in close:
                row_index = int(row_indices[index])
                distance = int(distances[index])
                prefiltered_distances[row_index] = min(
                    distance, prefiltered_distances.get(row_index, 65)
                )
        for row_index, (reference, fingerprint) in enumerate(rows):
            if prefiltered_distances is not None:
                art_distance = prefiltered_distances.get(row_index)
                if art_distance is None:
                    continue
            else:
                candidate_hashes = (reference.art_hash,) + tuple(
                    example_hash
                    for example_hash in catalog.examples.get(reference.scryfall_id, ())
                    if example_hash not in ignored_example_hashes
                )
                art_distance = min(
                    (scan_art ^ int(candidate_hash, 16)).bit_count()
                    for candidate_hash in candidate_hashes
                )
            # Artwork retrieves the card family. Complementary canonical
            # regions then rank reprints that reuse the same illustration.
            if art_distance <= 22:
                score = CardRecognizer._fingerprint_score(
                    scan_fingerprints, fingerprint, art_distance
                )
                if score >= 78:
                    matches.append((reference, score))
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches[:8]

    @staticmethod
    def _descriptor_matches(
        image: np.ndarray,
        identity_names: set[str],
        box_set_code: str | None = None,
        ignored_example_review_ids: set[str] | None = None,
    ) -> list[tuple[CardReference, float]]:
        """Rerank printings of an OCR-established card using local artwork details."""
        ranked, _art_ranked, _symbol_ranked = CardRecognizer._descriptor_matches_with_art(
            image,
            identity_names,
            box_set_code,
            ignored_example_review_ids,
        )
        return ranked

    @staticmethod
    def _descriptor_matches_with_art(
        image: np.ndarray,
        identity_names: set[str],
        box_set_code: str | None = None,
        ignored_example_review_ids: set[str] | None = None,
        allowed_ids: set[str] | None = None,
        art_only: bool = False,
    ) -> tuple[
        list[tuple[CardReference, float]],
        list[tuple[CardReference, float]],
        list[tuple[CardReference, float]],
    ]:
        """Return full-region and artwork-only rankings from one profile pass.

        Footer and frame descriptors are useful for most printings, but a noisy
        collector digit must never outweigh the illustration on a basic land.
        Keeping an independent artwork ranking lets land safety use genuinely
        independent evidence without loading every profile twice.
        """
        names = {name.casefold() for name in identity_names if name}
        if not names:
            return [], [], []
        scan = visual_descriptor_bundle(image)
        try:
            with SessionLocal() as db:
                statement = (
                    select(CardReference, CardVisualFingerprint)
                    .join(
                        CardVisualFingerprint,
                        CardVisualFingerprint.scryfall_id == CardReference.scryfall_id,
                    )
                    .where(func.lower(CardReference.name).in_(names))
                    .where(CardVisualFingerprint.descriptor_path.is_not(None))
                )
                if box_set_code:
                    statement = statement.where(CardReference.set_code == box_set_code.casefold())
                if allowed_ids:
                    statement = statement.where(CardReference.scryfall_id.in_(allowed_ids))
                rows = list(db.execute(statement))
                reference_ids = [reference.scryfall_id for reference, _fingerprint in rows]
                example_rows = (
                    list(
                        db.scalars(
                            select(CardVisualExample).where(
                                CardVisualExample.scryfall_id.in_(reference_ids),
                                CardVisualExample.descriptor_path.is_not(None),
                            )
                        )
                    )
                    if reference_ids
                    else []
                )
        except SQLAlchemyError:
            # Recognition must remain usable during first-run schema creation
            # and in isolated callers that intentionally provide only a card
            # provider. Descriptor evidence is optional and conservative.
            logger.debug("Visual descriptor catalog is not available yet", exc_info=True)
            return [], [], []
        ignored_reviews = ignored_example_review_ids or set()
        examples: dict[str, list[str]] = {}
        for example in example_rows:
            if example.source_review_id in ignored_reviews or not example.descriptor_path:
                continue
            examples.setdefault(example.scryfall_id, []).append(example.descriptor_path)
        ranked: list[tuple[CardReference, float]] = []
        art_ranked: list[tuple[CardReference, float]] = []
        symbol_ranked: list[tuple[CardReference, float]] = []
        for reference, fingerprint in rows:
            descriptor_paths = [
                fingerprint.descriptor_path,
                *examples.get(reference.scryfall_id, []),
            ]
            scores: list[float] = []
            art_scores: list[float] = []
            symbol_scores: list[float] = []
            for descriptor_path in descriptor_paths:
                if not descriptor_path:
                    continue
                known = _descriptor_bundle(descriptor_path)
                if known is None:
                    continue
                if len(known.get("art", ())) < 12:
                    continue
                art_score = CardRecognizer._descriptor_score(scan["art"], known["art"])
                if art_score is not None:
                    art_scores.append(art_score)
                if art_only:
                    continue
                score = CardRecognizer._descriptor_bundle_score(scan, known)
                if score is not None:
                    scores.append(score)
                symbol_score = CardRecognizer._ratio_descriptor_score(
                    scan.get("symbol"), known.get("symbol")
                )
                if symbol_score is not None:
                    symbol_scores.append(symbol_score)
            if scores:
                score = max(scores)
                if score >= 45:
                    ranked.append((reference, round(score, 3)))
            if art_scores:
                art_score = max(art_scores)
                if art_score >= 45:
                    art_ranked.append((reference, round(art_score, 3)))
            if symbol_scores:
                symbol_score = max(symbol_scores)
                if symbol_score >= 45:
                    symbol_ranked.append((reference, round(symbol_score, 3)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        art_ranked.sort(key=lambda item: item[1], reverse=True)
        symbol_ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:12], art_ranked[:12], symbol_ranked[:12]

    @staticmethod
    def _identity_visual_matches(
        scan_fingerprints: dict[str, str],
        identity_names: set[str],
        box_set_code: str | None = None,
    ) -> list[tuple[CardReference, float]]:
        """Compare all visual regions after OCR has constrained the card identity.

        The global catalog uses a strict artwork-hash prefilter for speed. That
        can miss a genuine webcam image with glare or perspective distortion.
        Once the title is known, the candidate set is tiny enough to score frame,
        footer, title, and set-symbol regions without that lossy prefilter.
        """
        names = {name.casefold() for name in identity_names if name}
        if not names or not scan_fingerprints:
            return []
        try:
            with SessionLocal() as db:
                statement = (
                    select(CardReference, CardVisualFingerprint)
                    .join(
                        CardVisualFingerprint,
                        CardVisualFingerprint.scryfall_id == CardReference.scryfall_id,
                    )
                    .where(func.lower(CardReference.name).in_(names))
                )
                if box_set_code:
                    statement = statement.where(CardReference.set_code == box_set_code.casefold())
                rows = list(db.execute(statement))
        except SQLAlchemyError:
            return []
        scan_art_hash = scan_fingerprints.get("art_hash")
        try:
            scan_art = int(scan_art_hash, 16) if scan_art_hash else None
        except ValueError:
            scan_art = None
        ranked = []
        for reference, fingerprint in rows:
            art_distance = None
            if scan_art is not None:
                try:
                    art_distance = (scan_art ^ int(fingerprint.art_hash, 16)).bit_count()
                except (TypeError, ValueError):
                    art_distance = None
            score = CardRecognizer._fingerprint_score(scan_fingerprints, fingerprint, art_distance)
            if score >= 55:
                ranked.append((reference, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:12]

    @staticmethod
    def is_basic_land(card: dict) -> bool:
        type_line = str(card.get("type_line") or "").casefold()
        if type_line.startswith("basic land"):
            return True
        return str(card.get("name") or "").casefold() in {
            "plains",
            "island",
            "swamp",
            "mountain",
            "forest",
            "wastes",
        }

    @staticmethod
    def has_decisive_art_match(
        card_id: str,
        descriptor_top_id: str | None,
        catalog_complete: bool,
        descriptor_score: float,
        descriptor_margin: float,
    ) -> bool:
        """Require independent exact-art evidence before auto-adding a land."""
        return bool(
            catalog_complete
            and card_id == descriptor_top_id
            and descriptor_score >= 88
            and descriptor_margin >= 18
        )

    @staticmethod
    def has_decisive_candidate_lead(
        top_confidence: float,
        runner_up_confidence: float,
        title_score: float,
        exhaustive_visual_agreement: bool,
    ) -> bool:
        """Promote a near-threshold match only when independent evidence agrees."""
        return bool(
            top_confidence >= 97.5
            and top_confidence - runner_up_confidence >= 8.0
            and title_score >= 0.93
            and exhaustive_visual_agreement
        )

    @staticmethod
    def has_safe_multimodal_printing_consensus(
        *,
        is_basic_land: bool,
        identity_is_constrained: bool,
        family_complete: bool,
        title_score: float,
        candidate_id: str,
        candidate_lead: float,
        neural_top_id: str | None,
        neural_score: float,
        neural_margin: float,
        visual_top_id: str | None,
        visual_score: float,
        visual_margin: float,
        art_top_id: str | None,
        art_score: float,
        symbol_top_set: str | None,
        candidate_set: str,
        symbol_score: float,
        symbol_margin: float,
        footer_contradiction: bool,
    ) -> bool:
        """Accept footerless non-basic printings only after independent consensus."""
        symbol_consensus = bool(
            not is_basic_land
            and identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and candidate_id == neural_top_id == visual_top_id
            and neural_score >= 0.84
            and neural_margin >= 0.06
            and visual_score >= 75
            and visual_margin >= 0.15
            and symbol_top_set
            and candidate_set.casefold() == symbol_top_set.casefold()
            and symbol_score >= 95
            and symbol_margin >= 4
            and not footer_contradiction
        )
        if symbol_consensus:
            return True
        common = bool(
            not is_basic_land
            and identity_is_constrained
            and family_complete
            and title_score >= 0.93
            and candidate_lead >= 8.0
            and candidate_id == neural_top_id == visual_top_id
            and visual_score >= 73
            and not footer_contradiction
        )
        if not common:
            return False
        strong_neural = neural_score >= 0.84 and neural_margin >= 0.10
        artwork_consensus = bool(
            candidate_id == art_top_id
            and art_score >= 90
            and neural_score >= 0.79
            and neural_margin >= 0.015
            and visual_margin >= 0.20
        )
        symbol_consensus = bool(
            symbol_top_set
            and candidate_set.casefold() == symbol_top_set.casefold()
            and symbol_score >= 95
            and symbol_margin >= 4
            and neural_score >= 0.80
            and neural_margin >= 0.06
            and visual_margin >= 0.5
        )
        return strong_neural or artwork_consensus or symbol_consensus

    @classmethod
    def has_safe_single_printing_identity(
        cls,
        *,
        is_basic_land: bool,
        identity_is_constrained: bool,
        family_complete: bool,
        observed_title: str | None,
        candidate_name: str,
    ) -> bool:
        """Trust a long exact title fragment when the card has one printing."""
        observed = cls.normalized_name(observed_title or "")
        candidate = cls.normalized_name(candidate_name)
        return bool(
            not is_basic_land
            and identity_is_constrained
            and family_complete
            and len(observed) >= 6
            and observed in candidate
        )

    @classmethod
    def has_safe_recovered_exact_footer(
        cls,
        *,
        is_basic_land: bool,
        identity_is_constrained: bool,
        family_complete: bool,
        number_score: float,
        set_score: float,
        candidate_name: str,
        neural_top_name: str | None,
        neural_score: float,
        neural_margin: float,
        footer_contradiction: bool,
    ) -> bool:
        """Anchor an exact footer pair to an independently recovered identity."""
        return bool(
            not is_basic_land
            and number_score == 1.0
            and set_score == 1.0
            and cls.normalized_name(candidate_name)
            == cls.normalized_name(neural_top_name or "")
            and neural_score >= 0.79
            and neural_margin >= 0.02
            and not footer_contradiction
        )

    @staticmethod
    def has_decisive_symbol_match(
        card_id: str,
        symbol_top_id: str | None,
        symbol_score: float,
        symbol_margin: float,
    ) -> bool:
        """Require a unique regional set-symbol match before prioritizing a set."""
        return bool(card_id == symbol_top_id and symbol_score >= 80 and symbol_margin >= 12)

    @staticmethod
    def has_repeated_footer_printing_evidence(
        text: str,
        card: dict,
        candidates: list[dict],
        printed_set_code: str | None,
    ) -> bool:
        """Accept repeated, uniquely resolving footer fragments from foil scans.

        Foil glare commonly drops the leading collector digit (``261`` becomes
        ``61``) or invents one (``269`` becomes ``869``).  Several OCR passes
        are merged into ``text``, so two observations with the same final two
        digits are strong evidence only when that suffix uniquely identifies a
        printing inside the OCR-matched set.  The denominator is deliberately
        ignored and a single reading can never auto-add a card.
        """
        card_set = str(card.get("set") or "")
        collector = re.sub(r"\D", "", str(card.get("collector_number") or ""))
        if len(collector) < 2:
            return False
        raw_set_match = bool(
            card_set
            and re.search(
                rf"(?<![a-z0-9]){re.escape(card_set)}(?![a-z0-9])",
                text or "",
                re.I,
            )
        )
        parsed_set_match = bool(
            printed_set_code
            and CardRecognizer.set_code_score(printed_set_code, card_set) >= 0.78
        )
        if not (raw_set_match or parsed_set_match):
            return False

        numerators = re.findall(r"(?<!\d)(\d{1,4})\s*[/|\\]\s*\d{1,4}", text or "")
        observed_numbers: list[str] = []
        for observed in numerators:
            normalized = observed.lstrip("0") or "0"
            if collector.endswith(normalized[-min(2, len(normalized)) :]):
                observed_numbers.append(normalized)

        def candidate_is_in_set(candidate: dict) -> bool:
            candidate_set = str(candidate.get("set") or "")
            if raw_set_match:
                return candidate_set.casefold() == card_set.casefold()
            return bool(
                printed_set_code
                and CardRecognizer.set_code_score(printed_set_code, candidate_set) >= 0.78
            )

        for observed in observed_numbers:
            if len(observed) < 2:
                continue
            suffix = observed[-2:]
            # A second pass may lose another leading digit (``69`` -> ``9``).
            # It still corroborates the longer reading, but a lone one-digit
            # observation is never enough to create evidence by itself.
            corroborating = sum(
                1
                for other in observed_numbers
                if other.endswith(suffix) or suffix.endswith(other)
            )
            if corroborating < 2:
                continue
            matching_ids = {
                str(candidate.get("id") or "")
                for candidate in candidates
                if candidate_is_in_set(candidate)
                and re.sub(
                    r"\D",
                    "",
                    str(candidate.get("collector_number") or ""),
                ).endswith(suffix)
            }
            if matching_ids == {str(card.get("id") or "")}:
                return True
        return False

    @staticmethod
    def has_unique_set_artist_evidence(
        text: str,
        card: dict,
        candidates: list[dict],
        printed_set_code: str | None,
        artist_score: float,
    ) -> bool:
        """Use a uniquely matching printed artist only inside a proven set."""
        artist = str(card.get("artist") or "").strip()
        card_set = str(card.get("set") or "")
        if artist_score < 0.9 or len(artist.split()) < 2 or not card_set:
            return False
        raw_set_match = bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(card_set)}(?![a-z0-9])",
                text or "",
                re.I,
            )
        )
        parsed_set_match = bool(
            printed_set_code
            and CardRecognizer.set_code_score(printed_set_code, card_set) >= 0.78
        )
        if not (raw_set_match or parsed_set_match):
            return False
        matching_printings = {
            (
                str(candidate.get("set") or "").casefold(),
                str(candidate.get("collector_number") or "").casefold(),
            )
            for candidate in candidates
            if str(candidate.get("set") or "").casefold() == card_set.casefold()
            and CardRecognizer.artist_text_score(text, candidate.get("artist")) >= 0.9
        }
        return matching_printings == {
            (
                card_set.casefold(),
                str(card.get("collector_number") or "").casefold(),
            )
        }

    @staticmethod
    def has_safe_basic_land_match(
        card_id: str,
        descriptor_top_id: str | None,
        catalog_complete: bool,
        descriptor_score: float,
        descriptor_margin: float,
        collector_number: str | None,
        printed_set_code: str | None,
        number_score: float,
        set_score: float,
        symbol_top_id: str | None = None,
        symbol_score: float = 0,
        symbol_margin: float = 0,
        card_set: str | None = None,
        symbol_top_set: str | None = None,
        symbol_set_score: float = 0,
        symbol_set_margin: float = 0,
        set_art_top_id: str | None = None,
        set_art_score: float = 0,
        set_art_margin: float = 0,
        artist_score: float = 0,
        set_art_catalog_complete: bool = False,
        release_year_matches: bool = False,
    ) -> bool:
        """Require decisive artwork plus exact set text or symbol evidence.

        Basic lands often share their name, frame, rules area, and even artwork
        across printings. The collector number is deliberately not required:
        those tiny digits are the least reliable webcam signal and previously
        overruled visibly correct artwork. Exact set text or a decisive visual
        set-symbol region narrows the family; artwork then selects the illustration.
        """
        global_art_matches = card_id == descriptor_top_id and catalog_complete
        global_evidence = global_art_matches and (
            # Foil glare can shave a few points from the illustration margin.
            # A very strong exhaustive-catalog art win plus the independently
            # read printed artist credit is still printing-specific evidence.
            # Reused-art lands naturally fail the art margin.
            (
                artist_score >= 0.9
                and descriptor_score >= 94
                and descriptor_margin >= 15
            )
            or (
                collector_number
                and number_score == 1.0
                and descriptor_score >= 98
                and descriptor_margin >= 25
            )
            or
            (
                collector_number
                and printed_set_code
                and number_score == 1.0
                and set_score == 1.0
                and ((descriptor_score >= 92 and descriptor_margin >= 10) or artist_score >= 0.78)
            )
            or (
                (
                    (printed_set_code and set_score == 1.0)
                    or CardRecognizer.has_decisive_symbol_match(
                        card_id,
                        symbol_top_id,
                        symbol_score,
                        symbol_margin,
                    )
                )
                and CardRecognizer.has_decisive_art_match(
                    card_id,
                    descriptor_top_id,
                    catalog_complete,
                    descriptor_score,
                    descriptor_margin,
                )
            )
        )
        set_scoped_evidence = (
            catalog_complete
            and CardRecognizer.has_decisive_symbol_set_match(
                card_set,
                symbol_top_set,
                symbol_set_score,
                symbol_set_margin,
            )
            and CardRecognizer.has_decisive_art_match(
                card_id,
                set_art_top_id,
                catalog_complete,
                set_art_score,
                set_art_margin,
            )
        )
        footer_set_scoped_evidence = (
            set_art_catalog_complete
            and bool(printed_set_code)
            and (number_score >= 0.78 or release_year_matches)
            and CardRecognizer.exact_set_code_match(
                printed_set_code, card_set or ""
            )
            and card_id == set_art_top_id
            and set_art_score >= 88
            and set_art_margin >= 12
        )
        return bool(global_evidence or set_scoped_evidence or footer_set_scoped_evidence)

    @staticmethod
    def has_safe_title_art_symbol_match(
        card_id: str,
        title_score: float,
        catalog_complete: bool,
        card_set: str | None,
        symbol_top_set: str | None,
        symbol_set_score: float,
        symbol_set_margin: float,
        set_art_top_id: str | None,
        set_art_score: float,
        set_art_margin: float,
    ) -> bool:
        """Prove an exact printing without relying on a visible footer.

        A title identifies the card family, the symbol identifies its set, and
        a decisive artwork margin within that complete set family identifies
        the printing. Reused artwork naturally produces a small margin and is
        kept in Review.
        """
        return bool(
            title_score >= 0.93
            and catalog_complete
            and CardRecognizer.has_decisive_symbol_set_match(
                card_set,
                symbol_top_set,
                symbol_set_score,
                symbol_set_margin,
            )
            and card_id == set_art_top_id
            and set_art_score >= 88
            and set_art_margin >= 18
        )

    @staticmethod
    def has_decisive_symbol_set_match(
        card_set: str | None,
        symbol_top_set: str | None,
        symbol_score: float,
        symbol_margin: float,
    ) -> bool:
        """Treat duplicate symbols inside one set as one set-level vote."""
        return bool(
            card_set
            and symbol_top_set
            and card_set.casefold() == symbol_top_set.casefold()
            and symbol_score >= 80
            and symbol_margin >= 12
        )

    @staticmethod
    def _descriptor_catalog_complete(scryfall_ids: set[str]) -> bool:
        if not scryfall_ids:
            return False
        try:
            with SessionLocal() as db:
                ready = set(
                    db.scalars(
                        select(CardVisualFingerprint.scryfall_id).where(
                            CardVisualFingerprint.scryfall_id.in_(scryfall_ids),
                            CardVisualFingerprint.descriptor_path.like("%/v3/%"),
                        )
                    )
                )
        except SQLAlchemyError:
            return False
        return ready == scryfall_ids

    @staticmethod
    def _descriptor_score(scan: np.ndarray, canonical: np.ndarray) -> float | None:
        if len(scan) < 12 or len(canonical) < 12:
            return None
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = sorted(matcher.match(scan, canonical), key=lambda match: match.distance)
        keep = matches[: min(30, len(matches))]
        if len(keep) < 8:
            return None
        mean_distance = sum(match.distance for match in keep) / len(keep)
        # Webcam/canonical benchmarks place exact art around 20-35 and adjacent
        # artwork around 47-65. The margin between candidates is the key signal.
        return min(99.5, max(0.0, 124.0 - mean_distance * 1.25))

    @staticmethod
    def _ratio_descriptor_score(
        scan: np.ndarray | None, canonical: np.ndarray | None
    ) -> float | None:
        """Score tiny exact-print regions using distinctive ratio-test matches."""
        if scan is None or canonical is None or len(scan) < 8 or len(canonical) < 8:
            return None
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(scan, canonical, k=2)
        good = [
            pair[0]
            for pair in pairs
            if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
        ]
        if len(good) < 5:
            return None
        median_distance = float(np.median([match.distance for match in good]))
        return min(99.5, max(0.0, len(good) * 5.0 + 85.0 - median_distance))

    @staticmethod
    def _descriptor_bundle_score(
        scan: dict[str, np.ndarray], canonical: dict[str, np.ndarray]
    ) -> float | None:
        """Fuse artwork identity with footer and set-symbol printing evidence."""
        art = CardRecognizer._descriptor_score(scan["art"], canonical["art"])
        if art is None:
            return None
        footer = CardRecognizer._ratio_descriptor_score(scan.get("footer"), canonical.get("footer"))
        symbol = CardRecognizer._ratio_descriptor_score(scan.get("symbol"), canonical.get("symbol"))
        # Artwork must remain the majority signal. Set symbols are identical on
        # every basic land in a set and a one-digit footer OCR error is precisely
        # the failure this reranker exists to catch. Regional descriptors support
        # an artwork match; they may not replace it.
        weighted_score = art * 0.55
        available_weight = 0.55
        if "footer" in canonical and len(canonical["footer"]) >= 8:
            weighted_score += (footer or 0.0) * 0.30
            available_weight += 0.30
        if "symbol" in canonical and len(canonical["symbol"]) >= 8:
            weighted_score += (symbol or 0.0) * 0.15
            available_weight += 0.15
        if available_weight == 0.55:
            return art
        return min(99.5, weighted_score / available_weight)

    @staticmethod
    def _get_visual_catalog() -> _VisualCatalog:
        """Reuse immutable visual rows instead of hydrating the full DB each scan."""
        global _visual_catalog
        now = time.monotonic()
        cached = _visual_catalog
        if cached and now - cached.loaded_at < _VISUAL_CATALOG_TTL_SECONDS:
            return cached
        with _visual_catalog_lock:
            cached = _visual_catalog
            if cached and now - cached.loaded_at < _VISUAL_CATALOG_TTL_SECONDS:
                return cached
            with SessionLocal() as db:
                rows = tuple(
                    db.execute(
                        select(CardReference, CardVisualFingerprint).outerjoin(
                            CardVisualFingerprint,
                            CardVisualFingerprint.scryfall_id == CardReference.scryfall_id,
                        )
                    )
                )
                rows_by_set_lists: dict[
                    str, list[tuple[CardReference, CardVisualFingerprint | None]]
                ] = {}
                references_by_name_lists: dict[str, list[CardReference]] = {}
                for reference, fingerprint in rows:
                    rows_by_set_lists.setdefault(reference.set_code, []).append(
                        (reference, fingerprint)
                    )
                    references_by_name_lists.setdefault(
                        reference.name.casefold(), []
                    ).append(reference)
                examples: dict[str, list[str]] = {}
                for scryfall_id, example_hash in db.execute(
                    select(CardVisualExample.scryfall_id, CardVisualExample.art_hash)
                ):
                    examples.setdefault(scryfall_id, []).append(example_hash)
            row_index_by_id = {
                reference.scryfall_id: index
                for index, (reference, _fingerprint) in enumerate(rows)
            }
            global_hashes: list[int] = []
            global_row_indices: list[int] = []
            global_hash_is_example: list[bool] = []
            for index, (reference, _fingerprint) in enumerate(rows):
                # Locally seeded unpublished pack inserts intentionally have no
                # canonical image. They remain fully searchable through their
                # printed title/set/collector evidence, but must not enter the
                # perceptual-hash catalog with a fabricated visual identity.
                if not reference.image_url:
                    continue
                global_hashes.append(int(reference.art_hash, 16))
                global_row_indices.append(index)
                global_hash_is_example.append(False)
            for scryfall_id, hashes in examples.items():
                row_index = row_index_by_id.get(scryfall_id)
                if row_index is None:
                    continue
                for example_hash in hashes:
                    global_hashes.append(int(example_hash, 16))
                    global_row_indices.append(row_index)
                    global_hash_is_example.append(True)
            names = tuple(
                references[0].name
                for references in references_by_name_lists.values()
            )
            names_by_prefix_lists: dict[str, list[str]] = {}
            for name in names:
                names_by_prefix_lists.setdefault(
                    CardRecognizer.normalized_name(name)[:3], []
                ).append(name)
            _visual_catalog = _VisualCatalog(
                loaded_at=now,
                rows=rows,
                rows_by_set={
                    key: tuple(value) for key, value in rows_by_set_lists.items()
                },
                references_by_name={
                    key: tuple(value)
                    for key, value in references_by_name_lists.items()
                },
                names=names,
                names_by_prefix={
                    prefix: tuple(names)
                    for prefix, names in names_by_prefix_lists.items()
                },
                examples={key: tuple(value) for key, value in examples.items()},
                global_hashes=np.asarray(global_hashes, dtype=np.uint64),
                global_row_indices=np.asarray(global_row_indices, dtype=np.int32),
                global_hash_is_example=np.asarray(
                    global_hash_is_example, dtype=bool
                ),
            )
            return _visual_catalog

    @staticmethod
    def invalidate_visual_catalog() -> None:
        global _visual_catalog
        with _visual_catalog_lock:
            _visual_catalog = None

    @staticmethod
    def _fingerprint_score(
        scan: dict[str, str],
        canonical: CardVisualFingerprint | None,
        art_distance: int | None,
    ) -> float:
        art_score = max(0.0, 99.5 - art_distance * 1.35) if art_distance is not None else 0.0
        if canonical is None:
            return art_score
        if len(scan) == 1 and art_distance is not None:
            return art_score
        weights = {
            "full_hash": 0.13,
            "title_hash": 0.10,
            "footer_hash": 0.12,
            "symbol_hash": 0.10,
            "frame_hash": 0.05,
        }
        score = art_score * 0.50 if art_distance is not None else 0.0
        total_weight = 0.50 if art_distance is not None else 0.0
        for field, weight in weights.items():
            canonical_hash = getattr(canonical, field, None)
            if not canonical_hash or field not in scan:
                continue
            distance = hash_distance(scan[field], canonical_hash)
            score += max(0.0, 99.5 - distance * 1.55) * weight
            total_weight += weight
        return min(99.5, round(score / total_weight, 3)) if total_weight else 0.0


def save_scan(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
