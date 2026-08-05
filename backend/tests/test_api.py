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


def test_eur_price_is_used_only_when_native_usd_is_missing():
    from decimal import Decimal

    from mtglogger.services.prices import _price

    card = {"prices": {"usd_foil": None, "eur_foil": "0.18"}}
    assert _price(card, True, Decimal("1.15")) == Decimal("0.21")
    card["prices"]["usd_foil"] = "0.19"
    assert _price(card, True, Decimal("1.15")) == Decimal("0.19")


def test_core_set_and_confusable_set_codes_are_exact_matches():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.set_code_score("M3", "m13") == 1.0
    assert CardRecognizer.set_code_score("MIS", "m15") == 1.0
    assert not CardRecognizer.exact_set_code_match("M3", "m13")
    assert CardRecognizer.exact_set_code_match("MIS", "m15")
    assert CardRecognizer.has_unique_printing_signal(
        0.94, None, 0.45, [], "MIS", "m15", 1
    )


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_upload_check_consumes_camera_payload_without_persisting(client):
    payload = b"x" * (2 * 1024 * 1024)
    response = client.post(
        "/api/scanner/upload-check",
        files={"image": ("probe.bin", payload, "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "bytes": len(payload)}
    assert client.get("/api/inventory").json()["total"] == 0
    assert client.get("/api/reviews").json() == []


def test_upload_check_rejects_payloads_above_recognition_limit(client):
    response = client.post(
        "/api/scanner/upload-check",
        files={"image": ("probe.bin", b"x" * 15_000_001, "application/octet-stream")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Image is larger than 15 MB"


def test_duplicate_printing_increments_quantity(client):
    first = client.post("/api/inventory", json=CARD)
    second = client.post("/api/inventory", json=CARD)
    assert first.status_code == 201
    assert second.json()["quantity"] == 2
    page = client.get("/api/inventory").json()
    assert page["total"] == 1
    assert page["total_cards"] == 2
    assert page["items"][0]["quantity"] == 2


def test_duplicate_printing_becomes_most_recent_collection_activity(client):
    first = client.post("/api/inventory", json=CARD).json()
    client.post(
        "/api/inventory",
        json={
            **CARD,
            "card_name": "Later Card",
            "scryfall_id": "00000000-0000-0000-0000-000000000099",
        },
    )

    duplicate = client.post("/api/inventory", json=CARD).json()
    recent = client.get("/api/inventory", params={"sort": "updated_at", "descending": True}).json()[
        "items"
    ]

    assert duplicate["id"] == first["id"]
    assert duplicate["updated_at"] > first["updated_at"]
    assert recent[0]["id"] == first["id"]


def test_inventory_finish_move_splits_foil_and_nonfoil_quantities(monkeypatch):
    import asyncio
    from decimal import Decimal

    from mtglogger.api import inventory
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import InventoryItem
    from mtglogger.schemas import InventoryFinishMove

    async def foil_price(_scryfall_id: str, foil: bool):
        return Decimal("2.75" if foil else "0.42")

    monkeypatch.setattr(inventory, "finish_price", foil_price)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        item = InventoryItem(**CARD, quantity=3)
        db.add(item)
        db.commit()
        target = asyncio.run(
            inventory.move_inventory_finish(item.id, InventoryFinishMove(foil=True, quantity=1), db)
        )
        variants = list(db.query(InventoryItem).all())

    assert target.foil is True
    assert target.quantity == 1
    assert target.market_price == Decimal("2.75")
    assert {(variant.foil, variant.quantity) for variant in variants} == {
        (False, 2),
        (True, 1),
    }


def test_inventory_copy_move_splits_selected_finish_and_condition(monkeypatch):
    import asyncio
    from decimal import Decimal

    from mtglogger.api import inventory
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import InventoryItem
    from mtglogger.schemas import InventoryCopyMove

    async def foil_price(_scryfall_id: str, foil: bool):
        return Decimal("2.75" if foil else "0.42")

    monkeypatch.setattr(inventory, "finish_price", foil_price)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        item = InventoryItem(**CARD, quantity=3)
        db.add(item)
        db.commit()
        target = asyncio.run(
            inventory.move_inventory_copies(
                item.id,
                InventoryCopyMove(quantity=2, foil=True, condition="lightly_played"),
                db,
            )
        )
        variants = list(db.query(InventoryItem).all())

    assert target.quantity == 2
    assert target.foil is True
    assert target.condition == "lightly_played"
    assert target.market_price == Decimal("2.75")
    assert {(variant.foil, variant.condition, variant.quantity) for variant in variants} == {
        (False, "near_mint", 1),
        (True, "lightly_played", 2),
    }


def test_inventory_copy_move_rejects_copies_assigned_to_deck(client):
    item = client.post("/api/inventory", json={**CARD, "quantity": 2}).json()
    deck = client.post("/api/decks", json={"name": "Burn"}).json()
    client.post(
        f"/api/decks/{deck['id']}/entries",
        json={"entries": [{"inventory_id": item["id"], "quantity": 1}]},
    )

    moved = client.post(
        f"/api/inventory/{item['id']}/move-copies",
        json={"quantity": 2, "foil": False, "condition": "lightly_played"},
    )
    assert moved.status_code == 409
    assert moved.json()["detail"] == "Only 1 unassigned copies can be changed"


def test_price_changes_retain_previous_value_and_collection_history(client):
    item = client.post("/api/inventory", json=CARD).json()
    first = client.patch(f"/api/inventory/{item['id']}", json={"market_price": "0.84"})
    assert first.status_code == 200

    listed = client.get("/api/inventory").json()["items"][0]
    assert listed["market_price"] == "0.84"
    assert listed["previous_market_price"] == "0.42"

    second = client.patch(f"/api/inventory/{item['id']}", json={"market_price": "1.26"})
    assert second.status_code == 200
    history = client.get("/api/prices/history").json()
    assert history["range"] == "1d"
    assert history["current_value"] == 1.26
    assert history["previous_value"] is None
    assert history["change"] is None
    assert history["change_percentage"] is None
    assert history["window_start"] < history["window_end"]
    assert len(history["history"]) == 2
    assert client.get("/api/prices/history", params={"range": "all"}).status_code == 422


def test_value_history_persists_its_initial_baseline(client):
    client.post("/api/inventory", json=CARD)
    first = client.get("/api/prices/history").json()
    second = client.get("/api/prices/history").json()

    assert first["history"] == second["history"]
    assert first["current_value"] == 0.42


def test_inventory_pagination_preserves_full_collection_totals(client):
    for index, name in enumerate(("Alpha", "Beta", "Gamma"), start=10):
        response = client.post(
            "/api/inventory",
            json={
                **CARD,
                "card_name": name,
                "collector_number": str(index),
                "scryfall_id": f"00000000-0000-0000-0000-{index:012d}",
                "market_price": "1.00",
            },
        )
        assert response.status_code == 201

    first = client.get(
        "/api/inventory",
        params={"sort": "card_name", "descending": False, "page": 1, "page_size": 2},
    ).json()
    second = client.get(
        "/api/inventory",
        params={"sort": "card_name", "descending": False, "page": 2, "page_size": 2},
    ).json()

    assert first["total"] == second["total"] == 3
    assert first["collection_value"] == second["collection_value"] == "3.00"
    assert first["page_size"] == second["page_size"] == 2
    assert [item["card_name"] for item in first["items"]] == ["Alpha", "Beta"]
    assert [item["card_name"] for item in second["items"]] == ["Gamma"]


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
    assert available["total"] == 0
    assert available["items"] == []
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
    assert available["total"] == 1
    assert available["items"][0]["available_quantity"] == 2


def test_deck_builder_pages_through_all_unassigned_entries(client):
    for index, name in enumerate(("Alpha", "Beta", "Gamma"), start=20):
        response = client.post(
            "/api/inventory",
            json={
                **CARD,
                "card_name": name,
                "collector_number": str(index),
                "scryfall_id": f"00000000-0000-0000-0000-{index:012d}",
            },
        )
        assert response.status_code == 201
    deck_id = client.post("/api/decks", json={"name": "Paged Deck"}).json()["id"]

    first = client.get(f"/api/decks/{deck_id}/available", params={"page": 1, "page_size": 2}).json()
    second = client.get(
        f"/api/decks/{deck_id}/available", params={"page": 2, "page_size": 2}
    ).json()

    assert first["total"] == second["total"] == 3
    assert [item["inventory"]["card_name"] for item in first["items"]] == ["Alpha", "Beta"]
    assert [item["inventory"]["card_name"] for item in second["items"]] == ["Gamma"]


def test_deck_format_suggestions_require_legality_and_structure(monkeypatch):
    import asyncio

    from mtglogger.api import decks
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import Deck, DeckEntry, InventoryItem

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        land = InventoryItem(**{**CARD, "type_line": "Basic Land — Swamp"}, quantity=60)
        deck = Deck(name="Old deck")
        db.add_all([land, deck])
        db.flush()
        db.add(DeckEntry(deck_id=deck.id, inventory_id=land.id, quantity=60))
        db.commit()

        async def cards(_provider, _ids):
            return [
                {
                    "id": land.scryfall_id,
                    "type_line": "Basic Land — Swamp",
                    "color_identity": ["B"],
                    "legalities": {"standard": "legal", "pioneer": "legal"},
                }
            ]

        monkeypatch.setattr(decks.ScryfallProvider, "get_cards", cards)
        result = asyncio.run(decks.format_suggestions(deck.id, db))

    assert result.complete_deck is True
    assert result.suggestions[0].format == "Standard"
    assert result.suggestions[0].confidence == "high"
    assert "60 total cards" in result.suggestions[0].reasons[0]
    assert result.suggestions[-1].format == "Casual / Kitchen Table"


def test_recognition_reference_status(client):
    response = client.get("/api/references/status")
    assert response.status_code == 200
    status = response.json()
    assert status["indexed_cards"] == 0
    assert status["fingerprinted_cards"] == 0
    assert status["cached_images"] == 0
    assert status["catalog_total"] is None
    assert status["coverage_percent"] is None
    assert status["estimated_seconds_remaining"] is None
    assert client.get("/api/references/sets").json() == []
    cards = client.get("/api/references/cards", params={"set_code": "ori"}).json()
    assert cards == {"items": [], "total": 0, "page": 1, "page_size": 40}


def test_exact_printing_details_preserve_scryfall_identity():
    from mtglogger.api.references import serialize_card_details

    details = serialize_card_details(
        {
            "id": "00000000-0000-0000-0000-000000000099",
            "oracle_id": "00000000-0000-0000-0000-000000000100",
            "name": "Test Card",
            "set": "tst",
            "set_name": "Test Set",
            "collector_number": "42",
            "image_uris": {"normal": "https://example.test/42.jpg"},
            "mana_cost": "{2}{B}",
            "type_line": "Creature — Test",
            "oracle_text": "Exact printing details.",
            "prices": {"usd": "1.25", "usd_foil": "2.50"},
            "legalities": {"commander": "legal"},
            "finishes": ["nonfoil", "foil"],
        }
    )
    assert details["scryfall_id"] == "00000000-0000-0000-0000-000000000099"
    assert details["collector_number"] == "42"
    assert details["image_url"] == "https://example.test/42.jpg"
    assert details["prices"]["usd_foil"] == "2.50"


def test_reference_priority_sets_are_normalized(monkeypatch):
    from mtglogger.config import Settings

    settings = Settings(reference_priority_sets=" ORI,ktk, M15 ,,ori ")
    assert settings.priority_reference_sets == ["ori", "ktk", "m15"]


def test_artwork_hash_is_stable_under_small_brightness_change():
    import cv2
    import numpy as np

    from mtglogger.services.references import artwork_hash, hash_distance

    image = np.zeros((1040, 745, 3), dtype=np.uint8)
    cv2.rectangle(image, (50, 130), (690, 590), (30, 170, 220), -1)
    cv2.circle(image, (360, 350), 120, (230, 40, 80), -1)
    brighter = cv2.convertScaleAbs(image, alpha=1.02, beta=3)
    assert hash_distance(artwork_hash(image), artwork_hash(brighter)) <= 2


def test_multi_region_visual_fingerprints_are_stable_and_distinct():
    import cv2
    import numpy as np

    from mtglogger.services.references import hash_distance, visual_fingerprints

    image = np.zeros((1040, 745, 3), dtype=np.uint8)
    cv2.rectangle(image, (30, 45), (715, 150), (210, 210, 210), -1)
    cv2.rectangle(image, (45, 160), (700, 600), (30, 170, 220), -1)
    cv2.circle(image, (360, 380), 120, (230, 40, 80), -1)
    cv2.putText(image, "123/272 ORI EN", (55, 1010), 1, 2, (255, 255, 255), 2)
    brighter = cv2.convertScaleAbs(image, alpha=1.02, beta=3)

    original = visual_fingerprints(image)
    adjusted = visual_fingerprints(brighter)

    assert set(original) == {
        "full_hash",
        "art_hash",
        "title_hash",
        "footer_hash",
        "symbol_hash",
        "frame_hash",
    }
    assert all(len(value) == 16 for value in original.values())
    assert all(hash_distance(original[key], adjusted[key]) <= 4 for key in original)


def test_local_artwork_descriptors_prefer_same_art_under_camera_changes():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer
    from mtglogger.services.references import artwork_descriptors

    image = np.zeros((840, 600, 3), dtype=np.uint8)
    rng = np.random.default_rng(42)
    texture = rng.integers(0, 256, (386, 534, 3), dtype=np.uint8)
    image[101:487, 33:567] = texture
    adjusted = cv2.convertScaleAbs(image, alpha=1.04, beta=5)
    different_image = np.zeros_like(image)
    different_image[101:487, 33:567] = rng.integers(0, 256, (386, 534, 3), dtype=np.uint8)

    query = artwork_descriptors(adjusted)
    same = CardRecognizer._descriptor_score(query, artwork_descriptors(image))
    different = CardRecognizer._descriptor_score(query, artwork_descriptors(different_image))

    assert same is not None and different is not None
    assert same > different + 10


def test_exact_print_descriptor_regions_separate_reused_artwork():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    rng = np.random.default_rng(84)
    art = rng.integers(0, 256, (64, 32), dtype=np.uint8)
    footer = rng.integers(0, 256, (48, 32), dtype=np.uint8)
    symbol = rng.integers(0, 256, (32, 32), dtype=np.uint8)
    scan = {"art": art, "footer": footer, "symbol": symbol}
    exact = {"art": art.copy(), "footer": footer.copy(), "symbol": symbol.copy()}
    reused_art = {
        "art": art.copy(),
        "footer": rng.integers(0, 256, (48, 32), dtype=np.uint8),
        "symbol": rng.integers(0, 256, (32, 32), dtype=np.uint8),
    }

    exact_score = CardRecognizer._descriptor_bundle_score(scan, exact)
    reused_score = CardRecognizer._descriptor_bundle_score(scan, reused_art)

    assert exact_score is not None and reused_score is not None
    assert exact_score > reused_score + 40


def test_basic_land_art_outvotes_one_wrong_footer_digit():
    """A shared set symbol and misread footer must not beat the actual artwork."""
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    rng = np.random.default_rng(261264)
    scanned_art = rng.integers(0, 256, (64, 32), dtype=np.uint8)
    scanned_footer = rng.integers(0, 256, (48, 32), dtype=np.uint8)
    shared_symbol = rng.integers(0, 256, (32, 32), dtype=np.uint8)
    scan = {
        "art": scanned_art,
        "footer": scanned_footer,
        "symbol": shared_symbol,
    }
    actual_print = {
        "art": scanned_art.copy(),
        "footer": rng.integers(0, 256, (48, 32), dtype=np.uint8),
        "symbol": shared_symbol.copy(),
    }
    ocr_selected_wrong_print = {
        "art": rng.integers(0, 256, (64, 32), dtype=np.uint8),
        "footer": scanned_footer.copy(),
        "symbol": shared_symbol.copy(),
    }

    actual_score = CardRecognizer._descriptor_bundle_score(scan, actual_print)
    wrong_score = CardRecognizer._descriptor_bundle_score(scan, ocr_selected_wrong_print)

    assert actual_score is not None and wrong_score is not None
    assert actual_score > wrong_score + 10


def test_multi_region_fingerprint_ranks_matching_footer_above_reprint():
    from types import SimpleNamespace

    from mtglogger.services.recognition import CardRecognizer

    scan = {
        "art_hash": "0000000000000000",
        "full_hash": "0000000000000000",
        "title_hash": "0000000000000000",
        "footer_hash": "0000000000000000",
        "symbol_hash": "0000000000000000",
        "frame_hash": "0000000000000000",
    }
    exact = SimpleNamespace(**scan)
    reused_art = SimpleNamespace(
        **{**scan, "full_hash": "ffffffffffffffff", "footer_hash": "ffffffffffffffff"}
    )

    assert CardRecognizer._fingerprint_score(scan, exact, 0) == 99.5
    assert CardRecognizer._fingerprint_score(scan, exact, 0) > (
        CardRecognizer._fingerprint_score(scan, reused_art, 0)
    )


def test_footer_enhancement_preserves_dimensions_and_increases_local_contrast():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    footer = np.full((180, 600, 3), 92, dtype=np.uint8)
    cv2.putText(footer, "123/272 ORI EN", (20, 110), 1, 2, (112, 112, 112), 2)
    enhanced = CardRecognizer.enhance_footer(footer)

    assert enhanced.shape == footer.shape
    assert enhanced.std() > footer.std()


def test_perspective_quad_expands_to_preserve_printed_footer():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    quad = np.array([[10, 10], [90, 10], [90, 190], [10, 190]], dtype="float32")
    expanded = CardRecognizer.expand_quad(quad, (200, 100, 3))

    assert expanded[0, 0] < quad[0, 0]
    assert expanded[0, 1] < quad[0, 1]
    assert expanded[2, 0] > quad[2, 0]
    assert expanded[2, 1] > quad[2, 1]
    assert expanded.min() >= 0
    assert expanded[:, 0].max() <= 99
    assert expanded[:, 1].max() <= 199


def test_rectify_finds_a_small_card_away_from_frame_center():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(frame, (55, 110), (295, 450), (110, 110, 110), -1)
    cv2.rectangle(frame, (55, 110), (295, 450), (255, 255, 255), 7)

    corrected = CardRecognizer.rectify(frame)

    assert corrected.shape == (840, 600, 3)
    assert corrected.mean() > 70


def test_rectify_joins_disconnected_off_center_sleeved_card_edges():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Four disconnected border segments mimic glare/wear breaking a sleeve edge.
    cv2.line(frame, (60, 45), (760, 45), (245, 245, 245), 8)
    cv2.line(frame, (60, 675), (760, 675), (245, 245, 245), 8)
    cv2.line(frame, (60, 45), (60, 675), (245, 245, 245), 8)
    cv2.line(frame, (760, 45), (760, 675), (245, 245, 245), 8)
    for y in (120, 390, 500, 630):
        cv2.line(frame, (110, y), (710, y), (180, 180, 180), 5)

    corrected = CardRecognizer.rectify(frame)

    assert corrected.shape == (840, 600, 3)
    assert CardRecognizer.has_card_structure(corrected)


def test_rectify_prefers_full_card_over_large_internal_mana_panel():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for start, end in (
        ((60, 45), (760, 45)),
        ((60, 675), (760, 675)),
        ((60, 45), (60, 675)),
        ((760, 45), (760, 675)),
    ):
        cv2.line(frame, start, end, (245, 245, 245), 8)
    frame[70:190, 90:730] = (220, 60, 30)
    cv2.rectangle(frame, (125, 285), (695, 650), (220, 220, 220), 8)
    for y in (350, 470, 610):
        cv2.line(frame, (150, y), (670, y), (180, 180, 180), 5)

    corrected = CardRecognizer.rectify(frame)

    # The blue title/art band exists only outside the tempting inner panel.
    assert corrected[:300, :, 0].mean() > corrected[:300, :, 1].mean() * 1.5


def test_card_structure_rejects_empty_table_but_accepts_card_frame():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    empty = np.zeros((840, 600, 3), dtype=np.uint8)
    card = empty.copy()
    cv2.rectangle(card, (80, 80), (520, 760), (255, 255, 255), 4)
    for y in (80, 300, 610, 760):
        cv2.line(card, (80, y), (520, y), (255, 255, 255), 4)

    assert CardRecognizer.has_card_structure(empty) is False
    assert CardRecognizer.has_card_structure(card) is True


def test_card_name_catalog_recovers_joined_and_misspelled_titles():
    from mtglogger.services.recognition import CardRecognizer

    names = [
        "Shadows of the Past",
        "Consecrated by Blood",
        "Gurmag Swiftwing",
        "Cast into Darkness",
        "Sins of the Past",
    ]

    assert CardRecognizer.closest_catalog_names("ShafowsofthePasi", names, 1)[0][0] == (
        "Shadows of the Past"
    )
    assert CardRecognizer.closest_catalog_names("Consecrafed by Blood", names, 1)[0][0] == (
        "Consecrated by Blood"
    )
    assert CardRecognizer.closest_catalog_names("Gurmag Swifrwing", names, 1)[0][0] == (
        "Gurmag Swiftwing"
    )


def test_card_name_catalog_treats_both_faces_as_exact_identity_aliases():
    from mtglogger.services.recognition import CardRecognizer

    double_faced = "Liliana, Heretical Healer // Liliana, Defiant Necromancer"

    assert CardRecognizer.card_name_similarity("Liliana,Defiant Necromancer", double_faced) == 1
    assert (
        CardRecognizer.closest_catalog_names(
            "Liliana,Defiant Necromancer", [double_faced, "Liliana, the Necromancer"], 1
        )[0][0]
        == double_faced
    )
    assert CardRecognizer.has_strong_card_identity(
        "Liliana,Defiant Necromancer", [{"name": double_faced}]
    )


def test_oracle_terms_recover_distinctive_rules_text_despite_ocr_damage():
    from mtglogger.services.recognition import CardRecognizer

    shadows = "Each opponent gains 2life. Activate only if there are four or more cards in graveyad"
    touch = "Target creature gainsdeathtouch until end of turn. dealt damage target creature"
    blood = "Enchant creature has lying and Sacrifce two other creatures: Regenerate this creature"
    languish = "All creatures get -4/-4 until end of turn. Life is such a fragile thing."

    assert CardRecognizer.oracle_terms(shadows) == [
        "gain 2 life",
        "four or more",
        "graveyard",
    ]
    assert CardRecognizer.oracle_terms(touch) == [
        "deathtouch",
        "target creature",
        "damage",
    ]
    assert CardRecognizer.oracle_terms(blood) == [
        "regenerate",
        "sacrifice",
        "flying",
    ]
    assert CardRecognizer.oracle_terms(languish) == [
        "-4/-4",
        "all creatures",
        "until end of turn",
    ]


def test_local_oracle_catalog_recovers_one_card_identity_without_network():
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all(
            [
                CardReference(
                    scryfall_id="00000000-0000-0000-0000-000000000121",
                    oracle_id="00000000-0000-0000-0000-000000000122",
                    name="Languish",
                    set_code="ori",
                    set_name="Magic Origins",
                    collector_number="105",
                    language="en",
                    oracle_text="All creatures get -4/-4 until end of turn.",
                    image_url="https://example.test/languish.jpg",
                    art_hash="0" * 16,
                ),
                CardReference(
                    scryfall_id="00000000-0000-0000-0000-000000000123",
                    oracle_id="00000000-0000-0000-0000-000000000124",
                    name="Other Card",
                    set_code="tst",
                    set_name="Test",
                    collector_number="1",
                    language="en",
                    oracle_text="Target creature gets -4/-4 until end of turn.",
                    image_url="https://example.test/other.jpg",
                    art_hash="1" * 16,
                ),
            ]
        )
        db.commit()

    matches = CardRecognizer._lookup_local_oracle_cards(
        ["all creatures", "-4/-4", "until end of turn"], "en"
    )

    assert matches == [
        {
            "name": "Languish",
            "oracle_text": "All creatures get -4/-4 until end of turn.",
        }
    ]


def test_local_printing_family_is_not_truncated_for_common_card_names():
    """Every artwork must reach exact-print ranking, including basic lands."""
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all(
            [
                CardReference(
                    scryfall_id=f"00000000-0000-0000-0000-{index:012d}",
                    name="Swamp",
                    set_code="rtr",
                    set_name="Return to Ravnica",
                    collector_number=str(230 + index),
                    language="en",
                    image_url=f"https://example.test/swamp-{index}.jpg",
                    art_hash=f"{index:016x}",
                )
                for index in range(1, 31)
            ]
        )
        db.commit()

    family, total = CardRecognizer._lookup_local_printing_family("Swamp", "en")

    assert total == 30
    assert len(family) == 30
    assert {card["collector_number"] for card in family} == {
        str(230 + index) for index in range(1, 31)
    }


def test_structured_printing_evidence_promotes_safe_auto_adds_only():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.structured_confidence(91, 0.94, 1, 1, 0) == 99.5
    assert CardRecognizer.structured_confidence(88, 0.82, 1, 1, 0) == 99.5
    assert CardRecognizer.structured_confidence(90, 1, 0.8, 0.8, 1) == 98.5
    assert CardRecognizer.structured_confidence(90, 0.96, 1, 0.8, 0) == 98.5
    assert CardRecognizer.structured_confidence(94, 1, 0.45, 0.45, 0) == 94
    assert CardRecognizer.structured_confidence(94, 0.7, 1, 1, 1) == 94


def test_printed_artist_credit_is_exact_art_evidence():
    from mtglogger.services.recognition import CardRecognizer

    footer = "261/274 C RTR EN © 2012 Richard Wright"

    assert CardRecognizer.artist_text_score(footer, "Richard Wright") == 1.0
    assert CardRecognizer.artist_text_score(footer, "Adam Paquette") == 0.0


def test_exact_footer_match_skips_printing_family_only_for_complete_evidence():
    from mtglogger.services.recognition import CardRecognizer

    card = {
        "name": "Death's Approach",
        "collector_number": "62",
        "set": "gtc",
    }

    assert CardRecognizer.has_exact_footer_match("Death's Approach", "62", "GTC", [card])
    assert not CardRecognizer.has_exact_footer_match("Death's Approach", "222", "GTC", [card])
    assert not CardRecognizer.has_exact_footer_match("Death's Approach", "62", None, [card])


def test_unique_set_and_collector_footer_is_strong_without_a_title():
    from mtglogger.services.recognition import CardRecognizer

    exact = {
        "name": "Liliana, Heretical Healer // Liliana, Defiant Necromancer",
        "collector_number": "106",
        "set": "ori",
    }
    promo = {
        "name": exact["name"],
        "collector_number": "106s",
        "set": "pori",
    }

    assert CardRecognizer.unique_exact_footer_card("106", "ORI", [exact, promo]) == exact
    assert CardRecognizer.has_strong_lookup_evidence(None, "106", "ORI", 2015, [exact, promo])
    assert CardRecognizer.unique_exact_footer_card("106", None, [exact]) is None
    assert CardRecognizer.unique_exact_footer_card("106", "ORI", [exact, exact]) is None


def test_oracle_recovery_orders_exact_regular_and_promo_printings_below_auto_add():
    from mtglogger.services.recognition import CardRecognizer

    regular = {"promo_types": []}
    game_day = {"promo_types": ["setpromo", "gameday"]}
    promo_set = {"promo_types": [], "set": "pori"}

    assert CardRecognizer.oracle_printing_cap(1, "105", 1, None, regular) == 94
    assert CardRecognizer.oracle_printing_cap(1, "105", 1, None, game_day) == 93.5
    assert CardRecognizer.oracle_printing_cap(1, "105", 1, None, promo_set) == 93.5
    assert CardRecognizer.oracle_printing_cap(1, "105", 1, "gameday", game_day) == 94
    assert CardRecognizer.oracle_printing_cap(1, "105", 1, "gameday", regular) == 89
    assert CardRecognizer.oracle_printing_cap(1, None, 0.45, None, regular) == 89


def test_set_code_score_handles_bounded_footer_glyph_confusion():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.set_code_score("MIS", "m15") == 1.0
    assert CardRecognizer.set_code_score("ISD", "isd") == 1.0
    assert CardRecognizer.set_code_score("MIS", "m13") < 1.0
    assert CardRecognizer.hints("Swamp\nMIS\nBasic Land-Swamp")[2] == "mis"


def test_printing_family_reports_when_a_result_is_truncated(monkeypatch):
    import asyncio

    from mtglogger.providers import scryfall

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "total_cards": 27,
                "data": [{"id": f"printing-{index}"} for index in range(15)],
            }

    async def fake_get(_url, **kwargs):
        assert kwargs["params"]["q"] == '!"Swamp" game:paper lang:en'
        assert kwargs["params"]["unique"] == "prints"
        return Response()

    scryfall._printing_family_cache.clear()
    monkeypatch.setattr(scryfall, "scryfall_api_get", fake_get)
    cards, total = asyncio.run(scryfall.ScryfallProvider().printing_family("Swamp", limit=12))

    assert len(cards) == 12
    assert total == 27


def test_printing_family_reuses_successful_lookup_for_one_day(monkeypatch):
    import asyncio

    from mtglogger.providers import scryfall

    calls = 0

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"total_cards": 2, "data": [{"id": "gtc"}, {"id": "jmp"}]}

    async def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return Response()

    scryfall._printing_family_cache.clear()
    monkeypatch.setattr(scryfall, "scryfall_api_get", fake_get)
    provider = scryfall.ScryfallProvider()
    first = asyncio.run(provider.printing_family("Death's Approach"))
    second = asyncio.run(provider.printing_family("Death's Approach"))

    assert first == second
    assert calls == 1


def test_focused_ocr_reads_only_enlarged_title_and_footer_when_title_is_usable():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    recognizer = CardRecognizer.__new__(CardRecognizer)
    calls = []

    def extract(image):
        calls.append(image.shape)
        return "Gurmag Swiftwing\n074/269U\nKTK+ENJEFFSIMPSON"

    recognizer.extract_text = extract
    text = recognizer.extract_identification_text(np.zeros((840, 600, 3), dtype=np.uint8))

    assert "Gurmag Swiftwing" in text
    assert "074/269U" in text
    assert len(calls) == 1
    assert calls[0][1] == 840


def test_incomplete_footer_gets_wide_high_resolution_collector_pass():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    recognizer = CardRecognizer.__new__(CardRecognizer)
    calls = []

    def extract(image):
        calls.append(image.shape)
        if len(calls) == 1:
            return "Basic Land - Swamp\nORI"
        return "264/272\nORI·EN"

    recognizer.extract_text = extract
    text = recognizer.extract_identification_text(np.zeros((840, 600, 3), dtype=np.uint8))

    assert "264/272" in text
    assert len(calls) == 2
    assert calls[0][1] == 840
    assert calls[1][1] == 1200


def test_collector_footer_survives_full_card_title_fallback():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    recognizer = CardRecognizer.__new__(CardRecognizer)
    responses = iter(("ORI", "264/272\nORI·EN", "Basic Land - Swamp"))
    recognizer.extract_text = lambda _image: next(responses)

    text = recognizer.extract_identification_text(np.zeros((840, 600, 3), dtype=np.uint8))
    title, number, set_code, _ = recognizer.hints(text)

    assert title == "Swamp"
    assert number == "264"
    assert set_code == "ori"


def test_footer_artist_line_is_not_invented_as_a_card_title():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, _ = CardRecognizer.hints("264/272L\nORI·EN IUNGPARK")

    assert title is None
    assert number == "264"
    assert set_code == "ori"


def test_full_frame_land_title_fuses_with_focused_collector_footer():
    import asyncio

    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    exact = {
        "id": "ori-swamp-264",
        "name": "Swamp",
        "set": "ori",
        "set_name": "Magic Origins",
        "collector_number": "264",
        "released_at": "2015-07-17",
    }

    class Provider:
        async def search(self, query, set_code=None, language=None):
            if query == '!"Swamp" cn:264' and set_code == "ori":
                return [exact]
            if "Swamp" in query:
                return [
                    exact,
                    {**exact, "id": "ori-swamp-262", "collector_number": "262"},
                ]
            return []

        @staticmethod
        def image_url(_card):
            return None

        @staticmethod
        def market_price(_card, foil=False):
            return None

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    recognizer._recognition_lock = asyncio.Lock()
    recognizer._lookup_local_cards = lambda *_args: []
    recognizer._lookup_local_cards_by_number = lambda *_args: []
    recognizer._lookup_local_printing_family = lambda *_args: ([], 0)
    image = np.zeros((840, 600, 3), dtype=np.uint8)
    recognizer.decode = lambda _raw: image
    recognizer.rectify = lambda decoded: decoded
    recognizer.extract_identification_text = lambda _image: "264/272L\nORI·EN IUNGPARK"
    recognizer.extract_text = lambda _image: "Basic Land - Swamp\nORI"
    recognizer._visual_matches = lambda *_args: []

    result = asyncio.run(recognizer.recognize(b"camera-frame"))

    assert result.candidates[0].scryfall_id == "ori-swamp-264"
    assert result.candidates[0].collector_number == "264"
    # Footer OCR identifies the likely collector number, but basic-land art is
    # too ambiguous to auto-add without independent exact-art agreement.
    assert result.confidence == 98.4


def test_basic_land_auto_add_requires_decisive_exact_art_evidence():
    from mtglogger.services.recognition import CardRecognizer

    swamp = {"id": "rtr-swamp-264", "name": "Swamp", "type_line": "Basic Land — Swamp"}
    assert CardRecognizer.is_basic_land(swamp)
    assert not CardRecognizer.has_decisive_art_match(swamp["id"], "rtr-swamp-261", True, 96, 24)
    assert not CardRecognizer.has_decisive_art_match(swamp["id"], swamp["id"], True, 87, 24)
    assert CardRecognizer.has_decisive_art_match(swamp["id"], swamp["id"], True, 92, 21)


def test_land_art_and_printed_artist_can_recover_a_glare_reduced_margin():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.has_safe_basic_land_match(
        "ths-mountain-244",
        "ths-mountain-244",
        True,
        95.49,
        16.2,
        None,
        None,
        0,
        0,
        artist_score=1.0,
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        "ths-mountain-244",
        "ths-mountain-244",
        True,
        95.49,
        14.9,
        None,
        None,
        0,
        0,
        artist_score=1.0,
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        "ths-mountain-244",
        "ths-mountain-244",
        True,
        95.49,
        16.2,
        None,
        None,
        0,
        0,
        artist_score=0.5,
    )
    assert CardRecognizer.has_decisive_symbol_match(swamp["id"], swamp["id"], 91, 18)
    assert CardRecognizer.set_code_score("ORL", "ori") == 1.0
    assert CardRecognizer.set_code_score("MIS", "m15") == 1.0
    assert CardRecognizer.set_code_score("2NC", "znc") == 1.0
    assert CardRecognizer.has_decisive_candidate_lead(98.4, 80.8, 1.0, True)
    assert CardRecognizer.has_decisive_candidate_lead(97.8, 86.1, 1.0, True)
    assert not CardRecognizer.has_decisive_candidate_lead(98.4, 93.0, 1.0, True)
    assert not CardRecognizer.has_decisive_candidate_lead(98.4, 80.8, 0.85, True)
    assert not CardRecognizer.has_decisive_candidate_lead(98.4, 80.8, 1.0, False)
    assert not CardRecognizer.has_decisive_symbol_match(swamp["id"], "rtr-swamp-261", 94, 20)
    m21_lands = [
        {"id": "m21-plains-260", "set": "m21", "collector_number": "260", "artist": "John Avon"},
        {"id": "m21-plains-261", "set": "m21", "collector_number": "261", "artist": "Nils Hamm"},
        {"id": "m21-plains-262", "set": "m21", "collector_number": "262", "artist": "Andreas Rocha"},
        {"id": "m21-mountain-269", "set": "m21", "collector_number": "269", "artist": "Cliff Childs"},
    ]
    assert CardRecognizer.has_repeated_footer_printing_evidence(
        "Plains\n61/274L\nM21\n61/274L\nM21",
        m21_lands[1],
        m21_lands,
        "21",
    )
    assert CardRecognizer.has_repeated_footer_printing_evidence(
        "Mountain\n69/274L\nM21\n9/274L\nM21",
        m21_lands[3],
        m21_lands,
        "A21",
    )
    assert CardRecognizer.has_repeated_footer_printing_evidence(
        "Mountain\n269/274L\nM2I EN",
        m21_lands[3],
        m21_lands,
        "M2I",
    )
    assert not CardRecognizer.has_repeated_footer_printing_evidence(
        "Plains\n61/274L\nM21",
        m21_lands[1],
        m21_lands,
        "M21",
    )
    assert not CardRecognizer.has_repeated_footer_printing_evidence(
        "Plains\n61/274L\nM21\n61/274L\nM21",
        m21_lands[0],
        m21_lands,
        "M21",
    )
    m21_swamps = [
        {"id": "m21-swamp-266", "set": "m21", "collector_number": "266", "artist": "Christine Choi"},
        {"id": "m21-swamp-267", "set": "m21", "collector_number": "267", "artist": "Jonas De Ro"},
    ]
    assert CardRecognizer.has_unique_set_artist_evidence(
        "Swamp\nM21\nCHRISTINE CHOI\nCHRISTINE CHOI",
        m21_swamps[0],
        m21_swamps,
        "M2I",
        1.0,
    )
    assert not CardRecognizer.has_unique_set_artist_evidence(
        "Swamp\nCHRISTINE CHOI",
        m21_swamps[0],
        m21_swamps,
        None,
        1.0,
    )
    # A decisive illustration match within an exact set remains safe even when
    # the tiny collector-number footer is unreadable or misread.
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 92, 21, None, "rtr", 0.0, 1.0
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 92, 21, "264", None, 1.0, 0.0
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"], "rtr-swamp-261", True, 96, 24, "264", "rtr", 1.0, 1.0
    )
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 92, 21, "264", "rtr", 1.0, 1.0
    )
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 96.9, 11.2, "264", "rtr", 1.0, 1.0
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 96.9, 9.9, "264", "rtr", 1.0, 1.0
    )
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"], swamp["id"], True, 92, 21, "261", "rtr", 0.0, 1.0
    )
    # Older cards may not print a readable set code. A decisive set-symbol
    # region plus the decisive artwork match provides the same independent proof.
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        swamp["id"],
        True,
        92,
        21,
        None,
        None,
        0.0,
        0.0,
        swamp["id"],
        91,
        18,
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        swamp["id"],
        True,
        92,
        21,
        None,
        None,
        0.0,
        0.0,
        "rtr-swamp-261",
        94,
        20,
    )
    # Identical symbols on several lands in one set must not cancel each other.
    # Once the symbol proves the set, compare artwork only within that set.
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        "same-art-reprint",
        True,
        95,
        2,
        None,
        None,
        0.0,
        0.0,
        card_set="rtr",
        symbol_top_set="rtr",
        symbol_set_score=93,
        symbol_set_margin=19,
        set_art_top_id=swamp["id"],
        set_art_score=94,
        set_art_margin=20,
    )
    # Foil glare can make reused artwork win globally. A near-exact footer may
    # still narrow to one set, where the correct illustration wins decisively.
    assert CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        "same-art-reprint",
        True,
        90,
        2,
        "64",
        "ORL",
        0.8,
        0.8,
        card_set="ori",
        set_art_top_id=swamp["id"],
        set_art_score=91,
        set_art_margin=10,
        set_art_catalog_complete=True,
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        "same-art-reprint",
        True,
        90,
        2,
        "64",
        "ORL",
        0.8,
        0.8,
        card_set="ori",
        set_art_top_id=swamp["id"],
        set_art_score=91,
        set_art_margin=4,
        set_art_catalog_complete=True,
    )
    assert not CardRecognizer.has_safe_basic_land_match(
        swamp["id"],
        "same-art-reprint",
        True,
        95,
        2,
        None,
        None,
        0.0,
        0.0,
        card_set="rtr",
        symbol_top_set="isd",
        symbol_set_score=93,
        symbol_set_margin=19,
        set_art_top_id=swamp["id"],
        set_art_score=94,
        set_art_margin=20,
    )


