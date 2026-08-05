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
    tables = inspector.get_table_names()
    if "decks" in tables:
        deck_columns = {column["name"] for column in inspector.get_columns("decks")}
        if "image_url" not in deck_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE decks ADD COLUMN image_url TEXT"))
    if "card_references" in tables:
        reference_columns = {
            column["name"] for column in inspector.get_columns("card_references")
        }
        if "released_at" not in reference_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE card_references ADD COLUMN released_at DATE")
                )
        additive_reference_columns = {
            "oracle_id": "VARCHAR(36)",
            "language": "VARCHAR(16) DEFAULT 'en'",
            "oracle_text": "TEXT",
            "artist": "VARCHAR(255)",
            "promo_types": "TEXT",
            "finishes": "TEXT",
            "color_identity": "VARCHAR(16) DEFAULT ''",
            "rarity": "VARCHAR(32)",
            "type_line": "VARCHAR(255)",
        }
        for column_name, column_type in additive_reference_columns.items():
            if column_name not in reference_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            f"ALTER TABLE card_references ADD COLUMN {column_name} {column_type}"
                        )
                    )
        # These indexes make local language and oracle-family lookups cheap.
        refreshed_columns = {
            column["name"] for column in inspect(engine).get_columns("card_references")
        }
        existing_indexes = {
            index["name"] for index in inspect(engine).get_indexes("card_references")
        }
        for column_name in ("oracle_id", "language", "released_at"):
            index_name = f"ix_card_references_{column_name}"
            if column_name in refreshed_columns and index_name not in existing_indexes:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            f"CREATE INDEX {index_name} ON card_references ({column_name})"
                        )
                    )
    if "card_visual_fingerprints" not in tables:
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
    if "descriptor_path" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE card_visual_fingerprints "
                    "ADD COLUMN descriptor_path TEXT"
                )
            )
    example_columns = {
        column["name"] for column in inspector.get_columns("card_visual_examples")
    }
    if "descriptor_path" not in example_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE card_visual_examples ADD COLUMN descriptor_path TEXT")
            )
    if "source_review_id" not in example_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE card_visual_examples ADD COLUMN source_review_id VARCHAR(36)")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_card_visual_examples_source_review_id "
                    "ON card_visual_examples (source_review_id)"
                )
            )
