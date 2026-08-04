import asyncio
import json
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
from sqlalchemy.exc import SQLAlchemyError

from ..database import SessionLocal
from ..models import CardReference, CardVisualExample, CardVisualFingerprint
from ..providers import ScryfallProvider
from ..schemas import Candidate
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
    timings_ms: dict[str, int] | None = None


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
        if not number or not set_code:
            # Tiny foil/set/collector text benefits from local contrast and
            # sharpening. Run this extra OCR pass only when the normal footer
            # did not already provide complete printing evidence.
            # Collector numbers are the decisive signal for basic-land artwork
            # and visually similar reprints.  Keep a wider slice than the fast
            # combined pass and render it substantially larger: on a 720p
            # camera feed the footer glyphs can otherwise be only 5-7 pixels
            # tall.  The extra OCR call is paid only when the fast pass did not
            # already recover complete printing evidence.
            collector_footer = footer[:, : int(width * 0.78)]
            collector_footer = self.scale_to_width(collector_footer, 1200)
            enhanced_footer = self.enhance_footer(collector_footer)
            enhanced_text = self.extract_text(enhanced_footer)
            if enhanced_text.strip():
                focused = "\n".join((focused, enhanced_text))
            focused_title, _, _, _ = self.hints(focused)
        if focused_title:
            return focused
        # Showcase frames and older layouts occasionally place the title outside
        # the normal band. Preserve reliability with a full-card fallback only
        # when the fast title pass produced no usable text.
        full_text = self.extract_text(image)
        # The full-card pass can recover a title from a type line while losing
        # the tiny collector footer. Preserve both independent observations.
        return "\n".join(part for part in (focused, full_text) if part.strip())

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
        # Copyright footers commonly contain a range (for example
        # "© 1993-2011 Wizards").  The final/latest year identifies the
        # physical printing; taking the first year incorrectly labels every
        # such card as a 1993 printing.
        years = [
            int(value)
            for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
        ]
        copyright_year = max(years) if years else None
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
        # Prefer a footer with a visible separator. A permissive joined-footer
        # match is useful for tiny text, but can hallucinate a language inside
        # an artist fragment (for example NGPARK -> NGP + AR) and must not
        # override a clean ``ORI-EN`` elsewhere in the OCR passes.
        for line in reversed(lines):
            match = re.match(
                rf"^\s*([A-Z][A-Z0-9]{{1,5}}?)[\s·•.+\-:]+(?:{languages})(?=\s|$|[A-Z])",
                line,
            )
            if match:
                set_code = match.group(1).lower()
                break
        if not set_code:
            for line in reversed(lines):
                match = re.match(
                    rf"^\s*([A-Z][A-Z0-9]{{1,5}}?)(?:{languages})(?=\s|$|[A-Z])",
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
                if (
                    numeric < 10
                    or 1900 <= numeric <= 2100
                    or "©" in line
                    or "Wizards" in line
                ):
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
                rf"^\s*[A-Z][A-Z0-9]{{1,5}}?[\s·•.+\-:]*(?:{languages})(?=\s|$|[A-Z])",
                line,
            ):
                return False
            return (
                not re.search(r"\d{3,}", line)
                and len(line) <= 60
                and sum(character.isalpha() for character in line) >= 3
            )

        title = next(
            (
                line
                for line in title_lines
                if plausible_title(line)
            ),
            None,
        )
        # A basic land's type line contains its actual card name after the dash.
        # This is the one safe case where a type line can recover identity when
        # glare or a dark frame hides the title. Keeping the allow-list narrow
        # avoids turning ordinary subtype text into a fabricated card name.
        if title is None and type_line_index is not None:
            type_line = lines[type_line_index]
            basic_land = re.match(
                r"^basic\s+land\s*[-—–:]\s*(plains|island|swamp|mountain|forest|wastes)\b",
                type_line,
                re.I,
            )
            if basic_land:
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

    @staticmethod
    def normalized_name(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    @classmethod
    def card_name_similarity(cls, observed: str | None, catalog_name: str) -> float:
        """Compare OCR against either face of a multi-faced card."""
        source = cls.normalized_name(observed or "")
        if not source:
            return 0.0
        names = [catalog_name, *catalog_name.split(" // ")]
        return max(
            SequenceMatcher(None, source, cls.normalized_name(name)).ratio()
            for name in names
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
        return [
            {"name": row.name, "oracle_text": row.oracle_text or ""}
            for row in unique.values()
        ]

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
                    abs(len(cls.normalized_name(face)) - len(query))
                    <= max(5, len(query) // 2)
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
        # A Scryfall set code and collector number are the canonical identity of
        # a physical printing. Foil glare frequently hides the title while the
        # footer remains perfectly readable; once that pair resolves to one
        # catalog object, another full-frame OCR pass cannot add identity
        # evidence and only delays the scanner.
        if cls.unique_exact_footer_card(number, printed_set_code, cards):
            return True
        if not title or not cards:
            return False
        title_scores = [
            cls.card_name_similarity(title, card["name"])
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
            and cls.set_code_score(printed_set_code, card["set"]) == 1.0
        ]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def has_strong_card_identity(cls, title: str | None, cards: list[dict]) -> bool:
        if not title or not cards:
            return False
        return any(
            cls.card_name_similarity(title, card["name"]) >= 0.90
            for card in cards
        )

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
    def has_constrained_visual_identity(
        cls, title: str | None, cards: list[dict], identity_names: set[str]
    ) -> bool:
        """Allow printing-level visual reranking only after one name is established."""
        return bool(
            len(identity_names) == 1
            and cards
            and cls.has_strong_card_identity(title, cards)
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

        # The growing local reference catalog is authoritative enough for card
        # identity and avoids putting every physical scan behind Scryfall's
        # network latency. Remote lookup remains the fallback for new/unindexed
        # cards and supplies printing-family completeness below.
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
        except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Scryfall lookup unavailable; preserving scan for Review: %s", exc)
            return []

    @classmethod
    def _lookup_local_cards(
        cls, title: str, number: str | None, preferred_set: str | None
    ) -> list[dict]:
        try:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(CardReference).where(
                            func.lower(CardReference.name) == title.casefold()
                        )
                    )
                )
                if not rows:
                    names = list(db.scalars(select(CardReference.name).distinct()))
                    closest = cls.closest_catalog_names(title, names, limit=1)
                    if not closest or closest[0][1] < 0.72:
                        return []
                    rows = list(
                        db.scalars(
                            select(CardReference).where(
                                func.lower(CardReference.name)
                                == closest[0][0].casefold()
                            )
                        )
                    )
        except SQLAlchemyError:
            return []
        if not rows:
            return []
        exact = [
            reference
            for reference in rows
            if (
                not preferred_set
                or cls.set_code_score(preferred_set, reference.set_code) == 1.0
            )
            and (
                not number
                or cls.collector_score(number, reference.collector_number) == 1.0
            )
        ]
        selected = exact or rows
        return [
            {
                "id": reference.scryfall_id,
                "name": reference.name,
                "set": reference.set_code,
                "set_name": reference.set_name,
                "collector_number": reference.collector_number,
                "released_at": (
                    reference.released_at.isoformat() if reference.released_at else "0000"
                ),
                "image_uris": {"normal": reference.image_url},
                "prices": {
                    "usd": (
                        str(reference.market_price)
                        if reference.market_price is not None
                        else None
                    )
                },
                "lang": reference.language or "en",
                "oracle_id": reference.oracle_id,
                "oracle_text": reference.oracle_text or "",
                "promo_types": json.loads(reference.promo_types or "[]"),
            }
            for reference in selected[:24]
        ]

    @classmethod
    def _lookup_local_printing_family(
        cls, name: str, language: str
    ) -> tuple[list[dict], int]:
        """Return a printing family from the completed local catalog.

        Exact-print recognition must never wait behind Scryfall once the same
        canonical printings are present locally. The bounded card list mirrors
        the provider API while the total preserves the conservative
        family-completeness check for very large families such as basic lands.
        """
        try:
            with SessionLocal() as db:
                total = db.scalar(
                    select(func.count()).select_from(CardReference).where(
                        func.lower(CardReference.name) == name.casefold(),
                        CardReference.language == language,
                    )
                ) or 0
        except SQLAlchemyError:
            return [], 0
        cards = cls._lookup_local_cards(name, None, None)
        return [card for card in cards if card.get("lang") == language], int(total)

    @classmethod
    def _lookup_local_cards_by_number(
        cls, number: str, preferred_set: str | None
    ) -> list[dict]:
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
            preferred = [
                row
                for row in rows
                if row.set_code.casefold() == preferred_set.casefold()
            ]
            rows = preferred or rows
        return [
            {
                "id": reference.scryfall_id,
                "name": reference.name,
                "set": reference.set_code,
                "set_name": reference.set_name,
                "collector_number": reference.collector_number,
                "released_at": (
                    reference.released_at.isoformat() if reference.released_at else "0000"
                ),
                "image_uris": {"normal": reference.image_url},
                "prices": {
                    "usd": (
                        str(reference.market_price)
                        if reference.market_price is not None
                        else None
                    )
                },
                "lang": "en",
            }
            for reference in rows
        ]

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
            cards = await lookup_task
            exact_footer_card = self.unique_exact_footer_card(
                number, printed_set_code, cards
            )
            if title is None and exact_footer_card:
                # Populate the title from the uniquely identified local
                # printing so downstream scoring follows the same path as a
                # readable title without paying for broad OCR recovery.
                title = exact_footer_card["name"]
            # A set code plus collector number is normally the canonical
            # identifier for a paper printing. Basic lands are the exception in
            # practice: one mistaken footer digit silently selects a different
            # artwork from the same set. Never let footer OCR bypass independent
            # artwork verification for those cards.
            exact_land_needs_art = bool(
                exact_footer_card and self.is_basic_land(exact_footer_card)
            )
            if exact_footer_card and not exact_land_needs_art:
                scan_fingerprints = {}
                visual_matches = []
            else:
                scan_fingerprints = await asyncio.to_thread(
                    visual_fingerprints, corrected
                )
                visual_matches = await asyncio.to_thread(
                    self._visual_matches,
                    scan_fingerprints,
                    printed_set_code or box_set_code,
                    *(
                        [ignored_visual_hashes]
                        if ignored_visual_hashes is not None
                        else []
                    ),
                )
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
                    # The focused crop is best at the tiny collector footer,
                    # while the noisier full-frame pass is often the only one
                    # that can read the title.  Fuse those complementary facts
                    # before accepting the broader recovery pool.  This is
                    # essential for basic lands whose names and set are shared
                    # by several distinct, potentially valuable artworks.
                    recovered_title, recovered_number, recovered_set, recovered_year = (
                        recovery_hints
                    )
                    fused_number = number or recovered_number
                    fused_set = printed_set_code or recovered_set
                    fused_year = copyright_year or recovered_year
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
            identity_names = {
                card["name"] for card in cards
            } or ({title} if title else set())
            identity_is_constrained = self.has_constrained_visual_identity(
                title, cards, identity_names
            )
            exact_footer_match = self.has_exact_footer_match(
                title, number, printed_set_code, cards
            )
            exact_footer_can_skip_art = exact_footer_match and not any(
                self.is_basic_land(card) for card in cards
            )
            family_complete = False
            if (
                identity_is_constrained
                and not exact_footer_can_skip_art
            ):
                try:
                    family_name = next(iter(identity_names))
                    family_cards, family_total = await asyncio.to_thread(
                        self._lookup_local_printing_family,
                        family_name,
                        language,
                    )
                    if (
                        not family_cards
                        and hasattr(self.provider, "printing_family")
                    ):
                        async with asyncio.timeout(3.0):
                            family_cards, family_total = (
                                await self.provider.printing_family(
                                    family_name, language
                                )
                            )
                    if family_cards:
                        family_complete = bool(
                            len(family_cards) == family_total
                        )
                        known_ids = {card["id"] for card in cards}
                        cards.extend(
                            card for card in family_cards if card["id"] not in known_ids
                        )
                        if family_complete:
                            await ensure_reference_profiles(self.provider, family_cards)
                except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError):
                    # The normal conservative path remains valid while offline.
                    family_complete = False
            if identity_is_constrained and not exact_footer_can_skip_art:
                descriptor_matches, identity_visual_matches = await asyncio.gather(
                    asyncio.to_thread(
                        self._descriptor_matches,
                        descriptor_image,
                        identity_names,
                        # A tiny footer can turn M15 into MIS. Never let uncertain
                        # OCR remove the correct artwork candidate; only explicit
                        # Box Mode is authoritative enough to constrain retrieval.
                        box_set_code,
                        ignored_example_review_ids,
                    ),
                    asyncio.to_thread(
                        self._identity_visual_matches,
                        scan_fingerprints,
                        identity_names,
                        box_set_code,
                    ),
                )
            else:
                # Regional ORB descriptors are excellent for separating known
                # printings of one established card name, but deliberately broad
                # pools (for example every printing numbered 105) contain many
                # unrelated layouts that can saturate the ratio score. Do not let
                # that identity-scoped reranker manufacture an identity when foil
                # glare hid the title. The exhaustive global fingerprints remain
                # available here and can conservatively recover the exact artwork.
                descriptor_matches = []
                identity_visual_matches = []
            known_card_ids = {card["id"] for card in cards}
            # The remote title lookup is intentionally bounded and may return
            # only the newest page of a name with hundreds of printings (basic
            # lands are the important case). Local identity-scoped matching can
            # find an older exact artwork/footer, so promote those references
            # into the ranking pool instead of merely attaching a score to a
            # card the pool does not contain.
            local_matches = [*descriptor_matches, *identity_visual_matches]
            for reference, _score in local_matches:
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
        for reference, score in identity_visual_matches:
            visual_scores[reference.scryfall_id] = max(
                score, visual_scores.get(reference.scryfall_id, 0)
            )
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
        descriptor_catalog_complete = family_complete and self._descriptor_catalog_complete(
            {card["id"] for card in cards}
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
                self.card_name_similarity(title, card["name"])
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
            # A readable title/footer is not enough to distinguish basic-land
            # artwork.  Sets routinely contain several Plains/Island/Swamp/
            # Mountain/Forest printings whose only meaningful difference is
            # the illustration and collector number.  A single OCR digit can
            # therefore produce a very confident, but wrong, exact printing.
            # Keep these below the automatic-add threshold unless the local
            # exhaustive visual catalogue independently agrees with a clear
            # margin.  They remain first-class suggestions for quick review.
            if self.is_basic_land(card) and not self.has_decisive_art_match(
                card["id"],
                descriptor_top_id,
                descriptor_catalog_complete,
                descriptor_score,
                descriptor_margin,
            ):
                confidence = min(confidence, 98.4)
            confidence = min(99.5, confidence)
            if oracle_recovery and not printing_signal:
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
            confidence=final_confidence,
            ocr_text=text,
            candidates=candidates[:5],
            corrected=review_image,
            processing_ms=round((finished - started) * 1000),
            card_structure=card_structure,
            timings_ms={
                "prepare": round((prepared - started) * 1000),
                "ocr": round((ocr_complete - prepared) * 1000),
                "lookup_visual": round((matching_complete - ocr_complete) * 1000),
                "rank": round((finished - matching_complete) * 1000),
            },
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
        scan = visual_descriptor_bundle(image)
        if len(scan["art"]) < 12:
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
                    loaded = np.load(descriptor_path, allow_pickle=False)
                except (OSError, ValueError, TypeError):
                    continue
                if isinstance(loaded, np.lib.npyio.NpzFile):
                    known = {key: loaded[key] for key in loaded.files}
                    loaded.close()
                else:
                    # User-confirmed examples and legacy canonical profiles
                    # contain artwork-only descriptors.
                    known = {"art": loaded}
                if len(known.get("art", ())) < 12:
                    continue
                score = CardRecognizer._descriptor_bundle_score(scan, known)
                if score is not None:
                    scores.append(score)
            if not scores:
                continue
            score = max(scores)
            if score >= 45:
                ranked.append((reference, round(score, 3)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:12]

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
        if not names:
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
                    statement = statement.where(
                        CardReference.set_code == box_set_code.casefold()
                    )
                rows = list(db.execute(statement))
        except SQLAlchemyError:
            return []
        scan_art = int(scan_fingerprints["art_hash"], 16)
        ranked = []
        for reference, fingerprint in rows:
            art_distance = (scan_art ^ int(fingerprint.art_hash, 16)).bit_count()
            score = CardRecognizer._fingerprint_score(
                scan_fingerprints, fingerprint, art_distance
            )
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
        footer = CardRecognizer._ratio_descriptor_score(
            scan.get("footer"), canonical.get("footer")
        )
        symbol = CardRecognizer._ratio_descriptor_score(
            scan.get("symbol"), canonical.get("symbol")
        )
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