def test_ocr_hints_recovers_joined_rarity_and_confused_znc_code():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, _year = CardRecognizer.hints(
        "Whispersteel Dagger\n005R\n2NC·EN\nSUUO"
    )

    assert title == "Whispersteel Dagger"
    assert number == "005"
    assert set_code == "2nc"


def test_catalog_fallback_recovers_canonical_name_with_exact_printing():
    import asyncio

    from mtglogger.services.recognition import CardRecognizer

    card = {
        "id": "ori-consecrated",
        "name": "Consecrated by Blood",
        "set": "ori",
        "set_name": "Magic Origins",
        "collector_number": "87",
    }

    class CatalogProvider:
        def __init__(self):
            self.calls = []

        async def search(self, query, set_code=None, language=None):
            self.calls.append((query, set_code, language))
            if query == '!"Consecrated by Blood" cn:087' and set_code == "ori":
                return [card]
            return []

        async def card_names(self):
            return ["Consecrated by Blood", "Consumed by Greed", "Blood Artist"]

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = CatalogProvider()
    cards = asyncio.run(recognizer._lookup_cards("Consecrafed by Blood", "087", "ori", None, "en"))

    assert cards == [card]
    assert ('!"Consecrated by Blood" cn:087', "ori", "en") in recognizer.provider.calls


