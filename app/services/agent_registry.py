from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_PATH = BASE_DIR / "data" / "runtime" / "discord_agents.json"
DEFAULT_EXAMPLE_PATH = BASE_DIR / "data" / "discord_agents.example.json"


def load_agent_registry() -> dict[str, Any]:
    configured = Path(os.getenv("DISCORD_AGENT_CONFIG_PATH", str(DEFAULT_RUNTIME_PATH)))
    source = configured if configured.exists() else DEFAULT_EXAMPLE_PATH
    with source.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_channel_agent(channel_name: str) -> dict[str, Any] | None:
    registry = load_agent_registry()
    for channel in registry.get("channels", []):
        if channel.get("channel_name", "").lower() == channel_name.lower():
            return channel
    return None
