from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PLANNER_CONFIG_PATH = BASE_DIR / "data" / "planner_config.json"


def load_planner_config() -> dict[str, Any]:
    cfg_path = Path(os.getenv("PLANNER_CONFIG_PATH", str(DEFAULT_PLANNER_CONFIG_PATH)))
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)