def test_intro_pack_footer_selects_promo_printing_not_regular_set_card():
    import asyncio

    from mtglogger.services.recognition import CardRecognizer

    regular = {
        "id": "regular-ori",
        "name": "Kothophed, Soul Hoarder",
        "set": "ori",
        "collector_number": "104",
        "promo_types": [],
    }
    intro_pack = {
        "id": "intro-pori",
        "name": "Kothophed, Soul Hoarder",
        "set": "pori",
        "collector_number": "104",
        "promo_types": ["setpromo", "intropack"],
    }
    prerelease = {
        "id": "prerelease-pori",
        "name": "Kothophed, Soul Hoarder",
        "set": "pori",
        "collector_number": "104s",
        "promo_types": ["setpromo", "prerelease", "datestamped"],
    }

    class Provider:
        def __init__(self):
            self.calls = []

        async def search(self, query, set_code=None, language=None):
            self.calls.append((query, set_code, language))
            return [regular, intro_pack, prerelease]

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    cards = asyncio.run(
        recognizer._lookup_cards("Kothophed, Soul Hoarder", "104", "ori", None, "en", "intropack")
    )

    assert cards == [intro_pack]
    assert recognizer.provider.calls[0] == (
        '!"Kothophed, Soul Hoarder" is:promo cn:104',
        None,
        "en",
    )


