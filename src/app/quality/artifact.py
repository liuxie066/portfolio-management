from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src import config


class QualityArtifactStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config.get_data_dir() / "quality" / "status.v1.json")

    def publish(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))
