"""Compute nutrition totals for a list of (nevo_code, grams) pairs.
Berekent voedingstotalen voor een lijst (nevo_code, grams)-paren.

Two DB round-trips: one to fetch food names (which also reveals missing
nevo_codes via the result set), one to fetch all relevant nutrient
values. All math + rounding happens in Python."""
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .models import CalcItem, CalcItemOut, CalcResponse, CalcTotals

# NEVO nutrient code -> CalcTotals field name. NA is special (mg -> g, *2.5).
NUTRIENT_TO_FIELD: dict[str, str] = {
    "ENERCC": "kcal",
    "ENERCJ": "kj",
    "PROT": "protein_g",
    "FAT": "fat_g",
    "FASAT": "saturated_fat_g",
    "CHO": "carbs_g",
    "SUGAR": "sugar_g",
    "FIBT": "fiber_g",
    "NA": "salt_g",
}

_CORE_CODES = list(NUTRIENT_TO_FIELD.keys())


class UnknownNevoCodes(Exception):
    """Raised when one or more requested nevo_codes don't exist in the DB."""

    def __init__(self, missing: list[int]):
        self.missing = missing
        super().__init__(f"unknown nevo_codes: {missing}")


async def calculate(
    pool: AsyncConnectionPool, items: list[CalcItem]
) -> CalcResponse:
    codes = [i.nevo_code for i in items]

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT nevo_code, name_nl, name_en
                FROM foods
                WHERE nevo_code = ANY(%s)
                """,
                (codes,),
            )
            food_rows = await cur.fetchall()
            food_by_code: dict[int, dict] = {r["nevo_code"]: r for r in food_rows}

            missing = [c for c in codes if c not in food_by_code]
            if missing:
                raise UnknownNevoCodes(sorted(set(missing)))

            await cur.execute(
                """
                SELECT nevo_code, nutrient_code, value_per_100::float8 AS v
                FROM food_nutrients
                WHERE nevo_code = ANY(%s) AND nutrient_code = ANY(%s)
                """,
                (codes, _CORE_CODES),
            )
            value_rows = await cur.fetchall()

    values: dict[tuple[int, str], float] = {
        (r["nevo_code"], r["nutrient_code"]): (r["v"] or 0.0) for r in value_rows
    }

    totals: dict[str, float] = {field: 0.0 for field in NUTRIENT_TO_FIELD.values()}
    items_out: list[CalcItemOut] = []

    for item in items:
        kcal_per_100 = values.get((item.nevo_code, "ENERCC"), 0.0)
        item_kcal = kcal_per_100 * item.grams / 100
        food = food_by_code[item.nevo_code]
        items_out.append(
            CalcItemOut(
                nevo_code=item.nevo_code,
                name_nl=food["name_nl"],
                name_en=food["name_en"],
                grams=item.grams,
                kcal=round(item_kcal, 1),
            )
        )
        for nut_code, field in NUTRIENT_TO_FIELD.items():
            v_per_100 = values.get((item.nevo_code, nut_code), 0.0)
            v = v_per_100 * item.grams / 100
            if nut_code == "NA":
                v = v * 2.5 / 1000  # mg natrium -> g salt
            totals[field] += v

    rounded = {
        k: round(v, 3 if k == "salt_g" else 1) for k, v in totals.items()
    }

    return CalcResponse(totals=CalcTotals(**rounded), items=items_out)
