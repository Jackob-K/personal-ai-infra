from __future__ import annotations

import base64
import os
import uuid
from datetime import datetime
from urllib import error, request


def create_calendar_event(summary: str, description: str, start: datetime, end: datetime) -> str | None:
    calendar_url = os.getenv("CALDAV_CALENDAR_URL", "").strip()
    username = os.getenv("CALDAV_USERNAME", "").strip()
    password = os.getenv("CALDAV_PASSWORD", "").strip()
    timezone_name = os.getenv("CALDAV_TIMEZONE", "Europe/Prague").strip() or "Europe/Prague"

    if not (calendar_url and username and password):
        return None

    event_uid = str(uuid.uuid4())
    event_url = f"{calendar_url.rstrip('/')}/{event_uid}.ics"

    payload = _render_ics(
        uid=event_uid,
        summary=summary,
        description=description,
        start=start,
        end=end,
        timezone_name=timezone_name,
    ).encode("utf-8")

    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    req = request.Request(
        event_url,
        data=payload,
        method="PUT",
        headers={
            "Content-Type": "text/calendar; charset=utf-8",
            "Authorization": f"Basic {auth}",
        },
    )

    try:
        with request.urlopen(req, timeout=12):
            return event_uid
    except error.URLError:
        return None


def _render_ics(uid: str, summary: str, description: str, start: datetime, end: datetime, timezone_name: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dtstart = start.strftime("%Y%m%dT%H%M%S")
    dtend = end.strftime("%Y%m%dT%H%M%S")
    escaped_summary = summary.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    escaped_description = (
        description.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )

    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ai-server//assistant//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={timezone_name}:{dtstart}",
            f"DTEND;TZID={timezone_name}:{dtend}",
            f"SUMMARY:{escaped_summary}",
            f"DESCRIPTION:{escaped_description}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )
