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
    assert "Lightning Bolt" in client.get("/api/inventory/export/csv").text
    assert client.get("/api/inventory/export/json").json()[0]["set_code"] == "fdn"


def test_sealed_inventory(client):
    response = client.post(
        "/api/sealed",
        json={"name": "Foundations Play Booster Box", "product_type": "booster_box", "quantity": 2},
    )
    assert response.status_code == 201
    assert client.get("/api/sealed").json()[0]["quantity"] == 2


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
