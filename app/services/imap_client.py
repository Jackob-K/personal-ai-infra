from __future__ import annotations

import email
import hashlib
import imaplib
import os
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime

from app.models import InboxAccountConfig, RawEmailMessage


def fetch_active_message_keys(accounts: list[InboxAccountConfig]) -> set[str]:
    active_keys: set[str] = set()
    for account in accounts:
        password = _resolve_password(account)
        if not password:
            continue

        try:
            with imaplib.IMAP4_SSL(account.host, account.port) as client:
                client.login(account.username, password)
                client.select(account.folder, readonly=True)
                # Existence check must scan the whole selected folder, not just the ingest subset.
                # Otherwise read emails can be incorrectly marked as "removed from source".
                ids = _search_uids(client, False)
                for imap_uid in ids:
                    status, fetched = client.uid(
                        "FETCH",
                        imap_uid,
                        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM SUBJECT)])",
                    )
                    if status != "OK" or not fetched or not fetched[0]:
                        continue
                    parsed = email.message_from_bytes(fetched[0][1])
                    source_message_id = _normalize_message_id(parsed.get("Message-ID"))
                    received_at = _parse_received(parsed.get("Date"))
                    active_keys.add(_make_stable_key(account.name, account.folder, parsed, source_message_id, received_at))
        except (imaplib.IMAP4.error, OSError):
            continue
    return active_keys


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
                ids = _search_uids(client, account.unseen_only)
                if not ids:
                    continue

                for imap_uid in ids[-max_per_account:]:
                    status, fetched = client.uid("FETCH", imap_uid, "(BODY.PEEK[])")
                    if status != "OK" or not fetched or not fetched[0]:
                        continue

                    raw_email = fetched[0][1]
                    parsed = email.message_from_bytes(raw_email)
                    results.append(_to_raw_message(account, imap_uid, parsed))
        except (imaplib.IMAP4.error, OSError):
            continue

    return results


def _resolve_password(account: InboxAccountConfig) -> str | None:
    if account.password:
        return account.password
    if account.password_env:
        return os.getenv(account.password_env)
    return None


def _search_uids(client: imaplib.IMAP4_SSL, unseen_only: bool) -> list[str]:
    criteria = "UNSEEN" if unseen_only else "ALL"
    status, raw_ids = client.uid("SEARCH", None, criteria)
    if status != "OK" or not raw_ids:
        return []
    return [item.decode("utf-8") for item in raw_ids[0].split()]


def _decode_text(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for part, enc in parts:
        if isinstance(part, bytes):
            encoding = (enc or "utf-8").strip().lower() if isinstance(enc, str) else "utf-8"
            if not encoding or encoding in {"unknown-8bit", "unknown_8bit", "x-unknown"}:
                encoding = "utf-8"
            try:
                decoded.append(part.decode(encoding, errors="replace"))
            except LookupError:
                decoded.append(part.decode("utf-8", errors="replace"))
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


def _to_raw_message(account: InboxAccountConfig, imap_uid: str, msg: Message) -> RawEmailMessage:
    received_at = _parse_received(msg.get("Date"))
    source_message_id = _normalize_message_id(msg.get("Message-ID"))
    stable_key = _make_stable_key(account.name, account.folder, msg, source_message_id, received_at)
    return RawEmailMessage(
        account_name=account.name,
        message_id=imap_uid,
        folder=account.folder,
        imap_uid=imap_uid,
        source_message_id=source_message_id,
        stable_key=stable_key,
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


def _normalize_message_id(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    value = raw_value.strip()
    if not value:
        return None
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value.strip().lower() or None


def _make_stable_key(
    account_name: str,
    folder: str,
    msg: Message,
    source_message_id: str | None,
    received_at: datetime | None,
) -> str:
    if source_message_id:
        return f"{account_name}:{folder}:{source_message_id}"

    sender = _decode_text(msg.get("From")).strip().lower()
    subject = _decode_text(msg.get("Subject")).strip().lower()
    stamp = received_at.isoformat() if received_at else ""
    digest = hashlib.sha1(f"{sender}|{subject}|{stamp}".encode("utf-8")).hexdigest()
    return f"{account_name}:{folder}:fallback:{digest}"
