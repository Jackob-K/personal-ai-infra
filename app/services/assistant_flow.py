from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parseaddr
from uuid import uuid4

from app.models import (
    ApproveProposalRequest,
    EmailClassifyRequest,
    IngestImapRequest,
    IngestImapResponse,
    TaskProposal,
    TimeBlock,
)
from app.services.caldav_client import create_calendar_event
from app.services.classifier import classify_email
from app.services.imap_client import fetch_active_message_keys, fetch_emails
from app.services.planner import plan_task_slot
from app.services.proposal_store import (
    list_proposals,
    mark_missing_proposals,
    reset_discord_notification,
    save_proposals,
    upsert_proposals,
)
from app.services.sync_state import record_sync_run


NON_CALENDAR_ROLES = {"SPAM", "PHISHING", "NEWSLETTER"}


def ingest_and_create_proposals(payload: IngestImapRequest, trigger: str = "manual") -> IngestImapResponse:
    emails = fetch_emails(payload.accounts, payload.max_per_account)
    proposals: list[TaskProposal] = []
    now = datetime.utcnow()

    for message in emails:
        classified = classify_email(
            EmailClassifyRequest(
                subject=message.subject,
                body=message.body,
                sender=message.sender,
                received_at=message.received_at,
            )
        )

        if not classified.requires_action and classified.role not in NON_CALENDAR_ROLES:
            continue

        proposals.append(
            TaskProposal(
                id=str(uuid4()),
                created_at=now,
                account_name=message.account_name,
                message_id=message.message_id,
                source_folder=message.folder,
                source_imap_uid=message.imap_uid,
                source_message_id=message.source_message_id,
                source_message_key=message.stable_key,
                source_status="active",
                source_last_seen_at=now,
                sender=message.sender,
                subject=message.subject,
                source_excerpt=message.body[:320],
                role=classified.role,
                handling=_initial_handling(classified.role, classified.requires_action),
                summary=classified.summary,
                requires_action=classified.requires_action,
                priority=classified.priority,
                duration_minutes=classified.suggested_duration_minutes,
                next_step=_make_next_step(classified.role, message.subject),
                bundle_key=_bundle_key(message.sender, message.subject, message.body),
                bundle_label=_bundle_label(message.sender, message.subject, message.body),
            )
        )

    created_count, updated_count, created_ids = upsert_proposals(proposals)
    active_message_keys = fetch_active_message_keys(payload.accounts)
    tracked_scopes = {(account.name, account.folder) for account in payload.accounts}
    removed_count = mark_missing_proposals(active_message_keys, tracked_scopes)
    result = IngestImapResponse(
        emails_count=len(emails),
        proposals_created=created_count,
        proposals_updated=updated_count,
        proposals_removed=removed_count,
        new_proposal_ids=created_ids,
        proposals=proposals,
    )
    record_sync_run(
        trigger=trigger,
        emails_count=result.emails_count,
        proposals_created=result.proposals_created,
        proposals_updated=result.proposals_updated,
        proposals_removed=result.proposals_removed,
        status="ok",
    )
    return result


def approve_or_reject_proposal(proposal_id: str, payload: ApproveProposalRequest) -> TaskProposal:
    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        raise ValueError(f"Proposal '{proposal_id}' not found")

    if not payload.approve:
        proposal.status = "rejected"
        save_proposals(proposals)
        return proposal

    proposal.status = "approved"
    if payload.role:
        if proposal.role != payload.role:
            proposal.role = payload.role
            reset_discord_notification(proposal)
    if payload.priority:
        proposal.priority = payload.priority
    if payload.duration_minutes:
        proposal.duration_minutes = payload.duration_minutes

    if proposal.role in NON_CALENDAR_ROLES:
        proposal.planned_start = None
        proposal.planned_end = None
        proposal.calendar_event_uid = None
        save_proposals(proposals)
        return proposal

    if not payload.auto_schedule_to_caldav:
        proposal.planned_start = None
        proposal.planned_end = None
        proposal.calendar_event_uid = None
        save_proposals(proposals)
        return proposal

    planning_date = payload.planning_date or date.today()
    occupied = _occupied_blocks_from_approved(proposals, exclude_id=proposal_id)
    plan = plan_task_slot(
        payload=_build_plan_payload(
            role=proposal.role,
            title=proposal.subject or proposal.summary,
            duration_minutes=proposal.duration_minutes,
            planning_date=planning_date,
            occupied=occupied,
        )
    )

    if plan.status == "planned" and plan.planned_start and plan.planned_end:
        proposal.planned_start = plan.planned_start
        proposal.planned_end = plan.planned_end
        if payload.auto_schedule_to_caldav:
            proposal.calendar_event_uid = create_calendar_event(
                summary=f"[{proposal.role}] {proposal.subject}",
                description=f"{proposal.summary}\n\nDalší krok: {proposal.next_step}",
                start=proposal.planned_start,
                end=proposal.planned_end,
            )

    save_proposals(proposals)
    return proposal


