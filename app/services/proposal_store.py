from __future__ import annotations

import json
from pathlib import Path

from app.models import TaskProposal


BASE_DIR = Path(__file__).resolve().parents[2]
PROPOSALS_PATH = BASE_DIR / "data" / "runtime" / "proposals.json"

ROLE_ALIASES = {
    "STARTUP": "TOKVEKO",
    "SKOLA": "UNIVERZITA",
    "FIRMA_ZAMESTNANI": "KLIMATIKA",
}


def list_proposals() -> list[TaskProposal]:
    if not PROPOSALS_PATH.exists():
        return []
    with PROPOSALS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw:
        role = str(item.get("role", "")).upper()
        if role in ROLE_ALIASES:
            item["role"] = ROLE_ALIASES[role]
    return [TaskProposal(**item) for item in raw]


def save_proposals(proposals: list[TaskProposal]) -> None:
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROPOSALS_PATH.open("w", encoding="utf-8") as f:
        json.dump([p.model_dump(mode="json") for p in proposals], f, ensure_ascii=False, indent=2)


def upsert_proposals(new_items: list[TaskProposal]) -> tuple[int, int, list[str]]:
    existing = list_proposals()
    by_key = {(p.account_name, p.message_id): p for p in existing}
    created = 0
    updated = 0
    created_ids: list[str] = []
    for item in new_items:
        key = (item.account_name, item.message_id)
        if key in by_key:
            # Keep existing user-edited state for already known emails.
            updated += 1
        else:
            created += 1
            created_ids.append(item.id)
            by_key[key] = item
    save_proposals(list(by_key.values()))
    return created, updated, created_ids


def delete_proposal(proposal_id: str) -> TaskProposal | None:
    proposals = list_proposals()
    idx = next((i for i, item in enumerate(proposals) if item.id == proposal_id), None)
    if idx is None:
        return None
    deleted = proposals.pop(idx)
    save_proposals(proposals)
    return deleted
