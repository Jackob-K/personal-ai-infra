from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FinanceTransaction:
    source_row: int
    booking_date: str
    amount: float
    currency: str
    counterparty: str
    counterparty_account: str
    own_account: str
    note: str
    raw_category: str


@dataclass(slots=True)
class CategorySuggestion:
    category: str
    confidence: float
    reason: str
    matched_on: str


@dataclass(slots=True)
class CategorizedTransaction:
    transaction: FinanceTransaction
    suggestion: CategorySuggestion | None


@dataclass(slots=True)
class TrainingExample:
    booking_date: str
    amount: float
    currency: str
    counterparty: str
    counterparty_account: str
    own_account: str
    note: str
    category: str
