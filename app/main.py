from __future__ import annotations

import html
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models import (
    ApproveProposalRequest,
    ApproveProposalResponse,
    ClassifyEmailResponse,
    EmailClassifyRequest,
    IngestImapRequest,
    IngestImapResponse,
    PlanTaskRequest,
    PlanTaskResponse,
    ProposalListResponse,
    TravelEstimateRequest,
    TravelEstimateResponse,
)
from app.services.assistant_flow import approve_or_reject_proposal, ingest_and_create_proposals
from app.services.classifier import classify_email
from app.services.feedback import record_feedback
from app.services.planner import plan_task_slot
from app.services.proposal_store import list_proposals, save_proposals
from app.services.roles import load_roles
from app.services.travel import estimate_travel


app = FastAPI(title="AI Server", version="0.5.0")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "AI Server is running. Open /docs for API documentation."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/classify-email", response_model=ClassifyEmailResponse)
def classify_email_endpoint(payload: EmailClassifyRequest) -> ClassifyEmailResponse:
    return classify_email(payload)


@app.post("/plan-task", response_model=PlanTaskResponse)
def plan_task_endpoint(payload: PlanTaskRequest) -> PlanTaskResponse:
    return plan_task_slot(payload)


@app.post("/imap/ingest", response_model=IngestImapResponse)
def ingest_imap_endpoint(payload: IngestImapRequest) -> IngestImapResponse:
    return ingest_and_create_proposals(payload)


@app.get("/proposals/pending", response_model=ProposalListResponse)
def pending_proposals_endpoint() -> ProposalListResponse:
    pending = [item for item in list_proposals() if item.status == "pending"]
    return ProposalListResponse(proposals=pending)


@app.post("/proposals/{proposal_id}/decision", response_model=ApproveProposalResponse)
def proposal_decision_endpoint(proposal_id: str, payload: ApproveProposalRequest) -> ApproveProposalResponse:
    try:
        proposal = approve_or_reject_proposal(proposal_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApproveProposalResponse(proposal=proposal)


@app.post("/travel/estimate", response_model=TravelEstimateResponse)
def travel_estimate_endpoint(payload: TravelEstimateRequest) -> TravelEstimateResponse:
    return estimate_travel(payload)


@app.get("/triage", response_class=HTMLResponse)
def triage_page(msg: str | None = None) -> HTMLResponse:
    pending = [item for item in list_proposals() if item.status == "pending"]
    roles = sorted(load_roles().keys())

    notice = f"<p style='color:#0b5'>{html.escape(msg)}</p>" if msg else ""
    if not pending:
        body = (
            "<h1>Triage Inbox</h1>"
            f"{notice}"
            "<p>Žádné čekající položky. Napiš v Discordu do orchestratora <code>pokracuj</code>.</p>"
            "<form method='post' action='/triage/submit'>"
            "<button type='submit' name='action' value='save_all_continue'>Pokračuj</button>"
            "</form>"
        )
        return HTMLResponse(_page(body))

    rows: list[str] = []
    for item in pending:
        escaped_subject = html.escape((item.subject or item.summary or "")[:120])
        escaped_sender = html.escape((item.sender or "")[:120])
        escaped_id = html.escape(item.id)
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(item.id[:8])}</code></td>"
            f"<td>{escaped_sender}</td>"
            f"<td><select name='role__{escaped_id}'>{_role_select_options(roles, item.role)}</select></td>"
            f"<td><input name='priority__{escaped_id}' type='number' min='1' max='5' value='{item.priority}' style='width:56px'></td>"
            f"<td>{escaped_subject}</td>"
            "<td>"
            f"<button type='submit' name='action' value='save:{escaped_id}'>Uložit</button> "
            f"<button type='submit' name='action' value='approve:{escaped_id}'>Uložit + Schválit</button> "
            f"<button type='submit' name='action' value='reject:{escaped_id}'>Odmítnout</button>"
            "</td>"
            "</tr>"
        )

    body = (
        "<h1>Triage Inbox</h1>"
        f"{notice}"
        "<p>Uprav roli/prioritu a potvrď. SPAM/PHISHING/NEWSLETTER se po schválení neplánují do kalendáře.</p>"
        "<form method='post' action='/triage/submit'>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>ID</th><th>Odesílatel</th><th>Role</th><th>P</th><th>Náhled</th><th>Akce</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "<p style='margin-top:14px'>"
        "<button type='submit' name='action' value='save_all_continue'>Pokračuj (uloží vše)</button>"
        "</p>"
        "</form>"
    )
    return HTMLResponse(_page(body))


