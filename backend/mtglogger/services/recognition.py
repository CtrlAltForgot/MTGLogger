import asyncio
import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock

import cv2
import httpx
import numpy as np
from sqlalchemy import func, select

from ..database import SessionLocal
from ..models import CardReference, CardVisualExample, CardVisualFingerprint
from ..providers import ScryfallProvider
from ..schemas import Candidate
from .references import artwork_descriptors, hash_distance, visual_fingerprints

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class _VisualCatalog:
    loaded_at: float
    rows: tuple[tuple[CardReference, CardVisualFingerprint | None], ...]
    examples: dict[str, tuple[str, ...]]


_visual_catalog: _VisualCatalog | None = None
_visual_catalog_lock = Lock()
_VISUAL_CATALOG_TTL_SECONDS = 60


@dataclass
class Recognition:
    confidence: float
    ocr_text: str
    candidates: list[Candidate]
    corrected: np.ndarray
    processing_ms: int
    card_structure: bool


class CardRecognizer:
    """Hybrid recognizer. PaddleOCR is optional so the API remains lightweight."""

    def __init__(self) -> None:
        self.provider = ScryfallProvider()
        self._recognition_lock = asyncio.Lock()
        self._ocr = None
        try:
            from paddleocr import PaddleOCR

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
        return cv2.warpPerspective(
            image, cv2.getPerspectiveTransform(ordered, target), (600, 840)
        )

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

    def extract_identification_text(self, image: np.ndarray) -> str:
        """Read the large title and enlarged printing footer, skipping rules text."""
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
        focused = self.extract_text(focused_image)
        focused_title, number, set_code, _ = self.hints(focused)
        if focused_title and (not number or not set_code):
            # Tiny foil/set/collector text benefits from local contrast and
            # sharpening. Run this extra OCR pass only when the normal footer
            # did not already provide complete printing evidence.
            enhanced_footer = self.enhance_footer(footer_left)
            enhanced_text = self.extract_text(enhanced_footer)
            if enhanced_text.strip():
                focused = "\n".join((focused, enhanced_text))
        if focused_title:
            return focused
        # Showcase frames and older layouts occasionally place the title outside
        # the normal band. Preserve reliability with a full-card fallback only
        # when the fast title pass produced no usable text.
        return self.extract_text(image)

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
        """Detect long horizontal frame/text-box edges absent from an empty table."""
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
        for [[x1, y1, x2, y2]] in lines:
            width = abs(x2 - x1)
            if abs(y2 - y1) <= max(4, width * 0.08):
                horizontal += 1
                if horizontal >= 3:
                    return True
        return False

    @staticmethod
    def hints(text: str) -> tuple[str | None, str | None, str | None, int | None]:
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 1]
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
        copyright_year = int(year_match.group(1)) if year_match else None
        if copyright_year is None:
            # Tiny copyright text often loses "20" while retaining a marker
            # and the final two digits (for example ©2013 -> "co13").
            footer = "\n".join(lines[-5:])
            short_year = re.search(
                r"(?:©|&|co|c|o)[^0-9\n]{0,2}([0-2]\d)(?!\d)", footer, re.I
            )
            if short_year:
                inferred = 2000 + int(short_year.group(1))
                if 1993 <= inferred <= 2030:
                    copyright_year = inferred
        set_code = None
        languages = "EN|ES|FR|DE|IT|PT|JA|KO|RU|ZHS|ZHT|HE|LA|GRC|AR|SA|PHY"
        for line in reversed(lines):
            match = re.match(
                rf"^\s*([A-Z][A-Z0-9]{{1,5}}?)[\s·•.+\-:]*(?:{languages})(?=\s|$|[A-Z])",
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
            for line in reversed(lines[-5:]):
                token = line.strip()
                if (
                    re.fullmatch(r"[A-Z][A-Z0-9]{1,4}", token)
                    and token not in language_tokens
                ):
                    set_code = token.lower()
                    break
        number = None
        # Collector numbers often share the copyright line. Prefer an explicit
        # numerator/denominator pair before filtering copyright years.
        for line in reversed(lines):
            match = re.search(r"(?<!\d)(\d{1,4}[a-z]?)\s*[/|\\]\s*\d{1,4}(?!\d)", line, re.I)
            if match:
                number = match.group(1)
                break
        # Low-resolution OCR commonly drops the slash ("062/249" -> "02 249").
        # On a copyright line, the last two non-year numbers are still a strong
        # collector/total pair; retain the leading zero as useful OCR evidence.
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
        for line in reversed(lines):
            if number:
                break
            matches = re.findall(r"(?:^|\s)(\d{1,4}[a-z]?)(?:/\d{1,4})?(?=\s|$)", line, re.I)
            for value in matches:
                numeric = int(re.match(r"\d+", value).group())
                # Copyright years commonly appear below the collector number.
                if 1900 <= numeric <= 2100 or "©" in line or "Wizards" in line:
                    continue
                number = value
                break
            if number:
                break
        title = next(
            (
                line
                for line in lines[:5]
                if not re.search(r"\d{3,}", line)
                and len(line) <= 60
                and sum(character.isalpha() for character in line) >= 3
            ),
            None,
        )
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

    @staticmethod
    def normalized_name(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

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
        ]
        return [
            phrase
            for phrase, threshold in vocabulary
            if cls.fuzzy_contains(text, phrase, threshold)
        ][:3]

    @classmethod
    def oracle_similarity(cls, ocr_text: str, oracle_text: str) -> float:
        left, right = cls.normalized_name(ocr_text), cls.normalized_name(oracle_text)
        if not left or not right:
            return 0
        left_grams = {left[index : index + 3] for index in range(max(1, len(left) - 2))}
        right_grams = {right[index : index + 3] for index in range(max(1, len(right) - 2))}
        containment = len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))
        return max(SequenceMatcher(None, left, right).ratio(), containment)

    async def _oracle_recovery(self, text: str, language: str) -> tuple[str | None, list[dict]]:
        if language != "en" or not hasattr(self.provider, "oracle_search"):
            return None, []
        terms = self.oracle_terms(text)
        if len(terms) < 2:
            return None, []
        try:
            async with asyncio.timeout(2.5):
                matches = await self.provider.oracle_search(terms)
        except (TimeoutError, httpx.HTTPError, ValueError):
            return None, []
        ranked = sorted(
            (
                (card, self.oracle_similarity(text, card.get("oracle_text", "")))
                for card in matches
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] < 0.48:
            return None, []
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.08:
            return None, []
        name = ranked[0][0]["name"]
        return name, await self._lookup_cards(name, None, None, None, language)

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
                (name, SequenceMatcher(None, query, cls.normalized_name(name)).ratio())
                for name in names
                if abs(len(cls.normalized_name(name)) - len(query)) <= max(5, len(query) // 2)
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
        variants = {
            source,
            source.translate(str.maketrans({"i": "1", "l": "1", "s": "5", "o": "0"})),
        }
        if target in variants:
            return 1.0
        return min(
            0.85,
            max(SequenceMatcher(None, variant, target).ratio() for variant in variants),
        )

    @classmethod
    def has_strong_lookup_evidence(
        cls,
        title: str | None,
        number: str | None,
        printed_set_code: str | None,
        copyright_year: int | None,
        cards: list[dict],
    ) -> bool:
        if not title or not cards:
            return False
        title_scores = [
            SequenceMatcher(None, title.casefold(), card["name"].casefold()).ratio()
            for card in cards
        ]
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
                card
                for card in cards
                if int(card.get("released_at", "0000")[:4]) == copyright_year
            ]
            return len(matching_years) == 1
        return False

    @classmethod
    def has_strong_card_identity(cls, title: str | None, cards: list[dict]) -> bool:
        if not title or not cards:
            return False
        normalized_title = cls.normalized_name(title)
        return any(
            SequenceMatcher(None, normalized_title, cls.normalized_name(card["name"])).ratio()
            >= 0.90
            for card in cards
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
        if (
            title_score >= 0.9
            and number_score >= 0.78
            and set_score >= 0.78
            and year_score == 1.0
        ):
            return max(confidence, 98.5)
        return confidence

    @staticmethod
    def has_unique_printing_signal(
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
            and (
                not competing_number_scores
                or number_score - max(competing_number_scores) >= 0.12
            )
        )
        unique_set = bool(
            printed_set_code
            and printed_set_code.casefold() == candidate_set_code.casefold()
            and candidates_in_set == 1
        )
        return title_score >= 0.95 and (unique_number or unique_set)

    @staticmethod
    def visual_only_score(score: float) -> float:
        # Artwork is intentionally supporting evidence, never proof of an exact
        # printing. Wizards frequently reuses identical art across sets, promos,
        # collector numbers, and finishes. Keep visual-only candidates useful at
        # the top of Review without allowing them to cross the 98.5% auto-add gate.
        return min(94.0, score)

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

        async def search_variants(
            candidate_title: str, *, relaxed: bool = False
        ) -> list[dict]:
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
                    cards = await self.provider.search(
                        f"cn:{number}", printed_set_code, language
                    )
                # Localized title text is not consistently searchable through
                # Scryfall's canonical-name field. Set + collector number + chosen
                # language identifies the printing without guessing an English ID.
                if not cards and number and language != "en" and preferred_set:
                    cards = await self.provider.search(
                        f"cn:{number}", preferred_set, language
                    )
                if not cards and title and hasattr(self.provider, "fuzzy_name"):
                    canonical_name = await self.provider.fuzzy_name(title)
                    if canonical_name:
                        cards = await search_variants(canonical_name, relaxed=True)
                if not cards and title and hasattr(self.provider, "card_names"):
                    catalog = await self.provider.card_names()
                    closest = await asyncio.to_thread(
                        self.closest_catalog_names, title, catalog
                    )
                    for canonical_name, _similarity in closest:
                        cards = await search_variants(canonical_name, relaxed=True)
                        if cards:
                            break
                if not cards and title:
                    cards = await search_variants(title, relaxed=True)
                return cards
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            logger.warning("Scryfall lookup unavailable; preserving scan for Review: %s", exc)
            return []

    async def recognize(
        self,
        raw: bytes,
        box_set_code: str | None = None,
        language: str = "en",
        ignored_visual_hashes: set[str] | None = None,
        ignored_example_review_ids: set[str] | None = None,
    ) -> Recognition:
        async with self._recognition_lock:
            started = time.perf_counter()
            recovery_used = False
            oracle_recovery = False
            decoded = self.decode(raw)
            corrected = await asyncio.to_thread(lambda: self.rectify(decoded))
            prepared = time.perf_counter()
            card_structure = await asyncio.to_thread(self.has_card_structure, corrected)
            text = await asyncio.to_thread(self.extract_identification_text, corrected)
            ocr_complete = time.perf_counter()
            title, number, printed_set_code, copyright_year = self.hints(text)
            promo_type = self.promo_type_hint(text)
            lookup_task = asyncio.create_task(
                self._lookup_cards(
                    title,
                    number,
                    printed_set_code,
                    box_set_code,
                    language,
                    promo_type,
                )
            )
            scan_fingerprints = await asyncio.to_thread(visual_fingerprints, corrected)
            visual_matches = await asyncio.to_thread(
                self._visual_matches,
                scan_fingerprints,
                printed_set_code or box_set_code,
                *([ignored_visual_hashes] if ignored_visual_hashes is not None else []),
            )
            cards = await lookup_task
            descriptor_image = corrected
            if not self.has_strong_lookup_evidence(
                title, number, printed_set_code, copyright_year, cards
            ):
                recovery_used = True
                focused_identity_is_strong = self.has_strong_card_identity(title, cards)
                # A fast crop can occasionally lock onto an internal rules box,
                # and tiny footers may be incomplete. OCR the original frame only
                # for weak scans, then rerank before interrupting the user.
                recovery_text = await asyncio.to_thread(self.extract_text, decoded)
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
                    text = recovery_text
                    title, number, printed_set_code, copyright_year = recovery_hints
                    cards = recovery_cards
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
            identity_names = {
                card["name"] for card in cards
            } or ({title} if title else set())
            descriptor_matches = await asyncio.to_thread(
                self._descriptor_matches,
                descriptor_image,
                identity_names,
                # A tiny footer can turn M15 into MIS. Never let uncertain OCR
                # remove the correct artwork candidate; only explicit Box Mode
                # is authoritative enough to constrain descriptor retrieval.
                box_set_code,
                ignored_example_review_ids,
            )
            known_card_ids = {card["id"] for card in cards}
            for reference, _score in descriptor_matches:
                if reference.scryfall_id in known_card_ids:
                    continue
                cards.append(
                    {
                        "id": reference.scryfall_id,
                        "name": reference.name,
                        "set": reference.set_code,
                        "set_name": reference.set_name,
                        "collector_number": reference.collector_number,
                        "released_at": (
                            reference.released_at.isoformat()
                            if reference.released_at
                            else "0000"
                        ),
                        "image_uris": {"normal": reference.image_url},
                        "prices": {
                            "usd": (
                                str(reference.market_price)
                                if reference.market_price is not None
                                else None
                            )
                        },
                        "lang": language,
                    }
                )
                known_card_ids.add(reference.scryfall_id)
            matching_complete = time.perf_counter()
        visual_scores = {reference.scryfall_id: score for reference, score in visual_matches}
        for reference, score in descriptor_matches:
            visual_scores[reference.scryfall_id] = max(
                score, visual_scores.get(reference.scryfall_id, 0)
            )
        descriptor_scores = {
            reference.scryfall_id: score for reference, score in descriptor_matches
        }
        descriptor_top_id = descriptor_matches[0][0].scryfall_id if descriptor_matches else None
        descriptor_margin = (
            descriptor_matches[0][1] - descriptor_matches[1][1]
            if len(descriptor_matches) > 1
            else (descriptor_matches[0][1] if descriptor_matches else 0)
        )
        ranked: dict[str, Candidate] = {}
        release_years = [int(card.get("released_at", "0000")[:4]) for card in cards]
        number_scores = [
            self.collector_score(number, card["collector_number"]) for card in cards
        ]
        exact_set_counts: dict[str, int] = {}
        for card in cards:
            code = card["set"].casefold()
            exact_set_counts[code] = exact_set_counts.get(code, 0) + 1
        for card in cards:
            title_score = (
                SequenceMatcher(
                    None,
                    self.normalized_name(title or ""),
                    self.normalized_name(card["name"]),
                ).ratio()
                if title
                else 0.55
            )
            number_score = self.collector_score(number, card["collector_number"])
            set_score = self.set_code_score(printed_set_code, card["set"])
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
            # An exact title with one known printing is an exact-printing match.
            # A unique matching copyright year can distinguish reused artwork.
            only_printing = len(cards) == 1
            unique_release_year = bool(
                copyright_year
                and year_score == 1.0
                and release_years.count(copyright_year) == 1
            )
            if title_score >= 0.93 and (only_printing or unique_release_year):
                confidence = max(confidence, 98.5)
            # Footer OCR sometimes loses a leading digit (123/272 -> 23/272)
            # while retaining enough evidence to distinguish every printing of
            # an exactly-read card name. Accept it only when one candidate has a
            # strong collector-number match with a clear margin over all others.
            competing_number_scores = [
                score
                for candidate, score in zip(cards, number_scores, strict=True)
                if candidate["id"] != card["id"]
            ]
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
            descriptor_score = descriptor_scores.get(card["id"], 0)
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
            confidence = min(99.5, confidence)
            if oracle_recovery and not printing_signal:
                # Rules text can identify a card, but it cannot prove which set,
                # collector number, or finish is physically present. Independent
                # collector/set footer evidence may still prove the printing.
                confidence = min(89.0, confidence)
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
            if cards and not any(
                SequenceMatcher(
                    None,
                    self.normalized_name(reference.name),
                    self.normalized_name(card["name"]),
                ).ratio()
                >= 0.90
                for card in cards
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
        # Review is an audit trail. Always retain the untouched camera frame for
        # uncertain scans so a bad perspective contour can never masquerade as
        # the photographed card or discard identifying regions.
        review_image = corrected if final_confidence >= 98.5 else decoded
        return Recognition(
            final_confidence,
            text,
            candidates[:5],
            review_image,
            round((finished - started) * 1000),
            card_structure,
        )

    @staticmethod
    def _visual_matches(
        scan_hash: str | dict[str, str],
        box_set_code: str | None,
        ignored_example_hashes: set[str] | None = None,
    ) -> list[tuple[CardReference, float]]:
        ignored_example_hashes = ignored_example_hashes or set()
        scan_fingerprints = (
            scan_hash if isinstance(scan_hash, dict) else {"art_hash": scan_hash}
        )
        catalog = CardRecognizer._get_visual_catalog()
        set_code = box_set_code.lower() if box_set_code else None
        scan_art = int(scan_fingerprints["art_hash"], 16)
        matches = []
        for reference, fingerprint in catalog.rows:
            if set_code and reference.set_code != set_code:
                continue
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
        names = {name.casefold() for name in identity_names if name}
        if not names:
            return []
        scan = artwork_descriptors(image)
        if len(scan) < 12:
            return []
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
            rows = list(db.execute(statement))
            reference_ids = [reference.scryfall_id for reference, _fingerprint in rows]
            example_rows = list(
                db.scalars(
                    select(CardVisualExample).where(
                        CardVisualExample.scryfall_id.in_(reference_ids),
                        CardVisualExample.descriptor_path.is_not(None),
                    )
                )
            ) if reference_ids else []
        ignored_reviews = ignored_example_review_ids or set()
        examples: dict[str, list[str]] = {}
        for example in example_rows:
            if example.source_review_id in ignored_reviews or not example.descriptor_path:
                continue
            examples.setdefault(example.scryfall_id, []).append(example.descriptor_path)
        ranked: list[tuple[CardReference, float]] = []
        for reference, fingerprint in rows:
            descriptor_paths = [
                fingerprint.descriptor_path,
                *examples.get(reference.scryfall_id, []),
            ]
            scores: list[float] = []
            for descriptor_path in descriptor_paths:
                if not descriptor_path:
                    continue
                try:
                    known = np.load(descriptor_path, allow_pickle=False)
                except (OSError, ValueError, TypeError):
                    continue
                if len(known) < 12:
                    continue
                score = CardRecognizer._descriptor_score(scan, known)
                if score is not None:
                    scores.append(score)
            if not scores:
                continue
            score = max(scores)
            if score >= 55:
                ranked.append((reference, round(score, 3)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:12]

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
                examples: dict[str, list[str]] = {}
                for scryfall_id, example_hash in db.execute(
                    select(CardVisualExample.scryfall_id, CardVisualExample.art_hash)
                ):
                    examples.setdefault(scryfall_id, []).append(example_hash)
            _visual_catalog = _VisualCatalog(
                loaded_at=now,
                rows=rows,
                examples={key: tuple(value) for key, value in examples.items()},
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
        art_distance: int,
    ) -> float:
        art_score = max(0.0, 99.5 - art_distance * 1.35)
        if canonical is None or len(scan) == 1:
            return art_score
        weights = {
            "full_hash": 0.13,
            "title_hash": 0.10,
            "footer_hash": 0.12,
            "symbol_hash": 0.10,
            "frame_hash": 0.05,
        }
        score = art_score * 0.50
        total_weight = 0.50
        for field, weight in weights.items():
            canonical_hash = getattr(canonical, field, None)
            if not canonical_hash or field not in scan:
                continue
            distance = hash_distance(scan[field], canonical_hash)
            score += max(0.0, 99.5 - distance * 1.55) * weight
            total_weight += weight
        return min(99.5, round(score / total_weight, 3))


def save_scan(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
