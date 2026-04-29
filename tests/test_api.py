"""End-to-end tests against the live local Postgres + FastAPI app.
Pre-conditie: NEVO 2025 is geladen via src/load_nevo.py.

Sample sanity values used:
  nevo_code=1  Aardappelen rauw      88 kcal/100g, 371 kJ/100g, 2 g protein
"""
from contextlib import asynccontextmanager

import httpx
import pytest

from src.app import app


# -- /health --------------------------------------------------------------


async def test_health_ok(client: httpx.AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "version" in body


async def test_health_degraded_when_db_down(client: httpx.AsyncClient):
    # Regression: status now reflects db reachability instead of always being "ok".
    @asynccontextmanager
    async def _broken_conn():
        raise RuntimeError("simulated db outage")
        yield  # unreachable, here to satisfy asynccontextmanager

    class _BrokenPool:
        def connection(self):
            return _broken_conn()

    original = app.state.pool
    app.state.pool = _BrokenPool()
    try:
        r = await client.get("/health")
    finally:
        app.state.pool = original
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["db"] == "unreachable"


# -- auth -----------------------------------------------------------------


async def test_foods_requires_api_key(client: httpx.AsyncClient):
    r = await client.get("/foods?q=appel")
    assert r.status_code == 401
    assert r.json() == {"error": "invalid api key"}


async def test_foods_wrong_api_key(client: httpx.AsyncClient):
    r = await client.get("/foods?q=appel", headers={"X-API-Key": "bogus"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid api key"}


async def test_foods_non_ascii_api_key(client: httpx.AsyncClient):
    # Regression: compare_digest on str rejects non-ASCII; we encode to bytes
    # so a malformed key returns 401 instead of crashing with TypeError.
    # httpx blocks str-with-non-ASCII at the client; bytes pass through.
    r = await client.get(
        "/foods?q=appel", headers={"X-API-Key": "blé".encode("latin-1")}
    )
    assert r.status_code == 401
    assert r.json() == {"error": "invalid api key"}


# -- /foods?q= ------------------------------------------------------------


async def test_search_finds_appel(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods?q=appel", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "appel"
    assert len(body["results"]) > 0
    names = " ".join(item["name_nl"].lower() for item in body["results"])
    assert "appel" in names


async def test_search_limit(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods?q=appel&limit=2", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 2


async def test_search_short_q_rejected(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods?q=a", headers=auth_headers)
    assert r.status_code == 422


# -- /foods/{nevo_code} ---------------------------------------------------


async def test_get_food_by_code(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods/1", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["nevo_code"] == 1
    assert "aardappel" in body["name_nl"].lower()
    by_code = {n["code"]: n for n in body["nutrients"]}
    assert by_code["ENERCC"]["value_per_100"] == pytest.approx(88, abs=1)
    assert by_code["ENERCC"]["unit"] == "kcal"
    assert by_code["ENERCJ"]["unit"] == "kJ"


async def test_get_food_404(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods/99999", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "food not found"
    assert body["nevo_code"] == 99999


# -- /calculate -----------------------------------------------------------


async def test_calculate_aardappel(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.post(
        "/calculate",
        headers=auth_headers,
        json={"items": [{"nevo_code": 1, "grams": 150}]},
    )
    assert r.status_code == 200
    body = r.json()
    # 88 kcal/100g * 1.5 = 132 kcal
    assert body["totals"]["kcal"] == pytest.approx(132, abs=1)
    assert body["totals"]["kj"] == pytest.approx(371 * 1.5, abs=1)
    assert len(body["items"]) == 1
    assert body["items"][0]["nevo_code"] == 1
    assert body["items"][0]["grams"] == 150
    assert body["items"][0]["kcal"] == pytest.approx(132, abs=1)


async def test_calculate_unknown_code(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.post(
        "/calculate",
        headers=auth_headers,
        json={"items": [{"nevo_code": 99999, "grams": 100}]},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "unknown nevo_code"
    assert 99999 in body["missing"]


async def test_calculate_validation_grams_zero(
    client: httpx.AsyncClient, auth_headers: dict
):
    r = await client.post(
        "/calculate",
        headers=auth_headers,
        json={"items": [{"nevo_code": 1, "grams": 0}]},
    )
    assert r.status_code == 422


async def test_calculate_validation_empty_items(
    client: httpx.AsyncClient, auth_headers: dict
):
    r = await client.post(
        "/calculate", headers=auth_headers, json={"items": []}
    )
    assert r.status_code == 422
