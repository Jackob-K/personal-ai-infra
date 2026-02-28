from __future__ import annotations

from datetime import date, datetime
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
from app.services.imap_client import fetch_emails
from app.services.planner import plan_task_slot
from app.services.proposal_store import list_proposals, save_proposals, upsert_proposals


def ingest_and_create_proposals(payload: IngestImapRequest) -> IngestImapResponse:
    emails = fetch_emails(payload.accounts, payload.max_per_account)
    proposals: list[TaskProposal] = []

    for message in emails:
        classified = classify_email(
            EmailClassifyRequest(
                subject=message.subject,
                body=message.body,
                sender=message.sender,
                received_at=message.received_at,
            )
        )

        if not classified.requires_action:
            continue

        proposals.append(
            TaskProposal(
                id=str(uuid4()),
                created_at=datetime.utcnow(),
                account_name=message.account_name,
                message_id=message.message_id,
                sender=message.sender,
                subject=message.subject,
                role=classified.role,
                summary=classified.summary,
                requires_action=classified.requires_action,
                priority=classified.priority,
                duration_minutes=classified.suggested_duration_minutes,
                next_step=_make_next_step(classified.role, message.subject),
            )
        )

    upsert_proposals(proposals)
    return IngestImapResponse(
        emails_count=len(emails),
        proposals_created=len(proposals),
        proposals=proposals,
    )


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
        proposal.role = payload.role
    if payload.priority:
        proposal.priority = payload.priority
    if payload.duration_minutes:
        proposal.duration_minutes = payload.duration_minutes

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
    if role == "PROFESOR":
        return "Navrhni odpověď profesorovi a potvrď nejbližší možný termín."
    if role == "DIPLOMKA":
        return "Rozděl úkol diplomky na 1 konkrétní 60min blok a připrav první odstavec/outline."
    if role == "FIRMA_ZAMESTNANI":
        return "Potvrď směnu nebo pracovní požadavek a zapiš návazný blok v kalendáři."
    if role == "STARTUP":
        return "Sepiš 3-bodový akční plán pro startup a pošli follow-up."
    if role == "SKOLA":
        return "Zkontroluj deadline a vlož přípravu do nejbližšího volného bloku."
    if role == "ASISTENT":
        return "Zkontroluj návrh asistenta, uprav prioritu a schval plán."
    return f"Navrhni první konkrétní krok k tématu: {subject[:80]}"
