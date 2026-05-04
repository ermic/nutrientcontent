"""Pydantic response shapes for the `food` entity.
Pydantic-respondsmodellen voor het `food`-entity."""
from pydantic import BaseModel


class FoodSummary(BaseModel):
    """Single search-result row (used by GET /foods?q=)."""

    nevo_code: int
    name_nl: str
    name_en: str
    food_group_nl: str
    food_group_en: str


class FoodVectorHit(FoodSummary):
    """Search-result row from GET /foods/vector — adds cosine similarity
    so the caller can apply its own confidence threshold."""

    similarity: float


class NutrientValue(BaseModel):
    """One nutrient measurement on a food (per 100g of product)."""

    code: str
    name_nl: str
    name_en: str
    group_nl: str
    group_en: str
    unit: str
    value_per_100: float | None


class FoodDetail(BaseModel):
    """Full per-food response (used by GET /foods/{nevo_code})."""

    nevo_code: int
    name_nl: str
    name_en: str
    food_group_nl: str
    food_group_en: str
    quantity: str
    synonyms: str | None
    note: str | None
    nutrients: list[NutrientValue]
