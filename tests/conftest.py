"""Async-test fixtures. Tests run against the real local Postgres."""
import httpx
import pytest_asyncio

from src.app import app
from src.shared.config import settings


@pytest_asyncio.fixture
async def client():
    """httpx.AsyncClient bound to the FastAPI app, with lifespan started so
    the connection pool is open."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as c:
            yield c


@pytest_asyncio.fixture
def auth_headers() -> dict:
    return {"X-API-Key": settings.api_key}
