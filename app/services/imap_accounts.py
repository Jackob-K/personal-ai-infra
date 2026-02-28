from __future__ import annotations

import json
import os
from pathlib import Path

from app.models import InboxAccountConfig


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_PATH = BASE_DIR / "data" / "runtime" / "imap_accounts.json"
DEFAULT_EXAMPLE_PATH = BASE_DIR / "data" / "imap_accounts.example.json"


def load_imap_accounts() -> list[InboxAccountConfig]:
    configured = Path(os.getenv("IMAP_ACCOUNTS_PATH", str(DEFAULT_RUNTIME_PATH)))
    source = configured if configured.exists() else DEFAULT_EXAMPLE_PATH
    with source.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [InboxAccountConfig(**item) for item in raw.get("accounts", [])]
