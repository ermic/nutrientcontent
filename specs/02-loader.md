# Spec 02 — Loader (`src/load_nevo.py`)

## Doel
Eenmalig (en bij elke NEVO-update) het xlsx-bestand omzetten naar rijen in
Postgres. Idempotent: opnieuw draaien moet veilig zijn en hetzelfde
eindresultaat opleveren.

## Aanroep
```bash
.venv/bin/python -m src.load_nevo NEVO2025_v9.0.xlsx
# optioneel:
.venv/bin/python -m src.load_nevo NEVO2025_v9.0.xlsx --truncate
```

## Bron-sheets

| Sheet | Rijen | Gebruik |
|---|---|---|
| `NEVO2025_Nutrienten_Nutrients` | 142 | → `nutrients` |
| `NEVO2025` (wide) | 2.328 | → `foods` (alleen metadata-kolommen) |
| `NEVO2025_Details` (long) | 270.811 | → `food_nutrients` |
| `NEVO2025_Recepten_Recipes` | 3.689 | **niet** voor MVP — out of scope |
| `NEVO2025_Referenties_References` | 2.197 | **niet** voor MVP |

## Strategie
1. Open xlsx via `openpyxl.load_workbook(path, read_only=True, data_only=True)`.
2. Open Postgres-connectie met **loader-rol**.
3. Begin een transactie. Bij fout: rollback, niets gewijzigd.
4. `TRUNCATE food_nutrients, foods, nutrients RESTART IDENTITY CASCADE` — schone start.
5. Vul `nutrients` (142 rijen) — gewoon `executemany`.
6. Vul `foods` (~2.328 rijen) — gewoon `executemany` of `COPY`.
7. Vul `food_nutrients` (~270.000 rijen) — **gebruik `COPY FROM STDIN`** voor snelheid
   (psycopg 3 heeft `cursor.copy(...)`).
8. Commit.
9. Print samenvatting: counts per tabel + duur.

## Kolom-mapping

### `nutrients`
Bron-sheet: `NEVO2025_Nutrienten_Nutrients`, header op rij 0.

| xlsx-kolom | DB-kolom |
|---|---|
| Voedingsstofgroep | group_nl |
| Component group   | group_en |
| Nutrient-code     | code |
| Voedingsstof      | name_nl (`.strip()`) |
| Component         | name_en (`.strip()`) |
| Eenheid/Unit      | unit |

### `foods`
Bron-sheet: `NEVO2025` (wide). Alleen de eerste 11 metadata-kolommen gebruiken;
de 137 nutriënt-kolommen daarna negeren (die komen uit `_Details`).

| xlsx-kolom | DB-kolom | Notitie |
|---|---|---|
| Voedingsmiddelgroep | food_group_nl | |
| Food group | food_group_en | |
| NEVO-code | nevo_code | cast naar `int` |
| Voedingsmiddelnaam/Dutch food name | name_nl | |
| Engelse naam/Food name | name_en | |
| Synoniem | synonyms | NULL als leeg |
| Hoeveelheid/Quantity | quantity | meestal `'per 100g'` |
| Opmerking | note | NULL als leeg |
| Bevat sporen van/Contains traces of | contains_traces_of | NULL als leeg |
| Is verrijkt met/Is fortified with | is_fortified_with | NULL als leeg |

(Kolom `NEVO-versie/NEVO-version` overslaan — staat al in commit-log/README.)

### `food_nutrients`
Bron-sheet: `NEVO2025_Details` (long). Header op rij 0.

| xlsx-kolom | DB-kolom |
|---|---|
| NEVO-code | nevo_code (cast int) |
| Nutrient-code | nutrient_code |
| Gehalte/Value | value_per_100 (cast → numeric of NULL — zie hieronder) |

Andere kolommen in `_Details` (groep, naam, eenheid) zijn redundant met
`nutrients` en worden genegeerd.

## Numerieke waarden

