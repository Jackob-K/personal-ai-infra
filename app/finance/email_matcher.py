from __future__ import annotations

import re
from datetime import date, datetime

from app.finance.models import (
    CategorizedTransaction,
    EmailMatch,
    EmailMatchCandidate,
    EmailMatchDebug,
    FinanceTransaction,
)
from app.services.proposal_store import list_proposals

MAX_EMAIL_MATCH_DAYS = 45
MATCH_THRESHOLD = 0.42


def match_transaction_emails(transaction: FinanceTransaction) -> EmailMatch | None:
    matched, _ = analyze_transaction_email_match(transaction)
    return matched


def analyze_transaction_email_match(transaction: FinanceTransaction) -> tuple[EmailMatch | None, EmailMatchDebug]:
    tx_date = _parse_iso_date(transaction.booking_date)
    tx_amount = abs(transaction.amount)
    tx_text = _normalize_text(f"{transaction.counterparty} {transaction.note}")
    candidates: list[tuple[float, EmailMatch]] = []
    debug_candidates: list[EmailMatchCandidate] = []
    scanned_count = 0
    within_window_count = 0

    for proposal in list_proposals():
        scanned_count += 1
        if proposal.status == "rejected":
            continue
        received_dt = proposal.source_received_at or proposal.created_at
        if tx_date and received_dt:
            delta_days = abs((received_dt.date() - tx_date).days)
            if delta_days > MAX_EMAIL_MATCH_DAYS:
                continue
        else:
            delta_days = 999
        within_window_count += 1

        body_text = f"{proposal.subject} {proposal.source_excerpt} {proposal.sender}"
        amounts = _extract_amounts(body_text)
        amount_score = 0.0
        if tx_amount > 0 and amounts:
            if any(abs(candidate - tx_amount) <= 1.0 for candidate in amounts):
                amount_score = 0.64
            elif any(abs(candidate - tx_amount) <= max(5.0, tx_amount * 0.03) for candidate in amounts):
                amount_score = 0.38

        text_score = _token_overlap(tx_text, _normalize_text(body_text))
        date_score = 0.0 if delta_days == 999 else max(0.0, 0.18 - delta_days * 0.02)
        sender_bonus = 0.06 if _normalize_text(transaction.counterparty) and _normalize_text(transaction.counterparty) in _normalize_text(proposal.sender) else 0.0
        score = min(0.99, amount_score + text_score * 0.3 + date_score + sender_bonus)

        reason_parts: list[str] = []
        if amount_score >= 0.64:
            reason_parts.append("shodná částka")
        elif amount_score > 0:
            reason_parts.append("podobná částka")
        if text_score >= 0.45:
            reason_parts.append("podobný text")
        if delta_days != 999 and delta_days <= 2:
            reason_parts.append("blízké datum")
        if not reason_parts:
            if text_score > 0:
                reason_parts.append("slabá textová shoda")
            elif amounts:
                reason_parts.append("částka nalezena, ale slabé skóre")
            else:
                reason_parts.append("bez jasné shody")

        if amount_score > 0 or text_score > 0.08 or score > 0.18:
            debug_candidates.append(
                EmailMatchCandidate(
                    proposal_id=proposal.id,
                    received_at=received_dt.date().isoformat() if received_dt else "",
                    sender=proposal.sender,
                    subject=proposal.subject,
                    score=round(score, 2),
                    amount_score=round(amount_score, 2),
                    text_score=round(text_score, 2),
                    date_score=round(date_score, 2),
                    delta_days=None if delta_days == 999 else delta_days,
                    passes_threshold=score >= MATCH_THRESHOLD,
                    reason=", ".join(reason_parts),
                )
            )

        if amount_score <= 0 and text_score < 0.3:
            continue
        if score < MATCH_THRESHOLD:
            continue

        candidates.append(
            (
                score,
                EmailMatch(
                    proposal_id=proposal.id,
                    received_at=received_dt.date().isoformat() if received_dt else "",
                    sender=proposal.sender,
                    subject=proposal.subject,
                    confidence=round(score, 2),
                    reason=", ".join(reason_parts) or "slabší textová shoda",
                ),
            )
        )

    debug_candidates.sort(key=lambda item: item.score, reverse=True)
    top_debug = debug_candidates[:3]

    if not candidates:
        summary = _build_debug_summary(
            scanned_count=scanned_count,
            within_window_count=within_window_count,
            top_candidates=top_debug,
            matched=False,
        )
        return None, EmailMatchDebug(
            scanned_count=scanned_count,
            within_window_count=within_window_count,
            threshold=MATCH_THRESHOLD,
            summary=summary,
            top_candidates=top_debug,
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], EmailMatchDebug(
        scanned_count=scanned_count,
        within_window_count=within_window_count,
        threshold=MATCH_THRESHOLD,
        summary=_build_debug_summary(
            scanned_count=scanned_count,
            within_window_count=within_window_count,
            top_candidates=top_debug,
            matched=True,
        ),
        top_candidates=top_debug,
    )


