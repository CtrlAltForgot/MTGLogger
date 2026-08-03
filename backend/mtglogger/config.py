from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./mtglogger.db"
    cors_origins: str = "http://localhost:5173"
    scryfall_user_agent: str = "MTGLogger/0.1"
    image_dir: Path = Path("data/scans")
    reference_image_dir: Path = Path("data/references")
    reference_image_cache: str = "compact"
    reference_refresh_hours: int = 24
    reference_auto_sync: bool = True
    reference_priority_sets: str = "ori,ktk,m15,isd,gtc,jou,m13"
    request_timeout: float = 8.0
    price_refresh_hours: int = 1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    @property
    def cache_reference_images(self) -> bool:
        return self.reference_image_cache.casefold() == "full"

    @property
    def priority_reference_sets(self) -> list[str]:
        values = [
            value.strip().casefold()
            for value in self.reference_priority_sets.split(",")
            if value.strip()
        ]
        return list(dict.fromkeys(values))


@lru_cache
def get_settings() -> Settings:
    return Settings()
