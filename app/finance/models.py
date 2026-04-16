from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FinanceTransaction:
    transaction_id: str
    source_row: int
    booking_date: str
    amount: float
    currency: str
    counterparty: str
    counterparty_account: str
    own_account: str
    note: str
    raw_category: str
    description: str = ""


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
    email_match: "EmailMatch" | None = None
    email_match_status: str = "unmatched"
    email_match_debug: "EmailMatchDebug | None" = None


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


@dataclass(slots=True)
class EmailMatch:
    proposal_id: str
    received_at: str
    sender: str
    subject: str
    confidence: float
    reason: str


@dataclass(slots=True)
class EmailMatchCandidate:
    proposal_id: str
    received_at: str
    sender: str
    subject: str
    score: float
    amount_score: float
    text_score: float
    date_score: float
    delta_days: int | None
    passes_threshold: bool
    reason: str


@dataclass(slots=True)
class EmailMatchDebug:
    scanned_count: int
    within_window_count: int
    threshold: float
    summary: str
    top_candidates: list[EmailMatchCandidate]
