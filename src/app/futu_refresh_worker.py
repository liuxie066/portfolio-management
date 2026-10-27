"""One serial, root-owned HTTP worker for durable full-sync requests."""
from __future__ import annotations

import logging
from threading import Event

from src.process_lock import process_lock

LOGGER = logging.getLogger(__name__)
POLL_SECONDS = 1.0
WORKER_LOCK_KEY = "futu-refresh-worker"


class FutuRefreshWorker:
    def __init__(self, store, refresh):
        self.store = store
        self.refresh = refresh
        self.stop = Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                with process_lock(
                    WORKER_LOCK_KEY, data_dir=self.store.db_path.resolve().parent,
                    blocking=False,
                ):
                    self._run_owned()
            except BlockingIOError:
                self.stop.wait(POLL_SECONDS)

    def _run_owned(self) -> None:
        while not self.stop.is_set():
            try:
                # The prior synchronous call has ended; settlement may have failed.
                self.store.recover_futu_refreshes()
                item = self.store.claim_futu_refresh()
                if item is None:
                    self.stop.wait(POLL_SECONDS)
                    continue
                identity = {key: item[key] for key in ("account", "request_id")}
                try:
                    result = self.refresh(**identity)
                except Exception as exc:
                    LOGGER.exception("pm_futu_refresh_sync_failed %s", identity)
                    result = {"success": False, "error": str(exc)}
                self.store.settle_futu_refresh(**identity, result=result)
            except Exception:
                LOGGER.exception("pm_futu_refresh_iteration_failed")
                self.stop.wait(POLL_SECONDS)
