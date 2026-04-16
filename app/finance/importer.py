from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta

from app.finance.models import FinanceTransaction, TrainingExample


COLUMN_ALIASES = {
    "booking_date": {"datum", "date", "booking_date", "datum zaúčtování", "datum zauctovani"},
    "amount": {"částka", "castka", "amount", "kolik"},
    "currency": {"měna", "mena", "currency"},
    "counterparty": {
        "obchodník",
        "obchodnik",
        "obchod",
        "merchant",
        "counterparty",
        "název protiúčtu",
        "nazev protiuctu",
        "protistrana",
        "kde",
        "description",
    },
    "counterparty_account": {
        "counterparty_account",
        "protiúčet",
        "protiucet",
        "číslo protiúčtu",
        "cislo protiuctu",
        "account_number",
    },
    "own_account": {"own_account", "můj účet", "muj ucet", "source_account", "účet", "ucet"},
    "note": {"poznámka", "poznamka", "note", "memo", "message", "popis"},
    "raw_category": {"kategorie", "category"},
}


def parse_transactions(content: str) -> list[FinanceTransaction]:
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ","

    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV nemá hlavičku.")

    mapped = {_map_header(name): name for name in reader.fieldnames if _map_header(name)}
    if "booking_date" not in mapped or "amount" not in mapped or "counterparty" not in mapped:
        raise ValueError(
            "CSV musí mít aspoň sloupce pro datum, částku a protistranu/obchodníka."
        )

    transactions: list[FinanceTransaction] = []
    for row_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            transactions.append(
                FinanceTransaction(
                    transaction_id=_build_transaction_id(
                        booking_date=_normalize_date(row.get(mapped["booking_date"], "")),
                        amount=_normalize_amount(row.get(mapped["amount"], "")),
                        currency=(row.get(mapped.get("currency", ""), "") or "CZK").strip().upper() or "CZK",
                        counterparty=(row.get(mapped["counterparty"], "") or "").strip(),
                        counterparty_account=(row.get(mapped.get("counterparty_account", ""), "") or "").strip(),
                        own_account=(row.get(mapped.get("own_account", ""), "") or "").strip(),
                        note=(row.get(mapped.get("note", ""), "") or "").strip(),
                    ),
                    source_row=row_number,
                    booking_date=_normalize_date(row.get(mapped["booking_date"], "")),
                    amount=_normalize_amount(row.get(mapped["amount"], "")),
                    currency=(row.get(mapped.get("currency", ""), "") or "CZK").strip().upper() or "CZK",
                    counterparty=(row.get(mapped["counterparty"], "") or "").strip(),
                    counterparty_account=(row.get(mapped.get("counterparty_account", ""), "") or "").strip(),
                    own_account=(row.get(mapped.get("own_account", ""), "") or "").strip(),
                    note=(row.get(mapped.get("note", ""), "") or "").strip(),
                    raw_category=(row.get(mapped.get("raw_category", ""), "") or "").strip(),
                    personal_amount=_normalize_amount(row.get(mapped["amount"], "")),
                    effective_month=_normalize_date(row.get(mapped["booking_date"], ""))[:7],
                )
            )
        except ValueError as exc:
            raise ValueError(f"Neplatná hodnota na řádku {row_number}: {exc}") from exc
    return transactions


def extract_training_examples(transactions: list[FinanceTransaction]) -> list[TrainingExample]:
    examples: list[TrainingExample] = []
    for item in transactions:
        if not item.raw_category.strip():
            continue
        examples.append(
            TrainingExample(
                booking_date=item.booking_date,
                amount=item.amount,
                currency=item.currency,
                counterparty=item.counterparty,
                counterparty_account=item.counterparty_account,
                own_account=item.own_account,
                note=item.note,
                category=item.raw_category,
            )
        )
    return examples


def _map_header(name: str) -> str | None:
    normalized = _normalize_header(name)
    for canonical, aliases in COLUMN_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _normalize_amount(value: str) -> float:
    text = (value or "").strip()
    text = text.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"částka '{value}' nejde převést na číslo") from exc


def _normalize_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{5}(?:\.0)?", text):
        serial = int(float(text))
        dt = datetime(1899, 12, 30) + timedelta(days=serial)
        return dt.date().isoformat()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _build_transaction_id(
    *,
    booking_date: str,
    amount: float,
    currency: str,
    counterparty: str,
    counterparty_account: str,
    own_account: str,
    note: str,
) -> str:
    payload = "||".join(
        [
            booking_date.strip(),
            f"{amount:.2f}",
            currency.strip().upper(),
            _normalize_id_text(counterparty),
            re.sub(r"\s+", "", counterparty_account.strip()),
            re.sub(r"\s+", "", own_account.strip()),
            _normalize_id_text(note),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_id_text(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = re.sub(r"[\W_]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()
