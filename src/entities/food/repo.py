"""Data-access for foods + their nutrients.
Data-access voor foods en hun nutriënten.

Lookups by name (FTS + trigram fallback) and by nevo_code (single product
with full nutrient list)."""
from typing import Literal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .models import FoodDetail, FoodSummary, NutrientValue

Lang = Literal["nl", "en"]

_LANG_TO_TS = {"nl": "dutch", "en": "english"}


async def search(
    pool: AsyncConnectionPool,
    q: str,
    lang: Lang,
    limit: int,
) -> list[FoodSummary]:
    """Full-text search; falls back to trigram similarity if FTS finds nothing."""
    search_col = "search_nl" if lang == "nl" else "search_en"
    name_col = "name_nl" if lang == "nl" else "name_en"
    ts_lang = _LANG_TO_TS[lang]

    fts_sql = f"""
        SELECT nevo_code, name_nl, name_en, food_group_nl, food_group_en
        FROM foods
        WHERE {search_col} @@ plainto_tsquery(%s, %s)
        ORDER BY ts_rank({search_col}, plainto_tsquery(%s, %s)) DESC
        LIMIT %s
    """
    trgm_sql = f"""
        SELECT nevo_code, name_nl, name_en, food_group_nl, food_group_en
        FROM foods
        WHERE similarity({name_col}, %s) > 0.2
        ORDER BY similarity({name_col}, %s) DESC
        LIMIT %s
    """

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(fts_sql, (ts_lang, q, ts_lang, q, limit))
            rows = await cur.fetchall()
            if not rows:
                await cur.execute(trgm_sql, (q, q, limit))
                rows = await cur.fetchall()
    return [FoodSummary(**r) for r in rows]


async def get_detail(
    pool: AsyncConnectionPool,
    nevo_code: int,
) -> FoodDetail | None:
    """Fetch one food + all its nutrient rows. None if nevo_code doesn't exist."""
    food_sql = """
        SELECT nevo_code, name_nl, name_en, food_group_nl, food_group_en,
               quantity, synonyms, note
        FROM foods
        WHERE nevo_code = %s
    """
    nutrients_sql = """
        SELECT n.code, n.name_nl, n.name_en, n.group_nl, n.group_en, n.unit,
               fn.value_per_100::float8 AS value_per_100
        FROM food_nutrients fn
        JOIN nutrients n ON n.code = fn.nutrient_code
        WHERE fn.nevo_code = %s
        ORDER BY n.group_nl, n.name_nl
    """

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(food_sql, (nevo_code,))
            food = await cur.fetchone()
            if food is None:
                return None
            await cur.execute(nutrients_sql, (nevo_code,))
            nutrient_rows = await cur.fetchall()

    return FoodDetail(
        **food,
        nutrients=[NutrientValue(**r) for r in nutrient_rows],
    )


async def find_missing_codes(
    pool: AsyncConnectionPool,
    codes: list[int],
) -> list[int]:
    """Return the subset of `codes` that are NOT present in the foods table."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT nevo_code FROM foods WHERE nevo_code = ANY(%s)",
                (codes,),
            )
            existing = {row[0] for row in await cur.fetchall()}
    return [c for c in codes if c not in existing]
