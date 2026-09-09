import cv2
import numpy as np

from mtglogger.services.recognition import CardRecognizer


def test_portrait_fallback_retains_left_title_and_footer(monkeypatch):
    image = np.zeros((720, 515, 3), np.uint8)
    image[20:60, 10:80] = (0, 100, 250)
    image[675:700, 5:90] = (250, 100, 0)
    monkeypatch.setattr(CardRecognizer, "has_card_structure", lambda _: False)
    result = CardRecognizer.rectify(image)
    assert result.shape == (840, 600, 3)
    assert result[35:65, 15:70, 2].mean() > 200
    assert result[795:805, 15:70, 0].mean() > 200


def test_rules_box_cannot_replace_a_close_portrait_card(monkeypatch):
    image = np.zeros((720, 515, 3), np.uint8)
    image[10:70, 10:450] = (20, 100, 250)
    cv2.rectangle(image, (100, 350), (400, 650), (220, 220, 220), 4)
    monkeypatch.setattr(CardRecognizer, "has_card_structure", lambda _: True)
    result = CardRecognizer.rectify(image)
    # The title stripe survives; an enlarged rules-box rectangle would lose it.
    assert result[25:55, 50:400, 2].mean() > 200


def test_full_iphone_photo_still_localizes_the_outer_card(monkeypatch):
    image = np.zeros((1200, 900, 3), np.uint8)
    cv2.rectangle(image, (150, 200), (750, 1040), (240, 240, 240), -1)
    image[260:340, 210:690] = (0, 80, 250)
    monkeypatch.setattr(CardRecognizer, "has_card_structure", lambda _: True)
    result = CardRecognizer.rectify(image, full_photo=True)
    # A full-frame resize leaves this bottom corner black. The physical card,
    # although smaller than 72% of the photo, must fill the recognition image.
    assert result[760:800, 500:540].mean() > 200


def test_clipped_hyphenated_title_is_not_singleton_printing_proof():
    assert not CardRecognizer.has_safe_single_printing_identity(
        is_basic_land=False, identity_is_constrained=True, family_complete=True,
        observed_title="-Monkey", candidate_name="Monkey-",
    )


def test_star_separator_is_printing_evidence_not_an_artist_fragment():
    text = "Illusion Spinners\nU0055\nIVS\nECL*EN\nZOLTAN BOROS"
    assert CardRecognizer.hints(text)[1:3] == ("0055", "ecl")


def test_complete_family_can_find_literal_set_before_joined_artist_language():
    cards = [{"set": "ecl"}, {"set": "pecl"}]
    assert CardRecognizer.exact_family_set_code_from_footer_text(
        "C0152\nECLEN\nDAREN BAOLR", cards
    ) == "ecl"
    assert CardRecognizer.exact_family_set_code_from_footer_text(
        "C0152\nECL*EN\nPECL*EN", cards
    ) is None


def test_zero_padded_collector_outranks_trailing_ocr_debris():
    assert CardRecognizer.hints("Boomerang\nBasics\n00046\nTLA·EN\nARTIST\n22")[1] == "00046"


def test_split_title_uses_full_catalog_identity_before_exact_short_word(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mtglogger.database import Base
    from mtglogger.models import CardReference
    from mtglogger.services import recognition

    engine = create_engine(f"sqlite:///{tmp_path}/catalog.db")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        names = ["Waterbending", "Waterbending Lesson", "Earth Kingdom Protectors"]
        for index, name in enumerate(names):
            db.add(CardReference(scryfall_id=str(index), name=name, set_code="test",
                                 set_name="Test", collector_number=str(index), image_url="",
                                 art_hash="0000000000000000"))
        db.commit()
    monkeypatch.setattr(recognition, "SessionLocal", sessions)
    recover = CardRecognizer.recover_joined_title
    assert recover("Waterbending\nLesson\n3", "Waterbending") == "Waterbending Lesson"
    assert recover("Earth\nKingdomProtectors\nmust", "Earth") == "Earth Kingdom Protectors"
    assert recover("Waterbending\nDraw three cards", "Waterbending") is None
    engine.dispose()
