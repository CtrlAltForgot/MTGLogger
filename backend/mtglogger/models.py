import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class InventoryStatus(str, enum.Enum):
    owned = "owned"
    wishlist = "wishlist"
    for_trade = "for_trade"
    for_sale = "for_sale"
    loaned = "loaned"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    resolved = "resolved"
    ignored = "ignored"


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint(
            "scryfall_id",
            "foil",
            "language",
            "condition",
            "collection_name",
            "storage_location",
            "status",
            name="uq_inventory_printing_defaults",
        ),
        Index("ix_inventory_name", "card_name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    card_name: Mapped[str] = mapped_column(String(255), index=True)
    set_code: Mapped[str] = mapped_column(String(16), index=True)
    set_name: Mapped[str] = mapped_column(String(255))
    collector_number: Mapped[str] = mapped_column(String(32))
    scryfall_id: Mapped[str] = mapped_column(String(36), index=True)
    oracle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quantity: Mapped[int] = mapped_column(default=1)
    foil: Mapped[bool] = mapped_column(default=False)
    language: Mapped[str] = mapped_column(String(16), default="en")
    condition: Mapped[str] = mapped_column(String(32), default="near_mint")
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    storage_location: Mapped[str] = mapped_column(String(255), default="Unsorted")
    collection_name: Mapped[str] = mapped_column(String(255), default="Main")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_identity: Mapped[str] = mapped_column(String(16), default="")
    rarity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[InventoryStatus] = mapped_column(
        Enum(InventoryStatus), default=InventoryStatus.owned
    )
    deck_entries: Mapped[list["DeckEntry"]] = relationship(
        back_populates="inventory", cascade="all, delete-orphan"
    )
    price_snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="inventory", cascade="all, delete-orphan"
    )

    @property
    def deck_assignments(self) -> list[dict]:
        return [
            {"deck_id": entry.deck_id, "deck_name": entry.deck.name, "quantity": entry.quantity}
            for entry in self.deck_entries
        ]


class ReviewItem(Base):
    __tablename__ = "review_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    image_path: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(default=0)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus), default=ReviewStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_inventory_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=True
    )
    resolved_inventory: Mapped[InventoryItem | None] = relationship()


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    inventory_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    market_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    inventory: Mapped[InventoryItem] = relationship(back_populates="price_snapshots")


class CollectionValueSnapshot(Base):
    __tablename__ = "collection_value_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    total_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class SealedProduct(Base):
    __tablename__ = "sealed_products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    product_type: Mapped[str] = mapped_column(String(64))
    set_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    quantity: Mapped[int] = mapped_column(default=1)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    storage_location: Mapped[str] = mapped_column(String(255), default="Unsorted")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CardReference(Base):
    __tablename__ = "card_references"
    __table_args__ = (Index("ix_reference_set", "set_code"),)
    scryfall_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    set_code: Mapped[str] = mapped_column(String(16))
    set_name: Mapped[str] = mapped_column(String(255))
    collector_number: Mapped[str] = mapped_column(String(32))
    released_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    image_url: Mapped[str] = mapped_column(Text)
    art_hash: Mapped[str] = mapped_column(String(16), index=True)
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    visual_examples: Mapped[list["CardVisualExample"]] = relationship(
        back_populates="reference", cascade="all, delete-orphan"
    )
    fingerprint: Mapped["CardVisualFingerprint | None"] = relationship(
        back_populates="reference", cascade="all, delete-orphan", uselist=False
    )


class CardVisualFingerprint(Base):
    """Compact visual catalog entry derived from a canonical card image."""

    __tablename__ = "card_visual_fingerprints"
    scryfall_id: Mapped[str] = mapped_column(
        ForeignKey("card_references.scryfall_id", ondelete="CASCADE"), primary_key=True
    )
    full_hash: Mapped[str] = mapped_column(String(16), index=True)
    art_hash: Mapped[str] = mapped_column(String(16), index=True)
    title_hash: Mapped[str] = mapped_column(String(16), index=True)
    footer_hash: Mapped[str] = mapped_column(String(16), index=True)
    symbol_hash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    frame_hash: Mapped[str] = mapped_column(String(16), index=True)
    language: Mapped[str] = mapped_column(String(16), default="en", index=True)
    layout: Mapped[str] = mapped_column(String(32), default="normal")
    cached_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    reference: Mapped[CardReference] = relationship(back_populates="fingerprint")


class CardVisualExample(Base):
    __tablename__ = "card_visual_examples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    scryfall_id: Mapped[str] = mapped_column(
        ForeignKey("card_references.scryfall_id", ondelete="CASCADE"), index=True
    )
    art_hash: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reference: Mapped[CardReference] = relationship(back_populates="visual_examples")


class Deck(Base):
    __tablename__ = "decks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    entries: Mapped[list["DeckEntry"]] = relationship(
        back_populates="deck", cascade="all, delete-orphan", order_by="DeckEntry.id"
    )


class DeckEntry(Base):
    __tablename__ = "deck_entries"
    __table_args__ = (UniqueConstraint("deck_id", "inventory_id", name="uq_deck_inventory"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deck_id: Mapped[str] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    inventory_id: Mapped[str] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(default=1)
    deck: Mapped[Deck] = relationship(back_populates="entries")
    inventory: Mapped[InventoryItem] = relationship(back_populates="deck_entries")
