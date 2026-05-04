-- 0003_pgvector_index.sql — HNSW index op foods.embedding.
--
-- LET OP: pas runnen NA `python -m src.load_embeddings` voor minstens één
-- volledige run. HNSW bouwen op een bijna-lege kolom is verspilling, en
-- bovendien wordt elke INSERT/UPDATE op rijen-zonder-embedding zwaarder.
--
-- Cosine-distance (`<=>`) is wat we runtime gebruiken — match je dus
-- met `vector_cosine_ops`. m=16, ef_construction=64 zijn pgvector-defaults
-- en prima voor ~2.3k rijen.

BEGIN;

CREATE INDEX foods_embedding_idx
    ON foods
    USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

COMMIT;