def test_intro_pack_hint_tolerates_footer_ocr_damage():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.promo_type_hint("104/272RInroOPack\nORIENTIANHUAX") == ("intropack")


def test_weak_internal_crop_recovers_from_original_camera_frame():
    import asyncio

    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    card = {
        "id": "ori-centaur",
        "name": "Returned Centaur",
        "set": "ori",
        "set_name": "Magic Origins",
        "collector_number": "116",
        "released_at": "2015-07-17",
    }

    class Provider:
        async def search(self, query, set_code=None, language=None):
            return [card] if "Returned Centaur" in query else []

        @staticmethod
        def image_url(_card):
            return None

        @staticmethod
        def market_price(_card, foil=False):
            return None

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    recognizer._recognition_lock = asyncio.Lock()
    recognizer._lookup_local_cards = lambda *_args: []
    recognizer._lookup_local_cards_by_number = lambda *_args: []
    recognizer._lookup_local_printing_family = lambda *_args: ([], 0)
    original = np.zeros((720, 1280, 3), dtype=np.uint8)
    bad_crop = np.zeros((840, 600, 3), dtype=np.uint8)
    recognizer.decode = lambda _raw: original
    recognizer.rectify = lambda _decoded: bad_crop
    recognizer.extract_identification_text = lambda _image: "Rerurnd entaur\n11/272\nORI·EN"
    recognizer.extract_text = lambda _image: (
        "Returned Centaur\n116/272 C\nORI·EN LUCAS GRACIANO\n2015"
    )
    recognizer._visual_matches = lambda _scan_hash, _set_code: []

    result = asyncio.run(recognizer.recognize(b"camera-frame"))

    assert result.candidates[0].name == "Returned Centaur"
    assert result.candidates[0].collector_number == "116"
    assert result.confidence == 99.5
    assert result.corrected is original


