"""Acme Store API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.db import init_db
from app.routes import cart, orders, products


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_db(settings.database_path)
        yield

    app = FastAPI(title="Acme Store API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(products.router)
    app.include_router(cart.router)
    app.include_router(orders.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
