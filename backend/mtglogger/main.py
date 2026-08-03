import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import __version__
from .api import dashboard, decks, inventory, prices, references, reviews, scanner, sealed
from .config import get_settings
from .database import Base, SessionLocal, engine
from .providers import close_scryfall_client
from .services.prices import price_refresh_loop
from .services.references import reference_refresh_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    get_settings().image_dir.mkdir(parents=True, exist_ok=True)
    get_settings().reference_image_dir.mkdir(parents=True, exist_ok=True)
    price_task = asyncio.create_task(price_refresh_loop(get_settings().price_refresh_hours))
    reference_task = (
        asyncio.create_task(reference_refresh_loop(get_settings().reference_refresh_hours))
        if get_settings().reference_auto_sync
        else None
    )
    try:
        yield
    finally:
        price_task.cancel()
        if reference_task:
            reference_task.cancel()
        with suppress(asyncio.CancelledError):
            await price_task
        if reference_task:
            with suppress(asyncio.CancelledError):
                await reference_task
        await close_scryfall_client()


app = FastAPI(title="MTGLogger API", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(inventory.router, prefix="/api")
app.include_router(scanner.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(sealed.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(references.router, prefix="/api")
app.include_router(prices.router, prefix="/api")
app.include_router(decks.router, prefix="/api")


@app.get("/api/health")
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc
    return {"status": "ok", "version": __version__}
