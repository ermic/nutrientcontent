"""Schema-asserts voor de pgvector-kolommen.

Detecteert regressies wanneer migration 0002 niet (volledig) is toegepast,
of wanneer iemand de kolommen droopt zonder TARGET_VERSION te bumpen.
Tests draaien tegen de live local DB als de api-rol (read-only).
"""
from __future__ import annotations

import psycopg
import pytest

from src.shared.config import settings
from src.shared.embeddings import EMBED_DIM


@pytest.fixture
async def aconn():
    async with await psycopg.AsyncConnection.connect(settings.nevo_api_url) as conn:
        yield conn


async def test_foods_has_embedding_column(aconn: psycopg.AsyncConnection):
    async with aconn.cursor() as cur:
        await cur.execute(
            """
            SELECT column_name, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'foods' AND column_name = 'embedding'
            """
        )
        row = await cur.fetchone()
    assert row is not None, "foods.embedding ontbreekt — migration 0002 niet toegepast?"
    _, udt_name, is_nullable = row
    assert udt_name == "vector"
    assert is_nullable == "YES"  # nullable: rijen mogen nog niet-geëmbed zijn


async def test_foods_has_embedding_version_column(aconn: psycopg.AsyncConnection):
    async with aconn.cursor() as cur:
        await cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'foods' AND column_name = 'embedding_version'
            """
        )
        row = await cur.fetchone()
    assert row is not None, "foods.embedding_version ontbreekt"
    _, data_type, is_nullable, default = row
    assert data_type == "smallint"
    assert is_nullable == "NO"
    # Default kan zijn: '0', '0::smallint', '(0)::smallint' — afhankelijk van pg-versie.
    assert default is not None and "0" in default


async def test_embedding_column_has_correct_dim(aconn: psycopg.AsyncConnection):
    """vector(768) — checks the typmod via pg_attribute."""
    async with aconn.cursor() as cur:
        await cur.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = 'foods'::regclass AND attname = 'embedding'
            """
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == f"vector({EMBED_DIM})"


async def test_api_role_can_select_embedding(aconn: psycopg.AsyncConnection):
    """Smoke: de read-only api-rol moet vector- en versie-kolom kunnen lezen.
    Faalt als ALTER TABLE per ongeluk grants resette."""
    async with aconn.cursor() as cur:
        await cur.execute("SELECT embedding, embedding_version FROM foods LIMIT 1")
        await cur.fetchone()  # Mag NULL zijn vóór backfill — alleen rechten testen.
