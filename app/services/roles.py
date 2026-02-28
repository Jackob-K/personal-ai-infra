from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
ROLES_PATH = BASE_DIR / "data" / "roles.json"


def load_roles() -> dict[str, Any]:
    with ROLES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_role_config(role: str) -> dict[str, Any]:
    roles = load_roles()
    return roles.get(role.upper(), {})

