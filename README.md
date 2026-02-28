# AI Server

Lokální backend pro AI asistenta, který:
- stáhne emaily z více schránek přes IMAP,
- shrne a klasifikuje je do rolí (`DIPLOMKA`, `PROFESOR`, `FIRMA_ZAMESTNANI`, `STARTUP`, `SKOLA`, `OSOBNI`, `ASISTENT`),
- vytvoří návrh úkolu (priorita, odhad času, další krok),
- předá návrh ke schválení,
- po schválení naplánuje časový slot a zapíše událost do CalDAV.

Projekt je navržený "approval-first": asistent navrhuje, člověk schvaluje.

## Co umí v `v0.3`
- `POST /imap/ingest`: ingest emailů z více IMAP schránek + generování návrhů.
- `GET /proposals/pending`: fronta čekajících návrhů ke schválení.
- `POST /proposals/{id}/decision`: schválit/odmítnout návrh, volitelně zapsat do CalDAV.
- `POST /travel/estimate`: odhad dopravy přes Google Maps Distance Matrix (fallback na config default).
- Zachovány endpointy:
  - `POST /classify-email`
  - `POST /plan-task`

## Lokální běh (bez Dockeru)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Docker (Ubuntu server)
```bash
cp .env.example .env
docker compose build
docker compose up -d
```

Swagger UI:
- `http://SERVER_IP:8000/docs`

## Konfigurace
- `.env`:
  - `OLLAMA_*` pro lokální LLM klasifikaci.
  - `CALDAV_*` pro zápis do Apple/CalDAV kalendáře.
  - `GOOGLE_MAPS_API_KEY` pro dynamické travel times.
  - `IMAP_*_PASSWORD` jako bezpečné heslo k mailboxům (přes `password_env`).
- `data/roles.json`: role, priority, defaultní délky.
- `data/planner_config.json`: fixní bloky, dny práce, commute buffery, travel defaults.
- `data/proposals.json`: persistovaná fronta návrhů.

## API příklady

### 1) Ingest emailů z více schránek
```bash
curl -X POST http://127.0.0.1:8000/imap/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "max_per_account": 10,
    "accounts": [
      {
        "name": "gmail",
        "host": "imap.gmail.com",
        "username": "you@gmail.com",
        "password_env": "IMAP_GMAIL_PASSWORD",
        "unseen_only": true
      },
      {
        "name": "postcz",
        "host": "imap.post.cz",
        "username": "you@post.cz",
        "password_env": "IMAP_POST_PASSWORD",
        "unseen_only": true
      },
      {
        "name": "websupport",
        "host": "imap.websupport.sk",
        "username": "you@domain.tld",
        "password_env": "IMAP_WEBSUPPORT_PASSWORD",
        "unseen_only": true
      }
    ]
  }'
```

### 2) Zobrazit návrhy ke schválení
```bash
curl http://127.0.0.1:8000/proposals/pending
```

### 3) Schválit návrh a zapsat do kalendáře
```bash
curl -X POST http://127.0.0.1:8000/proposals/<PROPOSAL_ID>/decision \
  -H "Content-Type: application/json" \
  -d '{
    "approve": true,
    "planning_date": "2026-02-23",
    "auto_schedule_to_caldav": true
  }'
```

### 4) Odhad dopravy
```bash
curl -X POST http://127.0.0.1:8000/travel/estimate \
  -H "Content-Type: application/json" \
  -d '{
    "origin": "Brno hlavní nádraží",
    "destination": "VUT FIT",
    "mode": "transit"
  }'
```

## Poznámky k bezpečnosti
- Doporučené je používat app-passwordy (Gmail/iCloud), ne hlavní hesla.
- Do API payloadu neposílej hesla přímo, použij `password_env` + `.env`.

## Struktura
```text
app/
  main.py
  models.py
  services/
    assistant_flow.py
    caldav_client.py
    classifier.py
    imap_client.py
    planner.py
    proposal_store.py
    roles.py
    settings.py
    travel.py
data/
  planner_config.json
  proposals.json
  roles.json
```

## Další doporučené kroky
- Přidat scheduler (cron/APScheduler) pro periodický ingest bez manuálního volání endpointu.
- Přidat per-role policy engine (co může asistent provést plně autonomně vs. vždy čekat na schválení).
- Přidat hybrid režim: lokální Ollama default + OpenAI fallback jen pro složité reasoning případy.
