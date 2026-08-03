from decimal import Decimal, InvalidOperation

import httpx

from ..config import get_settings


class ScryfallProvider:
    base_url = "https://api.scryfall.com"

    def __init__(self) -> None:
        settings = get_settings()
        self.headers = {"User-Agent": settings.scryfall_user_agent, "Accept": "application/json"}
        self.timeout = settings.request_timeout

    async def search(self, query: str, set_code: str | None = None) -> list[dict]:
        if set_code:
            query = f"{query} set:{set_code}"
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
