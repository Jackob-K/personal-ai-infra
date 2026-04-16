from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.models import TaskProposal
from app.services.agent_registry import find_role_channel


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


def list_active_proposals() -> list[TaskProposal]:
    return [item for item in list_proposals() if item.source_status == "active"]


def list_removed_pending_proposals() -> list[TaskProposal]:
    return [item for item in list_proposals() if item.status == "pending" and item.source_status == "removed"]


def list_pending_discord_notifications() -> list[TaskProposal]:
    pending: list[TaskProposal] = []
    for item in list_proposals():
        if item.source_status != "active" or item.status not in {"pending", "approved"}:
            continue
        target_channel = find_role_channel(item.role)
        if not target_channel:
            continue
        if item.discord_notified_channel == target_channel and item.discord_notified_at is not None:
            continue
        pending.append(item)
    return pending


def save_proposals(proposals: list[TaskProposal]) -> None:
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROPOSALS_PATH.open("w", encoding="utf-8") as f:
        json.dump([p.model_dump(mode="json") for p in proposals], f, ensure_ascii=False, indent=2)


def upsert_proposals(new_items: list[TaskProposal]) -> tuple[int, int, list[str]]:
    existing = list_proposals()
    by_key = {_proposal_lookup_key(p): p for p in existing}
    created = 0
    updated = 0
    reactivated = 0
    created_ids: list[str] = []
    now = datetime.utcnow()
    for item in new_items:
        key = _proposal_lookup_key(item)
        if key in by_key:
            existing_item = by_key[key]
            existing_item.account_name = item.account_name
            existing_item.message_id = item.message_id
            existing_item.source_folder = item.source_folder
            existing_item.source_imap_uid = item.source_imap_uid
            existing_item.source_message_id = item.source_message_id
            existing_item.source_message_key = item.source_message_key
            existing_item.source_last_seen_at = item.source_last_seen_at or now
            existing_item.sender = item.sender or existing_item.sender
            existing_item.subject = item.subject or existing_item.subject
            existing_item.source_excerpt = item.source_excerpt or existing_item.source_excerpt
            existing_item.source_body = item.source_body or existing_item.source_body
            if existing_item.source_status == "removed":
                existing_item.source_status = "active"
                existing_item.source_removed_at = None
                reactivated += 1
            updated += 1
        else:
            created += 1
            created_ids.append(item.id)
            by_key[key] = item
    save_proposals(list(by_key.values()))
    return created, updated + reactivated, created_ids


def mark_missing_proposals(active_message_keys: set[str], tracked_scopes: set[tuple[str, str]]) -> int:
    proposals = list_proposals()
    changed = 0
    now = datetime.utcnow()

    for proposal in proposals:
        if proposal.status != "pending":
            continue
        scope = (proposal.account_name, proposal.source_folder or "INBOX")
        if scope not in tracked_scopes:
            continue
        message_key = proposal.source_message_key or _legacy_message_key(proposal)
        if not message_key:
            continue
        if message_key in active_message_keys:
            if proposal.source_status == "removed":
                proposal.source_status = "active"
                proposal.source_removed_at = None
                proposal.source_removed_while_pending = False
                changed += 1
            continue
        if proposal.source_status == "removed":
            continue
        proposal.source_status = "removed"
        proposal.source_removed_at = now
        proposal.source_removed_while_pending = True
        changed += 1

    if changed:
        save_proposals(proposals)
    return changed


def delete_proposal(proposal_id: str) -> TaskProposal | None:
    proposals = list_proposals()
    idx = next((i for i, item in enumerate(proposals) if item.id == proposal_id), None)
    if idx is None:
        return None
    deleted = proposals.pop(idx)
    save_proposals(proposals)
    return deleted


def mark_discord_notified(proposal_ids: list[str], channel_name: str) -> None:
    proposals = list_proposals()
    changed = False
    now = datetime.utcnow()
    target = set(proposal_ids)
    for item in proposals:
        if item.id not in target:
            continue
        if item.discord_notified_channel == channel_name and item.discord_notified_at is not None:
            continue
        item.discord_notified_at = now
        item.discord_notified_channel = channel_name
        changed = True
    if changed:
        save_proposals(proposals)


def reset_discord_notification(proposal: TaskProposal) -> None:
    proposal.discord_notified_at = None
    proposal.discord_notified_channel = None


def _proposal_lookup_key(proposal: TaskProposal) -> str:
    return proposal.source_message_key or _legacy_message_key(proposal)


def _legacy_message_key(proposal: TaskProposal) -> str:
    folder = proposal.source_folder or "INBOX"
    if proposal.source_message_id:
        return f"{proposal.account_name}:{folder}:{proposal.source_message_id}"
    return f"{proposal.account_name}:{folder}:legacy:{proposal.message_id}"
