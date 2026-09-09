from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mtglogger.database import Base
from mtglogger.models import CardReference
from mtglogger.services.card_search import browse_catalog, search_printings


def reference(index, name, *, code="tst", set_name="Test Set", number=None, **extra):
    return CardReference(
        scryfall_id=f"00000000-0000-0000-0000-{index:012d}",
        name=name,
        set_code=code,
        set_name=set_name,
        collector_number=number or str(index),
        image_url=f"https://example.test/{index}.jpg",
        art_hash=f"{index:016x}",
        language="en",
        finishes='["nonfoil", "foil"]',
        released_at=date(2024, 1, 1),
        **extra,
    )


@pytest.fixture
def catalog_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                reference(1, "Lightning Bolt", code="m11", set_name="Magic 2011"),
                reference(2, "Lightning Bolt", code="sld", set_name="Secret Lair Drop"),
                reference(3, "Elvish Mystic", code="m14", set_name="Magic 2014"),
                reference(4, "Æther Vial"),
                reference(5, "Ugin's Conjurant"),
                reference(6, "SpongeBob SquarePants"),
                reference(
                    7,
                    "Counterspell",
                    flavor_name="Squidward, Sarcastic Squid",
                    code="sld",
                    set_name="Secret Lair Drop",
                    number="01735",
                ),
                reference(8, "Blot Out"),
                reference(9, "Café Cat"),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("bolt lightning", "Lightning Bolt"),
        ("lightn bolt", "Lightning Bolt"),
        ("elvish mystc", "Elvish Mystic"),
        ("blot lightning", "Lightning Bolt"),
        ("sponge bob", "SpongeBob SquarePants"),
        ("aether vial", "Æther Vial"),
        ("ugins conjurant", "Ugin's Conjurant"),
        ("cafe cat", "Café Cat"),
    ],
)
def test_partial_reordered_misspelled_and_normalized_names(catalog_db, query, name):
    result = browse_catalog(catalog_db, query, "en")
    assert result["items"][0]["oracle_name"] == name


def test_set_words_codes_and_zero_padded_numbers_can_mix_with_a_name(catalog_db):
    for query in ["bolt secret lair", "lightning sld", "sld bolt", "bolt set:SLD"]:
        result = browse_catalog(catalog_db, query, "en")
        assert result["total"] == 1
        assert result["items"][0]["set_code"] == "sld"
        assert result["items"][0]["printing_count"] == 1
    result = browse_catalog(catalog_db, "sld #1735", "en")
    assert result["items"][0]["name"] == "Squidward, Sarcastic Squid"
    assert result["items"][0]["collector_number"] == "01735"
    assert browse_catalog(catalog_db, "sld #1736", "en")["items"] == []
    filtered = browse_catalog(catalog_db, "bolt", "en", set_code="sld")
    assert filtered["items"][0]["printing_count"] == 1


def test_swapped_letters_work_in_a_crowded_vocabulary(catalog_db):
    # All these words outrank "bolt" in difflib's nearest matches for "blot".
    catalog_db.add(reference(20, "Blots Bloat Bloty Blote Bloti"))
    catalog_db.commit()
    result = browse_catalog(catalog_db, "blot lightning", "en")
    assert result["items"][0]["oracle_name"] == "Lightning Bolt"
    assert result["fuzzy"]


def test_exact_names_rank_first_and_printings_do_not_flood_name_results(catalog_db):
    catalog_db.add(reference(10, "Lightning Bolt Token"))
    catalog_db.commit()
    result = browse_catalog(catalog_db, "Lightning Bolt", "en")
    assert [item["oracle_name"] for item in result["items"]] == [
        "Lightning Bolt",
        "Lightning Bolt Token",
    ]
    assert result["items"][0]["printing_count"] == 2
    assert not result["fuzzy"]
    assert browse_catalog(catalog_db, "elvish mystc", "en")["fuzzy"]


def test_family_pagination_reaches_printings_beyond_the_old_250_limit(catalog_db):
    catalog_db.add_all(
        reference(index, "Plains", code="sld" if index % 2 else "m11") for index in range(100, 401)
    )
    catalog_db.commit()
    names = browse_catalog(catalog_db, "plains", "en")
    assert names["total"] == 1
    assert names["items"][0]["printing_count"] == 301
    seen = set()
    for page in range(1, 14):
        result = browse_catalog(catalog_db, "plains", "en", name="Plains", page=page)
        assert result["total"] == 301
        assert not seen.intersection(item["scryfall_id"] for item in result["items"])
        seen.update(item["scryfall_id"] for item in result["items"])
    assert len(seen) == 301
    subset = browse_catalog(catalog_db, "plains", "en", name="Plains", set_code="sld")
    assert subset["total"] == 150
    assert {item["set_code"] for item in subset["items"]} == {"sld"}
    assert {item["code"] for item in subset["sets"]} == {"m11", "sld"}


def test_language_filter_and_metadata_updates_are_not_lost_to_a_cache(catalog_db):
    card = catalog_db.get(CardReference, "00000000-0000-0000-0000-000000000007")
    assert browse_catalog(catalog_db, "squidward", "en")["total"] == 1
    assert browse_catalog(catalog_db, "squidward", "fr")["total"] == 0
    card.flavor_name = "Patrick Star"
    catalog_db.commit()
    assert browse_catalog(catalog_db, "patrick", "en")["total"] == 1
    assert browse_catalog(catalog_db, "squidward", "en")["total"] == 0


def test_blank_or_wildcard_searches_do_not_return_the_entire_catalog(catalog_db):
    for query in ["", "   ", "%", "_%", "'''", "zzzzzzzzzz"]:
        assert browse_catalog(catalog_db, query, "en")["items"] == []
    assert len(search_printings(catalog_db, "bolt lightning", "en")) == 2


def test_browse_http_contract_uses_forgiving_search(client):
    engine = create_engine(client.api_database_url)
    with Session(engine) as db:
        db.add(reference(1, "Elvish Mystic"))
        db.commit()
    response = client.get("/api/reviews/browse", params={"q": "elvish mystc"})
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Elvish Mystic"
    assert response.json()["fuzzy"]
    old_client = client.get("/api/reviews/search", params={"q": "mystic elvish"})
    assert old_client.status_code == 200
    assert old_client.json()[0]["name"] == "Elvish Mystic"
    engine.dispose()