def test_artwork_alone_cannot_auto_add_a_reused_printing():
    from mtglogger.services.recognition import CardRecognizer

    # Identical artwork can legitimately belong to multiple exact printings.
    # It should rank Review candidates, but must stay below the 98.5% auto-add gate.
    assert CardRecognizer.visual_only_score(99.5) == 94.0
    assert CardRecognizer.visual_only_score(87.0) == 87.0


def test_damaged_collector_number_can_uniquely_identify_printing():
    from mtglogger.services.recognition import CardRecognizer

    # Touch of Moonglove 123/272 was read as 23/272. The suffix remains a
    # strong match and no competing printing comes close.
    assert CardRecognizer.has_unique_printing_signal(1.0, "23", 0.8, [0.44, 0.33], None, "ori", 1)
    # Equal printing scores (Death's Approach #62 vs #222 with no footer) are
    # deliberately not enough to add anything automatically.
    assert not CardRecognizer.has_unique_printing_signal(1.0, None, 0.45, [0.45], None, "gtc", 1)


def test_exact_set_signal_can_uniquely_identify_printing():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.has_unique_printing_signal(1.0, None, 0.45, [0.45], "ORI", "ori", 1)
    assert not CardRecognizer.has_unique_printing_signal(1.0, None, 0.45, [0.45], "ORI", "ori", 2)


