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


def upsert_proposals(new_items: list[TaskProposal]) -> None:
    existing = list_proposals()
    by_key = {(p.account_name, p.message_id): p for p in existing}
    for item in new_items:
        by_key[(item.account_name, item.message_id)] = item
    save_proposals(list(by_key.values()))
