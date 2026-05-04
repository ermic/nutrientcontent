"""Unit tests for src.shared.embeddings — Gemini text-embedding-004 client.

Geen netwerk: monkeypatch over `httpx.post` resp. `httpx.AsyncClient`. We
testen de wrapper-laag (request-shape, error-vertaling, response-validatie),
niet Gemini zelf.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.shared import embeddings as emb


# ─── build_text ──────────────────────────────────────────────────────────


def test_build_text_combines_all_fields():
    out = emb.build_text("Chicken fillet", "Meat", "kipfilet, kippenborst")
    assert out == "Chicken fillet | Meat | kipfilet, kippenborst"


def test_build_text_skips_missing_synonyms():
    assert emb.build_text("Rice", "Cereals", None) == "Rice | Cereals"
    assert emb.build_text("Rice", "Cereals", "") == "Rice | Cereals"


def test_build_text_strips_whitespace():
    assert emb.build_text("  Onion ", " Vegetables  ", "  ui ") == "Onion | Vegetables | ui"


# ─── embed_batch (sync) ──────────────────────────────────────────────────


def _ok_batch_response(n: int) -> dict:
    return {"embeddings": [{"values": [0.01] * emb.EMBED_DIM} for _ in range(n)]}


def _stub_response(status: int, json_body: Any | None = None, text: str = "") -> httpx.Response:
    """httpx.Response zonder echt request — minimal voor onze parser."""
    return httpx.Response(status_code=status, json=json_body, text=text if json_body is None else None)


def test_embed_batch_empty_returns_empty(monkeypatch: pytest.MonkeyPatch):
    # Geen netwerk-call mag plaatsvinden voor lege input.
    def _no_post(*a, **kw):  # pragma: no cover - shouldn't fire
        raise AssertionError("httpx.post should not be called for empty input")

    monkeypatch.setattr(emb.httpx, "post", _no_post)
    assert emb.embed_batch([], api_key="k", task_type="RETRIEVAL_DOCUMENT") == []


def test_embed_batch_happy_path(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def _fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _stub_response(200, _ok_batch_response(2))

    monkeypatch.setattr(emb.httpx, "post", _fake_post)
    out = emb.embed_batch(
        ["chicken fillet | Meat", "rice | Cereals"],
        api_key="my-key",
        task_type="RETRIEVAL_DOCUMENT",
    )
    assert len(out) == 2
    assert all(len(v) == emb.EMBED_DIM for v in out)
    # request-shape assertions
    assert captured["url"].endswith(":batchEmbedContents")
    assert captured["params"] == {"key": "my-key"}
    assert len(captured["json"]["requests"]) == 2
    assert captured["json"]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert captured["json"]["requests"][0]["model"] == f"models/{emb.EMBED_MODEL}"
    assert captured["json"]["requests"][0]["content"]["parts"][0]["text"] == "chicken fillet | Meat"
    # gemini-embedding-001 default = 3072 dim — we vragen expliciet 768 om de
    # pgvector(768) kolom te matchen. Vergeten = silent dimension mismatch.
    assert captured["json"]["requests"][0]["outputDimensionality"] == emb.EMBED_DIM


def test_embed_batch_non_2xx_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(emb.httpx, "post", lambda *a, **kw: _stub_response(429, text="rate limited"))
    with pytest.raises(emb.EmbeddingError, match="429"):
        emb.embed_batch(["x"], api_key="k", task_type="RETRIEVAL_DOCUMENT")


def test_embed_batch_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch):
    # 2 inputs maar 1 embedding terug → moet falen, niet stilletjes 1 retourneren.
    monkeypatch.setattr(
        emb.httpx, "post", lambda *a, **kw: _stub_response(200, _ok_batch_response(1))
    )
    with pytest.raises(emb.EmbeddingError, match="expected 2"):
        emb.embed_batch(["a", "b"], api_key="k", task_type="RETRIEVAL_DOCUMENT")


def test_embed_batch_wrong_dim_raises(monkeypatch: pytest.MonkeyPatch):
    bad = {"embeddings": [{"values": [0.1] * 100}]}  # te kort
    monkeypatch.setattr(emb.httpx, "post", lambda *a, **kw: _stub_response(200, bad))
    with pytest.raises(emb.EmbeddingError, match="!= 768"):
        emb.embed_batch(["a"], api_key="k", task_type="RETRIEVAL_DOCUMENT")


# ─── embed_query (async) ─────────────────────────────────────────────────


# `_RealAsyncClient` houdt de échte class vast; de monkeypatched factory
# moet die gebruiken (anders roept de factory zichzelf aan via `httpx.AsyncClient`).
_RealAsyncClient = httpx.AsyncClient


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def _factory(**kw):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(emb.httpx, "AsyncClient", _factory)


async def test_embed_query_happy_path(monkeypatch: pytest.MonkeyPatch):
    body = {"embedding": {"values": [0.5] * emb.EMBED_DIM}}

    async def _handler(request: httpx.Request) -> httpx.Response:
        assert "embedContent" in str(request.url)
        assert request.url.params.get("key") == "k"
        return httpx.Response(200, json=body)

    _patch_async_client(monkeypatch, _handler)
    out = await emb.embed_query("kip", api_key="k")
    assert len(out) == emb.EMBED_DIM
    assert out[0] == 0.5


async def test_embed_query_non_2xx_raises(monkeypatch: pytest.MonkeyPatch):
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_async_client(monkeypatch, _handler)
    with pytest.raises(emb.EmbeddingError, match="500"):
        await emb.embed_query("kip", api_key="k")


async def test_embed_query_bad_shape_raises(monkeypatch: pytest.MonkeyPatch):
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embedding": {"values": [0.1] * 10}})

    _patch_async_client(monkeypatch, _handler)
    with pytest.raises(emb.EmbeddingError, match="!= 768"):
        await emb.embed_query("kip", api_key="k")
