from __future__ import annotations

import re
from collections import defaultdict

from app.finance.email_matcher import analyze_transaction_email_match, suggest_description
from app.finance.models import CategorizedTransaction, CategorySuggestion, FinanceTransaction, TrainingExample


def categorize_transactions(
    transactions: list[FinanceTransaction],
    training_examples: list[TrainingExample],
) -> list[CategorizedTransaction]:
    categorized: list[CategorizedTransaction] = []
    for item in transactions:
        email_match, email_debug = analyze_transaction_email_match(item)
        item.description = suggest_description(item, email_match)
        categorized.append(
            CategorizedTransaction(
                transaction=item,
                suggestion=suggest_category(item, training_examples),
                email_match=email_match,
                email_match_status="matched" if email_match else "unmatched",
                email_match_debug=email_debug,
            )
        )
    return categorized


def suggest_category(
    transaction: FinanceTransaction,
    training_examples: list[TrainingExample],
) -> CategorySuggestion | None:
    scored: list[tuple[float, str, TrainingExample]] = []
    tx_counterparty = _normalize_text(transaction.counterparty)
    tx_note = _normalize_text(transaction.note)
    tx_account = _normalize_account(transaction.counterparty_account)
    tx_own_account = _normalize_account(transaction.own_account)

    for example in training_examples:
        ex_counterparty = _normalize_text(example.counterparty)
        ex_note = _normalize_text(example.note)
        ex_account = _normalize_account(example.counterparty_account)
        ex_own_account = _normalize_account(example.own_account)

        score = 0.0
        reason = ""

        if tx_account and ex_account and tx_account == ex_account:
            score = 0.99
            reason = "shodný účet protistrany"
        elif tx_counterparty and ex_counterparty and tx_counterparty == ex_counterparty:
            score = 0.94
            reason = "shodná protistrana"
        else:
            overlap = _token_overlap(tx_counterparty, ex_counterparty)
            if overlap >= 0.75:
                score = 0.70 + overlap * 0.2
                reason = "podobný název protistrany"
            elif tx_note and ex_note:
                note_overlap = _token_overlap(tx_note, ex_note)
                if note_overlap >= 0.75:
                    score = 0.65 + note_overlap * 0.2
                    reason = "podobná poznámka"

        if score <= 0:
            continue
        if tx_own_account and ex_own_account and tx_own_account == ex_own_account:
            score += 0.02
        if tx_account and ex_account and tx_account != ex_account:
            score -= 0.12
        scored.append((min(score, 0.99), reason, example))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    grouped: dict[str, list[tuple[float, str, TrainingExample]]] = defaultdict(list)
    for item in scored[:5]:
        grouped[item[2].category].append(item)

    best_category, matches = max(grouped.items(), key=lambda item: (len(item[1]), sum(x[0] for x in item[1])))
    best_score = sum(item[0] for item in matches) / len(matches)
    top = matches[0]
    return CategorySuggestion(
        category=best_category,
        confidence=round(best_score, 2),
        reason=top[1],
        matched_on=_match_label(top[2]),
    )


def _match_label(example: TrainingExample) -> str:
    if example.counterparty_account.strip():
        return example.counterparty_account.strip()
    return example.counterparty.strip()[:80]


def _normalize_text(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = re.sub(r"[\W_]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def _normalize_account(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip())


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
