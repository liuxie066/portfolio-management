from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src import config


class FutuSyncEvidenceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (config.get_data_dir() / "futu_sync_receipts")

    def save(self, account: str, sync_run_id: str, receipt: dict[str, Any]) -> dict[str, str]:
        account_dir = self.root / account
        history_path = account_dir / "history" / f"{sync_run_id}.json"
        latest_path = account_dir / "latest.json"
        self._atomic_write(history_path, receipt)
        self._atomic_write(latest_path, receipt)
        return {
            "history_ref": f"futu-sync-receipt:{account}:{sync_run_id}",
            "latest_ref": f"futu-sync-receipt:{account}:latest",
        }

    def latest(self, account: str) -> dict[str, Any] | None:
        path = self.root / account / "latest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
