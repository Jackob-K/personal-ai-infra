from __future__ import annotations

import html
from contextlib import asynccontextmanager
from datetime import date
import json
from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.finance.categorizer import categorize_transactions, suggest_category
from app.finance.email_matcher import rematch_preview_rows
from app.finance.importer import extract_training_examples, parse_transactions
from app.finance.store import (
    load_month_snapshots,
    load_preview,
    load_training_examples,
    merge_training_examples,
    reset_month_categories,
    save_month_edits,
    save_month_snapshot,
    save_preview,
)
from app.finance.web import render_finance_page
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
from app.services.proposal_store import list_proposals, reset_discord_notification, save_proposals
from app.services.projects_store import add_subtask, create_project, list_projects, update_project_meta, update_subtask
from app.services.roles import load_roles
from app.services.sync_scheduler import start_sync_scheduler, stop_sync_scheduler
from app.services.sync_state import load_sync_state
from app.services.travel import estimate_travel


@asynccontextmanager
async def lifespan(_: FastAPI):
    start_sync_scheduler()
    try:
        yield
    finally:
        stop_sync_scheduler()


app = FastAPI(title="AI Server", version="0.5.0", lifespan=lifespan)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/web", status_code=303)


@app.get("/web", response_class=HTMLResponse)
def web_home() -> HTMLResponse:
    proposals = list_proposals()
    projects = list_projects()
    sync_state = load_sync_state()
    removed_pending = [p for p in proposals if p.status == "pending" and not _is_active_source(p)]
    counts = {
        "pending": len([p for p in proposals if p.status == "pending" and _is_active_source(p)]),
        "approved": len([p for p in proposals if p.status == "approved"]),
        "in_progress": len([p for p in proposals if p.status in {"in_progress", "submitted", "needs_revision"}]),
        "dispatched": len([p for p in proposals if p.status == "dispatched"]),
        "done": len([p for p in proposals if p.status == "done"]),
    }
    incoming_proposals = [
        p
        for p in proposals
        if p.status in {"pending", "approved", "dispatched"}
        and p.role != "ORCHESTRATOR"
        and (p.status != "pending" or _is_active_source(p))
    ]
    opened_proposals = [
        p for p in proposals if p.status in {"in_progress", "submitted", "needs_revision"} and p.role != "ORCHESTRATOR"
    ]
    incoming_proposals.sort(key=lambda p: (p.priority, p.created_at))
    opened_proposals.sort(key=lambda p: (p.priority, p.created_at))

    incoming_rows = "".join(
        [
            "<li style='margin-bottom:6px'>"
            f"<code>{html.escape(item.id[:8])}</code> "
            f"<b>{html.escape(item.role)}</b> "
            f"[{html.escape(item.status)} | P{item.priority}]<br>"
            f"<span style='color:#555'>{html.escape((item.sender or '')[:90])}</span><br>"
            f"{html.escape((item.subject or item.summary or '')[:110])}"
            "</li>"
            for item in incoming_proposals
        ]
    )
    opened_rows = "".join(
        [
            "<li style='margin-bottom:6px'>"
            f"<code>{html.escape(item.id[:8])}</code> "
            f"<b>{html.escape(item.role)}</b> "
            f"[{html.escape(item.status)} | P{item.priority}]<br>"
            f"<span style='color:#555'>{html.escape((item.sender or '')[:90])}</span><br>"
            f"{html.escape((item.subject or item.summary or '')[:110])}"
            "</li>"
            for item in opened_proposals
        ]
    )

    incoming_block = (
        "<p>Žádné nové úkoly.</p>"
        if not incoming_proposals
        else f"<ul style='padding-left:18px;margin-top:8px'>{incoming_rows}</ul>"
    )
    opened_block = (
        "<p>Žádné rozpracované úkoly.</p>"
        if not opened_proposals
        else f"<ul style='padding-left:18px;margin-top:8px'>{opened_rows}</ul>"
    )
    removed_block = (
        "<p>Žádné odložené návrhy.</p>"
        if not removed_pending
        else "<ul style='padding-left:18px;margin-top:8px'>"
        + "".join(
            [
                "<li style='margin-bottom:6px'>"
                f"<code>{html.escape(item.id[:8])}</code> "
                f"<b>{html.escape(item.role)}</b> [feedback]<br>"
                f"<span style='color:#555'>{html.escape((item.sender or '')[:90])}</span><br>"
                f"{html.escape((item.subject or item.summary or '')[:110])}"
                "</li>"
                for item in removed_pending
            ]
        )
        + "</ul>"
    )

    body = (
        "<h1>Home</h1>"
        "<div style='display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap'>"
        "<div style='flex:2;min-width:340px'>"
        "<p>Centrální přehled workflow.</p>"
        f"<p>Pending: <b>{counts['pending']}</b> | Approved: <b>{counts['approved']}</b> | "
        f"In progress: <b>{counts['in_progress']}</b> | Dispatched: <b>{counts['dispatched']}</b> | "
        f"Done: <b>{counts['done']}</b></p>"
        f"<p>Poslední sync: <b>{html.escape(sync_state.get('last_run_at', '') or 'nikdy')}</b> | "
        f"trigger: <b>{html.escape(sync_state.get('last_trigger', '') or '-')}</b> | "
        f"stav: <b>{html.escape(sync_state.get('last_status', '') or '-')}</b></p>"
        f"<p>Sync detail: emails={sync_state.get('last_emails_count', 0)}, "
        f"new={sync_state.get('last_proposals_created', 0)}, "
        f"updated={sync_state.get('last_proposals_updated', 0)}, "
        f"removed={sync_state.get('last_proposals_removed', 0)}</p>"
        "<ul>"
        "<li><a href='/triage'>Ingest + Triage</a></li>"
        "<li><a href='/finance'>Finance</a></li>"
        "<li><a href='/web/channels'>Jednotlivé kanály</a></li>"
        f"<li><a href='/web/projects'>Projekty</a> ({len(projects)})</li>"
        "<li><a href='/docs'>API Docs</a></li>"
        "</ul>"
        "</div>"
        "<div style='flex:1;min-width:320px;border:1px solid #ddd;border-radius:8px;padding:12px;max-height:70vh;overflow:auto'>"
        f"<h3 style='margin:0 0 8px 0'>Neotevřené ({len(incoming_proposals)})</h3>"
        f"{incoming_block}"
        "</div>"
        "<div style='flex:1;min-width:320px;border:1px solid #ddd;border-radius:8px;padding:12px;max-height:70vh;overflow:auto'>"
        f"<h3 style='margin:0 0 8px 0'>Rozpracované / čekající ({len(opened_proposals)})</h3>"
        f"{opened_block}"
        "</div>"
        "<div style='flex:1;min-width:320px;border:1px solid #ddd;border-radius:8px;padding:12px;max-height:70vh;overflow:auto'>"
        f"<h3 style='margin:0 0 8px 0'>Zmizelé ze zdroje ({len(removed_pending)})</h3>"
        f"{removed_block}"
        "</div>"
        "</div>"
    )
    return HTMLResponse(_page(body, active="home"))