def test_rules_recovery_does_not_discard_independent_printing_signal():
    from mtglogger.services.recognition import CardRecognizer

    # Rules text identifies Shadows of the Past, while ORI in the footer
    # independently identifies its physical printing.
    printing_signal = CardRecognizer.has_unique_printing_signal(
        1.0, None, 0.45, [0.45], "ORI", "ori", 1
    )
    confidence = 98.5
    oracle_recovery = True
    if oracle_recovery and not printing_signal:
        confidence = min(89.0, confidence)
    assert confidence == 98.5


def test_exact_card_name_prevents_rules_fallback_from_replacing_identity():
    from mtglogger.services.recognition import CardRecognizer

    cards = [{"name": "Rite of the Serpent"}]
    assert CardRecognizer.has_strong_card_identity("Rite of the Serpent", cards)
    assert CardRecognizer.has_strong_card_identity("RiteoftheSerpent", cards)
    assert not CardRecognizer.has_strong_card_identity("Sadistic Obsession", cards)


def test_full_frame_recovery_cannot_replace_a_proven_focused_title():
    import asyncio

    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    rite_printings = [
        {
            "id": "ktk-rite",
            "name": "Rite of the Serpent",
            "set": "ktk",
            "set_name": "Khans of Tarkir",
            "collector_number": "86",
            "released_at": "2014-09-26",
        },
        {
            "id": "c17-rite",
            "name": "Rite of the Serpent",
            "set": "c17",
            "set_name": "Commander 2017",
            "collector_number": "124",
            "released_at": "2017-08-25",
        },
    ]
    wrong_card = {
        "id": "mh1-sadistic",
        "name": "Sadistic Obsession",
        "set": "mh1",
        "set_name": "Modern Horizons",
        "collector_number": "105",
        "released_at": "2019-06-14",
    }

    class Provider:
        async def search(self, query, set_code=None, language=None):
            if "Rite of the Serpent" in query:
                if "cn:86" in query and set_code == "ktk":
                    return [rite_printings[0]]
                return rite_printings
            if "Sadistic Obsession" in query:
                return [wrong_card]
            return []

        @staticmethod
        def image_url(_card):
            return None

        @staticmethod
        def market_price(_card, foil=False):
            return None

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    recognizer._recognition_lock = asyncio.Lock()
    image = np.zeros((840, 600, 3), dtype=np.uint8)
    recognizer.decode = lambda _raw: image
    recognizer.rectify = lambda decoded: decoded
    recognizer.extract_identification_text = lambda _image: "Rite of the Serpent"
    recognizer.extract_text = lambda _image: "Sadistic Obsession\n86/269\nKTK·EN"
    recognizer._visual_matches = lambda _scan_hash, _set_code: []

    result = asyncio.run(recognizer.recognize(b"camera-frame"))

    assert result.candidates[0].name == "Rite of the Serpent"
    assert result.candidates[0].set_code == "ktk"
    assert result.candidates[0].collector_number == "86"
    assert result.confidence == 99.5


def test_scryfall_failure_preserves_recognition_for_local_review():
    import asyncio

    import httpx

    from mtglogger.services.recognition import CardRecognizer

    class OfflineProvider:
        async def search(self, _query, _set_code=None, _language=None):
            raise httpx.ConnectError("offline")

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = OfflineProvider()
    cards = asyncio.run(recognizer._lookup_cards("Lightning Bolt", "188", "fdn", None, "en"))
    assert cards == []


def test_scryfall_connection_pool_is_reused_and_closed_cleanly():
    import asyncio

    from mtglogger.providers.scryfall import close_scryfall_client, scryfall_client

    client = scryfall_client()
    assert scryfall_client() is client
    asyncio.run(close_scryfall_client())
    assert client.is_closed


def test_non_english_lookup_falls_back_to_exact_collector_and_language():
    import asyncio

    from mtglogger.services.recognition import CardRecognizer

    class LocalizedProvider:
        def __init__(self):
            self.calls = []

        async def search(self, query, set_code=None, language=None):
            self.calls.append((query, set_code, language))
            return [{"id": "japanese-printing"}] if query == "cn:188" else []

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = LocalizedProvider()
    cards = asyncio.run(recognizer._lookup_cards("稲妻", "188", "fdn", None, "ja"))
    assert cards == [{"id": "japanese-printing"}]
    assert recognizer.provider.calls == [
        ('!"稲妻" cn:188', "fdn", "ja"),
        ("cn:188", "fdn", "ja"),
    ]


def test_ocr_hints_normalize_printed_collector_number():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Abrade\nInstant\nU 0188\nT™ & © 2024 Wizards of the Coast\nFDN · EN"
    )
    assert title == "Abrade"
    assert number == "0188"
    assert set_code == "fdn"
    assert year == 2024


def test_ocr_hints_use_latest_year_in_copyright_range_and_ignore_mana_cost():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Abattoir Ghoul\n3\nCreature — Zombie\nDeathtouch\n"
        "Whenever a creature dies, gain 1 life.\n"
        "085/264 U\nISD · EN\n© 1993-2011 Wizards of the Coast"
    )

    assert title == "Abattoir Ghoul"
    assert number == "085"
    assert set_code == "isd"
    assert year == 2011


def test_ocr_hints_do_not_treat_sparse_mana_cost_as_collector_number():
    from mtglogger.services.recognition import CardRecognizer

    _, number, _, year = CardRecognizer.hints(
        "Abattoir Ghoul\n3\nCreature — Zombie\n© 1903-2011 Wizards"
    )

    assert number is None
    assert year == 2011


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


def test_ocr_hints_do_not_invent_title_from_type_or_rules_text():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "2?\nSorcery\nAll creatures get -4/-4 until end of turn.\n"
        "Life is such a fragile thing.\n105/272 R\nJEFF SIMPSON"
    )

    assert title is None
    assert number == "105"
    assert set_code is None
    assert year is None


def test_ocr_hints_recovers_basic_land_name_from_type_line():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints("Basic Land -Swamp\nORI")

    assert title == "Swamp"
    assert number is None
    assert set_code == "ori"
    assert year is None


def test_printing_descriptors_require_one_ocr_established_card_identity():
    from mtglogger.services.recognition import CardRecognizer

    languish = {"name": "Languish"}
    number_only_candidates = [languish, {"name": "Bone Splinters"}]

    assert CardRecognizer.has_constrained_visual_identity("Languish", [languish], {"Languish"})
    assert not CardRecognizer.has_constrained_visual_identity(
        None,
        number_only_candidates,
        {"Languish", "Bone Splinters"},
    )


def test_global_visual_match_can_rescue_a_weak_wrong_ocr_identity():
    from mtglogger.services.recognition import CardRecognizer

    wrong_ocr_pool = [{"name": "Liliana, the Necromancer"}]

    assert CardRecognizer.should_admit_visual_candidate(
        "Liliana, Heretical Healer // Liliana, Defiant Necromancer",
        wrong_ocr_pool,
        identity_is_constrained=False,
    )
    assert not CardRecognizer.should_admit_visual_candidate(
        "Unrelated Artwork Collision",
        wrong_ocr_pool,
        identity_is_constrained=True,
    )


