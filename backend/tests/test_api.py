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
    assert history["current_value"] == 1.26
    assert history["previous_value"] == 0.84
    assert history["change"] == 0.42
    assert history["change_percentage"] == 50.0
    assert len(history["history"]) == 2


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

    first = client.get(
        f"/api/decks/{deck_id}/available", params={"page": 1, "page_size": 2}
    ).json()
    second = client.get(
        f"/api/decks/{deck_id}/available", params={"page": 2, "page_size": 2}
    ).json()

    assert first["total"] == second["total"] == 3
    assert [item["inventory"]["card_name"] for item in first["items"]] == ["Alpha", "Beta"]
    assert [item["inventory"]["card_name"] for item in second["items"]] == ["Gamma"]


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


def test_card_structure_rejects_empty_table_but_accepts_card_frame():
    import cv2
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    empty = np.zeros((840, 600, 3), dtype=np.uint8)
    card = empty.copy()
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


def test_structured_printing_evidence_promotes_safe_auto_adds_only():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.structured_confidence(91, 0.94, 1, 1, 0) == 99.5
    assert CardRecognizer.structured_confidence(88, 0.82, 1, 1, 0) == 99.5
    assert CardRecognizer.structured_confidence(90, 1, 0.8, 0.8, 1) == 98.5
    assert CardRecognizer.structured_confidence(94, 1, 0.45, 0.45, 0) == 94
    assert CardRecognizer.structured_confidence(94, 0.7, 1, 1, 1) == 94


def test_focused_ocr_reads_only_enlarged_title_and_footer_when_title_is_usable():
    import numpy as np

    from mtglogger.services.recognition import CardRecognizer

    recognizer = CardRecognizer.__new__(CardRecognizer)
    calls = []

    def extract(image):
        calls.append(image.shape)
        return "Gurmag Swiftwing" if len(calls) == 1 else "074/269U\nKTK+ENJEFFSIMPSON"

    recognizer.extract_text = extract
    text = recognizer.extract_identification_text(np.zeros((840, 600, 3), dtype=np.uint8))

    assert "Gurmag Swiftwing" in text
    assert "074/269U" in text
    assert calls == [(420, 600, 3), (600, 600, 3)]


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
    cards = asyncio.run(
        recognizer._lookup_cards("Consecrafed by Blood", "087", "ori", None, "en")
    )

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
        recognizer._lookup_cards(
            "Kothophed, Soul Hoarder", "104", "ori", None, "en", "intropack"
        )
    )

    assert cards == [intro_pack]
    assert recognizer.provider.calls[0] == (
        '!"Kothophed, Soul Hoarder" is:promo cn:104',
        None,
        "en",
    )


def test_intro_pack_hint_tolerates_footer_ocr_damage():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.promo_type_hint("104/272RInroOPack\nORIENTIANHUAX") == (
        "intropack"
    )


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
    cards = asyncio.run(
        recognizer._lookup_cards("稲妻", "188", "fdn", None, "ja")
    )
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


def test_ocr_hints_read_set_code_when_language_and_artist_are_joined():
    from mtglogger.services.recognition import CardRecognizer

    title, number, set_code, year = CardRecognizer.hints(
        "Touch of Moonglove\nInstant\n123/272C\n&\n"
        "2015Wuarsoh\nORI·ENSCOTTMURPHY"
    )
    assert title == "Touch of Moonglove"
    assert number == "123"
    assert set_code == "ori"
    assert year == 2015


def test_ocr_hints_accept_plus_or_missing_set_language_separator():
    from mtglogger.services.recognition import CardRecognizer

    assert CardRecognizer.hints(
        "Gurmag Swiftwing\n074/269U\nKTK+ENJEFFSIMPSON"
    )[2] == "ktk"
    title, number, set_code, _ = CardRecognizer.hints(
        "Korhophed,Soul Hoarder\n104/272RintroPack\nORIENTIANHUAX"
    )
    assert title == "Korhophed,Soul Hoarder"
    assert number == "104"
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
        "Touch of Moonglove\nInstant\n123/272C\n&\n"
        "2015Wuarsoh\nORI·ENSCOTTMURPHY"
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
