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
_api_request_lock = asyncio.Lock()
_api_last_request_at = 0.0
_printing_family_cache: dict[tuple[str, str, int], tuple[float, list[dict], int]] = {}
_printing_family_lock = asyncio.Lock()
_set_metadata: dict[str, dict] | None = None
_set_metadata_loaded_at = 0.0
_set_metadata_lock = asyncio.Lock()


async def scryfall_api_get(url: str, **kwargs) -> httpx.Response:
    """Pace and retry API traffic so background indexing cannot starve scans."""
    global _api_last_request_at
    method = kwargs.pop("method", "GET")
    async with _api_request_lock:
        delay = 0.1 - (time.monotonic() - _api_last_request_at)
        if delay > 0:
            await asyncio.sleep(delay)
        for attempt in range(6):
            try:
                response = await scryfall_client().request(method, url, **kwargs)
                _api_last_request_at = time.monotonic()
                if response.status_code != 429:
                    return response
                retry_after = float(response.headers.get("Retry-After", attempt + 1))
                await asyncio.sleep(min(30, max(0.1, retry_after)))
            except (httpx.TimeoutException, httpx.TransportError):
                _api_last_request_at = time.monotonic()
                if attempt == 5:
                    raise
                await asyncio.sleep(min(10, 1.5**attempt))
        raise RuntimeError("Scryfall API remained rate limited after retries")


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
        response = await scryfall_api_get(
            f"{self.base_url}/cards/search", params={"q": query, "unique": "prints"}
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("data", [])[:12]

    async def get_card(self, scryfall_id: str) -> dict:
        response = await scryfall_api_get(f"{self.base_url}/cards/{scryfall_id}")
        response.raise_for_status()
        return response.json()

    async def get_cards(self, scryfall_ids: list[str]) -> list[dict]:
        """Fetch exact cards through Scryfall's bounded collection endpoint."""
        cards: list[dict] = []
        for offset in range(0, len(scryfall_ids), 75):
            response = await scryfall_api_get(
                f"{self.base_url}/cards/collection",
                method="POST",
                json={
                    "identifiers": [{"id": value} for value in scryfall_ids[offset : offset + 75]]
                },
            )
            response.raise_for_status()
            cards.extend(response.json().get("data", []))
        return cards

    async def set_metadata(self) -> dict[str, dict]:
        """Return authoritative set symbols, cached for one day."""
        global _set_metadata, _set_metadata_loaded_at
        if _set_metadata is not None and time.monotonic() - _set_metadata_loaded_at < 86_400:
            return _set_metadata
        async with _set_metadata_lock:
            if _set_metadata is not None and time.monotonic() - _set_metadata_loaded_at < 86_400:
                return _set_metadata
            response = await scryfall_api_get(f"{self.base_url}/sets")
            response.raise_for_status()
            _set_metadata = {
                item["code"].lower(): item
                for item in response.json().get("data", [])
                if item.get("code")
            }
            _set_metadata_loaded_at = time.monotonic()
            return _set_metadata

    async def fuzzy_name(self, name: str) -> str | None:
        """Resolve a slightly damaged OCR title without choosing its printing."""
        response = await scryfall_api_get(f"{self.base_url}/cards/named", params={"fuzzy": name})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("name")

    async def oracle_search(self, terms: list[str]) -> list[dict]:
        """Find paper cards containing several rules-text terms."""
        if not terms:
            return []
        query = " ".join(f'o:"{term}"' for term in terms) + " game:paper"
        response = await scryfall_api_get(
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
            response = await scryfall_api_get(f"{self.base_url}/catalog/card-names")
            response.raise_for_status()
            _card_names = response.json().get("data", [])
            _card_names_loaded_at = time.monotonic()
            return _card_names

    async def cards_for_set(self, set_code: str) -> list[dict]:
        cards: list[dict] = []
        url = f"{self.base_url}/cards/search"
        params = {"q": f"set:{set_code}", "unique": "prints", "order": "set"}
        while url:
            response = await scryfall_api_get(url, params=params if not cards else None)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            page = response.json()
            cards.extend(page.get("data", []))
            url = page.get("next_page") if page.get("has_more") else None
        return cards

    async def printing_family(
        self, name: str, language: str = "en", limit: int = 12
    ) -> tuple[list[dict], int]:
        """Return a bounded printing family and its true paper-printing count.

        Recognition uses the total to distinguish a complete small family from
        a truncated result such as a basic land with hundreds of printings.
        """
        key = (name.casefold(), language.casefold(), limit)
        cached = _printing_family_cache.get(key)
        if cached and time.monotonic() - cached[0] < 86_400:
            return cached[1], cached[2]
        async with _printing_family_lock:
            cached = _printing_family_cache.get(key)
            if cached and time.monotonic() - cached[0] < 86_400:
                return cached[1], cached[2]
            query = f'!"{name}" game:paper'
            if language:
                query += f" lang:{language}"
            response = await scryfall_api_get(
                f"{self.base_url}/cards/search",
                params={"q": query, "unique": "prints", "order": "released"},
            )
            if response.status_code == 404:
                result = ([], 0)
            else:
                response.raise_for_status()
                page = response.json()
                result = (
                    page.get("data", [])[:limit],
                    int(page.get("total_cards") or 0),
                )
            _printing_family_cache[key] = (time.monotonic(), *result)
            return result

    async def paper_printing_pages(self) -> AsyncIterator[list[dict]]:
        """Stream every paper printing without holding the catalog in memory."""
        url = f"{self.base_url}/cards/search"
        params = {"q": "game:paper", "unique": "prints", "order": "set"}
        while url:
            response = await scryfall_api_get(url, params=params)
            response.raise_for_status()
            page = response.json()
            yield page.get("data", [])
            url = page.get("next_page") if page.get("has_more") else None
            params = None

    async def paper_printing_count(self) -> int:
        """Return Scryfall's current number of distinct paper printings."""
        response = await scryfall_api_get(
            f"{self.base_url}/cards/search",
            params={"q": "game:paper", "unique": "prints", "page": 1},
        )
        response.raise_for_status()
        return int(response.json().get("total_cards") or 0)

    async def art_series_pages(self) -> AsyncIterator[list[dict]]:
        """Stream artwork-only Art Series cards for visual recognition."""
        url = f"{self.base_url}/cards/search"
        params = {"q": "layout:art_series", "unique": "prints", "order": "set"}
        while url:
            response = await scryfall_api_get(url, params=params)
            response.raise_for_status()
            page = response.json()
            yield page.get("data", [])
            url = page.get("next_page") if page.get("has_more") else None
            params = None

    async def art_series_count(self) -> int:
        response = await scryfall_api_get(
            f"{self.base_url}/cards/search",
            params={"q": "layout:art_series", "unique": "prints", "page": 1},
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
