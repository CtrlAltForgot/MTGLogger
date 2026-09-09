"""Human-friendly search over the local printing catalog, without OCR rules."""

import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from functools import lru_cache
from weakref import WeakKeyDictionary

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CardReference
from ..schemas import Candidate


@lru_cache(maxsize=65_536)
def normalize(text: str) -> str:
    text = text.casefold().replace("æ", "ae").replace("œ", "oe")
    text = text.replace("'", "").replace("’", "")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def number_key(value: str) -> str:
    return (value.lstrip("0") or "0") if value.isdigit() else value


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    name: str
    aliases: tuple[str, ...]
    name_words: frozenset[str]
    words: frozenset[str]
    set_code: str
    set_name: str
    number: str
    language: str
    released: int


class Catalog:
    def __init__(self, rows):
        self.entries: list[Entry] = []
        self.postings: dict[str, set[int]] = {}
        self.languages: dict[str, set[int]] = {}
        self.families: dict[str, set[int]] = {}
        self.sets: dict[str, set[int]] = {}
        for row in rows:
            aliases = tuple(
                dict.fromkeys(
                    normalize(value)
                    for value in (row.name, row.printed_name, row.flavor_name)
                    if value
                )
            )
            name_words = frozenset(word for alias in aliases for word in alias.split())
            words = (
                name_words
                | frozenset(normalize(row.set_name).split())
                | {
                    row.set_code.casefold(),
                    normalize(row.collector_number),
                    number_key(normalize(row.collector_number)),
                }
            )
            entry = Entry(
                row.scryfall_id,
                row.name,
                aliases,
                name_words,
                words,
                row.set_code.casefold(),
                row.set_name,
                row.collector_number,
                row.language,
                row.released_at.toordinal() if row.released_at else 0,
            )
            index = len(self.entries)
            self.entries.append(entry)
            for word in words:
                self.postings.setdefault(word, set()).add(index)
            self.languages.setdefault(entry.language, set()).add(index)
            self.families.setdefault(entry.name, set()).add(index)
            self.sets.setdefault(entry.set_code, set()).add(index)
        self.vocabulary = tuple(word for word in self.postings if word.isalpha())
        self._alternatives: dict[tuple[str, bool], dict[str, float]] = {}

    def alternatives(self, term: str, fuzzy: bool) -> dict[str, float]:
        key = (term, fuzzy)
        cached = self._alternatives.get(key)
        if cached is not None:
            return cached
        if term.isdigit():
            number = number_key(term)
            return {number: 1.0} if number in self.postings else {}
        options = {
            word: 1.0 if word == term else 0.95 if word.startswith(term) else 0.85
            for word in self.postings
            if term in word
        }
        if fuzzy and len(term) >= 4:
            for word in get_close_matches(term, self.vocabulary, n=5, cutoff=0.74):
                options.setdefault(word, 0.75 * SequenceMatcher(None, term, word).ratio())
            # A common swapped-letter typo must survive even when the full
            # catalog has many words that difflib considers closer (blot/bolt).
            for index in range(len(term) - 1):
                swapped = term[:index] + term[index + 1] + term[index] + term[index + 2 :]
                if swapped in self.postings:
                    options.setdefault(swapped, 0.7)
        if len(self._alternatives) >= 256:
            self._alternatives.clear()
        self._alternatives[key] = options
        return options

    def search(self, query: str, language: str, *, name: str = "", set_code: str = ""):
        # Accept the familiar optional set:SLD notation as well as ordinary words.
        explicit_sets = re.findall(r"\b(?:set|s):([a-z0-9]+)\b", query, re.IGNORECASE)
        query = re.sub(r"\b(?:set|s):[a-z0-9]+\b", " ", query, flags=re.IGNORECASE)
        terms = tuple(dict.fromkeys(normalize(query).split()))
        available = self.languages.get(language, set()).copy()
        if name:
            available.intersection_update(self.families.get(name, set()))
        for code in [set_code, *explicit_sets]:
            if code:
                available.intersection_update(self.sets.get(code.casefold(), set()))
        if not terms and not (name or set_code or explicit_sets):
            return [], False
        used_fuzzy = False
        options: list[dict[str, float]] = []
        matches: set[int] = set()
        for fuzzy in (False, True):
            options = [self.alternatives(term, fuzzy) for term in terms]
            matches = available.copy()
            for alternatives in options:
                ids: set[int] = set()
                for word in alternatives:
                    ids.update(self.postings[word])
                matches.intersection_update(ids)
                if not matches:
                    break
            if matches:
                used_fuzzy = fuzzy
                break

        phrase = normalize(query)

        def rank(index: int):
            entry = self.entries[index]
            name_scores = [
                max((choices.get(word, 0) for word in entry.name_words), default=0)
                for choices in options
            ]
            scores = [
                max((choices.get(word, 0) for word in entry.words), default=0)
                for choices in options
            ]
            return (
                phrase not in entry.aliases,
                not all(name_scores),
                -sum(scores),
                -sum(name_scores),
                len(entry.name),
                entry.name.casefold(),
                -entry.released,
                entry.set_code,
                entry.number,
                entry.id,
            )

        return [self.entries[index] for index in sorted(matches, key=rank)], used_fuzzy