def suggest_description(transaction: FinanceTransaction, email_match: EmailMatch | None) -> str:
    if transaction.description.strip():
        return transaction.description.strip()
    if email_match and email_match.subject.strip():
        return email_match.subject.strip()[:180]
    if transaction.note.strip():
        return transaction.note.strip()[:180]
    return ""


def rematch_preview_rows(rows: list[dict]) -> list[CategorizedTransaction]:
    refreshed: list[CategorizedTransaction] = []
    for row in rows:
        transaction = FinanceTransaction(
            transaction_id=str(row.get("transaction_id", "")).strip(),
            source_row=int(row.get("source_row", 0)),
            booking_date=str(row.get("booking_date", "")).strip(),
            amount=float(row.get("amount", 0)),
            currency=str(row.get("currency", "CZK")).strip() or "CZK",
            counterparty=str(row.get("counterparty", "")).strip(),
            counterparty_account=str(row.get("counterparty_account", "")).strip(),
            own_account=str(row.get("own_account", "")).strip(),
            note=str(row.get("note", "")).strip(),
            raw_category=str(row.get("raw_category", "")).strip(),
            description=str(row.get("description", "")).strip(),
        )
        email_match, email_debug = analyze_transaction_email_match(transaction)
        transaction.description = suggest_description(transaction, email_match)
        refreshed.append(
            CategorizedTransaction(
                transaction=transaction,
                suggestion=None,
                email_match=email_match,
                email_match_status="matched" if email_match else "unmatched",
                email_match_debug=email_debug,
            )
        )
    return refreshed


def _build_debug_summary(
    *,
    scanned_count: int,
    within_window_count: int,
    top_candidates: list[EmailMatchCandidate],
    matched: bool,
) -> str:
    if matched:
        return f"Nalezen match. Prohledáno {scanned_count} emailů, v okně {within_window_count}."
    if within_window_count == 0:
        return f"Žádný email v okně {MAX_EMAIL_MATCH_DAYS} dní."
    if not top_candidates:
        return f"V okně bylo {within_window_count} emailů, ale žádný nedal ani slabou textovou/částkovou shodu."
    best = top_candidates[0]
    return (
        f"V okně bylo {within_window_count} emailů. Nejlepší kandidát měl score {best.score} "
        f"pod prahem {MATCH_THRESHOLD}."
    )


def _parse_iso_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = re.sub(r"[^a-z0-9ěščřžýáíéůúňóäöüß]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(token for token in left.split() if len(token) > 2)
    right_tokens = set(token for token in right.split() if len(token) > 2)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def _extract_amounts(text: str) -> list[float]:
    normalized = (
        text.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("Kč", " ")
        .replace("CZK", " ")
        .replace("EUR", " ")
    )
    pattern = re.compile(r"(?<!\d)(\d{1,3}(?:[ .]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2}))(?!\d)")
    amounts: list[float] = []
    for match in pattern.findall(normalized):
        raw = match.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            amounts.append(abs(float(raw)))
        except ValueError:
            continue
    return amounts
