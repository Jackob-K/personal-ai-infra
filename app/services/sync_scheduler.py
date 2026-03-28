from __future__ import annotations

import logging
import os
import threading
import time

from app.models import IngestImapRequest
from app.services.assistant_flow import ingest_and_create_proposals
from app.services.imap_accounts import load_imap_accounts
from app.services.sync_state import record_sync_run


_stop_event = threading.Event()
_worker: threading.Thread | None = None
_logger = logging.getLogger(__name__)


def start_sync_scheduler() -> None:
    global _worker
    if _worker and _worker.is_alive():
        return
    if not _is_sync_enabled():
        return

    _stop_event.clear()
    _worker = threading.Thread(target=_run_loop, name="imap-sync", daemon=True)
    _worker.start()


def stop_sync_scheduler() -> None:
    _stop_event.set()


def _run_loop() -> None:
    interval = max(30, int(os.getenv("IMAP_SYNC_INTERVAL_SECONDS", "120")))
    while not _stop_event.is_set():
        try:
            accounts = load_imap_accounts()
            if accounts:
                result = ingest_and_create_proposals(
                    IngestImapRequest(accounts=accounts, max_per_account=_max_per_account()),
                    trigger="scheduler",
                )
                _logger.info(
                    "IMAP sync finished: emails=%s created=%s updated=%s removed=%s",
                    result.emails_count,
                    result.proposals_created,
                    result.proposals_updated,
                    result.proposals_removed,
                )
            else:
                _logger.info("IMAP sync skipped: no accounts configured")
        except Exception as exc:
            _logger.exception("IMAP sync failed")
            record_sync_run(
                trigger="scheduler",
                emails_count=0,
                proposals_created=0,
                proposals_updated=0,
                proposals_removed=0,
                status="error",
                error=str(exc),
            )
        _stop_event.wait(interval)


def _is_sync_enabled() -> bool:
    return os.getenv("IMAP_SYNC_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _max_per_account() -> int:
    raw = os.getenv("IMAP_SYNC_MAX_PER_ACCOUNT", "200").strip()
    try:
        return max(1, min(1000, int(raw)))
    except ValueError:
        return 200
