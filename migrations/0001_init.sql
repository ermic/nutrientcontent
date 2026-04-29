-- 0001_init.sql — initial schema for nutrientcontent_db
--
-- Run as the loader role (owner). Roles + database itself are created
-- separately by a postgres-superuser; see specs/01-database.md.
--
-- Idempotent re-run: not designed for that. Use 0002_*.sql for changes.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- nutrients: 142 rows, definition of every nutrient code in NEVO
-- ---------------------------------------------------------------------------
CREATE TABLE nutrients (
    code        TEXT PRIMARY KEY,
    group_nl    TEXT NOT NULL,
    group_en    TEXT NOT NULL,
    name_nl     TEXT NOT NULL,
    name_en     TEXT NOT NULL,
    unit        TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- foods: ~2328 rows, one per NEVO product
-- ---------------------------------------------------------------------------
CREATE TABLE foods (
    nevo_code           INTEGER PRIMARY KEY,
    name_nl             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    food_group_nl       TEXT NOT NULL,
    food_group_en       TEXT NOT NULL,
    synonyms            TEXT,
    quantity            TEXT NOT NULL,
    note                TEXT,
    contains_traces_of  TEXT,
    is_fortified_with   TEXT,
    search_nl TSVECTOR
        GENERATED ALWAYS AS (
            to_tsvector('dutch',
                coalesce(name_nl, '') || ' ' || coalesce(synonyms, ''))
        ) STORED,
    search_en TSVECTOR
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(name_en, ''))
        ) STORED
);

CREATE INDEX foods_search_nl_idx ON foods USING GIN (search_nl);
CREATE INDEX foods_search_en_idx ON foods USING GIN (search_en);
CREATE INDEX foods_name_nl_trgm  ON foods USING GIN (name_nl gin_trgm_ops);
CREATE INDEX foods_name_en_trgm  ON foods USING GIN (name_en gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- food_nutrients: ~270k rows, long form (one row per product/nutrient)
-- ---------------------------------------------------------------------------
CREATE TABLE food_nutrients (
    nevo_code      INTEGER NOT NULL REFERENCES foods(nevo_code)     ON DELETE CASCADE,
    nutrient_code  TEXT    NOT NULL REFERENCES nutrients(code),
    value_per_100  NUMERIC,
    PRIMARY KEY (nevo_code, nutrient_code)
);

CREATE INDEX food_nutrients_nutrient_idx ON food_nutrients(nutrient_code);

-- ---------------------------------------------------------------------------
-- Read-only access for the api role
-- ---------------------------------------------------------------------------
GRANT SELECT ON nutrients, foods, food_nutrients TO nutrientcontent_api;

COMMIT;
