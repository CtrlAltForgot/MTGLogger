import numpy as np


def test_new_correction_count_reports_only_the_latest_batch():
    from mtglogger.services.neural_maintenance import _new_correction_count

    assert _new_correction_count(119, 109) == 10
    assert _new_correction_count(109, 109) == 0
    assert _new_correction_count(10, -1) == 10


def test_neural_preprocess_matches_model_contract():
    from mtglogger.services.neural import NeuralEmbedder

    image = np.zeros((480, 320, 3), dtype=np.uint8)
    tensor = NeuralEmbedder.preprocess(image)
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert tensor.flags.c_contiguous
    np.testing.assert_allclose(tensor[0, :, 0, 0], [-2.117904, -2.035714, -1.804444], rtol=1e-5)


def test_neural_warm_executes_one_inference_and_is_idempotent(monkeypatch, tmp_path):
    from mtglogger.services.neural import NeuralEmbedder

    embedder = NeuralEmbedder(tmp_path)
    observed = []
    monkeypatch.setattr(
        embedder,
        "embed",
        lambda image: observed.append(image.copy()) or np.ones(4, dtype=np.float32),
    )

    embedder.warm()
    embedder.warm()

    assert len(observed) == 1
    assert observed[0].shape == (224, 224, 3)
    assert not observed[0].any()


def test_neural_vector_storage_and_exact_cosine_search():
    from mtglogger.database import Base, SessionLocal, engine
    from mtglogger.models import CardReference
    from mtglogger.services.neural import NeuralIndex, decode_vector, encode_vector, store_embedding

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for card_id, name in (("card-a", "Alpha"), ("card-b", "Beta")):
            db.add(
                CardReference(
                    scryfall_id=card_id,
                    name=name,
                    set_code="tst",
                    set_name="Test",
                    collector_number="1",
                    image_url=f"https://example.test/{card_id}.jpg",
                    art_hash="0" * 16,
                )
            )
        db.flush()
        store_embedding(
            db,
            scryfall_id="card-a",
            source_kind="canonical",
            source_id="card-a",
            vector=np.array([1.0, 0.0, 0.0], dtype=np.float32),
            model_version="test-model",
        )
        store_embedding(
            db,
            scryfall_id="card-b",
            source_kind="correction",
            source_id="review-b",
            vector=np.array([0.0, 1.0, 0.0], dtype=np.float32),
            model_version="test-model",
        )
        store_embedding(
            db,
            scryfall_id="card-a",
            source_kind="correction",
            source_id="review-a",
            vector=np.array([0.8, 0.2, 0.0], dtype=np.float32),
            model_version="test-model",
        )
        db.commit()
        index = NeuralIndex(db, "test-model")
        matches = index.search(np.array([0.1, 0.9, 0.0], dtype=np.float32), limit=2)
        constrained = index.search(
            np.array([0.1, 0.9, 0.0], dtype=np.float32),
            limit=2,
            allowed_names={"Alpha"},
        )
        held_out = index.search(
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            limit=2,
            ignored_source_ids={"review-b"},
        )

    assert [match.reference.scryfall_id for match in matches] == ["card-b", "card-a"]
    assert matches[0].source_kind == "correction"
    assert [match.reference.scryfall_id for match in constrained] == ["card-a"]
    assert [match.reference.scryfall_id for match in held_out] == ["card-a"]
    packed = encode_vector(np.array([0.6, 0.8], dtype=np.float32))
    np.testing.assert_allclose(decode_vector(packed, 2), [0.6, 0.8], atol=5e-4)


def test_neural_model_download_is_pinned():
    from mtglogger.services.neural import MODEL_SHA256, MODEL_URL

    assert MODEL_URL.startswith("https://paddle-model-ecology.bj.bcebos.com/")
    assert len(MODEL_SHA256) == 64


def test_confirmed_capture_preparation_preserves_detector_crop(monkeypatch):
    import cv2

    from mtglogger.services.neural_maintenance import _prepare_confirmed_capture
    from mtglogger.services.recognition import CardRecognizer

    card = np.zeros((840, 600, 3), dtype=np.uint8)
    cv2.rectangle(card, (20, 20), (580, 820), (255, 255, 255), 4)
    for y in (90, 300, 610, 760):
        cv2.line(card, (30, y), (570, y), (255, 255, 255), 4)
    monkeypatch.setattr(
        CardRecognizer,
        "rectify",
        staticmethod(lambda _image: (_ for _ in ()).throw(AssertionError("double crop"))),
    )

    prepared = _prepare_confirmed_capture(card)

    assert prepared.shape == (840, 600, 3)


def test_confirmed_capture_preparation_localizes_legacy_full_frame(monkeypatch):
    from mtglogger.services.neural_maintenance import _prepare_confirmed_capture
    from mtglogger.services.recognition import CardRecognizer

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    localized = np.full((840, 600, 3), 37, dtype=np.uint8)
    monkeypatch.setattr(CardRecognizer, "rectify", staticmethod(lambda _image: localized))

    assert _prepare_confirmed_capture(frame) is localized


def test_reference_guided_crop_recovers_card_from_legacy_frame():
    import cv2

    from mtglogger.services.neural_maintenance import _reference_guided_crop

    random = np.random.default_rng(20260805)
    reference = random.integers(0, 256, (420, 300, 3), dtype=np.uint8)
    cv2.rectangle(reference, (3, 3), (296, 416), (255, 255, 255), 6)
    source = np.float32([[0, 0], [299, 0], [299, 419], [0, 419]])
    destination = np.float32([[170, 70], [480, 105], [455, 600], [130, 570]])
    transform = cv2.getPerspectiveTransform(source, destination)
    frame = np.zeros((680, 900, 3), dtype=np.uint8)
    warped = cv2.warpPerspective(reference, transform, (900, 680))
    mask = cv2.warpPerspective(np.full(reference.shape[:2], 255, np.uint8), transform, (900, 680))
    frame[mask > 0] = warped[mask > 0]

    crop = _reference_guided_crop(frame, reference)

    assert crop is not None
    assert crop.shape == (840, 600, 3)
    normalized_reference = cv2.resize(reference, (600, 840))
    correlation = np.corrcoef(crop.reshape(-1), normalized_reference.reshape(-1))[0, 1]
    assert correlation > 0.7
