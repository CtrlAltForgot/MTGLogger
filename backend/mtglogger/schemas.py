from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .models import InventoryStatus, ReviewStatus


class InventoryCreate(BaseModel):
    card_name: str
    set_code: str
    set_name: str
    collector_number: str
    scryfall_id: str
    oracle_id: str | None = None
    quantity: int = Field(1, ge=1)
    foil: bool = False
    language: str = "en"
    condition: str = "near_mint"
    purchase_price: Decimal | None = None
    market_price: Decimal | None = None
    storage_location: str = "Unsorted"
    collection_name: str = "Main"
    image_url: str | None = None
    notes: str | None = None
    color_identity: str = ""
    rarity: str | None = None
    type_line: str | None = None
    status: InventoryStatus = InventoryStatus.owned


class InventoryUpdate(BaseModel):
    quantity: int | None = Field(None, ge=0)
    foil: bool | None = None
    language: str | None = None
    condition: str | None = None
    market_price: Decimal | None = None
    purchase_price: Decimal | None = None
    storage_location: str | None = None
    collection_name: str | None = None
    notes: str | None = None
    status: InventoryStatus | None = None


class InventoryRead(InventoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    date_added: datetime
    updated_at: datetime


class Page(BaseModel):
    items: list[InventoryRead]
    total: int
    page: int
    page_size: int


class ScanDefaults(BaseModel):
    condition: str = "near_mint"
    foil: bool = False
    language: str = "en"
    storage_location: str = "Unsorted"
    collection_name: str = "Main"
    status: InventoryStatus = InventoryStatus.owned
    box_set_code: str | None = None
    auto_add: bool = True


class Candidate(BaseModel):
    scryfall_id: str
    name: str
    set_code: str
    set_name: str
    collector_number: str
    image_url: str | None = None
    market_price: Decimal | None = None
    confidence: float
    oracle_id: str | None = None
    color_identity: str = ""
    rarity: str | None = None
    type_line: str | None = None


class ScanResult(BaseModel):
    disposition: str
    confidence: float
    inventory: InventoryRead | None = None
    candidates: list[Candidate] = []
    review_id: str | None = None
    message: str


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    image_path: str
    confidence: float
    ocr_text: str | None
    status: ReviewStatus
    created_at: datetime
    candidates: list[Candidate] = []


class ReviewResolve(BaseModel):
    candidate: Candidate
    defaults: ScanDefaults = ScanDefaults()


class SealedCreate(BaseModel):
    name: str
    product_type: str
    set_code: str | None = None
    quantity: int = Field(1, ge=1)
    purchase_price: Decimal | None = None
    market_price: Decimal | None = None
    storage_location: str = "Unsorted"
    notes: str | None = None


class SealedRead(SealedCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    date_added: datetime


class DashboardSummary(BaseModel):
    total_value: Decimal
    total_cards: int
    unique_printings: int
    review_count: int
    by_set: list[dict]
    by_color: list[dict]
    by_rarity: list[dict]
    most_valuable: list[InventoryRead]
    newest: list[InventoryRead]
