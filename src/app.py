"""FastAPI composition root.
Composition root van FastAPI.

Wires lifespan-managed connection pool, exception handlers that match
the API contract in specs/03-api.md, and includes one router per feature
slice."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from src.features.calculate.router import router as calculate_router
from src.features.get_food.router import router as get_food_router
from src.features.health.router import router as health_router
from src.features.search_foods.router import router as search_foods_router
from src.features.search_foods_vector.router import router as search_foods_vector_router
from src.shared.config import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("nutrientcontent")


async def _register_vector(conn) -> None:
    """Configure-callback: registreert pgvector-adapter op elke conn die
    de pool uitgeeft. Anders gaat een `vector(...)` parameter als
    `double precision[]` over de wire en faalt de cast in SQL."""
    await register_vector_async(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(
        settings.nevo_api_url,
        min_size=2,
        max_size=10,
        open=False,
        configure=_register_vector,
    )
    await pool.open()
    app.state.pool = pool
    log.info("pool opened (min=2, max=10)")
    try:
        yield
    finally:
        await pool.close()
        log.info("pool closed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="nutrientcontent",
        version=settings.app_version,
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException):
        # Spec: error responses use {"error": "..."} (not FastAPI's default
        # {"detail": "..."}). When a route already raises with detail=dict
        # (e.g. 404 with extras), pass it through as-is.
        if exc.status_code >= 500:
            log.error("%s %s -> %d: %s", request.method, request.url.path, exc.status_code, exc.detail)
        body = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            {"error": "validation failed", "details": exc.errors()},
            status_code=422,
        )

    app.include_router(health_router)
    app.include_router(search_foods_router)
    app.include_router(search_foods_vector_router)
    app.include_router(get_food_router)
    app.include_router(calculate_router)
    return app


app = create_app()
