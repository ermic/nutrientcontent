"""GET /foods/vector?q=<term>&limit=<n>&min_similarity=<f>

Semantische NEVO-search op basis van Gemini-embeddings. Bedoeld als
fallback voor de FTS-route in `/foods` (zie countcalories `match/route.ts`):
de caller probeert eerst FTS, en valt hierop terug bij synoniemen of
vrije omschrijvingen die FTS mist.

Latency: één Gemini-call (~150ms) plus pgvector HNSW-lookup (<5ms).
Wordt dus alleen aangeroepen wanneer de FTS-cascade niets accepteert.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from src.entities.food import repo as food_repo
from src.entities.food.models import FoodVectorHit
from src.shared.auth import require_api_key
from src.shared.config import settings
from src.shared.db import PoolDep
from src.shared.embeddings import EmbeddingError, embed_query

router = APIRouter()


@router.get("/foods/vector", dependencies=[Depends(require_api_key)])
async def search_foods_vector(
    pool: PoolDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    min_similarity: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
) -> dict:
    try:
        vector = await embed_query(q, api_key=settings.gemini_api_key)
    except EmbeddingError as e:
        # Gemini-uitval = retryable upstream-fail. 503 zodat de Next-app
        # er een gebruikersvriendelijke melding van kan maken.
        raise HTTPException(status_code=503, detail=f"embed unavailable: {e}") from e

    results: list[FoodVectorHit] = await food_repo.search_by_vector(
        pool, vector, limit=limit, min_similarity=min_similarity
    )
    return {"query": q, "results": results}
