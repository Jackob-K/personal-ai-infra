from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
SYNC_STATE_PATH = BASE_DIR / "data" / "runtime" / "sync_state.json"


def load_sync_state() -> dict[str, Any]:
    if not SYNC_STATE_PATH.exists():
        return _default_state()
    with SYNC_STATE_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return {**_default_state(), **raw}


def save_sync_state(state: dict[str, Any]) -> None:
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SYNC_STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def record_sync_run(
    *,
    trigger: str,
    emails_count: int,
    proposals_created: int,
    proposals_updated: int,
    proposals_removed: int,
    status: str,
    error: str | None = None,
) -> None:
    state = load_sync_state()
    state.update(
        {
            "last_run_at": datetime.utcnow().isoformat(),
            "last_trigger": trigger,
            "last_status": status,
            "last_error": error or "",
            "last_emails_count": emails_count,
            "last_proposals_created": proposals_created,
            "last_proposals_updated": proposals_updated,
            "last_proposals_removed": proposals_removed,
        }
    )
    save_sync_state(state)


def _default_state() -> dict[str, Any]:
    return {
        "last_run_at": "",
        "last_trigger": "",
        "last_status": "never",
        "last_error": "",
        "last_emails_count": 0,
        "last_proposals_created": 0,
        "last_proposals_updated": 0,
        "last_proposals_removed": 0,
    }
