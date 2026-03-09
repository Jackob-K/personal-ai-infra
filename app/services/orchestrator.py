from __future__ import annotations

import os
from datetime import date
from datetime import datetime
from uuid import uuid4

from app.models import ApproveProposalRequest, IngestImapRequest, IngestImapResponse, TaskProposal
from app.services.agent_registry import find_channel_agent, find_role_channel
from app.services.assistant_flow import approve_or_reject_proposal, ingest_and_create_proposals
from app.services.channel_memory import append_message, get_recent_messages
from app.services.feedback import record_feedback
from app.services.imap_accounts import load_imap_accounts
from app.services.proposal_store import delete_proposal, list_proposals, save_proposals
from app.services.projects_store import add_subtask, create_project, list_projects, remove_subtask
from app.services.roles import get_role_config, load_roles


ROLE_ALIASES = {
    "STARTUP": "TOKVEKO",
    "SKOLA": "UNIVERZITA",
    "FIRMA_ZAMESTNANI": "KLIMATIKA",
}

NON_CALENDAR_ROLES = {"SPAM", "PHISHING", "NEWSLETTER"}


HELP_TEXT = """Dostupné příkazy:
- help
- triage
- pending
- ingest
- dispatch
- pokracuj
- start <proposal_id>
- done <proposal_id>
- delete <proposal_id>
- set-group <proposal_id> <GROUP>
- comment <proposal_id> <TEXT>
- set-role <proposal_id> <ROLE>
- set-priority <proposal_id> <1-5>
- mark-newsletter <proposal_id>
- mark-spam <proposal_id>
- mark-phishing <proposal_id>
- approve <proposal_id> [YYYY-MM-DD]
- reject <proposal_id>

V tematických kanálech můžeš psát přirozeně. Agent odpoví v rámci své role."""


def handle_discord_message(channel_name: str, author_name: str, content: str) -> str:
    clean = content.strip()
    append_message(channel_name, author_name, clean)

    agent = find_channel_agent(channel_name)
    if agent is None:
        return (
            f"Kanál '{channel_name}' není namapovaný na žádného asistenta. "
            "Doplň ho do data/runtime/discord_agents.json."
        )

    if agent.get("agent") == "ORCHESTRATOR":
        return _handle_orchestrator(clean)

    return _handle_specialist(channel_name, agent.get("role", "OSOBNI"), author_name, clean)


def _handle_orchestrator(content: str) -> str:
    lower = content.lower()
    cmd = _extract_command(lower)

    if cmd == "help":
        return HELP_TEXT
    if cmd == "triage":
        return _format_triage()
    if cmd == "pending":
        return _format_pending()
    if cmd == "ingest":
        accounts = load_imap_accounts()
        result = ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=10))
        return _format_ingest_result(result)
    if cmd == "dispatch":
        return _dispatch_hint()
    if cmd in {"pokracuj", "pokračuj", "continue"}:
        return "Pokračuji dalším krokem. Pro detailní úpravy otevři web triage."
    if cmd == "start":
        return _status_command(content, target_status="in_progress")
    if cmd == "done":
        return _status_command(content, target_status="done")
    if cmd == "delete":
        return _delete_command(content)
    if cmd == "set-group":
        return _set_group_command(content)
    if cmd == "comment":
        return _comment_command(content)
    if cmd == "set-role":
        return _set_role_command(content)
    if cmd == "set-priority":
        return _set_priority_command(content)
    if cmd == "mark-newsletter":
        return _set_role_shortcut(content, "NEWSLETTER")
    if cmd == "mark-spam":
        return _set_role_shortcut(content, "SPAM")
    if cmd == "mark-phishing":
        return _set_role_shortcut(content, "PHISHING")
    if cmd == "approve":
        return _approve_command(content)
    if cmd == "reject":
        return _reject_command(content)
    return HELP_TEXT


