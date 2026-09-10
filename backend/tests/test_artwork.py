import asyncio
from collections import OrderedDict

import httpx
import pytest
from fastapi import FastAPI

from mtglogger.api import artwork

CARD_ID = "63e08eb1-de6e-4f31-a523-86b1e013b1ca"
PATH = f"/api/artwork/normal/front/{CARD_ID}.jpg"
JPEG = b"\xff\xd8\xff\xe0public card artwork"


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(artwork, "_cache", OrderedDict())
    monkeypatch.setattr(artwork, "_cache_bytes", 0)
    monkeypatch.setattr(artwork, "_locks", [asyncio.Lock() for _ in range(16)])
    monkeypatch.setattr(artwork, "_downloads", asyncio.Semaphore(6))


def app():
    instance = FastAPI()
    instance.include_router(artwork.router, prefix="/api")
    return instance


@pytest.mark.asyncio
async def test_same_origin_artwork_coalesces_requests_and_caches(monkeypatch):
    calls = []

    async def upstream(request):
        calls.append(str(request.url))
        await asyncio.sleep(0.02)
        return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as images:
        monkeypatch.setattr(artwork, "image_client", lambda: images)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app()), base_url="http://test"
        ) as client:
            first, second = await asyncio.gather(client.get(PATH), client.get(PATH))
            cached = await client.get(PATH)
    assert calls == [f"https://cards.scryfall.io/normal/front/6/3/{CARD_ID}.jpg"]
    assert first.content == second.content == cached.content == JPEG
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert first.headers["cache-control"] == "public, max-age=86400"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [(404, b"missing", 404), (200, b"<html>", 503)])
async def test_failed_images_are_not_cached(monkeypatch, status, body, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    ) as images:
        monkeypatch.setattr(artwork, "image_client", lambda: images)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app()), base_url="http://test"
        ) as client:
            response = await client.get(PATH)
    assert response.status_code == expected
    assert not artwork._cache


@pytest.mark.asyncio
async def test_image_download_size_is_bounded(monkeypatch):
    monkeypatch.setattr(artwork, "MAX_IMAGE_BYTES", 4)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=JPEG))
    ) as images:
        monkeypatch.setattr(artwork, "image_client", lambda: images)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app()), base_url="http://test"
        ) as client:
            response = await client.get(PATH)
    assert response.status_code == 503
    assert not artwork._cache


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    f"/api/artwork/other/front/{CARD_ID}.jpg",
    f"/api/artwork/normal/other/{CARD_ID}.jpg",
    "/api/artwork/normal/front/not-a-card.jpg",
])
async def test_only_card_ids_and_known_image_variants_are_accepted(monkeypatch, path):
    def no_network():
        pytest.fail("Invalid artwork paths must never contact a provider")

    monkeypatch.setattr(artwork, "image_client", no_network)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app()), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 422


def test_artwork_cache_has_size_and_age_limits(monkeypatch):
    monkeypatch.setattr(artwork, "MAX_CACHE_BYTES", 6)
    monkeypatch.setattr(artwork.time, "monotonic", lambda: 100)
    artwork.store_image("a", b"123")
    artwork.store_image("b", b"456")
    assert artwork.cached_image("a") == b"123"  # Most recently used survives eviction.
    artwork.store_image("c", b"789")
    assert artwork.cached_image("b") is None
    assert artwork._cache_bytes == 6
    monkeypatch.setattr(artwork.time, "monotonic", lambda: 100 + artwork.CACHE_SECONDS)
    assert artwork.cached_image("a") is None
    assert artwork.cached_image("c") is None
    assert artwork._cache_bytes == 0
