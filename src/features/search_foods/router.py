"""GET /foods?q=<term>&lang=<nl|en>&limit=<n>"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from src.entities.food import repo as food_repo
from src.entities.food.models import FoodSummary
from src.shared.auth import require_api_key
from src.shared.db import PoolDep

router = APIRouter()


@router.get("/foods", dependencies=[Depends(require_api_key)])
async def search_foods(
    pool: PoolDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    lang: Annotated[Literal["nl", "en"], Query()] = "nl",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    results: list[FoodSummary] = await food_repo.search(pool, q, lang, limit)
    return {"query": q, "results": results}
