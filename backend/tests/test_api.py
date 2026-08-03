CARD = {
    "card_name": "Lightning Bolt",
    "set_code": "fdn",
    "set_name": "Foundations",
    "collector_number": "188",
    "scryfall_id": "00000000-0000-0000-0000-000000000001",
    "oracle_id": "00000000-0000-0000-0000-000000000002",
    "market_price": "0.42",
    "rarity": "uncommon",
    "color_identity": "R",
    "type_line": "Instant",
}


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_duplicate_printing_increments_quantity(client):
    first = client.post("/api/inventory", json=CARD)
    second = client.post("/api/inventory", json=CARD)
    assert first.status_code == 201
    assert second.json()["quantity"] == 2
    page = client.get("/api/inventory").json()
    assert page["total"] == 1
    assert page["items"][0]["quantity"] == 2


def test_dashboard_and_exports(client):
    client.post("/api/inventory", json=CARD)
    summary = client.get("/api/dashboard/summary").json()
    assert summary["total_cards"] == 1
    assert summary["total_value"] == "0.42"
    assert client.get("/api/inventory").json()["collection_value"] == "0.42"
    assert "Lightning Bolt" in client.get("/api/inventory/export/csv").text
    assert client.get("/api/inventory/export/json").json()[0]["set_code"] == "fdn"


def test_inventory_organization_facets_and_filters(client):
    client.post(
        "/api/inventory",
        json={**CARD, "collection_name": "Trade Binder", "storage_location": "Shelf A"},
    )
    client.post(
        "/api/inventory",
        json={
            **CARD,
            "scryfall_id": "00000000-0000-0000-0000-000000000003",
            "collection_name": "Main",
            "storage_location": "Box 2",
        },
    )
    facets = client.get("/api/inventory/facets").json()
    assert facets["collections"] == ["Main", "Trade Binder"]
    assert facets["storage_locations"] == ["Box 2", "Shelf A"]
    filtered = client.get(
        "/api/inventory", params={"collection_name": "Trade Binder", "storage_location": "Shelf A"}
    ).json()
    assert filtered["total"] == 1


def test_sealed_inventory(client):
    response = client.post(
        "/api/sealed",
        json={"name": "Foundations Play Booster Box", "product_type": "booster_box", "quantity": 2},
    )
    assert response.status_code == 201
    assert client.get("/api/sealed").json()[0]["quantity"] == 2
    item_id = response.json()["id"]
    updated = client.patch(
        f"/api/sealed/{item_id}",
        json={
            "quantity": 3,
            "market_price": "149.99",
            "storage_location": "Shelf B",
            "notes": "Keep sealed",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["quantity"] == 3
    assert updated.json()["market_price"] == "149.99"
    assert updated.json()["storage_location"] == "Shelf B"
    assert client.patch(f"/api/sealed/{item_id}", json={"quantity": 0}).status_code == 422
    assert client.delete(f"/api/sealed/{item_id}").status_code == 204
    assert client.get("/api/sealed").json() == []


def test_decks_allocate_only_unassigned_physical_copies(client):
    card = {**CARD, "quantity": 3}
    inventory = client.post("/api/inventory", json=card).json()
    first = client.post("/api/decks", json={"name": "First Deck", "format": "Commander"})
    second = client.post("/api/decks", json={"name": "Second Deck"})
    assert first.status_code == 201
    first_id, second_id = first.json()["id"], second.json()["id"]
    allocation = {"entries": [{"inventory_id": inventory["id"], "quantity": 2}]}
    deck = client.post(f"/api/decks/{first_id}/entries", json=allocation)
    assert deck.status_code == 200
    assert deck.json()["total_cards"] == 2
    listed = client.get("/api/inventory").json()["items"][0]
    assert listed["deck_assignments"] == [
        {"deck_id": first_id, "deck_name": "First Deck", "quantity": 2}
    ]
    protected = client.patch(f"/api/inventory/{inventory['id']}", json={"quantity": 1})
    assert protected.status_code == 409
    assert "2 copies assigned" in protected.json()["detail"]
    resized = client.patch(f"/api/inventory/{inventory['id']}", json={"quantity": 2})
    assert resized.status_code == 200
    available = client.get(f"/api/decks/{first_id}/available").json()
    assert available == []
    too_many = client.post(
        f"/api/decks/{second_id}/entries",
        json={"entries": [{"inventory_id": inventory["id"], "quantity": 2}]},
    )
    assert too_many.status_code == 409
    assert (
        client.post(
            f"/api/decks/{second_id}/entries",
            json={"entries": [{"inventory_id": inventory["id"], "quantity": 1}]},
        ).status_code
        == 409
    )
    assert client.delete(f"/api/decks/{first_id}").status_code == 204
    available = client.get(f"/api/decks/{second_id}/available").json()
    assert available[0]["available_quantity"] == 2


def test_recognition_reference_status(client):
    response = client.get("/api/references/status")
    assert response.status_code == 200
    assert response.json()["indexed_cards"] == 0


def test_artwork_hash_is_stable_under_small_brightness_change():
    import cv2
    import numpy as np

    from mtglogger.services.references import artwork_hash, hash_distance

    image = np.zeros((1040, 745, 3), dtype=np.uint8)
    cv2.rectangle(image, (50, 130), (690, 590), (30, 170, 220), -1)
    cv2.circle(image, (360, 350), 120, (230, 40, 80), -1)
    brighter = cv2.convertScaleAbs(image, alpha=1.02, beta=3)
    assert hash_distance(artwork_hash(image), artwork_hash(brighter)) <= 2


def test_ocr_hints_normalize_printed_collector_number():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Abrade\nInstant\nU 0188\nT™ & © 2024 Wizards of the Coast\nFDN · EN"
    )
    assert title == "Abrade"
    assert number == "0188"
    assert set_code == "fdn"
    assert year == 2024


def test_ocr_hints_read_set_code_without_spaces():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Shadows of the Past\nEnchantment\n& 2015 Wizands of the Const\n118/272\nORI·EN RYANYEE"
    )
    assert title == "Shadows of the Past"
    assert number == "118"
    assert set_code == "ori"
    assert year == 2015


