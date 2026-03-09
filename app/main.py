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
from app.services.agent_registry import list_registry_channels
from app.services.classifier import classify_email
from app.services.feedback import record_feedback
from app.services.imap_accounts import load_imap_accounts
from app.services.orchestrator import update_proposal_status
from app.services.planner import plan_task_slot
from app.services.proposal_store import list_proposals, save_proposals
from app.services.projects_store import add_subtask, create_project, list_projects, update_project_meta, update_subtask
from app.services.roles import load_roles
from app.services.travel import estimate_travel


app = FastAPI(title="AI Server", version="0.5.0")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/web", status_code=303)


@app.get("/web", response_class=HTMLResponse)
def web_home() -> HTMLResponse:
    proposals = list_proposals()
    projects = list_projects()
    counts = {
        "pending": len([p for p in proposals if p.status == "pending"]),
        "approved": len([p for p in proposals if p.status == "approved"]),
        "in_progress": len([p for p in proposals if p.status == "in_progress"]),
        "dispatched": len([p for p in proposals if p.status == "dispatched"]),
        "done": len([p for p in proposals if p.status == "done"]),
    }
    body = (
        "<h1>Home</h1>"
        "<p>Centrální přehled workflow.</p>"
        f"<p>Pending: <b>{counts['pending']}</b> | Approved: <b>{counts['approved']}</b> | "
        f"In progress: <b>{counts['in_progress']}</b> | Dispatched: <b>{counts['dispatched']}</b> | "
        f"Done: <b>{counts['done']}</b></p>"
        "<ul>"
        "<li><a href='/triage'>Ingest + Triage</a></li>"
        "<li><a href='/web/channels'>Jednotlivé kanály</a></li>"
        f"<li><a href='/web/projects'>Projekty</a> ({len(projects)})</li>"
        "<li><a href='/docs'>API Docs</a></li>"
        "</ul>"
    )
    return HTMLResponse(_page(body, active="home"))


