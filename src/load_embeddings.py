"""Backfill `foods.embedding` via Gemini text-embedding-004.

Usage:
    .venv/bin/python -m src.load_embeddings
    .venv/bin/python -m src.load_embeddings --batch-size 64
    .venv/bin/python -m src.load_embeddings --force          # re-embed all
    .venv/bin/python -m src.load_embeddings --limit 50       # smoke-test

Idempotent: skipt rijen waar embedding_version >= TARGET_VERSION.
Commit per batch — Ctrl-C is veilig en hervat-baar.

Run NA migration 0002 (kolom + extension), VÓÓR migration 0003 (HNSW-index).
HNSW op een lege of half-gevulde kolom is verspilling.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from src.shared.embeddings import EmbeddingError, build_text, embed_batch

load_dotenv()

# Bump bij elke wijziging aan het embed-recept (model, build_text-format,
# task_type, outputDimensionality). Rijen met embedding_version < TARGET_VERSION
# worden opnieuw geëmbed door dezelfde loader, geen aparte migratie nodig.
#   1 = gemini-embedding-001 @ 768 dim, RETRIEVAL_DOCUMENT,
#       "name_en | food_group_en | synonyms"
TARGET_VERSION = 1


def fetch_pending(
    conn: psycopg.Connection,
    *,
    target_version: int,
    force: bool,
    limit: int | None,
) -> list[tuple[int, str, str, str | None]]:
    """Rijen die nog (her-)geëmbed moeten worden, gesorteerd op nevo_code."""
    where = "TRUE" if force else "(embedding_version < %s OR embedding IS NULL)"
    sql = f"""
        SELECT nevo_code, name_en, food_group_en, synonyms
        FROM foods
        WHERE {where}
        ORDER BY nevo_code
    """
    params: tuple = () if force else (target_version,)
    if limit is not None:
        sql += " LIMIT %s"
        params = (*params, limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def update_batch(
    conn: psycopg.Connection,
    rows: list[tuple[int, list[float]]],
    *,
    target_version: int,
) -> None:
    """Schrijft (embedding, embedding_version) per rij. Commit niet — caller
    bepaalt transactie-grenzen (zie main: commit per batch)."""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE foods SET embedding = %s, embedding_version = %s WHERE nevo_code = %s",
            [(emb, target_version, code) for code, emb in rows],
        )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Backfill foods.embedding via Gemini.")
    p.add_argument("--batch-size", type=int, default=100, help="texts per Gemini call (max ~100)")
    p.add_argument("--force", action="store_true", help="re-embed alles, negeer embedding_version")
    p.add_argument("--limit", type=int, default=None, help="cap voor smoke-tests")
    p.add_argument("--db-url", default=os.environ.get("NEVO_LOADER_URL"))
    p.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY"))
    args = p.parse_args(argv)

    if not args.db_url:
        raise SystemExit("NEVO_LOADER_URL not set (and --db-url not given)")
    if not args.api_key:
        raise SystemExit("GEMINI_API_KEY not set (and --api-key not given)")
    if args.batch_size < 1 or args.batch_size > 100:
        raise SystemExit("--batch-size must be in [1, 100]")

    t0 = time.perf_counter()
    with psycopg.connect(args.db_url, autocommit=False) as conn:
        register_vector(conn)
        pending = fetch_pending(
            conn,
            target_version=TARGET_VERSION,
            force=args.force,
            limit=args.limit,
        )
        total = len(pending)
        if total == 0:
            print(f"[embed] niks te doen — alles al op versie {TARGET_VERSION}")
            return 0
        print(f"[embed] {total} rijen, batch={args.batch_size}, model=gemini-embedding-001")

        done = 0
        for start in range(0, total, args.batch_size):
            chunk = pending[start : start + args.batch_size]
            texts = [build_text(name_en, fg_en, syn) for _, name_en, fg_en, syn in chunk]
            try:
                vectors = embed_batch(texts, api_key=args.api_key, task_type="RETRIEVAL_DOCUMENT")
            except EmbeddingError as e:
                print(f"[embed] FOUT op batch {start}-{start + len(chunk)}: {e}", file=sys.stderr)
                return 1
            update_batch(
                conn,
                [(code, vec) for (code, *_), vec in zip(chunk, vectors)],
                target_version=TARGET_VERSION,
            )
            conn.commit()
            done += len(chunk)
            print(f"[embed] {done}/{total}")

    dt = time.perf_counter() - t0
    print(f"[embed] klaar in {dt:.1f}s ({total} rijen)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
