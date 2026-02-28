from __future__ import annotations

import email
import imaplib
import os
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime

from app.models import InboxAccountConfig, RawEmailMessage


def fetch_emails(accounts: list[InboxAccountConfig], max_per_account: int) -> list[RawEmailMessage]:
    results: list[RawEmailMessage] = []
    for account in accounts:
        password = _resolve_password(account)
        if not password:
            continue

        try:
            with imaplib.IMAP4_SSL(account.host, account.port) as client:
                client.login(account.username, password)
                # Read-only mailbox access prevents the assistant from changing message flags.
                client.select(account.folder, readonly=True)
                criteria = "UNSEEN" if account.unseen_only else "ALL"
                status, raw_ids = client.search(None, criteria)
                if status != "OK" or not raw_ids:
                    continue

                ids = raw_ids[0].split()
                for message_id in ids[-max_per_account:]:
                    status, fetched = client.fetch(message_id, "(BODY.PEEK[])")
                    if status != "OK" or not fetched or not fetched[0]:
                        continue

                    raw_email = fetched[0][1]
                    parsed = email.message_from_bytes(raw_email)
                    results.append(_to_raw_message(account.name, message_id.decode("utf-8"), parsed))
        except (imaplib.IMAP4.error, OSError):
            continue

    return results


def _resolve_password(account: InboxAccountConfig) -> str | None:
    if account.password:
        return account.password
    if account.password_env:
        return os.getenv(account.password_env)
    return None


def _decode_text(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace").strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()
    return ""


def _to_raw_message(account_name: str, message_id: str, msg: Message) -> RawEmailMessage:
    received_at = _parse_received(msg.get("Date"))
    return RawEmailMessage(
        account_name=account_name,
        message_id=message_id,
        sender=_decode_text(msg.get("From")),
        subject=_decode_text(msg.get("Subject")),
        body=_extract_body(msg)[:5000],
        received_at=received_at,
    )


def _parse_received(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        dt = parsedate_to_datetime(raw_value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        return None