def _occupied_blocks_from_approved(proposals: list[TaskProposal], exclude_id: str) -> list[TimeBlock]:
    blocks: list[TimeBlock] = []
    for item in proposals:
        if item.id == exclude_id or item.status != "approved":
            continue
        if item.planned_start and item.planned_end:
            blocks.append(TimeBlock(start=item.planned_start, end=item.planned_end, label=item.subject))
    return blocks


def _build_plan_payload(role: str, title: str, duration_minutes: int, planning_date: date, occupied: list[TimeBlock]):
    from app.models import PlanTaskRequest

    return PlanTaskRequest(
        role=role,
        task_title=title,
        duration_minutes=duration_minutes,
        planning_date=planning_date,
        existing_events=occupied,
    )


def _make_next_step(role: str, subject: str) -> str:
    if role == "NEWSLETTER":
        return "Rychle se odhlas z newsletteru a případně nastav filtr/blokaci."
    if role == "SPAM":
        return "Ověř spam a ručně odhlaš subscription nebo nastav blokaci odesílatele."
    if role == "PHISHING":
        return "Neotvírej odkazy, ověř odesílatele a případ nahlas jako phishing."
    if role == "PROFESOR":
        return "Navrhni odpověď profesorovi a potvrď nejbližší možný termín."
    if role == "DIPLOMKA":
        return "Rozděl úkol diplomky na 1 konkrétní 60min blok a připrav první odstavec/outline."
    if role == "KLIMATIKA":
        return "Potvrď směnu nebo pracovní požadavek a zapiš návazný blok v kalendáři."
    if role == "TOKVEKO":
        return "Sepiš 3-bodový akční plán pro TOKVEKO a pošli follow-up."
    if role == "UNIVERZITA":
        return "Zkontroluj deadline a vlož přípravu do nejbližšího volného bloku."
    return f"Navrhni první konkrétní krok k tématu: {subject[:80]}"


def _initial_handling(role: str, requires_action: bool) -> str:
    if role in NON_CALENDAR_ROLES:
        return "process"
    if requires_action:
        return "needs_attention"
    return "process"


def _bundle_key(sender: str, subject: str, body: str) -> str:
    sender_domain = _sender_domain(sender)
    order_id = _extract_order_id(f"{subject}\n{body[:500]}")
    if order_id:
        return f"{sender_domain}:{order_id.lower()}"
    normalized = _normalize_subject_for_bundle(subject)
    return f"{sender_domain}:{normalized[:80]}"


def _bundle_label(sender: str, subject: str, body: str) -> str:
    order_id = _extract_order_id(f"{subject}\n{body[:500]}")
    if order_id:
        return f"Objednávka {order_id}"
    short = _normalize_subject_for_bundle(subject)[:60]
    return short or _sender_domain(sender)


def _sender_domain(sender: str) -> str:
    _, email_addr = parseaddr(sender or "")
    if "@" in email_addr:
        return email_addr.split("@", 1)[1].lower()
    return "unknown-sender"


def _extract_order_id(text: str) -> str | None:
    patterns = [
        r"(?:objedn[aá]vka|order)\s*[#:\-]?\s*([A-Z0-9\-]{5,})",
        r"(?:č[íi]slo|cislo)\s*(?:objedn[aá]vky)?\s*[#:\-]?\s*([A-Z0-9\-]{5,})",
        r"#([A-Z0-9\-]{5,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _normalize_subject_for_bundle(subject: str) -> str:
    text = (subject or "").lower()
    text = re.sub(r"^\s*(re|fwd?)\s*:\s*", "", text)
    text = re.sub(
        r"\b(přijata|prijata|potvrzení|potvrzeni|zpracování|zpracovani|odeslána|odeslana|"
        r"shipment|tracking|invoice|faktura|stav|status|update)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9ěščřžýáíéůúňó]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "email-thread"