@app.get("/web/channels", response_class=HTMLResponse)
def web_channels() -> HTMLResponse:
    channels = list_registry_channels()
    proposals = list_proposals()
    rows: list[str] = []
    for channel in channels:
        role = str(channel.get("role", ""))
        items = [p for p in proposals if p.role == role and p.status in {"approved", "in_progress", "dispatched"}]
        channel_name = html.escape(str(channel.get("channel_name", "")))
        rows.append(
            "<tr>"
            f"<td><a href='/web/channel/{channel_name}'>{channel_name}</a></td>"
            f"<td>{html.escape(role)}</td>"
            f"<td>{len(items)}</td>"
            "</tr>"
        )
    body = (
        "<h1>Jednotlivé Kanály</h1>"
        "<p>Přehled front podle kanálu/role.</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Kanál</th><th>Role</th><th>Aktivní úkoly</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return HTMLResponse(_page(body, active="channels"))


@app.get("/web/channel/{channel_name}", response_class=HTMLResponse)
def web_channel_detail(channel_name: str, msg: str | None = None) -> HTMLResponse:
    channels = list_registry_channels()
    channel = next((c for c in channels if str(c.get("channel_name", "")).lower() == channel_name.lower()), None)
    if channel is None:
        return HTMLResponse(_page(f"<h1>Kanál nenalezen</h1><p>{html.escape(channel_name)}</p>", active="channels"))

    role = str(channel.get("role", "")).upper().strip()
    items = [p for p in list_proposals() if p.role == role and p.status != "rejected"]
    projects = [p for p in list_projects() if p.role == role]
    roles = sorted(load_roles().keys()) + ["NEWSLETTER", "SPAM", "PHISHING"]
    handling_modes = ["review", "process", "needs_attention", "calendar"]
    notice = f"<p style='color:#0b5'>{html.escape(msg)}</p>" if msg else ""

    rows: list[str] = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(item.id[:8])}</code></td>"
            f"<td>{html.escape(item.status)}</td>"
            f"<td>P{item.priority}</td>"
            f"<td>{html.escape(item.sender or '')}</td>"
            f"<td>{html.escape(item.role)}</td>"
            f"<td>{html.escape(item.bundle_label or item.bundle_key or '')}</td>"
            f"<td>{html.escape(item.handling)}</td>"
            f"<td>{html.escape(_project_name(projects, item.project_id))}</td>"
            f"<td>{html.escape(item.task_group or '')}</td>"
            f"<td>{html.escape((item.subject or item.summary or '')[:120])}</td>"
            f"<td>{html.escape(item.comments[-1] if item.comments else '')}</td>"
            "<td>"
            "<form method='post' action='/web/task-update' style='display:inline'>"
            f"<input type='hidden' name='proposal_id' value='{html.escape(item.id)}'>"
            f"<input type='hidden' name='channel_name' value='{html.escape(channel_name)}'>"
            f"<select name='role'>{_role_select_options(roles, item.role)}</select> "
            f"<select name='handling'>{_mode_select_options(handling_modes, item.handling)}</select> "
            f"<select name='project_id'>{_project_select_options(projects, item.project_id)}</select> "
            "<input type='text' name='new_project_name' placeholder='Nový projekt' style='width:130px'> "
            "<input type='text' name='subtask_title' placeholder='Nový subtask' style='width:130px'> "
            f"<input type='text' name='task_group' placeholder='Skupina' value='{html.escape(item.task_group or '')}' style='width:140px'> "
            "<input type='text' name='comment' placeholder='Komentář' style='width:220px'> "
            "<select name='status'>"
            "<option value='keep'>Stav beze změny</option>"
            "<option value='in_progress'>Rozpracováno</option>"
            "<option value='done'>Hotovo</option>"
            "</select> "
            "<button type='submit'>Uložit</button>"
            "</form>"
            "</td>"
            "</tr>"
        )

    body = (
        f"<h1>Kanál: {html.escape(channel_name)}</h1>"
        f"<p>Role: <b>{html.escape(role)}</b></p>"
        f"{notice}"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>ID</th><th>Stav</th><th>Priorita</th><th>Odesílatel</th><th>Role</th><th>Bundle</th><th>Handling</th><th>Projekt</th><th>Skupina</th><th>Náhled</th><th>Poslední komentář</th><th>Akce</th></tr></thead>"
        "<tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='12'>Žádné položky</td></tr>")
        + "</tbody></table>"
    )
    return HTMLResponse(_page(body, active="channels"))


@app.post("/web/task-update")
async def web_task_update(request: Request) -> RedirectResponse:
    form = await request.form()
    proposal_id = str(form.get("proposal_id", "")).strip()
    channel_name = str(form.get("channel_name", "")).strip()
    status = str(form.get("status", "keep")).strip()
    role = str(form.get("role", "")).strip().upper()
    handling = str(form.get("handling", "")).strip()
    project_id = str(form.get("project_id", "")).strip()
    new_project_name = str(form.get("new_project_name", "")).strip()
    subtask_title = str(form.get("subtask_title", "")).strip()
    task_group = str(form.get("task_group", "")).strip()
    comment = str(form.get("comment", "")).strip()
    allowed_roles = set(load_roles().keys()) | {"NEWSLETTER", "SPAM", "PHISHING"}
    allowed_handling = {"review", "process", "needs_attention", "calendar"}

    proposals = list_proposals()
    proposal = _find_proposal_by_id_prefix(proposals, proposal_id)
    if proposal is None:
        return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Proposal+nenalezen", status_code=303)

    if role:
        if role not in allowed_roles:
            return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Neplatna+role", status_code=303)
        proposal.role = role
    if handling:
        if handling not in allowed_handling:
            return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Neplatny+handling", status_code=303)
        proposal.handling = handling
    if new_project_name:
        project = create_project(new_project_name, proposal.role)
        proposal.project_id = project.id
        project_id = project.id
    elif project_id == "__none__":
        proposal.project_id = None
        proposal.subtask_id = None
    elif project_id:
        known_project = next((p for p in list_projects() if p.id == project_id), None)
        if known_project is None:
            return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Project+nenalezen", status_code=303)
        proposal.project_id = project_id
    if subtask_title:
        if not proposal.project_id:
            return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Nejdriv+vyber+projekt", status_code=303)
        subtask = add_subtask(proposal.project_id, subtask_title, priority=proposal.priority)
        proposal.subtask_id = subtask.id
    if task_group:
        proposal.task_group = task_group[:120]
    if comment:
        proposal.comments.append(comment[:500])
    if status in {"in_progress", "done"}:
        proposal.status = status
    elif status != "keep":
        return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Neznamy+status", status_code=303)

    save_proposals(proposals)
    return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Zmena+ulozena", status_code=303)


