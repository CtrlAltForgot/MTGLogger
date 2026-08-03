import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select

from ..database import SessionLocal
from ..models import CardReference
from ..providers import ScryfallProvider
from ..schemas import Candidate
from .references import artwork_hash, hash_distance


@dataclass
class Recognition:
    confidence: float
    ocr_text: str
    candidates: list[Candidate]
    corrected: np.ndarray


class CardRecognizer:
    """Hybrid recognizer. PaddleOCR is optional so the API remains lightweight."""

    def __init__(self) -> None:
        self.provider = ScryfallProvider()
        self._ocr = None
        try:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
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
            target = np.array([[0, 0], [744, 0], [744, 1039], [0, 1039]], dtype="float32")
            return cv2.warpPerspective(
                image, cv2.getPerspectiveTransform(ordered, target), (745, 1040)
            )
        return image

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
            return ""

    @staticmethod
    def hints(text: str) -> tuple[str | None, str | None]:
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 1]
        number = next(
            (
                m.group(1)
                for line in reversed(lines)
                if (m := re.search(r"(?:^|\s)(\d{1,4}[a-z]?)(?:/\d{1,4})?(?:\s|$)", line, re.I))
            ),
            None,
        )
        title = next(
            (line for line in lines[:5] if not re.search(r"\d{3,}", line) and len(line) <= 60), None
        )
        return title, number

    async def recognize(self, raw: bytes, box_set_code: str | None = None) -> Recognition:
        corrected = self.rectify(self.decode(raw))
        text = self.extract_text(corrected)
        title, number = self.hints(text)
        cards: list[dict] = []
        if title or number:
            query = f'!"{title}"' if title else f"cn:{number}"
            if number:
                query += f" cn:{number}"
            cards = await self.provider.search(query, box_set_code)

        scan_hash = artwork_hash(corrected)
        visual_matches = self._visual_matches(scan_hash, box_set_code)
        visual_scores = {reference.scryfall_id: score for reference, score in visual_matches}
        ranked: dict[str, Candidate] = {}
        for card in cards:
            title_score = (
                SequenceMatcher(None, (title or "").lower(), card["name"].lower()).ratio()
                if title
                else 0.55
            )
            number_score = (
                1.0
                if number and number.lower() == card["collector_number"].lower()
                else (0.45 if not number else 0)
            )
            set_bonus = 0.08 if box_set_code and card["set"].lower() == box_set_code.lower() else 0
            ocr_score = (title_score * 0.66 + number_score * 0.34) * 100
            visual_score = visual_scores.get(card["id"])
            confidence = (
                ocr_score * 0.62 + visual_score * 0.38
                if visual_score is not None
                else ocr_score * 0.92
            )
            confidence = min(99.5, confidence + set_bonus * 100)
            ranked[card["id"]] = Candidate(
                scryfall_id=card["id"],
                name=card["name"],
                set_code=card["set"],
                set_name=card["set_name"],
                collector_number=card["collector_number"],
                image_url=self.provider.image_url(card),
                market_price=self.provider.market_price(card),
                confidence=round(confidence, 1),
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
                confidence=round(score, 1),
            )
        candidates = sorted(ranked.values(), key=lambda item: item.confidence, reverse=True)
        return Recognition(
            candidates[0].confidence if candidates else 0, text, candidates[:5], corrected
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
