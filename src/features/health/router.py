"""GET /health — liveness + DB-reachability probe. No auth."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.shared.config import settings
from src.shared.db import PoolDep

router = APIRouter()


@router.get("/health")
async def health(pool: PoolDep) -> JSONResponse:
    db_status = "ok"
    http_status = 200
    try:
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT 1")
            await cur.fetchone()
    except Exception:
        db_status = "unreachable"
        http_status = 503
    return JSONResponse(
        {"status": "ok", "db": db_status, "version": settings.app_version},
        status_code=http_status,
    )
