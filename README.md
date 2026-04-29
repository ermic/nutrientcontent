# nutrientcontent

Lokale microservice die de RIVM **NEVO 2025** voedingswaardetabel ontsluit voor
[calorietje.nl](https://calorietje.nl). Local microservice exposing the Dutch
NEVO 2025 nutrient database to calorietje.nl.

- HTTP server bindt op `127.0.0.1:5555` — niet bereikbaar van buiten.
- Auth via `X-API-Key` header.
- Postgres als opslag; geen externe afhankelijkheden.

## Stack
Python 3.12 · FastAPI · uvicorn · psycopg 3 · openpyxl · PostgreSQL 18

## Layout (Feature-Sliced Design)
```
src/
├── app.py                  composition root: lifespan, exception handlers, routers
├── shared/                 cross-cutting: config, db pool, auth
├── entities/food/          domain entity: pydantic models + repo
├── features/               vertical slices, één per use-case
│   ├── health/             GET  /health
│   ├── search_foods/       GET  /foods?q=
│   ├── get_food/           GET  /foods/{nevo_code}
│   └── calculate/          POST /calculate
└── load_nevo.py            xlsx → Postgres CLI loader
```
Dependency-richting: `app → features → entities → shared`. Nooit andersom.

## Endpoints

| Methode | Pad                  | Auth | Doel                                |
|---------|----------------------|------|-------------------------------------|
| GET     | `/health`            | nee  | liveness + DB-probe                  |
| GET     | `/foods?q=<term>`    | ja   | zoeken (FTS NL/EN, trigram-fallback) |
| GET     | `/foods/{nevo_code}` | ja   | full nutrient list voor 1 product    |
| POST    | `/calculate`         | ja   | totalen voor lijst van items         |

Auto-docs op:
- Swagger UI: `http://127.0.0.1:5555/docs`
- ReDoc: `http://127.0.0.1:5555/redoc`
- OpenAPI JSON: `http://127.0.0.1:5555/openapi.json`

## Setup (vanaf scratch)

### 1. Postgres-rollen + database
```bash
sudo -u postgres psql <<SQL
CREATE USER nutrientcontent_loader WITH PASSWORD '...';
CREATE USER nutrientcontent_api    WITH PASSWORD '...';
CREATE DATABASE nutrientcontent_db OWNER nutrientcontent_loader
    ENCODING 'UTF8' TEMPLATE template0;
\c nutrientcontent_db
GRANT CONNECT ON DATABASE nutrientcontent_db TO nutrientcontent_api;
GRANT USAGE   ON SCHEMA public TO nutrientcontent_api;
ALTER DEFAULT PRIVILEGES FOR ROLE nutrientcontent_loader IN SCHEMA public
  GRANT SELECT ON TABLES TO nutrientcontent_api;
SQL
```

### 2. `.env`
```bash
cp .env.example .env
chmod 600 .env
# vul wachtwoorden in en genereer een API-key:
openssl rand -hex 32
```

### 3. Python venv + dependencies
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 4. Schema
```bash
set -a; source .env; set +a
psql "$NEVO_LOADER_URL" -f migrations/0001_init.sql
```

### 5. Data laden
```bash
.venv/bin/python -m src.load_nevo NEVO2025_v9.0.xlsx
# einde: nutrients=137, foods=2328, food_nutrients=260400 (~32s)
```

### 6. systemd
De repo-unit is generiek; machine-specifieke paden + `User=` staan in een
drop-in op `/etc/systemd/system/nutrientcontent.service.d/local.conf`.

```bash
# 6a. base unit symlinken
sudo ln -sf /home/erik/microservices/nutrientcontent/systemd/nutrientcontent.service \
            /etc/systemd/system/nutrientcontent.service

# 6b. drop-in aanmaken vanaf voorbeeld; paden aanpassen indien nodig
sudo mkdir -p /etc/systemd/system/nutrientcontent.service.d
sudo cp /home/erik/microservices/nutrientcontent/systemd/local.conf.example \
        /etc/systemd/system/nutrientcontent.service.d/local.conf
sudoedit /etc/systemd/system/nutrientcontent.service.d/local.conf

# 6c. starten
sudo systemctl daemon-reload
sudo systemctl enable --now nutrientcontent
```

## Voorbeelden

```bash
API_KEY=$(grep ^API_KEY .env | cut -d= -f2)
H="X-API-Key: $API_KEY"

# 1. health (geen auth)
curl -s http://127.0.0.1:5555/health
# {"status":"ok","db":"ok","version":"0.1.0"}

# 2. zoeken (NL)
curl -s -H "$H" "http://127.0.0.1:5555/foods?q=appel&limit=3"

# 3. zoeken (EN)
curl -s -H "$H" "http://127.0.0.1:5555/foods?q=apple&lang=en&limit=3"

# 4. detail
curl -s -H "$H" http://127.0.0.1:5555/foods/1
# Aardappelen rauw, met alle 124 nutriëntwaarden

# 5. calculate (het belangrijkste endpoint)
curl -s -H "$H" -H "Content-Type: application/json" \
  -d '{"items":[{"nevo_code":1,"grams":150},{"nevo_code":147,"grams":120}]}' \
  http://127.0.0.1:5555/calculate
# → {"totals":{"kcal":...,"kj":...,...},"items":[{...},{...}]}
```

## Aanroep vanuit calorietje.nl
calorietje.nl draait op dezelfde server (`/www/countcalories`). De
`X-API-Key`-waarde staat in **die** project-config (b.v. `/www/countcalories/.env`)
met dezelfde waarde als hier.

```
POST http://127.0.0.1:5555/calculate
Headers:
  X-API-Key: <zelfde key>
  Content-Type: application/json
Body:
  {"items":[{"nevo_code":1,"grams":150},{"nevo_code":234,"grams":80}]}
```

## Updates uitrollen

**Code:**
```bash
cd ~/microservices/nutrientcontent
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart nutrientcontent
journalctl -u nutrientcontent -n 50 --no-pager
```

**Data (nieuwe NEVO-versie):**
```bash
.venv/bin/python -m src.load_nevo NEVO2026_v1.0.xlsx
# server hoeft niet te restarten — leest live van DB
```

## Tests
```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
# 12 passed in ~0.3s
```

Tests draaien tegen de **echte** lokale Postgres (geen mocks). Vereist dat
`load_nevo` is gedraaid.

## Logs
```bash
journalctl -u nutrientcontent -f             # live
journalctl -u nutrientcontent --since today  # vandaag
```

`LOG_LEVEL` kan `debug | info | warning | error` zijn (default `info`),
in `.env` aanpassen + `systemctl restart`.

## Licentie & data
De NEVO-tabel valt onder RIVM-licentie. Het xlsx-bestand zit
**niet** in deze repo (`.gitignore`). Bron: <https://www.rivm.nl/en/nevo>.
