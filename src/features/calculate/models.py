"""Pydantic models for POST /calculate."""
from typing import Annotated

from pydantic import BaseModel, Field


class CalcItem(BaseModel):
    nevo_code: int = Field(ge=1)
    grams: float = Field(gt=0, le=5000)


class CalcRequest(BaseModel):
    items: Annotated[list[CalcItem], Field(min_length=1, max_length=50)]


class CalcTotals(BaseModel):
    kcal: float
    kj: float
    protein_g: float
    fat_g: float
    saturated_fat_g: float
    carbs_g: float
    sugar_g: float
    fiber_g: float
    salt_g: float


class CalcItemOut(BaseModel):
    nevo_code: int
    name_nl: str
    name_en: str
    grams: float
    kcal: float


class CalcResponse(BaseModel):
    totals: CalcTotals
    items: list[CalcItemOut]
