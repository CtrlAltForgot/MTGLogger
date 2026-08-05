import numpy as np


def test_neural_preprocess_matches_model_contract():
    from mtglogger.services.neural import NeuralEmbedder

    image = np.zeros((480, 320, 3), dtype=np.uint8)
    tensor = NeuralEmbedder.preprocess(image)
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert tensor.flags.c_contiguous
    np.testing.assert_allclose(tensor[0, :, 0, 0], [-2.117904, -2.035714, -1.804444], rtol=1e-5)


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
        db.commit()
        index = NeuralIndex(db, "test-model")
        matches = index.search(np.array([0.1, 0.9, 0.0], dtype=np.float32), limit=2)

    assert [match.reference.scryfall_id for match in matches] == ["card-b", "card-a"]
    assert matches[0].source_kind == "correction"
    packed = encode_vector(np.array([0.6, 0.8], dtype=np.float32))
    np.testing.assert_allclose(decode_vector(packed, 2), [0.6, 0.8], atol=5e-4)


def test_neural_model_download_is_pinned():
    from mtglogger.services.neural import MODEL_SHA256, MODEL_URL

    assert MODEL_URL.startswith("https://paddle-model-ecology.bj.bcebos.com/")
    assert len(MODEL_SHA256) == 64
