# AI Server

Lokální backend pro osobního AI asistenta, který běží primárně na vlastním serveru a má dvě vrstvy:
- `FastAPI backend`: email triage, návrhy úkolů, plánování, CalDAV
- `Discord bot`: chatové rozhraní s orchestrátorem a specializovanými asistenty

## Co umí v `v0.4`
- IMAP ingest z více schránek přes `POST /imap/ingest`
- klasifikaci emailů do rolí:
  - `DIPLOMKA`
  - `PROFESOR`
  - `FIRMA_ZAMESTNANI`
  - `STARTUP`
  - `SKOLA`
  - `OSOBNI`
  - `ASISTENT`
- generování návrhů úkolů se schválením
- plánování časového slotu
- zápis do CalDAV kalendáře
- Discord orchestrátor a dedikované kanály pro jednotlivé asistenty

## Architektura Discordu
Doporučené kanály na Discord serveru:
- `#orchestrator`
- `#diplomka`
- `#profesor`
- `#firma`
- `#startup`
- `#skola`
- `#osobni`
- `#asistent`

Logika:
- `#orchestrator` je hlavní řídicí kanál
- ostatní kanály jsou dlouhodobé kontexty specializovaných agentů
- mapování kanálů se načítá z `data/runtime/discord_agents.json`
- pokud runtime soubor neexistuje, použije se `data/discord_agents.example.json`

## API endpointy
- `GET /`
- `GET /health`
- `POST /classify-email`
- `POST /plan-task`
- `POST /imap/ingest`
- `GET /proposals/pending`
- `POST /proposals/{id}/decision`
- `POST /travel/estimate`

Swagger UI:
- `http://SERVER_IP:8000/docs`

## Lokální běh
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Discord bot lokálně:
```bash
python -m app.discord_bot
```

## Docker nasazení
```bash
cp .env.example .env
docker compose build
docker compose up -d
```

Spouští se 3 služby:
- `ai-server`
- `discord-bot`
- `ollama`

## Konfigurace
### `.env`
- `DISCORD_BOT_TOKEN`: token Discord bota
- `DISCORD_AGENT_CONFIG_PATH`: cesta k runtime mapování kanálů
- `IMAP_ACCOUNTS_PATH`: cesta k runtime IMAP účtům
- `CALDAV_*`: Apple/CalDAV integrace
- `OLLAMA_*`: lokální LLM fallback
- `GOOGLE_MAPS_API_KEY`: volitelné, pro dynamické travel times

### Runtime soubory
Tyto soubory necommituj:
- `data/runtime/discord_agents.json`
- `data/runtime/imap_accounts.json`
- `data/runtime/proposals.json`
- `data/runtime/channel_memory.json`

Ponechaný placeholder v Gitu:
- `data/runtime/.gitkeep`

### Example konfigurace agentů
Soubor `data/discord_agents.example.json`:
```json
{
  "guild_name": "My Assistant Server",
  "channels": [
    {"channel_name": "orchestrator", "agent": "ORCHESTRATOR", "role": "ASISTENT"},
    {"channel_name": "diplomka", "agent": "DIPLOMKA", "role": "DIPLOMKA"}
  ]
}
```

### Example IMAP konfigurace
Soubor `data/imap_accounts.example.json`:
```json
{
  "accounts": [
    {
      "name": "postcz",
      "host": "imap.post.cz",
      "port": 993,
      "username": "you@post.cz",
      "password_env": "IMAP_POST_PASSWORD",
      "folder": "INBOX",
      "unseen_only": true
    },
    {
      "name": "tul",
      "host": "mbox.tul.cz",
      "port": 993,
      "username": "jmeno.prijmeni@tul.cz",
      "password_env": "IMAP_TUL_PASSWORD",
      "folder": "INBOX",
      "unseen_only": true
    }
  ]
}
```

Poznámka:
- IMAP ingest běží read-only a používá `BODY.PEEK`, takže zprávy nemá označovat jako přečtené.

## Discord příkazy
V kanálu `#orchestrator`:
- `help`
- `pending`
- `ingest`
- `approve <proposal_id> [YYYY-MM-DD]`
- `reject <proposal_id>`

V tematických kanálech můžeš zatím psát přirozeně. Bot vrátí odpověď v rámci role a drží jednoduchou per-channel paměť v `data/runtime/channel_memory.json`.

## Jak založit Discord bota
1. Otevři Discord Developer Portal.
2. Vytvoř `New Application`.
3. V sekci `Bot` vytvoř bota.
4. Zapni `Message Content Intent`.
5. Zkopíruj token do `.env` jako `DISCORD_BOT_TOKEN`.
6. V `OAuth2 > URL Generator` vyber scope:
   - `bot`
7. Vyber bot permissions:
   - `View Channels`
   - `Send Messages`
   - `Read Message History`
8. Otevři vygenerovaný invite link a přidej bota na svůj server.

## První praktický setup
1. Založ soukromý Discord server.
2. Vytvoř kanály `orchestrator`, `diplomka`, `profesor`, `firma`, `startup`, `skola`, `osobni`, `asistent`.
3. Zkopíruj `data/discord_agents.example.json` do `data/runtime/discord_agents.json`.
4. Uprav názvy kanálů, pokud se liší.
5. Zkopíruj `data/imap_accounts.example.json` do `data/runtime/imap_accounts.json`.
6. Doplň IMAP údaje a hesla do `.env`.
7. Spusť `docker compose up -d --build`.
8. Napiš do `#orchestrator` příkaz `help`.
9. Potom zkus `ingest` a `pending`.

## Co je připravené pro budoucí multi-user režim
Tento základ už odděluje:
- runtime stav od verzovaných dat
- kanálový kontext od business logiky
- orchestrátor od specializovaných agentů

Pro plný multi-user provoz bude další krok:
- přidat `workspace_id` / `user_id` do storage a modelů
- oddělit data per Discord server nebo per uživatel
- přesunout runtime JSON do SQLite/Postgres

## Další doporučené kroky
- čtení existujících eventů z CalDAV, ne jen zápis
- scheduler pro automatický IMAP polling
- plné LLM odpovědi v tematických kanálech
- approval tlačítka přímo v Discordu místo textových příkazů
- storage migrace z JSON na SQLite
