import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uid() -> str:
    return str(uuid.uuid4())


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
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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


class ReviewItem(Base):
    __tablename__ = "review_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    image_path: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(default=0)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus), default=ReviewStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    resolved_inventory_id: Mapped[str | None] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=True
    )
    resolved_inventory: Mapped[InventoryItem | None] = relationship()


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
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