@app.get("/web/projects", response_class=HTMLResponse)
def web_projects() -> HTMLResponse:
    projects = list_projects()
    proposals = list_proposals()
    rows: list[str] = []
    for project in projects:
        linked_count = len([p for p in proposals if p.project_id == project.id and p.status != "rejected"])
        deadline = project.deadline.isoformat() if project.deadline else "-"
        rows.append(
            "<tr>"
            f"<td><a href='/web/project/{html.escape(project.id)}'>{html.escape(project.name)}</a></td>"
            f"<td>{html.escape(project.role)}</td>"
            f"<td>{html.escape(project.status)}</td>"
            f"<td>{deadline}</td>"
            f"<td>{linked_count}</td>"
            f"<td>{len(project.subtasks)}</td>"
            "</tr>"
        )
    body = (
        "<h1>Projekty</h1>"
        "<p>Dlouhodobé linky nad emaily (bundle/project/subtask).</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Název</th><th>Role</th><th>Stav</th><th>Deadline</th><th>Napojené emaily</th><th>Subtasky</th></tr></thead>"
        "<tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='6'>Žádné projekty</td></tr>")
        + "</tbody></table>"
    )
    return HTMLResponse(_page(body, active="projects"))


@app.get("/web/project/{project_id}", response_class=HTMLResponse)
def web_project_detail(project_id: str, msg: str | None = None) -> HTMLResponse:
    projects = list_projects()
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        return HTMLResponse(_page("<h1>Projekt nenalezen</h1>", active="projects"))
    linked = [p for p in list_proposals() if p.project_id == project.id and p.status != "rejected"]
    notice = f"<p style='color:#0b5'>{html.escape(msg)}</p>" if msg else ""

    task_rows = "".join(
        [
            "<tr>"
            f"<td>{html.escape(t.title)}</td>"
            "<td>"
            "<form method='post' action='/web/subtask-update' style='display:inline'>"
            f"<input type='hidden' name='project_id' value='{html.escape(project.id)}'>"
            f"<input type='hidden' name='subtask_id' value='{html.escape(t.id)}'>"
            f"<select name='status'>{_subtask_status_options(t.status)}</select> "
            "<input type='text' name='note' placeholder='Poznámka (volitelná)' style='width:180px'> "
            "<button type='submit'>Uložit</button>"
            "</form>"
            "</td>"
            f"<td>P{t.priority}</td>"
            "</tr>"
            for t in project.subtasks
        ]
    ) or "<tr><td colspan='3'>Žádné subtasky</td></tr>"

    email_rows = "".join(
        [
            "<tr>"
            f"<td><code>{html.escape(p.id[:8])}</code></td>"
            f"<td>{html.escape(p.sender)}</td>"
            f"<td>{html.escape(p.subject or p.summary)}</td>"
            f"<td>{html.escape(p.status)}</td>"
            "</tr>"
            for p in linked[:100]
        ]
    ) or "<tr><td colspan='4'>Žádné emaily</td></tr>"

    body = (
        f"<h1>Projekt: {html.escape(project.name)}</h1>"
        f"{notice}"
        f"<p>Role: <b>{html.escape(project.role)}</b> | Stav: <b>{html.escape(project.status)}</b> | Deadline: <b>{html.escape(project.deadline.isoformat() if project.deadline else '-')}</b></p>"
        "<form method='post' action='/web/project-update' style='margin-bottom:14px'>"
        f"<input type='hidden' name='project_id' value='{html.escape(project.id)}'>"
        "<select name='status'>"
        + _project_status_options(project.status)
        + "</select> "
        f"<input type='date' name='deadline' value='{html.escape(project.deadline.isoformat() if project.deadline else '')}'> "
        "<button type='submit'>Uložit projekt</button>"
        "</form>"
        "<h2>Subtasky</h2>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>Název</th><th>Stav</th><th>Priorita</th></tr></thead>"
        f"<tbody>{task_rows}</tbody></table>"
        "<h2>Napojené emaily</h2>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>ID</th><th>Odesílatel</th><th>Předmět</th><th>Stav</th></tr></thead>"
        f"<tbody>{email_rows}</tbody></table>"
    )
    return HTMLResponse(_page(body, active="projects"))