def test_number_only_local_recovery_keeps_all_matching_printings():
    import asyncio
    from datetime import date
    from decimal import Decimal

    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add_all(
            [
                CardReference(
                    scryfall_id="ori-languish",
                    name="Languish",
                    set_code="ori",
                    set_name="Magic Origins",
                    collector_number="105",
                    released_at=date(2015, 7, 17),
                    image_url="https://example.test/languish.jpg",
                    art_hash="0000000000000000",
                    market_price=Decimal("0.60"),
                ),
                CardReference(
                    scryfall_id="other-105",
                    name="Another Card",
                    set_code="tst",
                    set_name="Test Set",
                    collector_number="0105",
                    released_at=date(2020, 1, 1),
                    image_url="https://example.test/other.jpg",
                    art_hash="ffffffffffffffff",
                ),
            ]
        )
        db.commit()

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = object()
    cards = asyncio.run(recognizer._lookup_cards(None, "105", None, None, "en"))

    assert {card["id"] for card in cards} == {"ori-languish", "other-105"}


def test_ocr_hints_read_set_code_when_language_and_artist_are_joined():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Touch of Moonglove\nInstant\n123/272C\n&\n2015Wuarsoh\nORI·ENSCOTTMURPHY"
    )
    assert title == "Touch of Moonglove"
    assert number == "123"
    assert set_code == "ori"
    assert year == 2015


def test_ocr_hints_accept_plus_or_missing_set_language_separator():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.hints("Gurmag Swiftwing\n074/269U\nKTK+ENJEFFSIMPSON")[2] == "ktk"
    title, number, set_code, _ = CardRecognizer.hints(
        "Korhophed,Soul Hoarder\n104/272RintroPack\nORIENTIANHUAX"
    )
    assert title == "Korhophed,Soul Hoarder"
    assert number == "104"
    assert set_code == "ori"


def test_ocr_hints_prefer_explicit_footer_over_joined_artist_noise():
    from mtglogger.services.recognition import CardRecognizer

    _, number, set_code, _ = CardRecognizer.hints(
        "264/272L\nORI-ENNGPARK\nBasic Land -Swamp\n264/272\nORI-EN\nLNGPAR"
    )

    assert number == "264"
    assert set_code == "ori"


def test_exact_joined_footer_reaches_near_certain_auto_add_confidence():
    import asyncio

    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    class Provider:
        async def search(self, query, set_code=None, language=None):
            assert query == '!"Touch of Moonglove" cn:123'
            assert set_code == "ori"
            assert language == "en"
            return [
                {
                    "id": "ori-touch",
                    "name": "Touch of Moonglove",
                    "set": "ori",
                    "set_name": "Magic Origins",
                    "collector_number": "123",
                    "released_at": "2015-07-17",
                    "lang": "en",
                    "finishes": ["nonfoil", "foil"],
                    "prices": {"usd": "0.09", "usd_foil": "1.14"},
                    "image_uris": {"normal": "https://example.test/ori-123.jpg"},
                }
            ]

        @staticmethod
        def image_url(card):
            return card["image_uris"]["normal"]

        @staticmethod
        def market_price(card, foil=False):
            return card["prices"]["usd_foil" if foil else "usd"]

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    recognizer._recognition_lock = asyncio.Lock()
    image = np.zeros((840, 600, 3), dtype=np.uint8)
    recognizer.decode = lambda _raw: image
    recognizer.rectify = lambda decoded: decoded
    recognizer.extract_text = lambda _image: (
        "Touch of Moonglove\nInstant\n123/272C\n&\n2015Wuarsoh\nORI·ENSCOTTMURPHY"
    )
    recognizer._visual_matches = lambda _scan_hash, _set_code: []

    result = asyncio.run(recognizer.recognize(b"camera-frame"))

    assert result.confidence == 99.5
    assert result.candidates[0].set_code == "ori"
    assert result.candidates[0].collector_number == "123"


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


def test_ocr_hints_recovers_truncated_footer_year():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.hints("Death'sApproach\nTerese Nielsen\nco13rdsofthCat2")[3] == 2013


def test_ocr_hints_recovers_corrupted_1996_footer_year():
    from mtglogger.services.recognition import CardRecognizer

    text = "Pacifism\nIllus. Robert Bliss\nOl9g6 Wuards of be Coaet, Inc. Alf righ rescred"
    assert CardRecognizer.hints(text)[3] == 1996


def test_unique_copyright_year_disambiguates_reused_printing():
    import asyncio

    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    cards = [
        {
            "id": "jmp-death",
            "name": "Death's Approach",
            "set": "jmp",
            "set_name": "Jumpstart",
            "collector_number": "222",
            "released_at": "2020-07-17",
        },
        {
            "id": "gtc-death",
            "name": "Death's Approach",
            "set": "gtc",
            "set_name": "Gatecrash",
            "collector_number": "62",
            "released_at": "2013-02-01",
        },
    ]

    class Provider:
        async def search(self, *_args, **_kwargs):
            return cards

        @staticmethod
        def image_url(_card):
            return None

        @staticmethod
        def market_price(_card, foil=False):
            return None

    recognizer = CardRecognizer.__new__(CardRecognizer)
    recognizer.provider = Provider()
    recognizer._recognition_lock = asyncio.Lock()
    image = np.zeros((840, 600, 3), dtype=np.uint8)
    recognizer.decode = lambda _raw: image
    recognizer.rectify = lambda decoded: decoded
    recognizer.extract_identification_text = lambda _image: "Death'sApproach\nco13rdsofthCat2"
    recognizer._visual_matches = lambda _scan_hash, _set_code: []

    result = asyncio.run(recognizer.recognize(b"camera-frame"))

    assert result.candidates[0].set_code == "gtc"
    assert result.candidates[0].collector_number == "62"
    assert result.confidence == 98.5


def test_finish_price_uses_requested_scryfall_finish(monkeypatch):
    import asyncio
    from decimal import Decimal

    from mtglogger.api import inventory

    class Prices:
        async def get_card(self, scryfall_id):
            assert scryfall_id == "printing-id"
            return {"prices": {"usd": "0.40", "usd_foil": "2.75"}}

        @staticmethod
        def market_price(card, foil=False):
            return Decimal(card["prices"]["usd_foil" if foil else "usd"])

    monkeypatch.setattr(inventory, "prices", Prices())
    assert asyncio.run(inventory.finish_price("printing-id", True)) == Decimal("2.75")


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