def _handle_specialist(channel_name: str, role: str, author_name: str, content: str) -> str:
    recent = get_recent_messages(channel_name)
    recent_count = max(0, len(recent) - 1)
    prefix = _role_prefix(role)
    cmd = _extract_command(content.lower())

    if cmd in {"help", "napoveda", "nápověda"}:
        return (
            f"{prefix}\n\n"
            "Specifické příkazy kanálu:\n"
            "- project <název projektu>\n"
            "- task <popis úkolu>\n"
            "- delete <proposal_id>\n"
            "- pending\n\n"
            "Když napíšeš běžnou větu, vytvořím z ní rychlý úkol v tomto kanálu."
        )

    if "pending" in content.lower():
        return f"{prefix}\n\n{_format_pending(role_filter=role)}"

    if cmd == "project":
        project_name = content.split(maxsplit=1)[1].strip() if len(content.split(maxsplit=1)) > 1 else ""
        if not project_name:
            return "Použití: project <název projektu>"
        project = create_project(project_name, _normalize_role(role))
        return (
            f"{prefix}\n\n"
            f"Projekt založen: `{project.name}` ({project.id[:8]}). "
            "Další úkoly můžeš posílat přes `task ...` nebo běžnou větu."
        )

    if cmd == "task":
        text = content.split(maxsplit=1)[1].strip() if len(content.split(maxsplit=1)) > 1 else ""
        if not text:
            return "Použití: task <popis úkolu>"
        created = _create_manual_specialist_task(role, author_name, channel_name, text)
        return (
            f"{prefix}\n\n"
            f"Úkol vytvořen: `{created.id[:8]}` | {created.role} | P{created.priority}\n"
            f"Náhled: {created.subject}"
        )

    if cmd == "delete":
        return _delete_command(content)

    created = _create_manual_specialist_task(role, author_name, channel_name, content)
    return (
        f"{prefix}\n\n"
        f"Založil jsem rychlý úkol `{created.id[:8]}` v roli `{created.role}`. "
        f"Kontext kanálu: {recent_count} zpráv. "
        "Když chceš explicitní projekt, použij `project <název>`."
    )


def _approve_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        return "Použití: approve <proposal_id> [YYYY-MM-DD]"
    proposal_id = _resolve_proposal_id(parts[1])
    planning_date = None
    if len(parts) > 2:
        try:
            planning_date = date.fromisoformat(parts[2])
        except ValueError:
            return "Neplatný datum formát. Použij YYYY-MM-DD."

    try:
        proposal = approve_or_reject_proposal(
            proposal_id,
            ApproveProposalRequest(approve=True, planning_date=planning_date, auto_schedule_to_caldav=False),
        )
    except ValueError as exc:
        return str(exc)

    if proposal.planned_start and proposal.planned_end:
        return (
            f"Návrh {proposal.id} schválen. "
            f"Naplánováno {proposal.planned_start.isoformat()} -> {proposal.planned_end.isoformat()}."
        )
    if proposal.role in NON_CALENDAR_ROLES:
        return f"Návrh {proposal.id} schválen jako {proposal.role}. Nebyl plánován do kalendáře."
    return f"Návrh {proposal.id} schválen. Plánování je v tomto režimu manuální."


def _reject_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        return "Použití: reject <proposal_id>"
    proposal_id = _resolve_proposal_id(parts[1])
    try:
        proposal = approve_or_reject_proposal(
            proposal_id,
            ApproveProposalRequest(approve=False, auto_schedule_to_caldav=False),
        )
    except ValueError as exc:
        return str(exc)
    return f"Návrh {proposal.id} byl odmítnut."


def _delete_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        return "Použití: delete <proposal_id>"
    try:
        proposal_id = _resolve_proposal_id(parts[1])
    except ValueError as exc:
        return str(exc)
    proposal = next((item for item in list_proposals() if item.id == proposal_id), None)
    if proposal is None:
        return f"Proposal '{proposal_id}' not found"
    deleted = delete_proposal(proposal_id)
    if deleted is None:
        return f"Proposal '{proposal_id}' not found"
    remove_subtask(deleted.project_id, deleted.subtask_id)
    return f"Proposal {deleted.id[:8]} byl smazán."