@app.post("/web/project-update")
async def web_project_update(request: Request) -> RedirectResponse:
    form = await request.form()
    project_id = str(form.get("project_id", "")).strip()
    status = str(form.get("status", "")).strip()
    deadline_raw = str(form.get("deadline", "")).strip()
    allowed = {"open", "blocked", "waiting", "done"}
    if status not in allowed:
        return RedirectResponse(url=f"/web/project/{project_id}?msg=Neplatny+status", status_code=303)

    deadline = None
    if deadline_raw:
        try:
            deadline = date.fromisoformat(deadline_raw)
        except ValueError:
            return RedirectResponse(url=f"/web/project/{project_id}?msg=Neplatny+deadline", status_code=303)
    try:
        update_project_meta(project_id, status=status, deadline=deadline)
    except ValueError:
        return RedirectResponse(url="/web/projects?msg=Project+nenalezen", status_code=303)
    return RedirectResponse(url=f"/web/project/{project_id}?msg=Projekt+ulozen", status_code=303)


@app.post("/web/subtask-update")
async def web_subtask_update(request: Request) -> RedirectResponse:
    form = await request.form()
    project_id = str(form.get("project_id", "")).strip()
    subtask_id = str(form.get("subtask_id", "")).strip()
    status = str(form.get("status", "")).strip()
    note = str(form.get("note", "")).strip()
    try:
        update_subtask(project_id=project_id, subtask_id=subtask_id, status=status, note=note or None)
    except ValueError as exc:
        return RedirectResponse(url=f"/web/project/{project_id}?msg={html.escape(str(exc))}", status_code=303)
    return RedirectResponse(url=f"/web/project/{project_id}?msg=Subtask+ulozen", status_code=303)


@app.post("/web/task-status")
async def web_task_status(request: Request) -> RedirectResponse:
    form = await request.form()
    proposal_id = str(form.get("proposal_id", "")).strip()
    channel_name = str(form.get("channel_name", "")).strip()
    status = str(form.get("status", "")).strip()
    if status not in {"in_progress", "done"}:
        return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Neznamy+status", status_code=303)
    try:
        update_proposal_status(proposal_id, status)
    except ValueError:
        return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Proposal+nenalezen", status_code=303)
    return RedirectResponse(url=f"/web/channel/{channel_name}?msg=Stav+ulozen", status_code=303)


