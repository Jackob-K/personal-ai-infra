# AI Server

Lokální backend pro osobního AI asistenta, který běží primárně na vlastním serveru a má dvě vrstvy:
- `FastAPI backend`: email triage, návrhy úkolů, plánování, CalDAV
- `Discord bot`: chatové rozhraní s orchestrátorem a specializovanými asistenty

## Co umí v `v0.5`
- IMAP ingest z více schránek přes `POST /imap/ingest`
- klasifikaci emailů do rolí:
  - `DIPLOMKA`
  - `PROFESOR`
  - `KLIMATIKA`
  - `TOKVEKO`
  - `UNIVERZITA`
  - `OSOBNI`
  - `NEWSLETTER`
  - `SPAM`
  - `PHISHING`
- generování návrhů úkolů se schválením
- plánování časového slotu
- zápis do CalDAV kalendáře
- Discord orchestrátor a dedikované kanály pro jednotlivé asistenty

## Architektura Discordu
Doporučené kanály na Discord serveru:
- `#orchestrator`
- `#diplomka`
- `#profesor`
- `#klimatika`
- `#tokveko`
- `#univerzita`
- `#osobni`

Logika:
- `#orchestrator` je hlavní řídicí kanál a přebírá i původní koordinační roli asistenta
- ostatní kanály jsou dlouhodobé kontexty specializovaných agentů
- mapování kanálů se načítá z `data/runtime/discord_agents.json`
- pokud runtime soubor neexistuje, použije se `data/discord_agents.example.json`

## API endpointy
- `GET /`
- `GET /web` (home dashboard)
- `GET /web/channels`
- `GET /web/channel/{channel_name}`
- `GET /web/projects`
- `GET /web/project/{project_id}`
- `POST /web/project-update`
- `POST /web/subtask-update`
- `POST /web/ingest`
- `POST /web/task-update`
- `POST /web/task-status`
- `GET /health`
- `GET /triage` (web triage UI)
- `POST /triage/submit`
- `POST /triage/continue`
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
- `TRIAGE_WEB_URL`: odkaz, který orchestrátor po `ingest` připojí do zprávy
- `CALDAV_*`: Apple/CalDAV integrace
- `OLLAMA_*`: lokální LLM fallback
- `GOOGLE_MAPS_API_KEY`: volitelné, pro dynamické travel times

### Runtime soubory
Tyto soubory necommituj:
- `data/runtime/discord_agents.json`
- `data/runtime/imap_accounts.json`
- `data/runtime/proposals.json`
- `data/runtime/projects.json`
- `data/runtime/channel_memory.json`
- `data/runtime/feedback.json`

Ponechaný placeholder v Gitu:
- `data/runtime/.gitkeep`

### Example konfigurace agentů
Soubor `data/discord_agents.example.json`:
```json
{
  "guild_name": "My Assistant Server",
  "channels": [
    {"channel_name": "orchestrator", "agent": "ORCHESTRATOR", "role": "ORCHESTRATOR"},
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
- `triage`
- `pending`
- `ingest`
- `dispatch`
- `start <proposal_id>`
- `done <proposal_id>`
- `delete <proposal_id>`
- `set-group <proposal_id> <GROUP>`
- `comment <proposal_id> <TEXT>`
- `set-role <proposal_id> <ROLE>`
- `set-priority <proposal_id> <1-5>`
- `mark-newsletter <proposal_id>`
- `mark-spam <proposal_id>`
- `mark-phishing <proposal_id>`
- `approve <proposal_id> [YYYY-MM-DD]`
- `reject <proposal_id>`

V tematických kanálech:
- `project <název projektu>` založí dlouhodobý projekt v dané roli
- `task <popis úkolu>` založí úkol a naváže ho na aktivní projekt
- `delete <proposal_id>` smaže omylem vytvořený úkol
- běžná věta se automaticky uloží jako rychlý úkol (manual task)

Bot drží jednoduchou per-channel paměť v `data/runtime/channel_memory.json`.

Poznámka k `ingest`:
- vypíše počty načtených emailů
- vypíše nově zachycené návrhy
- znovu připomene všechny stále čekající návrhy, aby se neztratily v chatu
- připojí odkaz na web triage (`TRIAGE_WEB_URL`)
- návrhy můžeš před schválením upravit přes `set-role` a `set-priority`
- ruční opravy se ukládají jako feedback (učení podle odesílatele)
- schválení v aktuálním pipeline automaticky nic neplánuje do kalendáře (kalendář řešíš ručně až v dalším kroku)
- ingest nově přidává `bundle` (seskupení souvisejících emailů, např. objednávka/faktura/doprava)
- nad bundle je nově `project` vrstva s `subtask` položkami

Web triage:
- otevři `/triage`
- uprav roli/prioritu v tabulce (včetně viditelného odesílatele)
- `Uložit` nebo `Uložit + Schválit`
- pokud je vše správně, klikni `Pokračuj (uloží vše)` (stejnou větu můžeš napsat i do Discordu)
- když máš vše připravené najednou, použij `Uložit + Schválit vše`
- pro přehled používej `/web` a `/web/channels`
- na `/web` jsou vpravo dva panely:
  - `Neotevřené` (pending/approved/dispatched)
  - `Rozpracované / čekající` (in_progress)
- detail kanálu otevřeš přes `/web/channel/<nazev_kanalu>` (např. `/web/channel/klimatika`)
- v detailu kanálu vidíš i `Odesílatele`
- v detailu kanálu můžeš změnit i `Role` (tím položku přesuneš do jiného kanálu), doplnit `Skupinu` (větší úkol), přidat `Komentář` a změnit stav na `Rozpracováno/Hotovo`
- v detailu kanálu můžeš email přiřadit k existujícímu projektu nebo založit nový projekt + subtask
- navíc je tam `Handling` rozhodnutí:
  - `process` = zpracovat bez kalendáře
  - `needs_attention` = vyžaduje tvoji pozornost
  - `calendar` = kandidát na ruční naplánování do kalendáře
  - `review` = ještě nerozhodnuto

## Aktuální pipeline (manuální kalendář)
1. `ingest` načte emaily a připraví návrhy.
2. V `/triage` upravíš role/prioritu a dáš `Uložit + Schválit vše`.
3. V Discord `#orchestrator` spustíš `dispatch`.
4. V `/web/channel/<kanal>` průběžně doplňuješ `handling`, komentáře, skupiny, přiřazení na projekt/subtask a stav (`in_progress`/`done`).
5. Plánování do kalendáře je v této fázi manuální.

## Project / Subtask vrstva
- `/web/projects`: seznam dlouhodobých projektů (deadline/stav/počet napojených emailů).
- `/web/project/{project_id}`: detail projektu + subtasky + napojené emaily.
- v detailu projektu můžeš nastavit `status` a `deadline`.
- subtask stavy: `todo`, `in_progress`, `submitted`, `needs_revision`, `done`
- v kanálovém detailu:
  - vyber `Projekt` z dropdownu nebo vyplň `Nový projekt`
  - volitelně vyplň `Nový subtask`
  - `Uložit` přiřadí email do dlouhodobé projektové linie

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
2. Vytvoř kanály `orchestrator`, `diplomka`, `profesor`, `klimatika`, `tokveko`, `univerzita`, `osobni`.
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
