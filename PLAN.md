# PLAN — nutrientcontent microservice

## Doel
Een lokale microservice die calorietje.nl (`/www/countcalories`) helpt om vanuit
gedetecteerde ingrediënten + geschatte gewichten een **calorie- en
voedingswaarde-totaal** te berekenen, op basis van de officiële RIVM
NEVO-tabel 2025.

## Scope
**In scope (MVP)**
- NEVO 2025 inladen in Postgres (tabel `foods`, `nutrients`, `food_nutrients`).
- 4 HTTP-endpoints: `/health`, `/foods?q=`, `/foods/{code}`, `/calculate`.
- Auth via statische API-key in `X-API-Key` header.
- systemd-service die start bij boot.
- Bind alleen op `127.0.0.1:5555`.

**Out of scope (later)**
- NEVO-recepten (sheet `NEVO2025_Recepten_Recipes`) — ingrediënt-decompositie van
  samengestelde producten.
- Fuzzy matching tussen door AI gedetecteerde ingrediëntnamen en NEVO-namen
  (we beginnen met simpele Postgres-FTS, NL).
- Caching, rate-limiting, metrics.
- Webfrontend / admin UI.

## Architectuur (high level)
```
   ┌────────────────────────┐
   │  calorietje.nl         │
   │  (/www/countcalories)  │
   └──────────┬─────────────┘
              │  HTTP localhost:5555
              │  X-API-Key
              ▼
   ┌────────────────────────┐    ┌──────────────────┐
   │  FastAPI / uvicorn     │    │  PostgreSQL 18    │
   │  (rol: ..._api)        │───▶│  nutrientcontent_ │
   │  127.0.0.1:5555        │    │  db               │
   └──────────┬─────────────┘    └──────────────────┘
              │ start ▲
              │       │
   ┌──────────▼───────┴─────┐
   │  systemd unit          │
   │  nutrientcontent.service│
   └────────────────────────┘

   ┌────────────────────────┐
   │  load_nevo.py (CLI)    │   leest NEVO2025_v9.0.xlsx,
   │  (rol: ..._loader)     │   vult foods/nutrients/food_nutrients
   └────────────────────────┘
```

## Fases / milestones

### Fase 1 — Database (specs/01-database.md)
- [ ] DB + 2 rollen aanmaken
- [ ] Schema-migratie `migrations/0001_init.sql` toepassen
- [ ] FTS-indexen (NL + EN) draaien

**Klaar wanneer**: `\dt` in psql toont 3 tabellen, beide rollen werken met juiste rechten.

### Fase 2 — Loader (specs/02-loader.md)
- [ ] `src/load_nevo.py` — leest 5 sheets, vult tabellen idempotent
- [ ] Eenmalig draaien op de server: ~270k food_nutrients-rijen

**Klaar wanneer**: `SELECT COUNT(*) FROM foods` ≈ 2.328, `nutrients` = 142,
`food_nutrients` ≈ 270.000.

### Fase 3 — API (specs/03-api.md)
- [ ] `src/app.py` + handlers + pydantic-modellen
- [ ] Auth-dependency (`secrets.compare_digest`)
- [ ] `/health`, `/foods?q=`, `/foods/{code}`, `/calculate`
- [ ] Een paar pytest-integratietests

**Klaar wanneer**: handmatige `curl` tegen alle 4 endpoints werkt, en
`pytest` is groen.

### Fase 4 — Deployment (specs/04-deployment.md)
- [ ] systemd unit installeren, enable + start
- [ ] Auto-restart bij crash
- [ ] Logs in journald

**Klaar wanneer**: na `sudo systemctl reboot` is de service vanzelf bereikbaar
op `http://127.0.0.1:5555/health`.

### Fase 5 — Documentatie & overdracht
- [ ] Voorbeeld-`curl` aanroepen in `README.md`
- [ ] Een korte aantekening in `/www/countcalories` (later) hoe je deze service gebruikt

## Belangrijke beslissingen

| Beslissing | Keuze | Reden |
|---|---|---|
| Webframework | FastAPI | Auto-docs, type-validatie, kleine codebasis voor beginner |
| DB-driver | psycopg 3 zonder ORM | SQL leesbaar, geen ORM-magic; bij 3 tabellen overhead niet nodig |
| Schema-vorm | Long table (`food_nutrients`) | Flexibel: 142 nutriënten in een wide table is onhandig en migreert slecht |
| xlsx-bron | `NEVO2025_Details` sheet (al long form) | Scheelt transformatie; identiek aan ons schema |
| Auth | Statische API-key + constant-time compare | Eenvoudig, voldoende voor pure-localhost |
| Bind | `127.0.0.1` | Geen extern verkeer; firewall niet nodig |
| Rollen | 2 (loader + api) | Server kan alleen lezen → schade beperkt bij compromise |
| Migrations-tool | Geen (rauwe SQL via `psql -f`) | 1 of 2 migraties per jaar; overhead niet waard |

## Risico's & mitigaties
- **NEVO licentie**: xlsx in publieke git-repo? Niet doen tot RIVM-licentie gecheckt.
  Mitigatie: `.gitignore` + bron in `README` linken.
- **Karakter-encoding**: NEVO heeft µ, ë, é. Mitigatie: alle DB-cols `TEXT` met UTF-8
  (Postgres-default), Python `open(..., encoding='utf-8')`.
- **Numerieke waarden in xlsx**: bevatten soms placeholders (`-`, `Sp.`, lege cel).
  Mitigatie: loader behandelt non-numerieke waarden als `NULL` en logt counts.
- **API-key lekken**: Mitigatie: `.env` mode 600, niet in git, separate van loader-creds.

## Volgorde van implementatie
1. specs lezen en akkoord met Erik.
2. Postgres opzetten (DB, rollen, schema).
3. Loader bouwen + draaien.
4. API bouwen + testen.
5. systemd unit + reboot-test.
6. Voorbeeld-aanroepen documenteren.

## Open vragen aan Erik
- Geen meer voor MVP — alle eerdere vragen zijn beantwoord (Python + Postgres,
  port 5555, multilingual, kern-nutriënten lijst).
- **Later**: gewenste fuzzy-matching strategie voor AI-gedetecteerde namen
  (Postgres `pg_trgm`? embeddings?). Pas relevant zodra calorietje.nl draait.
