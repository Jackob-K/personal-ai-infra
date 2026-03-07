from __future__ import annotations

import os
from datetime import date

from app.models import ApproveProposalRequest, IngestImapRequest, IngestImapResponse, TaskProposal
from app.services.agent_registry import find_channel_agent
from app.services.assistant_flow import approve_or_reject_proposal, ingest_and_create_proposals
from app.services.channel_memory import append_message, get_recent_messages
from app.services.feedback import record_feedback
from app.services.imap_accounts import load_imap_accounts
from app.services.proposal_store import list_proposals, save_proposals
from app.services.roles import load_roles


HELP_TEXT = """Dostupné příkazy:
- help
- triage
- pending
- ingest
- pokracuj
- set-role <proposal_id> <ROLE>
- set-priority <proposal_id> <1-5>
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
    if lower in {"help", "!help"}:
        return HELP_TEXT
    if lower in {"triage", "!triage"}:
        return _format_triage()
    if lower in {"pending", "!pending"}:
        return _format_pending()
    if lower in {"ingest", "!ingest"}:
        accounts = load_imap_accounts()
        result = ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=10))
        return _format_ingest_result(result)
    if lower in {"pokracuj", "pokračuj", "continue"}:
        return "Pokračuji dalším krokem. Pro detailní úpravy otevři web triage."
    if lower.startswith("set-role "):
        return _set_role_command(content)
    if lower.startswith("set-priority "):
        return _set_priority_command(content)
    if lower.startswith("mark-spam "):
        return _set_role_shortcut(content, "SPAM")
    if lower.startswith("mark-phishing "):
        return _set_role_shortcut(content, "PHISHING")
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
    if proposal.role in {"SPAM", "PHISHING"}:
        return f"Návrh {proposal.id} schválen jako {proposal.role}. Nebyl plánován do kalendáře."
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


def _format_triage() -> str:
    pending = [item for item in list_proposals() if item.status == "pending"]
    if not pending:
        return "Triage fronta je prázdná."

    header = "ID       | ROLE             | P | Náhled"
    rows = [header, "-" * len(header)]
    for item in pending[:15]:
        short_id = item.id[:8]
        role = item.role[:16].ljust(16)
        preview = (item.subject or item.summary).replace("\n", " ").strip()[:70]
        rows.append(f"{short_id} | {role} | {item.priority} | {preview}")
    if len(pending) > 15:
        rows.append(f"... a dalších {len(pending) - 15} položek.")

    rows.append("")
    rows.append("Úpravy: set-role <id> <ROLE>, set-priority <id> <1-5>, approve <id>, reject <id>")
    return "```text\n" + "\n".join(rows) + "\n```"


def _set_role_command(content: str) -> str:
    parts = content.split()
    if len(parts) < 3:
        return "Použití: set-role <proposal_id> <ROLE>"
    proposal_id = _resolve_proposal_id(parts[1])
    role = parts[2].upper()
    allowed_roles = set(load_roles().keys()) | {"SPAM", "PHISHING"}
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
    save_proposals(proposals)
    record_feedback(proposal.sender, role=role)
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
    save_proposals(proposals)
    record_feedback(proposal.sender, priority=priority)
    return f"Proposal {proposal.id[:8]} má novou prioritu: {priority}."


def _set_role_shortcut(content: str, role: str) -> str:
    parts = content.split()
    if len(parts) < 2:
        return f"Použití: {'mark-spam' if role == 'SPAM' else 'mark-phishing'} <proposal_id>"
    return _set_role_command(f"set-role {parts[1]} {role}")


def _role_prefix(role: str) -> str:
    mapping = {
        "DIPLOMKA": "Agent DIPLOMKA sleduje thesis práci, termíny a výstupy.",
        "PROFESOR": "Agent PROFESOR řeší akademickou komunikaci a odpovědi.",
        "FIRMA_ZAMESTNANI": "Agent FIRMA_ZAMESTNANI řeší směny, práci a navazující bloky.",
        "STARTUP": "Agent STARTUP řeší startup operativu a follow-upy.",
        "SKOLA": "Agent SKOLA řeší studijní administrativu a přípravu.",
        "OSOBNI": "Agent OSOBNI řeší osobní agendu.",
        "SPAM": "Agent SPAM řeší nevyžádané zprávy a subscriptions.",
        "PHISHING": "Agent PHISHING řeší bezpečnostní a podvodné zprávy.",
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

    triage_url = os.getenv("TRIAGE_WEB_URL", "").strip()
    if triage_url:
        lines.append("")
        lines.append(f"Uprav na webu: {triage_url}")

    return "\n".join(lines)


def _proposal_lines(items: list[TaskProposal]) -> list[str]:
    lines: list[str] = []
    for item in items:
        lines.append(f"- {item.id[:8]} | {item.role} | P{item.priority} | {item.subject or item.summary}")
    return lines


def _next_step_for_role(role: str, subject: str) -> str:
    if role == "SPAM":
        return "Ověř spam a ručně odhlaš subscription nebo nastav blokaci odesílatele."
    if role == "PHISHING":
        return "Neotvírej odkazy, ověř odesílatele a případ nahlas jako phishing."
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
    return f"Navrhni první konkrétní krok k tématu: {subject[:80]}"
