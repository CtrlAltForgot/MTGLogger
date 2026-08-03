import asyncio
import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import httpx
import numpy as np
from sqlalchemy import select

from ..database import SessionLocal
from ..models import CardReference
from ..providers import ScryfallProvider
from ..schemas import Candidate
from .references import artwork_hash, hash_distance

logger = logging.getLogger("uvicorn.error")


@dataclass
class Recognition:
    confidence: float
    ocr_text: str
    candidates: list[Candidate]
    corrected: np.ndarray
    processing_ms: int


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
    def rectify(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 140)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            perimeter = cv2.arcLength(contour, True)
            polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
            if (
                len(polygon) != 4
                or cv2.contourArea(polygon) < image.shape[0] * image.shape[1] * 0.15
            ):
                continue
            points = polygon.reshape(4, 2).astype("float32")
            sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
            ordered = np.array(
                [
                    points[np.argmin(sums)],
                    points[np.argmin(diffs)],
                    points[np.argmax(sums)],
                    points[np.argmax(diffs)],
                ]
            )
            target = np.array([[0, 0], [599, 0], [599, 839], [0, 839]], dtype="float32")
            return cv2.warpPerspective(
                image, cv2.getPerspectiveTransform(ordered, target), (600, 840)
            )
        # The browser deliberately centers cards in a fixed guide. Low-contrast
        # sleeves may hide the outer contour, so normalize that guide instead of
        # sending an entire widescreen frame through OCR and artwork matching.
        height, width = image.shape[:2]
        crop_height = int(height * 0.92)
        crop_width = min(int(width * 0.46), int(crop_height * 63 / 88))
        x1, y1 = (width - crop_width) // 2, (height - crop_height) // 2
        guide = image[y1 : y1 + crop_height, x1 : x1 + crop_width]
        return cv2.resize(guide, (600, 840), interpolation=cv2.INTER_AREA)

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

    @staticmethod
    def hints(text: str) -> tuple[str | None, str | None, str | None, int | None]:
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 1]
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
        copyright_year = int(year_match.group(1)) if year_match else None
        set_code = None
        languages = "EN|ES|FR|DE|IT|PT|JA|KO|RU|ZHS|ZHT|HE|LA|GRC|AR|SA|PHY"
        for line in reversed(lines):
            match = re.match(
                rf"^\s*([A-Z][A-Z0-9]{{1,5}})\s*[·•.\-:]\s*(?:{languages})(?:\s|$)",
                line,
                re.I,
            )
            if match:
                set_code = match.group(1).lower()
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
    ) -> list[dict]:
        if not title and not number:
            return []
        title_query = f'!"{title}"' if title else ""
        query = f"{title_query} cn:{number}".strip() if number else title_query
        preferred_set = printed_set_code or box_set_code
        try:
            # Recognition must not sit behind a long external outage. All lookup
            # attempts share one short budget; the captured frame still proceeds
            # through local artwork matching and into Review if Scryfall is down.
            async with asyncio.timeout(3.5):
                cards = await self.provider.search(query, preferred_set, language)
                # Localized title text is not consistently searchable through
                # Scryfall's canonical-name field. Set + collector number + chosen
                # language identifies the printing without guessing an English ID.
                if not cards and number and language != "en" and preferred_set:
                    cards = await self.provider.search(
                        f"cn:{number}", preferred_set, language
                    )
                if not cards and title and number:
                    cards = await self.provider.search(title_query, preferred_set, language)
                # An imperfect set-code OCR should lower confidence, not erase otherwise
                # useful candidates from the confirmation list.
                if not cards and printed_set_code:
                    cards = await self.provider.search(query, box_set_code, language)
                return cards
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            logger.warning("Scryfall lookup unavailable; preserving scan for Review: %s", exc)
            return []

    async def recognize(
        self, raw: bytes, box_set_code: str | None = None, language: str = "en"
    ) -> Recognition:
        async with self._recognition_lock:
            started = time.perf_counter()
            corrected = await asyncio.to_thread(lambda: self.rectify(self.decode(raw)))
            prepared = time.perf_counter()
            text = await asyncio.to_thread(self.extract_text, corrected)
            ocr_complete = time.perf_counter()
            title, number, printed_set_code, copyright_year = self.hints(text)
            lookup_task = asyncio.create_task(
                self._lookup_cards(title, number, printed_set_code, box_set_code, language)
            )
            scan_hash = await asyncio.to_thread(artwork_hash, corrected)
            visual_matches = await asyncio.to_thread(
                self._visual_matches, scan_hash, printed_set_code or box_set_code
            )
            cards = await lookup_task
            matching_complete = time.perf_counter()
        visual_scores = {reference.scryfall_id: score for reference, score in visual_matches}
        ranked: dict[str, Candidate] = {}
        for card in cards:
            title_score = (
                SequenceMatcher(None, (title or "").lower(), card["name"].lower()).ratio()
                if title
                else 0.55
            )
            number_score = self.collector_score(number, card["collector_number"])
            set_score = (
                1.0
                if printed_set_code and card["set"].lower() == printed_set_code
                else (0.45 if not printed_set_code else 0.0)
            )
            if printed_set_code:
                ocr_score = (title_score * 0.5 + number_score * 0.3 + set_score * 0.2) * 100
            elif copyright_year:
                released_year = int(card.get("released_at", "0000")[:4])
                year_score = 1.0 if released_year == copyright_year else 0.0
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
            confidence = min(99.5, confidence)
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
            "Recognition timings prep=%dms ocr=%dms lookup+visual=%dms rank=%dms total=%dms",
            (prepared - started) * 1000,
            (ocr_complete - prepared) * 1000,
            (matching_complete - ocr_complete) * 1000,
            (finished - matching_complete) * 1000,
            (finished - started) * 1000,
        )
        return Recognition(
            candidates[0].confidence if candidates else 0,
            text,
            candidates[:5],
            corrected,
            round((finished - started) * 1000),
        )

    @staticmethod
    def _visual_matches(
        scan_hash: str, box_set_code: str | None
    ) -> list[tuple[CardReference, float]]:
        with SessionLocal() as db:
            statement = select(CardReference)
            if box_set_code:
                statement = statement.where(CardReference.set_code == box_set_code.lower())
            references = list(db.scalars(statement))
            matches = []
            for reference in references:
                distance = hash_distance(scan_hash, reference.art_hash)
                # pHash has 64 bits. Distances above 18 are visually unrelated.
                if distance <= 18:
                    score = max(0.0, 99.5 - distance * 1.35)
                    matches.append((reference, score))
            matches.sort(key=lambda item: item[1], reverse=True)
            return matches[:8]


def save_scan(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