_catalogs: WeakKeyDictionary = WeakKeyDictionary()
_catalog_lock = threading.Lock()


def catalog_for(db: Session) -> Catalog:
    # The stamp invalidates searches after catalog syncs or metadata edits,
    # including updates that do not change the total number of printings.
    stamp = tuple(db.execute(select(func.count(), func.max(CardReference.updated_at))).one())
    engine = db.get_bind()
    with _catalog_lock:
        cached = _catalogs.get(engine)
        if cached and cached[0] == stamp:
            return cached[1]
        rows = db.execute(
            select(
                CardReference.scryfall_id,
                CardReference.name,
                CardReference.printed_name,
                CardReference.flavor_name,
                CardReference.set_code,
                CardReference.set_name,
                CardReference.collector_number,
                CardReference.language,
                CardReference.released_at,
            )
        )
        catalog = Catalog(rows)
        _catalogs[engine] = (stamp, catalog)
        return catalog


def as_candidate(card: CardReference) -> Candidate:
    return Candidate(
        scryfall_id=card.scryfall_id,
        name=card.flavor_name or card.printed_name or card.name,
        set_code=card.set_code,
        set_name=card.set_name,
        collector_number=card.collector_number,
        image_url=card.image_url,
        market_price=card.market_price,
        finishes=json.loads(card.finishes) if card.finishes else [],
        language=card.language,
        confidence=0,
        oracle_id=card.oracle_id,
        color_identity=card.color_identity,
        rarity=card.rarity,
        type_line=card.type_line,
    )


def candidates_for(db: Session, entries: list[Entry]) -> list[Candidate]:
    if not entries:
        return []
    cards = {
        card.scryfall_id: card
        for card in db.scalars(
            select(CardReference).where(
                CardReference.scryfall_id.in_([entry.id for entry in entries])
            )
        )
    }
    return [as_candidate(cards[entry.id]) for entry in entries if entry.id in cards]


def search_printings(db: Session, query: str, language: str, limit: int = 250):
    entries, _ = catalog_for(db).search(query, language)
    return candidates_for(db, entries[:limit])


def browse_catalog(
    db: Session,
    query: str,
    language: str,
    *,
    name: str = "",
    set_code: str = "",
    page: int = 1,
    page_size: int = 24,
):
    catalog = catalog_for(db)
    entries, fuzzy = catalog.search(query, language, name=name)
    sets: dict[str, dict] = {}
    for entry in entries:
        info = sets.setdefault(
            entry.set_code,
            {
                "code": entry.set_code,
                "name": entry.set_name,
                "count": 0,
            },
        )
        info["count"] += 1
    if set_code:
        entries = [entry for entry in entries if entry.set_code == set_code.casefold()]
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.name] = counts.get(entry.name, 0) + 1
    if not name:
        seen: set[str] = set()
        grouped = []
        for entry in entries:
            if entry.name not in seen:
                seen.add(entry.name)
                grouped.append(entry)
        entries = grouped
    total = len(entries)
    start = (page - 1) * page_size
    chosen = entries[start : start + page_size]
    candidates = {item.scryfall_id: item for item in candidates_for(db, chosen)}
    return {
        "items": [
            {
                **candidates[entry.id].model_dump(),
                "oracle_name": entry.name,
                "printing_count": counts[entry.name],
            }
            for entry in chosen
            if entry.id in candidates
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "fuzzy": fuzzy,
        "mode": "printings" if name else "cards",
        "catalog_count": len(catalog.entries),
        "sets": sorted(sets.values(), key=lambda item: item["name"]) if name else [],
    }
