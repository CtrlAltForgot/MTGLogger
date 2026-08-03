from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./mtglogger.db"
    cors_origins: str = "http://localhost:5173"
    scryfall_user_agent: str = "MTGLogger/0.1"
    image_dir: Path = Path("data/scans")
    request_timeout: float = 8.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
