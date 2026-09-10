"""Serve public card artwork without requiring a browser connection to the CDN."""

import asyncio
import time
from collections import OrderedDict
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Response

from ..config import get_settings

router = APIRouter(prefix="/artwork", tags=["card artwork"])
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 86_400
_cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_cache_bytes = 0
_locks = [asyncio.Lock() for _ in range(16)]
_downloads = asyncio.Semaphore(6)
_client: httpx.AsyncClient | None = None


def image_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(4, connect=2),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=6),
            headers={"User-Agent": get_settings().scryfall_user_agent},
            follow_redirects=False,
        )
    return _client


async def close_image_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def cached_image(key: str) -> bytes | None:
    global _cache_bytes
    cached = _cache.get(key)
    if cached is None:
        return None
    created, data = cached
    if time.monotonic() - created >= CACHE_SECONDS:
        _cache.pop(key)
        _cache_bytes -= len(data)
        return None
    _cache.move_to_end(key)
    return data


def store_image(key: str, data: bytes) -> None:
    global _cache_bytes
    previous = _cache.pop(key, None)
    if previous:
        _cache_bytes -= len(previous[1])
    while _cache and _cache_bytes + len(data) > MAX_CACHE_BYTES:
        _, (_, evicted) = _cache.popitem(last=False)
        _cache_bytes -= len(evicted)
    if len(data) <= MAX_CACHE_BYTES:
        _cache[key] = (time.monotonic(), data)
        _cache_bytes += len(data)


@router.get("/{size}/{face}/{scryfall_id}.jpg")
async def card_artwork(
    size: Literal["small", "normal", "large"],
    face: Literal["front", "back"],
    scryfall_id: UUID,
):
    card_id = str(scryfall_id)
    key = f"{size}/{face}/{card_id}"
    data = cached_image(key)
    if data is None:
        try:
            async with asyncio.timeout(8), _locks[hash(key) % len(_locks)]:
                data = cached_image(key)
                if data is None:
                    # Build the URL from typed path segments, never a caller-supplied URL.
                    url = (
                        f"https://cards.scryfall.io/{size}/{face}/"
                        f"{card_id[0]}/{card_id[1]}/{card_id}.jpg"
                    )
                    async with _downloads, image_client().stream("GET", url) as upstream:
                        if upstream.status_code == 404:
                            raise HTTPException(404, "Artwork not available for this printing")
                        upstream.raise_for_status()
                        content = bytearray()
                        async for chunk in upstream.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_IMAGE_BYTES:
                                raise ValueError("Artwork exceeds image size limit")
                        data = bytes(content)
                        if not data.startswith(b"\xff\xd8\xff"):
                            raise ValueError("Artwork provider did not return a JPEG")
                    store_image(key, data)
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise HTTPException(503, "Artwork is temporarily unavailable. Try again.") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": f"public, max-age={CACHE_SECONDS}",
            "X-Content-Type-Options": "nosniff",
        },
    )