NEVO gebruikt soms placeholders i.p.v. getallen. Behandel zo:

| Cel-inhoud | DB-waarde |
|---|---|
| `123`, `1.5`, `0`, getal | `NUMERIC(123)` |
| `''`, `None`, lege cel | `NULL` |
| `'-'`, `'Sp.'`, `'tr'`, andere strings | `NULL` + tellen voor logging |

Pseudocode:
```python
def parse_value(raw):
    if raw is None or raw == '':
        return None
    if isinstance(raw, (int, float)):
        return raw
    s = str(raw).strip().replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None  # Sp., -, tr, etc.
```

Aan het eind logt de loader: `food_nutrients: 270811 rijen, waarvan 12345 NULL`.

## Idempotentie & herhaalbaarheid
- `TRUNCATE ... CASCADE` aan het begin — geen "oude" rijen die blijven hangen.
- Hele loader in één transactie — bij crash na 50% blijft DB consistent (oude staat).
- Bij re-run met **identiek** xlsx: zelfde row-counts, zelfde sums.

## Performance-target
Op deze server (Postgres lokaal, SSD): hele load < **30 seconden**.
- 142 nutrients: <100 ms
- 2.328 foods: <500 ms
- 270.000 food_nutrients via `COPY`: ~5–10 s
- xlsx-parsing zelf: ~10–20 s (openpyxl is niet snel, maar acceptabel voor
  een one-shot job).

## Foutafhandeling
- Onbekende `nutrient_code` in `_Details` (komt niet voor in `_Nutrients`):
  log waarschuwing, sla rij over (FK zou anders falen).
- Onbekende `nevo_code` in `_Details` (komt niet voor in `NEVO2025`):
  zelfde — log + skip.
- Verbindingsfout naar Postgres: faal direct met duidelijke melding +
  exit-code 1.

## Logging (stdout, niet structured)
```
[loader] reading xlsx: NEVO2025_v9.0.xlsx (21.6 MB)
[loader] truncating tables…
[loader] inserting nutrients: 142 rows
[loader] inserting foods: 2328 rows
[loader] copying food_nutrients: 270811 rows (12345 NULL)
[loader] commit OK in 17.4s
```

## CLI-flags
| Flag | Default | Doel |
|---|---|---|
| `--truncate / --no-truncate` | `--truncate` | wel/niet `TRUNCATE` vooraf |
| `--dry-run` | off | parsen zonder schrijven |
| `--db-url` | `$NEVO_LOADER_URL` | overschrijven |

## Voorbeeld-skeleton
```python
"""Loader: NEVO xlsx → Postgres. / Loader: NEVO xlsx into Postgres."""
import argparse
import os
import sys
import time
from pathlib import Path

import psycopg
from openpyxl import load_workbook
from dotenv import load_dotenv

load_dotenv()

def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--truncate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-url", default=os.environ["NEVO_LOADER_URL"])
    args = parser.parse_args(argv)

    wb = load_workbook(args.xlsx, read_only=True, data_only=True)
    nutrients = parse_nutrients(wb["NEVO2025_Nutrienten_Nutrients"])
    foods     = parse_foods(wb["NEVO2025"])
    details   = parse_details(wb["NEVO2025_Details"])

    if args.dry_run:
        print("dry-run: counts =", len(nutrients), len(foods), len(details))
        return 0

    with psycopg.connect(args.db_url) as conn, conn.cursor() as cur:
        if args.truncate:
            cur.execute("TRUNCATE food_nutrients, foods, nutrients RESTART IDENTITY CASCADE")
        cur.executemany("INSERT INTO nutrients (...) VALUES (%s,%s,%s,%s,%s,%s)", nutrients)
        cur.executemany("INSERT INTO foods (...) VALUES (...)", foods)
        with cur.copy("COPY food_nutrients (nevo_code, nutrient_code, value_per_100) FROM STDIN") as cp:
            for row in details:
                cp.write_row(row)
        conn.commit()

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```
