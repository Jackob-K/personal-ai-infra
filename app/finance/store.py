from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from app.finance.models import CategorizedTransaction, TrainingExample
from app.services.settings import BASE_DIR


FINANCE_RUNTIME_DIR = BASE_DIR / "data" / "runtime"
FINANCE_TRAINING_PATH = FINANCE_RUNTIME_DIR / "finance_training.json"
FINANCE_PREVIEW_PATH = FINANCE_RUNTIME_DIR / "finance_preview.json"
FINANCE_MONTHS_PATH = FINANCE_RUNTIME_DIR / "finance_months.json"


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
        row["email_match"] = asdict(item.email_match) if item.email_match else None
        row["email_match_status"] = item.email_match_status
        row["email_match_debug"] = asdict(item.email_match_debug) if item.email_match_debug else None
        rows.append(row)
    _write_json(FINANCE_PREVIEW_PATH, rows)


def load_preview() -> list[dict]:
    payload = _load_json(FINANCE_PREVIEW_PATH, default=[])
    return [item for item in payload if isinstance(item, dict)]


def update_preview_description(transaction_id: str, description: str) -> bool:
    rows = load_preview()
    changed = False
    for item in rows:
        if str(item.get("transaction_id", "")).strip() != transaction_id:
            continue
        item["description"] = description
        changed = True
        break
    if changed:
        _write_json(FINANCE_PREVIEW_PATH, rows)
    return changed


def update_preview_category(transaction_id: str, category: str) -> bool:
    rows = load_preview()
    changed = False
    for item in rows:
        if str(item.get("transaction_id", "")).strip() != transaction_id:
            continue
        item["selected_category"] = category
        changed = True
        break
    if changed:
        _write_json(FINANCE_PREVIEW_PATH, rows)
    return changed


def save_month_edits(month_id: str, updates: dict[str, dict[str, str]]) -> int:
    changed = 0

    preview_rows = load_preview()
    preview_changed = False
    for item in preview_rows:
        if _month_key(item) != month_id:
            continue
        transaction_id = str(item.get("transaction_id", "")).strip()
        update = updates.get(transaction_id)
        if not update:
            continue
        new_description = update.get("description", str(item.get("description", "")))
        new_category = update.get("selected_category", str(item.get("selected_category", "")))
        if str(item.get("description", "")) != new_description:
            item["description"] = new_description
            item["description_locked"] = True
            preview_changed = True
            changed += 1
        if str(item.get("selected_category", "")) != new_category:
            item["selected_category"] = new_category
            item["category_locked"] = True
            preview_changed = True
            changed += 1
    if preview_changed:
        _write_json(FINANCE_PREVIEW_PATH, preview_rows)

    snapshots = load_month_snapshots()
    snapshot = snapshots.get(month_id)
    if snapshot:
        snapshot_changed = False
        for item in snapshot.get("rows", []):
            transaction_id = str(item.get("transaction_id", "")).strip()
            update = updates.get(transaction_id)
            if not update:
                continue
            new_description = update.get("description", str(item.get("description", "")))
            new_category = update.get("selected_category", str(item.get("selected_category", "")))
            if str(item.get("description", "")) != new_description:
                item["description"] = new_description
                item["description_locked"] = True
                snapshot_changed = True
            if str(item.get("selected_category", "")) != new_category:
                item["selected_category"] = new_category
                item["category_locked"] = True
                snapshot_changed = True
        if snapshot_changed:
            snapshots[month_id] = snapshot
            _write_json(FINANCE_MONTHS_PATH, snapshots)

    return changed


def reset_month_categories(month_id: str) -> int:
    changed = 0

    preview_rows = load_preview()
    preview_changed = False
    for item in preview_rows:
        if _month_key(item) != month_id:
            continue
        suggestion = item.get("suggestion") or {}
        target = (
            str(suggestion.get("category", "")).strip()
            or str(item.get("raw_category", "")).strip()
            or "Nezařazeno"
        )
        if str(item.get("selected_category", "")).strip() != target:
            item["selected_category"] = target
            item["category_locked"] = False
            preview_changed = True
            changed += 1
    if preview_changed:
        _write_json(FINANCE_PREVIEW_PATH, preview_rows)

    snapshots = load_month_snapshots()
    snapshot = snapshots.get(month_id)
    if snapshot:
        snapshot_changed = False
        for item in snapshot.get("rows", []):
            suggestion = item.get("suggestion") or {}
            target = (
                str(suggestion.get("category", "")).strip()
                or str(item.get("raw_category", "")).strip()
                or "Nezařazeno"
            )
            if str(item.get("selected_category", "")).strip() != target:
                item["selected_category"] = target
                item["category_locked"] = False
                snapshot_changed = True
        if snapshot_changed:
            snapshots[month_id] = snapshot
            _write_json(FINANCE_MONTHS_PATH, snapshots)

    return changed


def load_month_snapshots() -> dict[str, dict]:
    payload = _load_json(FINANCE_MONTHS_PATH, default={})
    return payload if isinstance(payload, dict) else {}


def save_month_snapshot(month_id: str, rows: list[dict]) -> None:
    payload = load_month_snapshots()
    payload[month_id] = {
        "month_id": month_id,
        "closed": True,
        "rows": rows,
    }
    _write_json(FINANCE_MONTHS_PATH, payload)


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


def _month_key(item: dict) -> str:
    booking_date = str(item.get("booking_date", "")).strip()
    return booking_date[:7] if len(booking_date) >= 7 else ""