@app.post("/triage/submit")
async def triage_submit(request: Request) -> RedirectResponse:
    form = await request.form()
    action = str(form.get("action", "")).strip()
    proposals = list_proposals()
    allowed_roles = set(load_roles().keys())

    if action == "save_all_continue":
        pending = [item for item in proposals if item.status == "pending"]
        for proposal in pending:
            _apply_row_changes(form, proposal, allowed_roles)
        save_proposals(proposals)
        return RedirectResponse(url="/triage?msg=Vse+ulozeno,+pokracuj", status_code=303)

    if ":" not in action:
        return RedirectResponse(url="/triage?msg=Neznamy+action", status_code=303)

    verb, proposal_id = action.split(":", 1)
    proposal = _find_proposal_by_id_prefix(proposals, proposal_id)
    if proposal is None:
        return RedirectResponse(url="/triage?msg=Proposal+nenalezen", status_code=303)

    if verb == "reject":
        approve_or_reject_proposal(proposal.id, ApproveProposalRequest(approve=False, auto_schedule_to_caldav=False))
        return RedirectResponse(url="/triage?msg=Polozka+odmitnuta", status_code=303)

    ok, error = _apply_row_changes(form, proposal, allowed_roles)
    if not ok:
        return RedirectResponse(url=f"/triage?msg={error}", status_code=303)
    save_proposals(proposals)

    if verb == "approve":
        approve_or_reject_proposal(proposal.id, ApproveProposalRequest(approve=True, planning_date=date.today()))
        return RedirectResponse(url="/triage?msg=Polozka+schvalena", status_code=303)

    return RedirectResponse(url="/triage?msg=Zmeny+ulozeny", status_code=303)


@app.post("/triage/continue")
def triage_continue() -> RedirectResponse:
    return RedirectResponse(url="/triage?msg=Pokracuj+-+kategorizace+je+hotova", status_code=303)


def _apply_row_changes(form, proposal, allowed_roles: set[str]) -> tuple[bool, str]:
    role_raw = str(form.get(f"role__{proposal.id}", proposal.role)).upper().strip()
    if role_raw not in allowed_roles:
        return False, "Neplatna+role"

    priority_raw = form.get(f"priority__{proposal.id}", proposal.priority)
    try:
        priority = int(priority_raw)
    except (TypeError, ValueError):
        return False, "Neplatna+priorita"

    proposal.role = role_raw
    proposal.priority = max(1, min(5, priority))
    record_feedback(
        proposal.sender,
        role=proposal.role,
        priority=proposal.priority,
        context_text=f"{proposal.subject} {proposal.source_excerpt}",
    )
    return True, "ok"


def _find_proposal_by_id_prefix(proposals, candidate: str):
    exact = next((item for item in proposals if item.id == candidate), None)
    if exact:
        return exact
    matches = [item for item in proposals if item.id.startswith(candidate)]
    if len(matches) == 1:
        return matches[0]
    return None


def _role_select_options(roles: list[str], selected: str) -> str:
    options: list[str] = []
    for role in roles:
        sel = " selected" if role == selected else ""
        options.append(f"<option value='{html.escape(role)}'{sel}>{html.escape(role)}</option>")
    return "".join(options)


def _page(body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Triage</title>"
        "<style>body{font-family:Arial,sans-serif;margin:20px}table{width:100%}th{text-align:left}</style>"
        "</head><body>"
        f"{body}"
        "</body></html>"
    )
