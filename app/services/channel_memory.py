from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
MEMORY_PATH = BASE_DIR / "data" / "runtime" / "channel_memory.json"


def append_message(channel_name: str, author: str, content: str) -> None:
    data = _load()
    messages = data.setdefault(channel_name, [])
    messages.append(
        {
            "timestamp": datetime.utcnow().isoformat(),
            "author": author,
            "content": content,
        }
    )
    data[channel_name] = messages[-50:]
    _save(data)


def get_recent_messages(channel_name: str, limit: int = 8) -> list[dict[str, str]]:
    data = _load()
    return data.get(channel_name, [])[-limit:]


def _load() -> dict[str, list[dict[str, str]]]:
    if not MEMORY_PATH.exists():
        return {}
    with MEMORY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, list[dict[str, str]]]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
