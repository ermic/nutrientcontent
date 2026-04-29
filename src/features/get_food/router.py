"""GET /foods/{nevo_code} — full nutrient list for one food."""
from fastapi import APIRouter, Depends, HTTPException, status

from src.entities.food import repo as food_repo
from src.entities.food.models import FoodDetail
from src.shared.auth import require_api_key
from src.shared.db import PoolDep

router = APIRouter()


@router.get("/foods/{nevo_code}", dependencies=[Depends(require_api_key)])
async def get_food(pool: PoolDep, nevo_code: int) -> FoodDetail:
    food = await food_repo.get_detail(pool, nevo_code)
    if food is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "food not found", "nevo_code": nevo_code},
        )
    return food
