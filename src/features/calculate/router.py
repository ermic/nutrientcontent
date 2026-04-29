"""POST /calculate — main consumer endpoint for calorietje.nl."""
from fastapi import APIRouter, Depends, HTTPException, status

from src.shared.auth import require_api_key
from src.shared.db import PoolDep

from .models import CalcRequest, CalcResponse
from .service import UnknownNevoCodes, calculate

router = APIRouter()


@router.post("/calculate", dependencies=[Depends(require_api_key)])
async def post_calculate(pool: PoolDep, body: CalcRequest) -> CalcResponse:
    try:
        return await calculate(pool, body.items)
    except UnknownNevoCodes as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "unknown nevo_code", "missing": exc.missing},
        ) from exc