def _status_command(content: str, target_status: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        return f"Použití: {'start' if target_status == 'in_progress' else 'done'} <proposal_id>"
    try:
        proposal_id = _resolve_proposal_id(parts[1])
        updated = update_proposal_status(proposal_id, target_status)
    except ValueError as exc:
        return str(exc)
    return f"Proposal {updated.id[:8]} má stav: {updated.status}."


def _set_group_command(content: str) -> str:
    parts = content.split(maxsplit=2)
    if len(parts) < 3:
        return "Použití: set-group <proposal_id> <GROUP>"
    try:
        proposal_id = _resolve_proposal_id(parts[1])
    except ValueError as exc:
        return str(exc)
    group = parts[2].strip()
    if not group:
        return "Group nesmí být prázdná."

    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        return f"Proposal '{proposal_id}' not found"
    proposal.task_group = group
    save_proposals(proposals)
    return f"Proposal {proposal.id[:8]} má skupinu: {group}."


def _comment_command(content: str) -> str:
    parts = content.split(maxsplit=2)
    if len(parts) < 3:
        return "Použití: comment <proposal_id> <TEXT>"
    try:
        proposal_id = _resolve_proposal_id(parts[1])
    except ValueError as exc:
        return str(exc)
    note = parts[2].strip()
    if not note:
        return "Komentář nesmí být prázdný."

    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        return f"Proposal '{proposal_id}' not found"
    proposal.comments.append(note[:500])
    save_proposals(proposals)
    return f"Komentář uložen k {proposal.id[:8]}."


def _format_pending(role_filter: str | None = None) -> str:
    pending = [item for item in list_proposals() if item.status in {"pending", "approved", "in_progress"}]
    if role_filter:
        role_filter = _normalize_role(role_filter)
        pending = [item for item in pending if item.role == role_filter]
    if not pending:
        return "Žádné čekající návrhy."

    return "Čekající návrhy:\n" + "\n".join(_proposal_lines(pending))


def _format_triage() -> str:
    pending = [item for item in list_proposals() if item.status in {"pending", "approved", "in_progress"}]
    if not pending:
        return "Triage fronta je prázdná."

    header = "ID       | ROLE             | P | Odesílatel                   | Náhled"
    rows = [header, "-" * len(header)]
    for item in pending[:15]:
        short_id = item.id[:8]
        role = item.role[:16].ljust(16)
        sender = (item.sender or "").replace("\n", " ").strip()[:28].ljust(28)
        preview = (item.subject or item.summary).replace("\n", " ").strip()[:70]
        rows.append(f"{short_id} | {role} | {item.priority} | {sender} | {preview}")
    if len(pending) > 15:
        rows.append(f"... a dalších {len(pending) - 15} položek.")

    rows.append("")
    rows.append(
        "Úpravy: set-role <id> <ROLE>, set-priority <id> <1-5>, mark-newsletter <id>, approve <id>, reject <id>"
    )
    return "```text\n" + "\n".join(rows) + "\n```"


def _set_role_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 3:
        return "Použití: set-role <proposal_id> <ROLE>"
    proposal_id = _resolve_proposal_id(parts[1])
    role = _normalize_role(parts[2])
    allowed_roles = set(load_roles().keys()) | {"SPAM", "PHISHING", "NEWSLETTER"}
    if role not in allowed_roles:
        return f"Neznámá role '{role}'. Dostupné: {', '.join(sorted(allowed_roles))}"

    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        return f"Proposal '{proposal_id}' not found"

    role_cfg = load_roles().get(role, {})
    proposal.role = role
    if isinstance(role_cfg.get("priority"), int):
        proposal.priority = int(role_cfg["priority"])
    if isinstance(role_cfg.get("default_duration_minutes"), int):
        proposal.duration_minutes = max(1, int(role_cfg["default_duration_minutes"]))
    proposal.requires_action = True
    proposal.next_step = _next_step_for_role(role, proposal.subject)
    if proposal.status in {"dispatched", "done"}:
        proposal.status = "in_progress"
    save_proposals(proposals)
    record_feedback(
        proposal.sender,
        role=role,
        context_text=f"{proposal.subject} {proposal.source_excerpt}",
    )
    return (
        f"Proposal {proposal.id[:8]} má novou roli: {role}. "
        f"Nastavená priorita: {proposal.priority}, odhad: {proposal.duration_minutes} min."
    )


def _set_priority_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 3:
        return "Použití: set-priority <proposal_id> <1-5>"
    proposal_id = _resolve_proposal_id(parts[1])
    try:
        priority = int(parts[2])
    except ValueError:
        return "Priorita musí být číslo 1-5."
    if priority < 1 or priority > 5:
        return "Priorita musí být v rozsahu 1-5."

    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        return f"Proposal '{proposal_id}' not found"

    proposal.priority = priority
    if proposal.status in {"dispatched", "done"}:
        proposal.status = "in_progress"
    save_proposals(proposals)
    record_feedback(
        proposal.sender,
        priority=priority,
        context_text=f"{proposal.subject} {proposal.source_excerpt}",
    )
    return f"Proposal {proposal.id[:8]} má novou prioritu: {priority}."


def _set_role_shortcut(content: str, role: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        if role == "NEWSLETTER":
            return "Použití: mark-newsletter <proposal_id>"
        return f"Použití: {'mark-spam' if role == 'SPAM' else 'mark-phishing'} <proposal_id>"
    return _set_role_command(f"set-role {parts[1]} {role}")


def _role_prefix(role: str) -> str:
    mapping = {
        "DIPLOMKA": "Agent DIPLOMKA sleduje thesis práci, termíny a výstupy.",
        "PROFESOR": "Agent PROFESOR řeší akademickou komunikaci a odpovědi.",
        "KLIMATIKA": "Agent KLIMATIKA řeší směny, práci a navazující bloky.",
        "TOKVEKO": "Agent TOKVEKO řeší operativu a follow-upy firmy TOKVEKO.",
        "UNIVERZITA": "Agent UNIVERZITA řeší studijní administrativu a přípravu.",
        "OSOBNI": "Agent OSOBNI řeší osobní agendu.",
        "NEWSLETTER": "Agent NEWSLETTER řeší rychlé odhlášení odběrů.",
        "SPAM": "Agent SPAM řeší nevyžádané zprávy a subscriptions.",
        "PHISHING": "Agent PHISHING řeší bezpečnostní a podvodné zprávy.",
    }
    return mapping.get(_normalize_role(role), f"Agent {role} je aktivní.")


def _resolve_proposal_id(candidate: str) -> str:
    proposals = list_proposals()
    exact = next((item for item in proposals if item.id == candidate), None)
    if exact:
        return exact.id

    prefix_matches = [item.id for item in proposals if item.id.startswith(candidate)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise ValueError("Prefix ID není jednoznačný. Použij delší část ID.")
    raise ValueError(f"Proposal '{candidate}' not found")


def _format_ingest_result(result: IngestImapResponse) -> str:
    pending = [item for item in list_proposals() if item.status in {"pending", "approved", "in_progress"}]
    new_items = [item for item in result.proposals if item.id in set(result.new_proposal_ids)]
    lines = [
        "IMAP ingest hotov.",
        f"Načteno emailů: {result.emails_count}.",
        f"Nové návrhy: {result.proposals_created}.",
        f"Již známé nezpracované návrhy znovu nalezeny: {result.proposals_updated}.",
    ]

    if new_items:
        lines.append("")
        lines.append("Nově zachycené návrhy:")
        lines.extend(_proposal_lines(new_items[:10]))

    if pending:
        lines.append("")
        lines.append(f"Celkem čekajících návrhů: {len(pending)}.")
        lines.extend(_proposal_lines(pending[:10]))
        if len(pending) > 10:
            lines.append(f"... a dalších {len(pending) - 10}.")
    else:
        lines.append("")
        lines.append("Žádné čekající návrhy.")

    triage_url = os.getenv("TRIAGE_WEB_URL", "").strip()
    if triage_url:
        lines.append("")
        lines.append(f"Uprav na webu: {triage_url}")

    return "\n".join(lines)


def _proposal_lines(items: list[TaskProposal]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(
            f"- {item.id[:8]} | {item.role} | P{item.priority} | {item.sender} | {item.subject or item.summary}"
        )
    return lines


def _next_step_for_role(role: str, subject: str) -> str:
    role = _normalize_role(role)
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


def _normalize_role(role: str) -> str:
    normalized = role.upper().strip()
    return ROLE_ALIASES.get(normalized, normalized)


def _extract_command(content_lower: str) -> str:
    text = content_lower.strip()
    if not text:
        return ""
    return text.split()[0].lstrip("!/")


def get_dispatch_candidates() -> list[TaskProposal]:
    return [item for item in list_proposals() if item.status == "approved"]


def dispatch_grouped_by_channel() -> dict[str, list[TaskProposal]]:
    grouped: dict[str, list[TaskProposal]] = {}
    for item in get_dispatch_candidates():
        channel = find_role_channel(item.role) or "orchestrator"
        grouped.setdefault(channel, []).append(item)
    return grouped


def mark_dispatched(proposal_ids: list[str]) -> None:
    proposals = list_proposals()
    target = set(proposal_ids)
    changed = False
    for item in proposals:
        if item.id in target and item.status == "approved":
            item.status = "dispatched"
            changed = True
    if changed:
        save_proposals(proposals)


def update_proposal_status(proposal_id: str, status: str) -> TaskProposal:
    proposals = list_proposals()
    proposal = next((item for item in proposals if item.id == proposal_id), None)
    if proposal is None:
        raise ValueError(f"Proposal '{proposal_id}' not found")
    proposal.status = status
    save_proposals(proposals)
    return proposal


def _dispatch_hint() -> str:
    count = len(get_dispatch_candidates())
    if count == 0:
        return "Není co dispatchovat. Schval nejdřív položky přes approve / web triage."
    return f"Připraveno k dispatch: {count}. Bot je po příkazu rozešle do kanálů."


def _create_manual_specialist_task(role: str, author_name: str, channel_name: str, text: str) -> TaskProposal:
    normalized_role = _normalize_role(role)
    cfg = get_role_config(normalized_role)
    priority = int(cfg.get("priority", 3))
    duration = int(cfg.get("default_duration_minutes", 45))
    subject = text.strip()[:180]

    proposal = TaskProposal(
        id=str(uuid4()),
        created_at=datetime.utcnow(),
        status="in_progress",
        account_name="manual_discord",
        message_id=f"manual:{channel_name}:{uuid4()}",
        sender=author_name,
        subject=subject,
        source_excerpt=text[:320],
        role=normalized_role,
        handling="needs_attention",
        summary=subject,
        requires_action=True,
        priority=max(1, min(5, priority)),
        duration_minutes=max(5, min(600, duration)),
        next_step=f"Rozpracuj úkol z kanálu {channel_name}.",
        bundle_key=f"manual:{channel_name}",
        bundle_label=f"Discord {channel_name}",
        comments=[f"Vytvořeno z Discord zprávy: {text[:200]}"],
    )

    project = _latest_open_project_for_role(normalized_role)
    if project is None:
        project = create_project(f"{normalized_role} Inbox", normalized_role)
    proposal.project_id = project.id
    subtask = add_subtask(project.id, subject, priority=proposal.priority)
    proposal.subtask_id = subtask.id

    proposals = list_proposals()
    proposals.append(proposal)
    save_proposals(proposals)
    return proposal


def _latest_open_project_for_role(role: str):
    projects = [p for p in list_projects() if p.role == role and p.status in {"open", "waiting"}]
    if not projects:
        return None
    projects.sort(key=lambda p: p.created_at, reverse=True)
    return projects[0]
