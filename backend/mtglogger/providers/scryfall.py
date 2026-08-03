import asyncio
import time
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation

import httpx

from ..config import get_settings

_client: httpx.AsyncClient | None = None
_card_names: list[str] | None = None
_card_names_loaded_at = 0.0
_card_names_lock = asyncio.Lock()


def scryfall_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        settings = get_settings()
        _client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.scryfall_user_agent,
                "Accept": "application/json",
            },
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close_scryfall_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


class ScryfallProvider:
    base_url = "https://api.scryfall.com"

    def __init__(self) -> None:
        settings = get_settings()
        self.timeout = settings.request_timeout

    async def search(
        self, query: str, set_code: str | None = None, language: str | None = None
    ) -> list[dict]:
        if set_code:
            query = f"{query} set:{set_code}"
        if language:
            query = f"{query} lang:{language}"
        response = await scryfall_client().get(
            f"{self.base_url}/cards/search", params={"q": query, "unique": "prints"}
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("data", [])[:12]

    async def get_card(self, scryfall_id: str) -> dict:
        response = await scryfall_client().get(f"{self.base_url}/cards/{scryfall_id}")
        response.raise_for_status()
        return response.json()

    async def fuzzy_name(self, name: str) -> str | None:
        """Resolve a slightly damaged OCR title without choosing its printing."""
        response = await scryfall_client().get(
            f"{self.base_url}/cards/named", params={"fuzzy": name}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("name")

    async def oracle_search(self, terms: list[str]) -> list[dict]:
        """Find paper cards containing several rules-text terms."""
        if not terms:
            return []
        query = " ".join(f'o:"{term}"' for term in terms) + " game:paper"
        response = await scryfall_client().get(
            f"{self.base_url}/cards/search",
            params={"q": query, "unique": "cards", "order": "name"},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("data", [])[:175]

    async def card_names(self) -> list[str]:
        """Return Scryfall's small canonical-name catalog, cached for one day."""
        global _card_names, _card_names_loaded_at
        if _card_names is not None and time.monotonic() - _card_names_loaded_at < 86_400:
            return _card_names
        async with _card_names_lock:
            if _card_names is not None and time.monotonic() - _card_names_loaded_at < 86_400:
                return _card_names
            response = await scryfall_client().get(f"{self.base_url}/catalog/card-names")
            response.raise_for_status()
            _card_names = response.json().get("data", [])
            _card_names_loaded_at = time.monotonic()
            return _card_names

    async def cards_for_set(self, set_code: str) -> list[dict]:
        cards: list[dict] = []
        url = f"{self.base_url}/cards/search"
        params = {"q": f"set:{set_code}", "unique": "prints", "order": "set"}
        client = scryfall_client()
        while url:
            response = await client.get(url, params=params if not cards else None)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            page = response.json()
            cards.extend(page.get("data", []))
            url = page.get("next_page") if page.get("has_more") else None
        return cards

    async def paper_printing_pages(self) -> AsyncIterator[list[dict]]:
        """Stream every paper printing without holding the catalog in memory."""
        url = f"{self.base_url}/cards/search"
        params = {"q": "game:paper", "unique": "prints", "order": "set"}
        client = scryfall_client()
        while url:
            response = None
            for attempt in range(6):
                try:
                    response = await client.get(url, params=params)
                    if response.status_code == 429:
                        delay = float(response.headers.get("Retry-After", attempt + 1))
                        await asyncio.sleep(min(30, delay))
                        continue
                    response.raise_for_status()
                    break
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt == 5:
                        raise
                    await asyncio.sleep(min(10, 1.5**attempt))
            if response is None:
                raise RuntimeError("Scryfall catalog page did not return a response")
            page = response.json()
            yield page.get("data", [])
            url = page.get("next_page") if page.get("has_more") else None
            params = None

    async def paper_printing_count(self) -> int:
        """Return Scryfall's current number of distinct paper printings."""
        response = await scryfall_client().get(
            f"{self.base_url}/cards/search",
            params={"q": "game:paper", "unique": "prints", "page": 1},
        )
        response.raise_for_status()
        return int(response.json().get("total_cards") or 0)

    async def download_image(self, url: str) -> bytes:
        response = await scryfall_client().get(url)
        response.raise_for_status()
        return response.content

    @staticmethod
    def market_price(card: dict, foil: bool = False) -> Decimal | None:
        value = card.get("prices", {}).get("usd_foil" if foil else "usd")
        try:
            return Decimal(value) if value else None
        except InvalidOperation:
            return None

    @staticmethod
    def image_url(card: dict) -> str | None:
        images = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris", {})
        return images.get("normal")
