from decimal import Decimal, InvalidOperation

import httpx

from ..config import get_settings


class ScryfallProvider:
    base_url = "https://api.scryfall.com"

    def __init__(self) -> None:
        settings = get_settings()
        self.headers = {"User-Agent": settings.scryfall_user_agent, "Accept": "application/json"}
        self.timeout = settings.request_timeout

    async def search(
        self, query: str, set_code: str | None = None, language: str | None = None
    ) -> list[dict]:
        if set_code:
            query = f"{query} set:{set_code}"
        if language:
            query = f"{query} lang:{language}"
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/cards/search", params={"q": query, "unique": "prints"}
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return response.json().get("data", [])[:12]

    async def get_card(self, scryfall_id: str) -> dict:
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/cards/{scryfall_id}")
            response.raise_for_status()
            return response.json()

    async def cards_for_set(self, set_code: str) -> list[dict]:
        cards: list[dict] = []
        url = f"{self.base_url}/cards/search"
        params = {"q": f"set:{set_code}", "unique": "prints", "order": "set"}
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            while url:
                response = await client.get(url, params=params if not cards else None)
                if response.status_code == 404:
                    return []
                response.raise_for_status()
                page = response.json()
                cards.extend(page.get("data", []))
                url = page.get("next_page") if page.get("has_more") else None
        return cards

    async def download_image(self, url: str) -> bytes:
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(url)
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
