"""End-to-end tests voor GET /foods/vector.

embed_query wordt gemonkeypatched zodat tests geen Gemini-calls maken.
We vragen via de DB een bestaande embedding op (codes 1-9 zijn al
backfilled in fase 1f) en gebruiken die als "fake query embedding". Top-1
moet dan dezelfde rij teruggeven met similarity ≈ 1.0.
"""
from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from pgvector.psycopg import register_vector

from src.shared.config import settings


# ─── Helpers ──────────────────────────────────────────────────────────────


def _fetch_known_embedding(nevo_code: int) -> tuple[str, str, list[float]]:
    """Pak een echte embedding uit de DB om tegen te zoeken — voorkomt
    Gemini-roundtrips in tests."""
    with psycopg.connect(settings.nevo_api_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name_nl, name_en, embedding FROM foods WHERE nevo_code = %s",
                (nevo_code,),
            )
            row = cur.fetchone()
    if row is None or row[2] is None:
        pytest.skip(f"nevo_code={nevo_code} heeft geen embedding (fase 1f niet gedraaid?)")
    name_nl, name_en, vec = row
    return name_nl, name_en, list(vec)


def _patch_embed_query(monkeypatch: pytest.MonkeyPatch, vector: list[float]):
    """Vervangt src.shared.embeddings.embed_query (binnen de router-module)."""
    async def _fake(text: str, *, api_key: str, timeout_s: float = 10.0):
        return vector

    # De router importeert embed_query bij module-load; monkeypatch op
    # de bron én de gebonden naam in de router-module om beide paden
    # af te dekken.
    from src.features.search_foods_vector import router as r
    monkeypatch.setattr(r, "embed_query", _fake)


# ─── auth + validation ────────────────────────────────────────────────────


async def test_vector_requires_api_key(client: httpx.AsyncClient):
    r = await client.get("/foods/vector?q=chicken")
    assert r.status_code == 401


async def test_vector_wrong_api_key(client: httpx.AsyncClient):
    r = await client.get("/foods/vector?q=chicken", headers={"X-API-Key": "bogus"})
    assert r.status_code == 401


async def test_vector_q_too_short(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods/vector?q=a", headers=auth_headers)
    assert r.status_code == 422


async def test_vector_limit_out_of_range(client: httpx.AsyncClient, auth_headers: dict):
    r = await client.get("/foods/vector?q=chicken&limit=999", headers=auth_headers)
    assert r.status_code == 422


async def test_vector_min_similarity_out_of_range(
    client: httpx.AsyncClient, auth_headers: dict
):
    r = await client.get(
        "/foods/vector?q=chicken&min_similarity=2.0", headers=auth_headers
    )
    assert r.status_code == 422


# ─── happy path ───────────────────────────────────────────────────────────


async def test_vector_top1_is_self_with_high_similarity(
    client: httpx.AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    # Gebruik de embedding van nevo_code 1 (Aardappelen rauw) als query;
    # top-1 hoort dus die rij zelf te zijn met similarity ≈ 1.0.
    name_nl, name_en, vec = _fetch_known_embedding(nevo_code=1)
    _patch_embed_query(monkeypatch, vec)

    r = await client.get("/foods/vector?q=potato", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "potato"
    assert len(body["results"]) >= 1
    top = body["results"][0]
    assert top["nevo_code"] == 1
    assert top["name_nl"] == name_nl
    assert top["name_en"] == name_en
    assert 0.99 <= top["similarity"] <= 1.0001  # ≈1.0, met float-fuzz


async def test_vector_results_sorted_by_similarity_desc(
    client: httpx.AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    _, _, vec = _fetch_known_embedding(nevo_code=1)
    _patch_embed_query(monkeypatch, vec)

    r = await client.get("/foods/vector?q=potato&limit=5", headers=auth_headers)
    assert r.status_code == 200
    sims = [item["similarity"] for item in r.json()["results"]]
    assert sims == sorted(sims, reverse=True)


async def test_vector_respects_limit(
    client: httpx.AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    _, _, vec = _fetch_known_embedding(nevo_code=1)
    _patch_embed_query(monkeypatch, vec)

    r = await client.get("/foods/vector?q=potato&limit=3", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["results"]) == 3


async def test_vector_min_similarity_filters_low_matches(
    client: httpx.AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    _, _, vec = _fetch_known_embedding(nevo_code=1)
    _patch_embed_query(monkeypatch, vec)

    # Threshold 0.95: alleen near-duplicates van Aardappelen rauw.
    r = await client.get(
        "/foods/vector?q=potato&limit=20&min_similarity=0.95",
        headers=auth_headers,
    )
    assert r.status_code == 200
    items = r.json()["results"]
    # Alle terugkomende similarity-scores moeten boven de threshold liggen.
    assert all(it["similarity"] >= 0.95 for it in items)
    # Eigen-rij is sowieso boven; dus minimaal één resultaat.
    assert len(items) >= 1


# ─── failure modes ────────────────────────────────────────────────────────


async def test_vector_returns_503_when_gemini_fails(
    client: httpx.AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    from src.features.search_foods_vector import router as r_mod
    from src.shared.embeddings import EmbeddingError

    async def _explode(*a: Any, **kw: Any):
        raise EmbeddingError("simulated 500")

    monkeypatch.setattr(r_mod, "embed_query", _explode)
    r = await client.get("/foods/vector?q=chicken", headers=auth_headers)
    assert r.status_code == 503
    assert "embed" in r.json().get("error", "").lower()
