from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.finance.models import CategorizedTransaction, TrainingExample
from app.services.settings import BASE_DIR


FINANCE_RUNTIME_DIR = BASE_DIR / "data" / "runtime"
FINANCE_TRAINING_PATH = FINANCE_RUNTIME_DIR / "finance_training.json"
FINANCE_PREVIEW_PATH = FINANCE_RUNTIME_DIR / "finance_preview.json"


def load_training_examples() -> list[TrainingExample]:
    payload = _load_json(FINANCE_TRAINING_PATH, default=[])
    return [TrainingExample(**item) for item in payload if isinstance(item, dict)]


def save_training_examples(examples: list[TrainingExample]) -> None:
    _write_json(FINANCE_TRAINING_PATH, [asdict(item) for item in examples])


def merge_training_examples(new_examples: list[TrainingExample]) -> int:
    existing = load_training_examples()
    merged: dict[str, TrainingExample] = {_training_key(item): item for item in existing}
    before = len(merged)
    for item in new_examples:
        merged[_training_key(item)] = item
    save_training_examples(list(merged.values()))
    return len(merged) - before


def save_preview(transactions: list[CategorizedTransaction]) -> None:
    rows = []
    for item in transactions:
        row = asdict(item.transaction)
        row["suggestion"] = asdict(item.suggestion) if item.suggestion else None
        rows.append(row)
    _write_json(FINANCE_PREVIEW_PATH, rows)


def load_preview() -> list[dict]:
    payload = _load_json(FINANCE_PREVIEW_PATH, default=[])
    return [item for item in payload if isinstance(item, dict)]


def _training_key(item: TrainingExample) -> str:
    return "||".join(
        [
            item.booking_date.strip(),
            f"{item.amount:.2f}",
            item.currency.strip().upper(),
            item.counterparty.strip().lower(),
            item.counterparty_account.strip(),
            item.own_account.strip(),
            item.note.strip().lower(),
            item.category.strip().lower(),
        ]
    )


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
