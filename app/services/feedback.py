from __future__ import annotations

import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
FEEDBACK_PATH = BASE_DIR / "data" / "runtime" / "feedback.json"


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def apply_feedback(sender: str | None, role: str, priority: int) -> tuple[str, int]:
    data = _load_feedback()
    sender_key = _sender_key(sender)
    if not sender_key:
        return role, priority

    learned_role = data.get("sender_role", {}).get(sender_key)
    learned_priority = data.get("sender_priority", {}).get(sender_key)
    if learned_role:
        role = learned_role
    if isinstance(learned_priority, int):
        priority = learned_priority
    return role, priority


def record_feedback(sender: str | None, role: str | None = None, priority: int | None = None) -> None:
    sender_key = _sender_key(sender)
    if not sender_key:
        return

    data = _load_feedback()
    data.setdefault("sender_role", {})
    data.setdefault("sender_priority", {})

    if role:
        data["sender_role"][sender_key] = role
    if priority is not None:
        data["sender_priority"][sender_key] = int(priority)

    _save_feedback(data)


def _sender_key(sender: str | None) -> str:
    if not sender:
        return ""
    match = EMAIL_RE.search(sender)
    return match.group(0).lower() if match else sender.strip().lower()


def _load_feedback() -> dict:
    if not FEEDBACK_PATH.exists():
        return {"sender_role": {}, "sender_priority": {}}
    with FEEDBACK_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_feedback(data: dict) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
