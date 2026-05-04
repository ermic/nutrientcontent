"""Gemini gemini-embedding-001 client.

Used by the offline loader (`src.load_embeddings`) and by the runtime vector
search endpoint to embed query strings. Single source of truth for:
- which model we call
- which API endpoint
- which task_type we use (RETRIEVAL_DOCUMENT for ingest, RETRIEVAL_QUERY at runtime)
- the embedding dimension (768)

Asymmetric task_types matter: retrieval-document is optimised for the side
that gets indexed (the NEVO row), retrieval-query for the user-side text.
Mixing them costs ~2-5% recall at no benefit.

`gemini-embedding-001` defaults to 3072-dim; we vragen expliciet 768 om
de pgvector-kolom (`vector(768)`) te matchen. 768 is meer dan voldoende
voor onze ~2.3k NEVO-rijen en houdt de HNSW-index klein.
"""
from __future__ import annotations

from typing import Literal

import httpx

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768
EMBED_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


class EmbeddingError(RuntimeError):
    pass


def build_text(name_en: str, food_group_en: str, synonyms: str | None) -> str:
    """Compose the document-side text we embed for one NEVO row.

    Format: "<name_en> | <food_group_en> | <synonyms>". Synoniemen zijn in
    NEVO Nederlands; gemini-embedding-001 is meertalig, dus dat is geen
    probleem en helpt voor inputs die toevallig Nederlands zijn. Bumps to
    this format require a TARGET_VERSION bump in src/load_embeddings.py.
    """
    parts = [name_en.strip(), food_group_en.strip()]
    if synonyms:
        s = synonyms.strip()
        if s:
            parts.append(s)
    return " | ".join(p for p in parts if p)


def _request_body(texts: list[str], task_type: TaskType) -> dict:
    return {
        "requests": [
            {
                "model": f"models/{EMBED_MODEL}",
                "content": {"parts": [{"text": t}]},
                "taskType": task_type,
                "outputDimensionality": EMBED_DIM,
            }
            for t in texts
        ]
    }


def embed_batch(
    texts: list[str],
    *,
    api_key: str,
    task_type: TaskType,
    timeout_s: float = 30.0,
) -> list[list[float]]:
    """Synchronous batched embed. For loader use. Up to ~100 texts per call.

    Raises EmbeddingError on non-2xx or shape mismatch — caller decides retry.
    """
    if not texts:
        return []
    url = f"{EMBED_BASE_URL}/models/{EMBED_MODEL}:batchEmbedContents"
    resp = httpx.post(
        url,
        params={"key": api_key},
        json=_request_body(texts, task_type),
        timeout=timeout_s,
    )
    if resp.status_code != 200:
        raise EmbeddingError(
            f"Gemini embed HTTP {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise EmbeddingError(
            f"Gemini embed: expected {len(texts)} embeddings, got "
            f"{len(embeddings) if isinstance(embeddings, list) else type(embeddings)}"
        )
    out: list[list[float]] = []
    for i, e in enumerate(embeddings):
        values = e.get("values") if isinstance(e, dict) else None
        if not isinstance(values, list) or len(values) != EMBED_DIM:
            raise EmbeddingError(
                f"Gemini embed: bad shape at index {i}: "
                f"{len(values) if isinstance(values, list) else 'n/a'} != {EMBED_DIM}"
            )
        out.append(values)
    return out


async def embed_query(text: str, *, api_key: str, timeout_s: float = 10.0) -> list[float]:
    """Async single-query embed. For runtime use in the search endpoint."""
    url = f"{EMBED_BASE_URL}/models/{EMBED_MODEL}:embedContent"
    body = {
        "model": f"models/{EMBED_MODEL}",
        "content": {"parts": [{"text": text}]},
        "taskType": "RETRIEVAL_QUERY",
        "outputDimensionality": EMBED_DIM,
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, params={"key": api_key}, json=body)
    if resp.status_code != 200:
        raise EmbeddingError(
            f"Gemini embed HTTP {resp.status_code}: {resp.text[:300]}"
        )
    values = resp.json().get("embedding", {}).get("values")
    if not isinstance(values, list) or len(values) != EMBED_DIM:
        raise EmbeddingError(
            f"Gemini embed: bad shape: "
            f"{len(values) if isinstance(values, list) else 'n/a'} != {EMBED_DIM}"
        )
    return values