def test_confirmed_scan_is_preserved_as_durable_labeled_evidence(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from mtglogger.schemas import Candidate
    from mtglogger.services import evaluation

    source = tmp_path / "capture.jpg"
    source.write_bytes(b"physical webcam evidence")
    corpus = tmp_path / "corpus"
    monkeypatch.setattr(evaluation, "get_settings", lambda: SimpleNamespace(evaluation_dir=corpus))
    candidate = Candidate(
        scryfall_id="00000000-0000-0000-0000-000000000259",
        name="Swamp",
        set_code="m15",
        set_name="Magic 2015",
        collector_number="259",
        confidence=99.5,
    )

    preserved = evaluation.preserve_confirmed_scan(source, "review-259", candidate, "en")

    assert preserved.read_bytes() == source.read_bytes()
    manifest = json.loads((corpus / "manifest.json").read_text())
    assert manifest == [
        {
            "review_id": "review-259",
            "image_path": str(preserved),
            "scryfall_id": candidate.scryfall_id,
            "name": "Swamp",
            "set_code": "m15",
            "collector_number": "259",
            "language": "en",
        }
    ]


def test_every_queued_scan_is_archived_before_review_deletion(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from mtglogger.services import evaluation

    source = tmp_path / "capture.jpg"
    source.write_bytes(b"unlabeled webcam evidence")
    corpus = tmp_path / "corpus"
    monkeypatch.setattr(evaluation, "get_settings", lambda: SimpleNamespace(evaluation_dir=corpus))

    preserved = evaluation.preserve_review_scan(source, "review-raw")

    assert preserved.read_bytes() == source.read_bytes()
    assert json.loads((corpus / "raw" / "manifest.json").read_text()) == [
        {"review_id": "review-raw", "image_path": str(preserved)}
    ]


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


def test_confirmed_visual_examples_supplement_canonical_artwork():
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference, CardVisualExample
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        reference = CardReference(
            scryfall_id=CARD["scryfall_id"],
            name=CARD["card_name"],
            set_code=CARD["set_code"],
            set_name=CARD["set_name"],
            collector_number=CARD["collector_number"],
            image_url="https://example.test/card.jpg",
            art_hash="ffffffffffffffff",
        )
        db.add(reference)
        db.flush()
        db.add(
            CardVisualExample(
                scryfall_id=reference.scryfall_id,
                art_hash="0000000000000000",
            )
        )
        db.commit()

    matches = CardRecognizer._visual_matches("0000000000000000", None)
    assert matches[0][0].scryfall_id == CARD["scryfall_id"]
    assert matches[0][1] == 99.5


def test_reference_metadata_enrichment_reuses_finished_visual_profile(tmp_path, monkeypatch):
    """New catalog fields must not trigger another image/descriptor rebuild."""
    import asyncio
    from datetime import date
    from types import SimpleNamespace

    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference, CardVisualFingerprint
    from mtglogger.services import references

    descriptor = tmp_path / "v3" / "00" / "printing.npz"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_bytes(b"already indexed")
    monkeypatch.setattr(
        references,
        "get_settings",
        lambda: SimpleNamespace(cache_reference_images=False),
    )

    class Provider:
        @staticmethod
        def image_url(_card):
            return "https://example.test/card.jpg"

        @staticmethod
        def market_price(_card):
            return "1.23"

        async def download_image(self, _url):  # pragma: no cover - must never run
            raise AssertionError("finished visual profile was downloaded again")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(
            CardReference(
                scryfall_id="printing",
                name="Old name",
                set_code="old",
                set_name="Old Set",
                collector_number="1",
                image_url="https://example.test/card.jpg",
                art_hash="0" * 16,
            )
        )
        db.add(
            CardVisualFingerprint(
                scryfall_id="printing",
                full_hash="0" * 16,
                art_hash="0" * 16,
                title_hash="0" * 16,
                footer_hash="0" * 16,
                symbol_hash="0" * 16,
                frame_hash="0" * 16,
                descriptor_path=str(descriptor),
            )
        )
        db.commit()
        downloaded = asyncio.run(
            references._index_card(
                db,
                Provider(),
                {
                    "id": "printing",
                    "name": "Enriched name",
                    "set": "new",
                    "set_name": "New Set",
                    "collector_number": "42",
                    "oracle_id": "oracle-family",
                    "lang": "en",
                    "oracle_text": "Updated rules text",
                    "promo_types": ["setpromo"],
                    "released_at": "2026-08-04",
                },
            )
        )
        enriched = db.get(CardReference, "printing")

    assert downloaded is False
    assert enriched is not None
    assert enriched.oracle_id == "oracle-family"
    assert enriched.oracle_text == "Updated rules text"
    assert enriched.promo_types == '["setpromo"]'
    assert enriched.released_at == date(2026, 8, 4)


def test_reference_metadata_refresh_does_not_download_images():
    """A precompiled visual catalog can receive searchable metadata in bulk."""
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference
    from mtglogger.services import references

    class Provider:
        @staticmethod
        def image_url(_card):
            return "https://example.test/card.jpg"

        @staticmethod
        def market_price(_card):
            return "1.23"

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(
            CardReference(
                scryfall_id="printing",
                name="Swamp",
                set_code="m21",
                set_name="Core Set 2021",
                collector_number="266",
                image_url="https://example.test/card.jpg",
                art_hash="0" * 16,
            )
        )
        db.commit()
        refreshed = references._refresh_reference_metadata(
            db,
            Provider(),
            [
                {
                    "id": "printing",
                    "name": "Swamp",
                    "set": "m21",
                    "set_name": "Core Set 2021",
                    "collector_number": "266",
                    "artist": "Christine Choi",
                    "released_at": "2020-07-03",
                }
            ],
        )
        enriched = db.get(CardReference, "printing")

    assert refreshed == 1
    assert enriched.artist == "Christine Choi"
    assert enriched.released_at == date(2020, 7, 3)


def test_confirmed_descriptor_examples_improve_matching_without_leaking_into_holdout(
    monkeypatch, tmp_path
):
    import numpy as np

    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference, CardVisualExample, CardVisualFingerprint
    from mtglogger.services import recognition
    from mtglogger.services.recognition import CardRecognizer

    canonical_path = tmp_path / "canonical.npy"
    example_path = tmp_path / "example.npy"
    np.save(canonical_path, np.full((16, 32), 200, dtype=np.uint8))
    np.save(example_path, np.full((16, 32), 1, dtype=np.uint8))
    monkeypatch.setattr(
        recognition,
        "visual_descriptor_bundle",
        lambda _image: {"art": np.full((16, 32), 1, dtype=np.uint8)},
    )
    monkeypatch.setattr(
        CardRecognizer,
        "_descriptor_score",
        staticmethod(lambda _scan, known: 92.0 if int(known[0, 0]) == 1 else 10.0),
    )

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        reference = CardReference(
            scryfall_id=CARD["scryfall_id"],
            name=CARD["card_name"],
            set_code=CARD["set_code"],
            set_name=CARD["set_name"],
            collector_number=CARD["collector_number"],
            image_url="https://example.test/card.jpg",
            art_hash="ffffffffffffffff",
        )
        db.add(reference)
        db.flush()
        db.add(
            CardVisualFingerprint(
                scryfall_id=reference.scryfall_id,
                full_hash="0" * 16,
                art_hash="0" * 16,
                title_hash="0" * 16,
                footer_hash="0" * 16,
                frame_hash="0" * 16,
                descriptor_path=str(canonical_path),
            )
        )
        db.add(
            CardVisualExample(
                scryfall_id=reference.scryfall_id,
                art_hash="0" * 16,
                descriptor_path=str(example_path),
                source_review_id="held-out-review",
            )
        )
        db.commit()

    image = np.zeros((840, 600, 3), dtype=np.uint8)
    learned = CardRecognizer._descriptor_matches(image, {CARD["card_name"]})
    held_out = CardRecognizer._descriptor_matches(
        image, {CARD["card_name"]}, ignored_example_review_ids={"held-out-review"}
    )

    assert learned[0][0].scryfall_id == CARD["scryfall_id"]
    assert learned[0][1] == 92.0
    assert held_out == []


def test_descriptor_catalog_must_cover_every_candidate_before_visual_auto_add():
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference, CardVisualFingerprint
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ids = {"printing-a", "printing-b"}
    with SessionLocal() as db:
        for scryfall_id in ids:
            db.add(
                CardReference(
                    scryfall_id=scryfall_id,
                    name="Repeated Art Card",
                    set_code="one" if scryfall_id == "printing-a" else "two",
                    set_name="Set One" if scryfall_id == "printing-a" else "Set Two",
                    collector_number="1",
                    image_url=f"https://example.test/{scryfall_id}.jpg",
                    art_hash="0" * 16,
                )
            )
        db.flush()
        db.add(
            CardVisualFingerprint(
                scryfall_id="printing-a",
                full_hash="0" * 16,
                art_hash="0" * 16,
                title_hash="0" * 16,
                footer_hash="0" * 16,
                frame_hash="0" * 16,
                descriptor_path="/descriptors/v3/printing-a.npz",
            )
        )
        db.commit()

    assert not CardRecognizer._descriptor_catalog_complete(ids)

    with SessionLocal() as db:
        db.add(
            CardVisualFingerprint(
                scryfall_id="printing-b",
                full_hash="0" * 16,
                art_hash="0" * 16,
                title_hash="0" * 16,
                footer_hash="0" * 16,
                frame_hash="0" * 16,
                descriptor_path="/descriptors/v3/printing-b.npz",
            )
        )
        db.commit()

    assert CardRecognizer._descriptor_catalog_complete(ids)


def test_identity_visual_matching_bypasses_global_artwork_prefilter():
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference, CardVisualFingerprint
    from mtglogger.services.recognition import CardRecognizer

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(
            CardReference(
                scryfall_id="identity-printing",
                name="Known Card Name",
                set_code="set",
                set_name="Known Set",
                collector_number="12",
                image_url="https://example.test/identity.jpg",
                art_hash="ffffffffffffffff",
            )
        )
        db.flush()
        db.add(
            CardVisualFingerprint(
                scryfall_id="identity-printing",
                # Deliberately outside the global <=22 art-distance gate.
                full_hash="0" * 16,
                art_hash="f" * 16,
                title_hash="0" * 16,
                footer_hash="0" * 16,
                symbol_hash="0" * 16,
                frame_hash="0" * 16,
            )
        )
        db.commit()

    scan = {
        "full_hash": "0" * 16,
        "art_hash": "0" * 16,
        "title_hash": "0" * 16,
        "footer_hash": "0" * 16,
        "symbol_hash": "0" * 16,
        "frame_hash": "0" * 16,
    }
    matches = CardRecognizer._identity_visual_matches(scan, {"Known Card Name"})

    assert matches[0][0].scryfall_id == "identity-printing"
    assert matches[0][1] > 55


def test_visual_catalog_is_reused_until_explicitly_invalidated():
    from mtglogger.services.recognition import CardRecognizer

    CardRecognizer.invalidate_visual_catalog()
    first = CardRecognizer._get_visual_catalog()
    second = CardRecognizer._get_visual_catalog()
    assert second is first

    CardRecognizer.invalidate_visual_catalog()
    assert CardRecognizer._get_visual_catalog() is not first
