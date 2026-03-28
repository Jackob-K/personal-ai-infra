from __future__ import annotations

import os
import threading
import time

from app.models import IngestImapRequest
from app.services.assistant_flow import ingest_and_create_proposals
from app.services.imap_accounts import load_imap_accounts


_stop_event = threading.Event()
_worker: threading.Thread | None = None


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
                ingest_and_create_proposals(IngestImapRequest(accounts=accounts, max_per_account=_max_per_account()))
        except Exception:
            # Keep the scheduler alive even when one sync run fails.
            pass
        _stop_event.wait(interval)


def _is_sync_enabled() -> bool:
    return os.getenv("IMAP_SYNC_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


def _max_per_account() -> int:
    raw = os.getenv("IMAP_SYNC_MAX_PER_ACCOUNT", "200").strip()
    try:
        return max(1, min(1000, int(raw)))
    except ValueError:
        return 200