@app.get("/web/channels", response_class=HTMLResponse)
def web_channels() -> HTMLResponse:
    channels = list_registry_channels()
    proposals = list_proposals()
    rows: list[str] = []
    for channel in channels:
        role = str(channel.get("role", ""))
        items = [
            p
            for p in proposals
            if p.role == role and p.status in {"approved", "in_progress", "submitted", "needs_revision", "dispatched"}
        ]
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
        source_note = "removed" if not _is_active_source(item) else "active"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(item.id[:8])}</code></td>"
            f"<td>{html.escape(item.status)}</td>"
            f"<td>P{item.priority}</td>"
            f"<td>{html.escape(item.sender or '')}</td>"
            f"<td>{html.escape(item.role)}</td>"
            f"<td>{html.escape(item.bundle_label or item.bundle_key or '')}</td>"
            f"<td>{html.escape(item.handling)} / {source_note}</td>"
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
        if proposal.role != role:
            proposal.role = role
            reset_discord_notification(proposal)
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

    # Keep linked proposal status in sync with subtask lifecycle so Home reflects reality.
    proposals = list_proposals()
    changed = False
    mapped_status = _proposal_status_from_subtask_status(status)
    subtask_title = ""
    project = next((p for p in list_projects() if p.id == project_id), None)
    if project:
        st = next((s for s in project.subtasks if s.id == subtask_id), None)
        if st:
            subtask_title = (st.title or "").strip().lower()

    for proposal in proposals:
        exact_link = proposal.project_id == project_id and proposal.subtask_id == subtask_id
        legacy_link = (
            proposal.project_id == project_id
            and not proposal.subtask_id
            and subtask_title
            and (proposal.subject or "").strip().lower() == subtask_title
        )
        if exact_link or legacy_link:
            if proposal.status != mapped_status or (legacy_link and not proposal.subtask_id):
                proposal.status = mapped_status
                if legacy_link and not proposal.subtask_id:
                    proposal.subtask_id = subtask_id
                changed = True
    if changed:
        save_proposals(proposals)

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
    ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=10), trigger="web")
    return RedirectResponse(url="/triage?msg=Ingest+hotov", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/finance", response_class=HTMLResponse)
def finance_page(msg: str | None = None, error: str | None = None, month: str | None = None) -> HTMLResponse:
    preview_rows = load_preview()
    snapshots = load_month_snapshots()
    preview_months = {_month_key(item) for item in preview_rows if _month_key(item)}
    available_months = sorted(set(preview_months) | set(snapshots.keys()), reverse=True)
    selected_month = month or (available_months[0] if available_months else "")
    month_rows = [item for item in preview_rows if _month_key(item) == selected_month]
    is_closed_month = False
    if not month_rows and selected_month in snapshots:
        month_rows = list(snapshots[selected_month].get("rows", []))
        is_closed_month = bool(snapshots[selected_month].get("closed"))
    category_options = _finance_category_options(preview_rows, snapshots)
    training_count = len(load_training_examples())
    body = render_finance_page(
        preview_rows=preview_rows,
        month_rows=month_rows,
        selected_month=selected_month,
        available_months=available_months,
        is_closed_month=is_closed_month,
        category_options=category_options,
        training_count=training_count,
        last_import_count=len(preview_rows),
        message=msg,
        error=error,
    )
    return HTMLResponse(_page(body, active="finance"))


@app.post("/finance/preview")
async def finance_preview(request: Request) -> RedirectResponse:
    form = await request.form()
    upload = form.get("statement")
    csv_text = str(form.get("csv_text", "")).strip()
    save_training = str(form.get("save_training", "")).strip() == "1"

    content = csv_text
    if upload and getattr(upload, "filename", ""):
        raw = await upload.read()
        content = raw.decode("utf-8-sig")

    if not content.strip():
        return RedirectResponse(url="/finance?error=Chybi+CSV+soubor+nebo+vlozeny+text", status_code=303)

    try:
        transactions = parse_transactions(content)
    except ValueError as exc:
        return RedirectResponse(url=f"/finance?error={quote_plus(str(exc))}", status_code=303)

    training_examples = load_training_examples()
    imported_examples = extract_training_examples(transactions) if save_training else []
    all_examples = training_examples + imported_examples
    categorized = categorize_transactions(transactions, all_examples)
    save_preview(categorized)

    added = 0
    if imported_examples:
        added = merge_training_examples(imported_examples)

    message = f"Nacteno+{len(transactions)}+transakci"
    if added:
        message += f",+ulozeno+{added}+novych+trenovacich+prikladu"
    return RedirectResponse(url=f"/finance?msg={quote_plus(message.replace('+', ' '))}", status_code=303)


@app.post("/finance/month/save")
async def finance_month_save(request: Request) -> RedirectResponse:
    form = await request.form()
    month_id = str(form.get("month_id", "")).strip()
    if not month_id:
        return RedirectResponse(url="/finance?error=Chybi+mesic", status_code=303)
    preview_rows = load_preview()
    snapshots = load_month_snapshots()
    month_rows = [item for item in preview_rows if _month_key(item) == month_id]
    if not month_rows and month_id in snapshots:
        month_rows = list(snapshots[month_id].get("rows", []))
    updates: dict[str, dict[str, str]] = {}
    payload_json = str(form.get("payload_json", "")).strip()
    if payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return RedirectResponse(url=f"/finance?month={quote_plus(month_id)}&error=Neplatny+payload+z+tabulky", status_code=303)
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                transaction_id = str(item.get("transaction_id", "")).strip()
                if not transaction_id:
                    continue
                updates[transaction_id] = {
                    "description": str(item.get("description", "")).strip(),
                    "selected_category": str(item.get("selected_category", "")).strip(),
                }
    if not updates:
        row_keys = [str(item).strip() for item in form.getlist("row_key") if str(item).strip()]
        for row_key in row_keys:
            if not row_key:
                continue
            matching_item = next(
                (
                    item for item in month_rows
                    if (str(item.get("transaction_id", "")).strip() or f"row-{str(item.get('source_row', '')).strip()}") == row_key
                ),
                {},
            )
            updates[row_key] = {
                "description": str(form.get(f"description__{row_key}", matching_item.get("description", ""))).strip(),
                "selected_category": str(
                    form.get(
                        f"selected_category__{row_key}",
                        matching_item.get("selected_category", "") or (matching_item.get("suggestion") or {}).get("category", "") or matching_item.get("raw_category", ""),
                    )
                ).strip(),
                "entry_type": str(form.get(f"entry_type__{row_key}", matching_item.get("entry_type", "standard"))).strip(),
                "personal_amount": str(form.get(f"personal_amount__{row_key}", matching_item.get("personal_amount", matching_item.get("amount", 0)))).strip(),
                "effective_month": str(form.get(f"effective_month__{row_key}", matching_item.get("effective_month", _month_key(matching_item)))).strip(),
                "related_party": str(form.get(f"related_party__{row_key}", matching_item.get("related_party", ""))).strip(),
            }
    if not updates:
        return RedirectResponse(url=f"/finance?month={quote_plus(month_id)}&error=Z+formulare+se+neodeslaly+zadne+zmeny", status_code=303)
    changed = save_month_edits(month_id, updates)
    return RedirectResponse(
        url=f"/finance?month={quote_plus(month_id)}&msg={quote_plus(f'Ulozeno zmen: {changed}')}",
        status_code=303,
    )


@app.post("/finance/month/reset-categories")
async def finance_month_reset_categories(request: Request) -> RedirectResponse:
    form = await request.form()
    month_id = str(form.get("month_id", "")).strip()
    if not month_id:
        return RedirectResponse(url="/finance?error=Chybi+mesic", status_code=303)
    changed = reset_month_categories(month_id)
    return RedirectResponse(
        url=f"/finance?month={quote_plus(month_id)}&msg={quote_plus(f'Obnoveno kategorii: {changed}')}",
        status_code=303,
    )


@app.post("/finance/rematch")
def finance_rematch() -> RedirectResponse:
    preview_rows = load_preview()
    if not preview_rows:
        return RedirectResponse(url="/finance?error=Neni+co+prepocitat", status_code=303)
    refreshed = rematch_preview_rows(preview_rows)
    training_examples = load_training_examples()
    for item in refreshed:
        item.suggestion = suggest_category(item.transaction, training_examples)
    save_preview(refreshed)
    matched = sum(1 for item in refreshed if item.email_match_status == "matched")
    return RedirectResponse(
        url=f"/finance?msg={quote_plus(f'Prepocitano {len(refreshed)} transakci, naparovano {matched} emailu')}",
        status_code=303,
    )


@app.post("/finance/close-month")
async def finance_close_month(request: Request) -> RedirectResponse:
    form = await request.form()
    month_id = str(form.get("month_id", "")).strip()
    preview_rows = load_preview()
    if not month_id:
        return RedirectResponse(url="/finance?error=Chybi+mesic+k+uzavreni", status_code=303)
    month_rows = [item for item in preview_rows if _month_key(item) == month_id]
    if not month_rows:
        return RedirectResponse(url=f"/finance?month={quote_plus(month_id)}&error=Pro+mesic+nejsou+zadna+data", status_code=303)
    save_month_snapshot(month_id, month_rows)
    return RedirectResponse(url=f"/finance?month={quote_plus(month_id)}&msg=Mesic+uzavren", status_code=303)


@app.post("/classify-email", response_model=ClassifyEmailResponse)
def classify_email_endpoint(payload: EmailClassifyRequest) -> ClassifyEmailResponse:
    return classify_email(payload)


@app.post("/plan-task", response_model=PlanTaskResponse)
def plan_task_endpoint(payload: PlanTaskRequest) -> PlanTaskResponse:
    return plan_task_slot(payload)


@app.post("/imap/ingest", response_model=IngestImapResponse)
def ingest_imap_endpoint(payload: IngestImapRequest) -> IngestImapResponse:
    return ingest_and_create_proposals(payload, trigger="api")


@app.get("/proposals/pending", response_model=ProposalListResponse)
def pending_proposals_endpoint() -> ProposalListResponse:
    pending = [item for item in list_proposals() if item.status == "pending" and _is_active_source(item)]
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
    proposals = list_proposals()
    pending = [item for item in proposals if item.status == "pending" and _is_active_source(item)]
    removed_pending = [item for item in proposals if item.status == "pending" and not _is_active_source(item)]
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

    body = "".join(
        [
            "<h1>Triage Inbox</h1>",
            notice,
            "<form method='post' action='/web/ingest'><button type='submit'>Spustit ingest</button></form>",
            "<p>Uprav roli/prioritu a potvrď. SPAM/PHISHING/NEWSLETTER se po schválení neplánují do kalendáře.</p>",
            "<form method='post' action='/triage/submit'>",
            "<table border='1' cellpadding='6' cellspacing='0'>",
            "<thead><tr><th>ID</th><th>Odesílatel</th><th>Role</th><th>P</th><th>Náhled</th><th>Akce</th></tr></thead>",
            "<tbody>",
            "".join(rows),
            "</tbody></table>",
            (
                ""
                if not removed_pending
                else "<h3 style='margin-top:18px'>Odstraněné ze schránky, ponechané pro feedback</h3><ul>"
                + "".join(
                    [
                        "<li>"
                        f"<code>{html.escape(item.id[:8])}</code> {html.escape(item.role)}: "
                        f"{html.escape((item.subject or item.summary or '')[:120])}"
                        "</li>"
                        for item in removed_pending[:20]
                    ]
                )
                + "</ul>"
            ),
            "<p style='margin-top:14px'>",
            "<button type='submit' name='action' value='save_all_continue'>Pokračuj (uloží vše)</button>",
            " ",
            "<button type='submit' name='action' value='save_all_approve'>Uložit + Schválit vše</button>",
            "</p>",
            "</form>",
        ]
    )
    return HTMLResponse(_page(body, active="triage"))


@app.post("/triage/submit")
async def triage_submit(request: Request) -> RedirectResponse:
    form = await request.form()
    action = str(form.get("action", "")).strip()
    proposals = list_proposals()
    allowed_roles = set(load_roles().keys())

    if action == "save_all_continue":
        pending = [item for item in proposals if item.status == "pending" and _is_active_source(item)]
        for proposal in pending:
            _apply_row_changes(form, proposal, allowed_roles)
        save_proposals(proposals)
        return RedirectResponse(url="/triage?msg=Vse+ulozeno,+pokracuj", status_code=303)

    if action == "save_all_approve":
        pending = [item for item in proposals if item.status == "pending" and _is_active_source(item)]
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

    if proposal.role != role_raw:
        proposal.role = role_raw
        reset_discord_notification(proposal)
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


def _proposal_status_from_subtask_status(subtask_status: str) -> str:
    mapping = {
        "todo": "approved",
        "in_progress": "in_progress",
        "submitted": "submitted",
        "needs_revision": "needs_revision",
        "done": "done",
    }
    return mapping.get(subtask_status, "in_progress")


def _page(body: str, active: str = "home") -> str:
    nav = (
        "<nav style='margin-bottom:14px'>"
        f"<a href='/web' style='margin-right:12px;{_tab_style(active == 'home')}'>Home</a>"
        f"<a href='/triage' style='margin-right:12px;{_tab_style(active == 'triage')}'>Ingest + Triage</a>"
        f"<a href='/finance' style='margin-right:12px;{_tab_style(active == 'finance')}'>Finance</a>"
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


def _is_active_source(proposal) -> bool:
    return getattr(proposal, "source_status", "active") == "active"


def _month_key(item: dict) -> str:
    booking_date = str(item.get("booking_date", "")).strip()
    return booking_date[:7] if len(booking_date) >= 7 else ""


def _finance_category_options(preview_rows: list[dict], snapshots: dict[str, dict]) -> list[str]:
    categories: set[str] = {
        "Auto – provoz, opravy",
        "Bydlení",
        "Dárky",
        "Investování",
        "Já",
        "Kapesné",
        "Obědy",
        "Potraviny",
        "Restaurace",
        "Telefon a internet",
        "Výplata",
        "Webové služby",
        "Výlety",
        "Škola, univerzita",
        "Nezařazeno",
    }
    for row in preview_rows:
        for key in ("selected_category", "raw_category"):
            value = str(row.get(key, "")).strip()
            if value:
                categories.add(value)
        suggestion = row.get("suggestion") or {}
        value = str(suggestion.get("category", "")).strip()
        if value:
            categories.add(value)
    for snapshot in snapshots.values():
        for row in snapshot.get("rows", []):
            for key in ("selected_category", "raw_category"):
                value = str(row.get(key, "")).strip()
                if value:
                    categories.add(value)
    return sorted(categories)