@app.post("/web/ingest")
def web_ingest() -> RedirectResponse:
    accounts = load_imap_accounts()
    ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=10))
    return RedirectResponse(url="/triage?msg=Ingest+hotov", status_code=303)


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
        return HTMLResponse(_page(body, active="triage"))

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
        "<form method='post' action='/web/ingest'><button type='submit'>Spustit ingest</button></form>"
        "<p>Uprav roli/prioritu a potvrď. SPAM/PHISHING/NEWSLETTER se po schválení neplánují do kalendáře.</p>"
        "<form method='post' action='/triage/submit'>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>ID</th><th>Odesílatel</th><th>Role</th><th>P</th><th>Náhled</th><th>Akce</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "<p style='margin-top:14px'>"
        "<button type='submit' name='action' value='save_all_continue'>Pokračuj (uloží vše)</button>"
        " "
        "<button type='submit' name='action' value='save_all_approve'>Uložit + Schválit vše</button>"
        "</p>"
        "</form>"
    )
    return HTMLResponse(_page(body, active="triage"))


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

    if action == "save_all_approve":
        pending = [item for item in proposals if item.status == "pending"]
        for proposal in pending:
            ok, error = _apply_row_changes(form, proposal, allowed_roles)
            if not ok:
                return RedirectResponse(url=f"/triage?msg={error}", status_code=303)
        save_proposals(proposals)
        for proposal in pending:
            approve_or_reject_proposal(
                proposal.id,
                ApproveProposalRequest(approve=True, planning_date=date.today(), auto_schedule_to_caldav=False),
            )
        return RedirectResponse(url="/triage?msg=Vse+ulozeno+a+schvaleno", status_code=303)

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
        approve_or_reject_proposal(
            proposal.id,
            ApproveProposalRequest(approve=True, planning_date=date.today(), auto_schedule_to_caldav=False),
        )
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


def _mode_select_options(modes: list[str], selected: str) -> str:
    options: list[str] = []
    for mode in modes:
        sel = " selected" if mode == selected else ""
        options.append(f"<option value='{html.escape(mode)}'{sel}>{html.escape(mode)}</option>")
    return "".join(options)


def _project_select_options(projects, selected: str | None) -> str:
    options = ["<option value='__none__'>Bez projektu</option>"]
    for project in projects:
        sel = " selected" if project.id == selected else ""
        options.append(f"<option value='{html.escape(project.id)}'{sel}>{html.escape(project.name)}</option>")
    return "".join(options)


def _project_name(projects, project_id: str | None) -> str:
    if not project_id:
        return "-"
    project = next((p for p in projects if p.id == project_id), None)
    if project is None:
        return "?"
    return project.name


def _project_status_options(selected: str) -> str:
    values = ["open", "blocked", "waiting", "done"]
    options: list[str] = []
    for value in values:
        sel = " selected" if value == selected else ""
        options.append(f"<option value='{value}'{sel}>{value}</option>")
    return "".join(options)


def _subtask_status_options(selected: str) -> str:
    values = ["todo", "in_progress", "submitted", "needs_revision", "done"]
    options: list[str] = []
    for value in values:
        sel = " selected" if value == selected else ""
        options.append(f"<option value='{value}'{sel}>{value}</option>")
    return "".join(options)


def _page(body: str, active: str = "home") -> str:
    nav = (
        "<nav style='margin-bottom:14px'>"
        f"<a href='/web' style='margin-right:12px;{_tab_style(active == 'home')}'>Home</a>"
        f"<a href='/triage' style='margin-right:12px;{_tab_style(active == 'triage')}'>Ingest + Triage</a>"
        f"<a href='/web/channels' style='margin-right:12px;{_tab_style(active == 'channels')}'>Jednotlivé kanály</a>"
        f"<a href='/web/projects' style='{_tab_style(active == 'projects')}'>Projekty</a>"
        "</nav>"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Triage</title>"
        "<style>body{font-family:Arial,sans-serif;margin:20px}table{width:100%}th{text-align:left}</style>"
        "</head><body>"
        f"{nav}"
        f"{body}"
        "</body></html>"
    )


def _tab_style(is_active: bool) -> str:
    return "font-weight:bold;text-decoration:underline;" if is_active else "text-decoration:none;"
