# Spec 04 — Deployment

## Doel
Hoe de service op deze server (Ubuntu 24.04) draait als systemd-unit,
welke files waar staan, en welke environment-variables gebruikt worden.

## Vereisten op de server
- Ubuntu 24.04 ✓
- PostgreSQL 18 ✓ (via PGDG)
- Python 3.12 ✓
- `python3.12-venv` ✓ (apt)

## Environment-variabelen (`.env`)

`.env` ligt in `~/microservices/nutrientcontent/.env`, **mode 600**, **niet** in git.

```bash
# .env
APP_VERSION=0.1.0

# Server-rol — alleen SELECT
NEVO_API_URL=postgresql://nutrientcontent_api:<api-pw>@127.0.0.1:5432/nutrientcontent_db

# Loader-rol — DDL + writes (alleen lokaal gebruiken, niet in server-process)
NEVO_LOADER_URL=postgresql://nutrientcontent_loader:<loader-pw>@127.0.0.1:5432/nutrientcontent_db

# 32 random bytes hex
API_KEY=<32 hex bytes — zie hieronder>

# logging
LOG_LEVEL=info
```

API-key genereren:
```bash
openssl rand -hex 32
```

`.env.example` (in git) heeft dezelfde structuur met placeholder-waarden.

## File-permissies
```bash
chmod 600 ~/microservices/nutrientcontent/.env
chown erik:erik ~/microservices/nutrientcontent/.env
```

## systemd-unit

`/etc/systemd/system/nutrientcontent.service`:

```ini
[Unit]
Description=nutrientcontent (NEVO microservice)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=exec
User=erik
Group=erik
WorkingDirectory=/home/erik/microservices/nutrientcontent
EnvironmentFile=/home/erik/microservices/nutrientcontent/.env
ExecStart=/home/erik/microservices/nutrientcontent/.venv/bin/uvicorn \
    src.app:app \
    --host 127.0.0.1 \
    --port 5555 \
    --no-access-log \
    --log-level info
Restart=on-failure
RestartSec=3
# we loggen naar journald (default voor stdout/stderr)
StandardOutput=journal
StandardError=journal

# kleine hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/erik/microservices/nutrientcontent
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

> **Let op**: `ProtectSystem=strict` + `ProtectHome=read-only` blokkeren standaard
> writes naar `/home`. Daarom is `ReadWritePaths=` expliciet gezet zodat de service
> bij eventuele logging/temp-files in de project-folder kan schrijven.

## Installatie

```bash
# 1. Symlink (handig zodat updates aan de file in het project worden getrackt)
sudo ln -sf /home/erik/microservices/nutrientcontent/systemd/nutrientcontent.service \
            /etc/systemd/system/nutrientcontent.service

# 2. Reload + enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now nutrientcontent

# 3. Status checken
sudo systemctl status nutrientcontent
journalctl -u nutrientcontent -f
```

## Verificatie na deploy
```bash
# bind test
ss -ltnp | grep 5555
# → 127.0.0.1:5555  LISTEN  …  uvicorn

# health
curl -s http://127.0.0.1:5555/health
# → {"status":"ok","db":"ok","version":"0.1.0"}

# auth test (zou 401 moeten geven zonder key)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5555/foods?q=appel
# → 401

# echte aanroep
API_KEY=$(grep ^API_KEY .env | cut -d= -f2)
curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:5555/foods?q=appel | jq
```

## Reboot-test
```bash
sudo systemctl reboot
# wacht, log opnieuw in
curl -s http://127.0.0.1:5555/health
# moet weer "ok" geven zonder dat je iets gestart hebt
```

## Updates uitrollen

Code-update:
```bash
cd ~/microservices/nutrientcontent
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart nutrientcontent
journalctl -u nutrientcontent -n 50 --no-pager
```

Data-update (nieuwe NEVO-versie):
```bash
cd ~/microservices/nutrientcontent
.venv/bin/python -m src.load_nevo NEVO2026_v1.0.xlsx
# server hoeft niet te restarten — leest live van DB
```

## Logging
- Stdout/stderr → journald.
- Bekijken: `journalctl -u nutrientcontent -f` (live) of
  `journalctl -u nutrientcontent --since today`.
- INFO-niveau standaard. DEBUG via `LOG_LEVEL=debug` in `.env` + restart.

## Toegang voor calorietje.nl

calorietje.nl draait op dezelfde server (`/www/countcalories`). Het roept de
microservice aan via `http://127.0.0.1:5555`. De API-key staat dan in **die**
project-config (b.v. `/www/countcalories/.env`), met dezelfde waarde als hier.

Voorbeeld (taal-onafhankelijk):
```
POST http://127.0.0.1:5555/calculate
Headers:
  X-API-Key: <zelfde key>
  Content-Type: application/json
Body:
  {"items":[{"nevo_code":1,"grams":150},{"nevo_code":234,"grams":80}]}
```

## Backup
Voor MVP: niet nodig — data komt uit het xlsx, dat staat los.
Bij productie:
```bash
# dagelijks via cron
pg_dump -U nutrientcontent_loader nutrientcontent_db \
    | gzip > /home/erik/backups/nutrientcontent_$(date +%F).sql.gz
```

## Rollback
- Code: `git revert <sha>` + restart.
- Data: re-run `load_nevo` met vorige xlsx-versie.
- Schema: nieuwe genummerde migration met de tegenovergestelde `ALTER`.

## Monitoring (later)
Out-of-scope voor MVP. Mogelijke uitbreidingen:
- Prometheus-exporter (`prometheus-fastapi-instrumentator`).
- Healthcheck-cron via systemd-timer die elke minuut `/health` raakt.