def test_ocr_hints_skip_mana_cost_noise_above_title():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "2@\nConsecrated by Blood\nEnchantment\n087/272U\n"
        "M&2015WizardsoftheCoast\nORI·EN IOHNSTANKO"
    )
    assert title == "Consecrated by Blood"
    assert number == "087"
    assert set_code == "ori"
    assert year == 2015


def test_ocr_hints_recover_legacy_collector_pair_from_copyright_line():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Death's Approach\nEnchantment-Aura\nTerese Nielsen\n& 2013 Wizards of the Coast 02 249"
    )
    assert title == "Death's Approach"
    assert number == "02"
    assert set_code is None
    assert year == 2013
    assert CardRecognizer.collector_score(number, "62") > CardRecognizer.collector_score(
        number, "222"
    )


def test_scans_auto_add_only_near_certain_matches_by_default():
    from mtglogger.schemas import ScanDefaults

    assert ScanDefaults().auto_add is True
    assert ScanDefaults(auto_add=False).auto_add is False


def test_scan_results_expose_recognition_latency():
    from mtglogger.schemas import ScanResult

    result = ScanResult(
        disposition="queued",
        confidence=0,
        candidates=[],
        message="Saved to review queue",
        processing_ms=2784,
    )
    assert result.processing_ms == 2784


def test_review_serialization_preserves_scan_defaults():
    from datetime import UTC, datetime

    from mtglogger.api.reviews import serialize
    from mtglogger.models import ReviewItem, ReviewStatus

    review = ReviewItem(
        image_path="scan.jpg",
        id="review-1",
        confidence=42,
        status=ReviewStatus.pending,
        created_at=datetime.now(UTC),
        candidates_json='{"candidates": [], "defaults": {"foil": true, '
        '"condition": "lightly_played", "storage_location": "Box 7", '
        '"deck_id": "deck-1"}}',
    )
    serialized = serialize(review)
    assert serialized.defaults.foil is True
    assert serialized.defaults.condition == "lightly_played"
    assert serialized.defaults.storage_location == "Box 7"
    assert serialized.defaults.deck_id == "deck-1"


def test_review_serialization_supports_legacy_candidate_lists():
    from datetime import UTC, datetime

    from mtglogger.api.reviews import serialize
    from mtglogger.models import ReviewItem, ReviewStatus

    review = ReviewItem(
        id="review-2",
        image_path="scan.jpg",
        candidates_json="[]",
        confidence=0,
        status=ReviewStatus.pending,
        created_at=datetime.now(UTC),
    )
    serialized = serialize(review)
    assert serialized.candidates == []
    assert serialized.defaults.storage_location == "Unsorted"


def test_inventory_updates_allow_physical_card_attributes():
    from mtglogger.schemas import InventoryUpdate

    update = InventoryUpdate(foil=True, language="ja", condition="lightly_played")
    assert update.foil is True
    assert update.language == "ja"


def test_candidate_supports_separate_foil_price():
    from mtglogger.schemas import Candidate

    candidate = Candidate(
        scryfall_id=CARD["scryfall_id"],
        name=CARD["card_name"],
        set_code=CARD["set_code"],
        set_name=CARD["set_name"],
        collector_number=CARD["collector_number"],
        market_price="0.42",
        foil_market_price="1.25",
        confidence=99.5,
    )
    assert str(candidate.market_price) == "0.42"
    assert str(candidate.foil_market_price) == "1.25"


def test_candidate_identifies_only_metadata_proven_foil_printings():
    from mtglogger.schemas import Candidate

    common = {
        "scryfall_id": CARD["scryfall_id"],
        "name": CARD["card_name"],
        "set_code": CARD["set_code"],
        "set_name": CARD["set_name"],
        "collector_number": CARD["collector_number"],
        "confidence": 99.5,
    }
    assert Candidate(**common, finishes=["foil"]).is_foil_only() is True
    assert Candidate(**common, finishes=["etched"]).is_foil_only() is True
    assert Candidate(**common, finishes=["nonfoil", "foil"]).is_foil_only() is False
    assert Candidate(**common).is_foil_only() is False


def test_inventory_delete_preserves_resolved_review_history():
    from mtglogger.api.inventory import delete_item_preserving_reviews
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import InventoryItem, ReviewItem, ReviewStatus

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        item = InventoryItem(**CARD)
        db.add(item)
        db.flush()
        review = ReviewItem(
            image_path="test.jpg",
            candidates_json="[]",
            status=ReviewStatus.resolved,
            resolved_inventory_id=item.id,
        )
        db.add(review)
        db.commit()
        item_id, review_id = item.id, review.id
        delete_item_preserving_reviews(db, item)
        assert db.get(InventoryItem, item_id) is None
        assert db.get(ReviewItem, review_id).resolved_inventory_id is None
