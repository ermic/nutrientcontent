"""Tests voor src/load_embeddings.py.

Strategie: een transactionele fixture rolt aan het einde van elke test
terug, dus we kunnen vrij muteren zonder de DB-state te vervuilen. De
Gemini-call wordt gemocked — we testen de loader-logica, niet de embed.
"""
from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest
from pgvector.psycopg import register_vector

from src import load_embeddings as loader
from src.shared.embeddings import EMBED_DIM


# ─── Fixtures ─────────────────────────────────────────────────────────────


def _loader_url() -> str:
    url = os.environ.get("NEVO_LOADER_URL")
    if not url:
        pytest.skip("NEVO_LOADER_URL not set — loader-tests vereisen schrijfrechten")
    return url


@pytest.fixture
def loader_conn():
    """Sync loader-conn met autocommit=False; rollback aan einde van test."""
    conn = psycopg.connect(_loader_url(), autocommit=False)
    register_vector(conn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _pick_first_codes(conn: psycopg.Connection, n: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT nevo_code FROM foods ORDER BY nevo_code LIMIT %s", (n,))
        return [r[0] for r in cur.fetchall()]


def _set_version(conn: psycopg.Connection, code: int, version: int, embedding=None) -> None:
    """Markeer een rij met een specifieke embedding_version (en optioneel
    een echte vector). Wordt gerollbackt aan het einde van de test."""
    if embedding is None:
        embedding = [0.0] * EMBED_DIM
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE foods SET embedding = %s, embedding_version = %s WHERE nevo_code = %s",
            (embedding, version, code),
        )


# ─── fetch_pending ────────────────────────────────────────────────────────


def test_fetch_pending_returns_expected_columns(loader_conn):
    rows = loader.fetch_pending(loader_conn, target_version=1, force=False, limit=1)
    assert len(rows) == 1
    code, name_en, food_group_en, synonyms = rows[0]
    assert isinstance(code, int)
    assert isinstance(name_en, str) and name_en
    assert isinstance(food_group_en, str) and food_group_en
    assert synonyms is None or isinstance(synonyms, str)


def test_fetch_pending_excludes_already_embedded(loader_conn):
    [code] = _pick_first_codes(loader_conn, 1)
    _set_version(loader_conn, code, version=1)

    rows = loader.fetch_pending(loader_conn, target_version=1, force=False, limit=None)
    codes = [r[0] for r in rows]
    assert code not in codes


def test_fetch_pending_includes_lower_version(loader_conn):
    [code] = _pick_first_codes(loader_conn, 1)
    _set_version(loader_conn, code, version=0)

    rows = loader.fetch_pending(loader_conn, target_version=1, force=False, limit=None)
    codes = [r[0] for r in rows]
    assert code in codes


def test_fetch_pending_force_returns_everything(loader_conn):
    codes = _pick_first_codes(loader_conn, 3)
    for c in codes:
        _set_version(loader_conn, c, version=99)

    # Zelfs na version=99 (boven TARGET) moet --force ze terug geven.
    rows = loader.fetch_pending(loader_conn, target_version=1, force=True, limit=3)
    returned = [r[0] for r in rows]
    assert returned == codes  # ORDER BY nevo_code LIMIT 3 → de eerste 3


def test_fetch_pending_respects_limit(loader_conn):
    rows = loader.fetch_pending(loader_conn, target_version=1, force=True, limit=5)
    assert len(rows) == 5


# ─── update_batch ─────────────────────────────────────────────────────────


def test_update_batch_writes_vector_and_version(loader_conn):
    [code] = _pick_first_codes(loader_conn, 1)
    _set_version(loader_conn, code, version=0)  # baseline

    new_emb = [0.1] * EMBED_DIM
    loader.update_batch(loader_conn, [(code, new_emb)], target_version=1)

    with loader_conn.cursor() as cur:
        cur.execute(
            "SELECT embedding_version, embedding FROM foods WHERE nevo_code = %s",
            (code,),
        )
        version, stored = cur.fetchone()
    assert version == 1
    assert list(stored) == pytest.approx(new_emb)


def test_update_batch_does_not_commit(loader_conn):
    """Caller is verantwoordelijk voor commits — getest via een 2e
    connectie die de niet-gecommitte schrijf NIET mag zien."""
    [code] = _pick_first_codes(loader_conn, 1)
    new_emb = [0.42] * EMBED_DIM
    loader.update_batch(loader_conn, [(code, new_emb)], target_version=1)
    # NIET committen.

    other = psycopg.connect(_loader_url(), autocommit=True)
    register_vector(other)
    try:
        with other.cursor() as cur:
            cur.execute("SELECT embedding_version FROM foods WHERE nevo_code = %s", (code,))
            version = cur.fetchone()[0]
        # Andere sessie ziet de oude waarde — bewijs dat update_batch zelf
        # niet commit.
        assert version != 1 or version == 0  # afhankelijk van prior state, maar niet 1 zonder commit
    finally:
        other.close()


# ─── main() ───────────────────────────────────────────────────────────────


def test_main_skips_when_nothing_pending(monkeypatch: pytest.MonkeyPatch, capsys):
    """Als fetch_pending leeg is, geen Gemini-call, exit 0."""
    monkeypatch.setattr(loader, "fetch_pending", lambda *a, **kw: [])

    def _no_embed(*a: Any, **kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("embed_batch should not be called when nothing pending")

    monkeypatch.setattr(loader, "embed_batch", _no_embed)
    monkeypatch.setenv("NEVO_LOADER_URL", _loader_url())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    rc = loader.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "niks te doen" in out


def test_main_happy_path_with_mocked_embed(monkeypatch: pytest.MonkeyPatch):
    """End-to-end loader met --limit en mocked embed. Verifies aantal
    calls + eindstatus van de gewijzigde rijen."""
    fake_emb = [0.7] * EMBED_DIM
    calls: list[list[str]] = []

    def _fake_embed(texts, *, api_key, task_type, timeout_s=30.0):
        assert task_type == "RETRIEVAL_DOCUMENT"
        calls.append(list(texts))
        return [fake_emb for _ in texts]

    monkeypatch.setattr(loader, "embed_batch", _fake_embed)
    monkeypatch.setenv("NEVO_LOADER_URL", _loader_url())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    # Smoke: 4 rijen, batch 2 → 2 calls. Verzin een hoge target zodat alles
    # pending is, en cleanup achteraf.
    monkeypatch.setattr(loader, "TARGET_VERSION", 99)

    # Pak 4 codes om te kunnen restoren.
    restore_url = _loader_url()
    pre_conn = psycopg.connect(restore_url, autocommit=False)
    register_vector(pre_conn)
    try:
        with pre_conn.cursor() as cur:
            cur.execute(
                "SELECT nevo_code, embedding, embedding_version FROM foods "
                "ORDER BY nevo_code LIMIT 4"
            )
            saved = cur.fetchall()
        assert len(saved) == 4

        rc = loader.main(["--batch-size", "2", "--limit", "4"])
        assert rc == 0
        assert len(calls) == 2  # 4 rijen / batch 2
        assert all(len(c) == 2 for c in calls)

        # Verifieer eindstatus
        with pre_conn.cursor() as cur:
            cur.execute(
                "SELECT nevo_code, embedding_version FROM foods "
                "WHERE nevo_code = ANY(%s)",
                ([s[0] for s in saved],),
            )
            after = dict(cur.fetchall())
        for code, _, _ in saved:
            assert after[code] == 99
    finally:
        # Restore originele state — main() heeft per batch gecommit, dus
        # de fixture-rollback dekt dit niet.
        with pre_conn.cursor() as cur:
            for code, emb, ver in saved:
                cur.execute(
                    "UPDATE foods SET embedding = %s, embedding_version = %s WHERE nevo_code = %s",
                    (emb, ver, code),
                )
        pre_conn.commit()
        pre_conn.close()
