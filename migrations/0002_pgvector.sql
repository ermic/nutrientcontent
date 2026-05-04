-- 0002_pgvector.sql — add pgvector column for semantic NEVO match-fallback.
--
-- Achtergrond: Gemini-foto-pipeline matched ingrediënten nu via tsvector +
-- pg_trgm (zie repo `foods.search` / `nutrientcontent_api`). Bij synoniemen
-- en losse omschrijvingen mist FTS soms; vector-search met embeddings van
-- name_en/food_group_en lost dat op zonder de FTS-happy-path te raken.
--
-- Embedding wordt gevuld door `python -m src.load_embeddings` (gebruikt
-- Gemini gemini-embedding-001 met outputDimensionality=768). De HNSW-index
-- zit in 0003_*.sql en wordt pas ná de backfill aangelegd (lege HNSW heeft
-- geen waarde en slows down updates).
--
-- Run als loader-rol.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE foods
    ADD COLUMN embedding vector(768);

-- Versie-tag zodat we later het embed-recept (model, prompt-template) kunnen
-- wijzigen en gericht kunnen re-embedden zonder alles te flushen.
--   0 = niet gevuld
--   1 = gemini-embedding-001 @ 768 dim, "name_en | food_group_en | synonyms"
ALTER TABLE foods
    ADD COLUMN embedding_version SMALLINT NOT NULL DEFAULT 0;

COMMIT;
