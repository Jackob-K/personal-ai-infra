from __future__ import annotations

import json
from pathlib import Path

from app.models import TaskProposal


BASE_DIR = Path(__file__).resolve().parents[2]
PROPOSALS_PATH = BASE_DIR / "data" / "runtime" / "proposals.json"


def list_proposals() -> list[TaskProposal]:
    if not PROPOSALS_PATH.exists():
        return []
    with PROPOSALS_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
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
            existing_item = by_key[key]
            item.id = existing_item.id
            item.created_at = existing_item.created_at
            item.status = existing_item.status
            item.planned_start = existing_item.planned_start
            item.planned_end = existing_item.planned_end
            item.calendar_event_uid = existing_item.calendar_event_uid
            updated += 1
        else:
            created += 1
            created_ids.append(item.id)
        by_key[key] = item
    save_proposals(list(by_key.values()))
    return created, updated, created_ids
