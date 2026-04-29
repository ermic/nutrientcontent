# Spec 01 — Database

## Doel
Definieer het Postgres-schema, de rollen en de indexen. Genoeg detail dat
implementatie 1-op-1 uitvoerbaar is.

## Database & rollen
- DB-naam: `nutrientcontent_db`
- Encoding: UTF-8, locale `en_US.UTF-8` (default voor Postgres 18 op Ubuntu).
- Rollen:
  - `nutrientcontent_loader` — owner van schema en tabellen.
    - Mag DDL (CREATE/ALTER/DROP), `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`.
    - Wachtwoord in `.env` (alleen op server, mode 600).
  - `nutrientcontent_api` — runtime-rol voor de FastAPI-server.
    - Alleen `USAGE` op schema + `SELECT` op alle tabellen.
    - **Geen** rechten op DDL of write.

### Aanmaak (eenmalig, als postgres-superuser)
```sql
CREATE USER nutrientcontent_loader WITH PASSWORD '<sterk-wachtwoord>';
CREATE USER nutrientcontent_api    WITH PASSWORD '<ander-wachtwoord>';

CREATE DATABASE nutrientcontent_db OWNER nutrientcontent_loader
  ENCODING 'UTF8' TEMPLATE template0;

\c nutrientcontent_db

GRANT CONNECT ON DATABASE nutrientcontent_db TO nutrientcontent_api;
GRANT USAGE   ON SCHEMA public TO nutrientcontent_api;

-- na schema-migratie:
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nutrientcontent_api;
ALTER DEFAULT PRIVILEGES FOR ROLE nutrientcontent_loader IN SCHEMA public
  GRANT SELECT ON TABLES TO nutrientcontent_api;
```

## Schema (`migrations/0001_init.sql`)

### Tabel `nutrients`
142 rijen — definitie van elke nutriënt-code.
```sql
CREATE TABLE nutrients (
    code         TEXT PRIMARY KEY,            -- bv. 'ENERCC', 'PROT', 'NA'
    group_nl     TEXT NOT NULL,               -- 'Energie en macronutriënten'
    group_en     TEXT NOT NULL,               -- 'Energy and macronutrients'
    name_nl      TEXT NOT NULL,               -- 'Energie kcal'
    name_en      TEXT NOT NULL,               -- 'Energy kcal'
    unit         TEXT NOT NULL                -- 'kcal', 'g', 'mg', 'µg', 'kJ'
);
```

### Tabel `foods`
~2.328 rijen — één rij per NEVO-product.
```sql
CREATE TABLE foods (
    nevo_code      INTEGER PRIMARY KEY,
    name_nl        TEXT NOT NULL,
    name_en        TEXT NOT NULL,
    food_group_nl  TEXT NOT NULL,
    food_group_en  TEXT NOT NULL,
    synonyms       TEXT,                       -- vrij veld, kan leeg zijn
    quantity       TEXT NOT NULL,              -- bv. 'per 100g'
    note           TEXT,                       -- 'Opmerking' uit xlsx
    contains_traces_of  TEXT,
    is_fortified_with   TEXT,
    -- voorbereid voor full-text search:
    search_nl      TSVECTOR
                   GENERATED ALWAYS AS (
                       to_tsvector('dutch',
                           coalesce(name_nl,'') || ' ' || coalesce(synonyms,''))
                   ) STORED,
    search_en      TSVECTOR
                   GENERATED ALWAYS AS (
                       to_tsvector('english', coalesce(name_en,''))
                   ) STORED
);

CREATE INDEX foods_search_nl_idx ON foods USING GIN (search_nl);
CREATE INDEX foods_search_en_idx ON foods USING GIN (search_en);

-- trigram-index voor fuzzy ILIKE-zoekopdrachten:
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX foods_name_nl_trgm ON foods USING GIN (name_nl gin_trgm_ops);
```

### Tabel `food_nutrients`
~270.000 rijen — long form, één rij per (product, nutriënt).
```sql
CREATE TABLE food_nutrients (
    nevo_code      INTEGER NOT NULL REFERENCES foods(nevo_code) ON DELETE CASCADE,
    nutrient_code  TEXT    NOT NULL REFERENCES nutrients(code),
    value_per_100  NUMERIC,                     -- NULL = onbekend / placeholder
    PRIMARY KEY (nevo_code, nutrient_code)
);

CREATE INDEX food_nutrients_nutrient_idx ON food_nutrients(nutrient_code);
```

> **Waarom NUMERIC, geen DOUBLE PRECISION?** Voedingswaarden in NEVO hebben max 2
> decimalen; NUMERIC houdt de exacte waarden uit het xlsx. Performance-impact bij
> 270k rijen is verwaarloosbaar.

> **Waarom `value_per_100` (geen `_g`)?** De eenheid varieert per nutriënt
> (kcal, kJ, g, mg, µg). De eenheid leeft in `nutrients.unit`. De waarde is altijd
> "per 100 g van het product" zoals NEVO zelf documenteert.

## Kern-nutriënten voor `/calculate`

| API-key (response)   | NEVO-code | Eenheid | Conversie naar response-eenheid |
|---|---|---|---|
| `kcal`               | `ENERCC`  | kcal    | direct |
| `kj`                 | `ENERCJ`  | kJ      | direct |
| `protein_g`          | `PROT`    | g       | direct |
| `fat_g`              | `FAT`     | g       | direct |
| `saturated_fat_g`    | `FASAT`   | g       | direct |
| `carbs_g`            | `CHO`     | g       | direct |
| `sugar_g`            | `SUGAR`   | g       | direct |
| `fiber_g`            | `FIBT`    | g       | direct |
| `salt_g`             | `NA`      | mg      | `salt_g = NA_mg * 2.5 / 1000` |

Zie ook [03-api.md](03-api.md) voor de exacte response-shape.

## Kern-query: `/calculate`

Voor één request `[{nevo_code, grams}, ...]`, in één SQL-statement:

```sql
WITH input(nevo_code, grams) AS (
    VALUES (1::int, 150::numeric), (2::int, 80::numeric)  -- via psycopg copy/values
)
SELECT
    n.code,
    n.unit,
    SUM(fn.value_per_100 * i.grams / 100) AS total
FROM input i
JOIN food_nutrients fn ON fn.nevo_code = i.nevo_code
JOIN nutrients      n  ON n.code       = fn.nutrient_code
WHERE n.code IN ('ENERCC','ENERCJ','PROT','FAT','FASAT','CHO','SUGAR','FIBT','NA')
GROUP BY n.code, n.unit;
```

De Python-laag mapt vervolgens `code → response-key` en past de `salt_g`-conversie
toe op `NA`.

## Verificatie na migratie
```sql
-- moet 142 zijn
SELECT COUNT(*) FROM nutrients;

-- moet ~2328 zijn
SELECT COUNT(*) FROM foods;

-- moet ~270000 zijn
SELECT COUNT(*) FROM food_nutrients;

-- spot-check: aardappel rauw moet ~88 kcal hebben
SELECT f.name_nl, fn.value_per_100, n.unit
FROM food_nutrients fn
JOIN foods f      ON f.nevo_code = fn.nevo_code
JOIN nutrients n  ON n.code      = fn.nutrient_code
WHERE f.nevo_code = 1 AND n.code = 'ENERCC';
-- → ('Aardappelen rauw', 88, 'kcal')
```

## Backups (later)
Voor MVP n.v.t. — data is reproduceerbaar uit het xlsx. Bij productie:
```bash
pg_dump -U nutrientcontent_loader nutrientcontent_db > backup.sql
```
