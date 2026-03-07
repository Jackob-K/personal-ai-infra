from __future__ import annotations

import json
import os
from urllib import error, request

from app.models import ClassifyEmailResponse, EmailClassifyRequest
from app.services.feedback import apply_feedback
from app.services.roles import load_roles


KEYWORD_MAP: list[tuple[str, str]] = [
    ("unsubscribe", "SPAM"),
    ("odhlasit", "SPAM"),
    ("newsletter", "SPAM"),
    ("vyher", "SPAM"),
    ("lottery", "SPAM"),
    ("phishing", "PHISHING"),
    ("verify your account", "PHISHING"),
    ("suspicious login", "PHISHING"),
    ("reset password", "PHISHING"),
    ("bitcoin", "PHISHING"),
    ("diplom", "DIPLOMKA"),
    ("thesis", "DIPLOMKA"),
    ("profesor", "PROFESOR"),
    ("student", "PROFESOR"),
    ("faktura", "STARTUP"),
    ("startup", "STARTUP"),
    ("smena", "FIRMA_ZAMESTNANI"),
    ("shift", "FIRMA_ZAMESTNANI"),
    ("zkouska", "SKOLA"),
    ("school", "SKOLA"),
]


def classify_email(payload: EmailClassifyRequest) -> ClassifyEmailResponse:
    result: ClassifyEmailResponse
    if os.getenv("OLLAMA_ENABLED", "false").lower() == "true":
        llm_result = _classify_via_ollama(payload)
        if llm_result:
            result = llm_result
        else:
            result = _classify_heuristic(payload)
    else:
        result = _classify_heuristic(payload)

    learned_role, learned_priority = apply_feedback(payload.sender, result.role, result.priority)
    result.role = learned_role
    result.priority = max(1, min(5, learned_priority))
    if result.role in {"SPAM", "PHISHING"}:
        result.requires_action = True
        if result.role == "SPAM":
            result.suggested_duration_minutes = 5
        if result.role == "PHISHING":
            result.suggested_duration_minutes = 10
    return result


def _classify_via_ollama(payload: EmailClassifyRequest) -> ClassifyEmailResponse | None:
    model = os.getenv("OLLAMA_MODEL", "llama3:8b")
    url = f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat"
    roles = ", ".join(load_roles().keys())
    user_text = f"Subject: {payload.subject}\nBody: {payload.body}\nSender: {payload.sender or ''}"
    prompt = (
        "Classify this email into one role from: "
        f"{roles}. Return JSON with keys role, requires_action, suggested_duration_minutes, priority, summary."
    )

    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": "You are an email triage assistant. Output valid JSON only."},
            {"role": "user", "content": f"{prompt}\n\n{user_text}"},
        ],
    }

    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = raw.get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        return ClassifyEmailResponse(**parsed)
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def _classify_heuristic(payload: EmailClassifyRequest) -> ClassifyEmailResponse:
    text = f"{payload.subject} {payload.body} {payload.sender or ''}".lower()
    role = "OSOBNI"
    for keyword, mapped_role in KEYWORD_MAP:
        if keyword in text:
            role = mapped_role
            break

    requires_action = any(token in text for token in ["pros", "urgent", "deadline", "term", "reply", "odpove"])
    priority = 4 if "urgent" in text or "asap" in text else 3 if requires_action else 2
    suggested_duration = 45 if requires_action else 20
    if role == "SPAM":
        requires_action = True
        priority = 1
        suggested_duration = 5
    if role == "PHISHING":
        requires_action = True
        priority = 5
        suggested_duration = 10
    summary_source = payload.subject.strip() or payload.body.strip()[:120] or "Email without clear content."

    return ClassifyEmailResponse(
        role=role,
        requires_action=requires_action,
        suggested_duration_minutes=suggested_duration,
        priority=priority,
        summary=summary_source,
    )
