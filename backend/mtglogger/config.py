from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./mtglogger.db"
    cors_origins: str = "http://localhost:5173"
    scryfall_user_agent: str = "MTGLogger/0.1"
    image_dir: Path = Path("data/scans")
    deck_image_dir: Path = Path("data/decks")
    evaluation_dir: Path = Path("data/evaluation")
    reference_image_dir: Path = Path("data/references")
    reference_descriptor_dir: Path = Path("data/descriptors")
    neural_model_dir: Path = Path("data/neural/model")
    neural_index_dir: Path = Path("data/neural/index")
    neural_enabled: bool = True
    neural_shadow_mode: bool = True
    neural_auto_download: bool = False
    neural_maintenance_hour: int = 6
    neural_maintenance_timezone: str = "America/Chicago"
    neural_model_version: str = "PP-ShiTuV2_rec-paddle3.0b2"
    reference_image_cache: str = "compact"
    reference_refresh_hours: int = 24
    reference_auto_sync: bool = True
    # Keep currently opened booster products and their physical insert sets at
    # the front of the resumable catalog queue.
    reference_priority_sets: str = "tla,tm21,tecl,ori,ktk,m15,isd,gtc,jou,m13"
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
