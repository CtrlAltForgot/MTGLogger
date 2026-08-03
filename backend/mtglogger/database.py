from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


url = get_settings().database_url
engine_options = (
    {"connect_args": {"check_same_thread": False}}
    if url.startswith("sqlite")
    else {"pool_pre_ping": True, "pool_recycle": 1800}
)
engine = create_engine(url, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def migrate_schema() -> None:
    """Apply small additive migrations for installations predating Alembic."""
    inspector = inspect(engine)
    if "card_visual_fingerprints" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("card_visual_fingerprints")}
    if "symbol_hash" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE card_visual_fingerprints ADD COLUMN symbol_hash VARCHAR(16)")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_card_visual_fingerprints_symbol_hash "
                    "ON card_visual_fingerprints (symbol_hash)"
                )
            )
