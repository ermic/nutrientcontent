# Spec 03 — API (FastAPI)

## Doel
Definieer endpoints, request/response-shapes, foutgedrag, auth en logging.

## Bind
- Host: `127.0.0.1` (verplicht — geen 0.0.0.0).
- Port: `5555`.
- Geen TLS — verkeer blijft op de loopback. Als calorietje.nl ooit verhuist:
  zet er een nginx-reverse-proxy met TLS voor. Voor MVP n.v.t.

## Authenticatie
- Header: `X-API-Key: <waarde>`.
- Waarde komt uit `.env` (`API_KEY=...`), 32+ random bytes (b.v. `openssl rand -hex 32`).
- Vergelijking via `secrets.compare_digest` (constant-time).
- Ontbrekende of foutieve key → `401 Unauthorized` met body
  `{"error": "invalid api key"}`. Geen verdere details.
- `/health` is **uitgesloten** van auth (handig voor systemd/healthchecks).

```python
# src/auth.py
import secrets
from fastapi import Header, HTTPException, status

from .config import settings

async def require_api_key(x_api_key: str = Header(...)):
    if not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key")
```

## Endpoints

### `GET /health`
Liveness, geen auth.

**Response 200**
```json
{ "status": "ok", "db": "ok", "version": "0.1.0" }
```
Bij DB-fout: `503` met `"db": "unreachable"`.

---

### `GET /foods?q=<term>&lang=<nl|en>&limit=<n>`
Zoeken op naam of synoniem.

**Query params**
- `q` (verplicht, ≥2 chars): zoekterm.
- `lang` (default `nl`): `nl` of `en` — bepaalt welke FTS-index gebruikt wordt.
- `limit` (default 20, max 100).

**Logica**
1. Probeer eerst FTS: `WHERE search_<lang> @@ plainto_tsquery('<lang>', $1)`.
2. Als 0 resultaten: trigram-fallback `WHERE name_<lang> %% $1 ORDER BY similarity(...) DESC`.

**Response 200**
```json
{
  "query": "appel",
  "results": [
    {
      "nevo_code": 234,
      "name_nl": "Appel rauw",
      "name_en": "Apple raw",
      "food_group_nl": "Fruit",
      "food_group_en": "Fruit"
    }
  ]
}
```

---

### `GET /foods/{nevo_code}?lang=<nl|en>`
Volledige nutriëntinfo voor één product.

**Response 200**
```json
{
  "nevo_code": 1,
  "name_nl": "Aardappelen rauw",
  "name_en": "Potatoes raw",
  "food_group_nl": "Aardappelen en knolgewassen",
  "food_group_en": "Potatoes and tubers",
  "quantity": "per 100g",
  "synonyms": null,
  "note": null,
  "nutrients": [
    { "code": "ENERCC", "name_nl": "Energie kcal", "name_en": "Energy kcal",
      "group_nl": "Energie en macronutriënten", "group_en": "Energy and macronutrients",
      "unit": "kcal", "value_per_100": 88 },
    { "code": "PROT", "...": "..." }
  ]
}
```

**Response 404** als `nevo_code` niet bestaat:
```json
{ "error": "food not found", "nevo_code": 99999 }
```

---

### `POST /calculate`
**Het belangrijkste endpoint** — dit is wat calorietje.nl gaat aanroepen.

**Request body**
```json
{
  "items": [
    { "nevo_code": 1,   "grams": 150 },
    { "nevo_code": 234, "grams": 80 }
  ]
}
```
Validatie (pydantic):
- `items` lengte 1–50.
- `nevo_code` ≥ 1.
- `grams` 0 < g ≤ 5000 (sanity check).

**Response 200**
```json
{
  "totals": {
    "kcal": 211.0,
    "kj": 894.0,
    "protein_g": 4.7,
    "fat_g": 0.3,
    "saturated_fat_g": 0.05,
    "carbs_g": 47.6,
    "sugar_g": 4.1,
    "fiber_g": 5.2,
    "salt_g": 0.024
  },
  "items": [
    {
      "nevo_code": 1,
      "name_nl": "Aardappelen rauw",
      "name_en": "Potatoes raw",
      "grams": 150,
      "kcal": 132.0
    },
    {
      "nevo_code": 234,
      "name_nl": "Appel rauw",
      "name_en": "Apple raw",
      "grams": 80,
      "kcal": 79.0
    }
  ]
}
```

**Response 400** bij validatiefout — pydantic levert dit automatisch.

**Response 422** als één van de `nevo_code`-en niet bestaat:
```json
{ "error": "unknown nevo_code", "missing": [99999] }
```

**Berekening**
- Per item: `nutrient_value = value_per_100 * grams / 100`.
- Totaal: som over alle items per nutriënt-code.
- `salt_g = total_NA_mg * 2.5 / 1000`.
- Afronding: 1 decimaal voor `kcal`, `kj`, `protein/fat/carbs/sugar/fiber_g`.
  3 decimalen voor `salt_g`. NULL waarden → behandel als 0.

**Performance-target**: < 50 ms voor een lijst van 10 items.

## Foutgedrag (algemeen)
- 4xx: `{"error": "<short>", ...details?}`.
- 5xx: log volledige stack-trace, retourneer `{"error": "internal"}` (geen interne details).
- Alle requests/responses worden gelogd op INFO-niveau (zonder body voor 200-OK
  om logs klein te houden; bij ≥400 wel met body).

## Pydantic-modellen (sketch)
```python
# src/models.py
from pydantic import BaseModel, Field, conlist

class CalcItem(BaseModel):
    nevo_code: int = Field(ge=1)
    grams: float = Field(gt=0, le=5000)

class CalcRequest(BaseModel):
    items: conlist(CalcItem, min_length=1, max_length=50)

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
```

## Connection pool
Eén globale `psycopg_pool.AsyncConnectionPool` met `min_size=2, max_size=10`.
Lifecycle via FastAPI's lifespan-event.

```python
# src/app.py (gedeelte)
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool

@asynccontextmanager
async def lifespan(app):
    app.state.pool = AsyncConnectionPool(settings.api_db_url,
                                         min_size=2, max_size=10, open=False)
    await app.state.pool.open()
    yield
    await app.state.pool.close()
```

## Voorbeeld-curl
```bash
API_KEY=$(grep ^API_KEY .env | cut -d= -f2)
H="X-API-Key: $API_KEY"

curl -s "http://127.0.0.1:5555/health"
curl -s -H "$H" "http://127.0.0.1:5555/foods?q=appel"
curl -s -H "$H" "http://127.0.0.1:5555/foods/1"
curl -s -H "$H" -H "Content-Type: application/json" \
  -d '{"items":[{"nevo_code":1,"grams":150},{"nevo_code":234,"grams":80}]}' \
  "http://127.0.0.1:5555/calculate"
```

## OpenAPI-docs
FastAPI publiceert automatisch:
- Swagger UI:   `http://127.0.0.1:5555/docs`
- ReDoc:        `http://127.0.0.1:5555/redoc`
- OpenAPI JSON: `http://127.0.0.1:5555/openapi.json`

(Allemaal achter dezelfde localhost-bind, dus alleen jij ziet ze.)
