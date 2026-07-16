"""Direct dependencies for nav_history repair commands."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src import config
from src.portfolio import PortfolioManager
from src.feishu_storage import FeishuStorage


@dataclass
class NavRepairContext:
    account: str
    storage: Any
    portfolio: Any


def create_nav_repair_context(*, account: Optional[str] = None) -> NavRepairContext:
    storage = FeishuStorage()
    resolved_account = account or config.get_account()
    return NavRepairContext(
        account=resolved_account,
        storage=storage,
        portfolio=PortfolioManager(storage),
    )
