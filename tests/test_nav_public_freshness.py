"""Public NAV reads must observe the canonical writer across processes."""

from src.feishu_storage import FeishuStorage
from src.local_cache import LocalNavIndexCache
from src.service import PortfolioService


def _row(day, record_id):
    return {
        "record_id": record_id,
        "fields": {
            "account": "lx",
            "date": day,
            "total_value": 1000,
            "shares": 1000,
            "nav": 1.0,
            "cash_flow": 0,
            "pnl": 0,
            "mtd_nav_change": 0,
            "ytd_nav_change": 0,
            "mtd_pnl": 0,
            "ytd_pnl": 0,
        },
    }


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.fail = False

    def list_records(self, table_name, **_kwargs):
        assert table_name == "nav_history"
        self.calls += 1
        if self.fail:
            raise RuntimeError("canonical NAV unavailable")
        return list(self.rows)


def test_public_nav_sees_separate_writer_without_rewriting_disk_cache(tmp_path):
    cache_file = tmp_path / "nav.json"
    client = _Client([_row("2026-09-24", "old")])
    writer = FeishuStorage(client=client, local_nav_index_cache=LocalNavIndexCache(cache_file))
    writer.preload_nav_index("lx", force_refresh=True)
    writer._local_nav_index_cache.flush()

    api = FeishuStorage(client=client, local_nav_index_cache=LocalNavIndexCache(cache_file))
    service = PortfolioService(storage=api)
    assert service.get_nav(account="lx", days=30)["latest"]["date"] == "2026-09-24"

    client.rows.append(_row("2026-09-25", "new"))
    writer.preload_nav_index("lx", force_refresh=True)
    writer._local_nav_index_cache.flush()
    disk_after_writer = cache_file.read_bytes()
    calls_before = client.calls

    result = service.get_nav(account="lx", days=30)

    assert result["success"] is True
    assert result["latest"]["date"] == "2026-09-25"
    assert client.calls == calls_before + 1
    api._local_nav_index_cache.flush()
    assert cache_file.read_bytes() == disk_after_writer


def test_public_nav_empty_and_canonical_failure_each_read_once(tmp_path):
    client = _Client([])
    storage = FeishuStorage(
        client=client,
        local_nav_index_cache=LocalNavIndexCache(tmp_path / "nav.json"),
    )
    service = PortfolioService(storage=storage)

    assert service.get_nav(account="lx", days=30)["success"] is False
    assert client.calls == 1

    client.rows.append(_row("2026-09-24", "old"))
    assert service.get_nav(account="lx", days=30)["success"] is True
    client.fail = True
    result = service.get_nav(account="lx", days=30)
    assert result["success"] is False
    assert "canonical NAV unavailable" in result["error"]
    assert client.calls == 3
