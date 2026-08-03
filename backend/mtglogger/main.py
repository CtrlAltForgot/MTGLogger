import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import dashboard, decks, inventory, prices, references, reviews, scanner, sealed
from .config import get_settings
from .database import Base, engine
from .services.prices import price_refresh_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    get_settings().image_dir.mkdir(parents=True, exist_ok=True)
    price_task = asyncio.create_task(price_refresh_loop(get_settings().price_refresh_hours))
    try:
        yield
    finally:
        price_task.cancel()


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
    return {"status": "ok", "version": __version__}
