from __future__ import annotations

from datetime import date

from app.models import ApproveProposalRequest, IngestImapRequest, IngestImapResponse, TaskProposal
from app.services.agent_registry import find_channel_agent
from app.services.assistant_flow import approve_or_reject_proposal, ingest_and_create_proposals
from app.services.channel_memory import append_message, get_recent_messages
from app.services.imap_accounts import load_imap_accounts
from app.services.proposal_store import list_proposals


HELP_TEXT = """Dostupné příkazy:
- help
- pending
- ingest
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
    if lower in {"help", "!help"}:
        return HELP_TEXT
    if lower in {"pending", "!pending"}:
        return _format_pending()
    if lower in {"ingest", "!ingest"}:
        accounts = load_imap_accounts()
        result = ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=10))
        return _format_ingest_result(result)
    if lower.startswith("approve "):
        return _approve_command(content)
    if lower.startswith("reject "):
        return _reject_command(content)
    return HELP_TEXT


def _handle_specialist(channel_name: str, role: str, author_name: str, content: str) -> str:
    recent = get_recent_messages(channel_name)
    recent_count = max(0, len(recent) - 1)
    prefix = _role_prefix(role)

    if "pending" in content.lower():
        return f"{prefix}\n\n{_format_pending(role_filter=role)}"

    return (
        f"{prefix}\n\n"
        f"Zprávu od {author_name} jsem zařadil do role `{role}`. "
        f"Aktuální kanál má uložený kontext {recent_count} předchozích zpráv. "
        "V další iteraci sem připojíme plné reasoning workflow a akce nad tasky/emaily."
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
            ApproveProposalRequest(approve=True, planning_date=planning_date, auto_schedule_to_caldav=True),
        )
    except ValueError as exc:
        return str(exc)
    if proposal.planned_start and proposal.planned_end:
        return (
            f"Návrh {proposal.id} schválen. "
            f"Naplánováno {proposal.planned_start.isoformat()} -> {proposal.planned_end.isoformat()}."
        )
    return f"Návrh {proposal.id} schválen, ale nepodařilo se najít časový slot."


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


def _format_pending(role_filter: str | None = None) -> str:
    pending = [item for item in list_proposals() if item.status == "pending"]
    if role_filter:
        pending = [item for item in pending if item.role == role_filter]
    if not pending:
        return "Žádné čekající návrhy."

    return "Čekající návrhy:\n" + "\n".join(_proposal_lines(pending))


def _role_prefix(role: str) -> str:
    mapping = {
        "DIPLOMKA": "Agent DIPLOMKA sleduje thesis práci, termíny a výstupy.",
        "PROFESOR": "Agent PROFESOR řeší akademickou komunikaci a odpovědi.",
        "FIRMA_ZAMESTNANI": "Agent FIRMA_ZAMESTNANI řeší směny, práci a navazující bloky.",
        "STARTUP": "Agent STARTUP řeší startup operativu a follow-upy.",
        "SKOLA": "Agent SKOLA řeší studijní administrativu a přípravu.",
        "OSOBNI": "Agent OSOBNI řeší osobní agendu.",
        "ASISTENT": "Agent ASISTENT koordinuje ostatní asistenty a schvalovací vrstvu.",
    }
    return mapping.get(role, f"Agent {role} je aktivní.")


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
    pending = [item for item in list_proposals() if item.status == "pending"]
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

    return "\n".join(lines)


def _proposal_lines(items: list[TaskProposal]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(f"- {item.id[:8]} | {item.role} | P{item.priority} | {item.subject or item.summary}")
    return lines
