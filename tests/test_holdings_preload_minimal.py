"""Minimal (no pytest) tests for holdings preload/index cache behavior."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.app.cash_service import CashService
from src.feishu.repositories.holdings_repository import HoldingsIntegrityError
from src.feishu_storage import FeishuStorage
from src.models import Holding, AssetType


def _assert_canonical_holding_date(value: Any) -> None:
    assert isinstance(value, str)
    parsed = datetime.strptime(value, "%Y/%m/%d")
    assert parsed.strftime("%Y/%m/%d") == value


class StubHoldingsClient:
    def __init__(self, initial_records: Optional[List[Dict[str, Any]]] = None):
        self._records = list(initial_records or [])
        self.list_records_calls: List[Dict[str, Any]] = []
        self.update_record_calls: List[Dict[str, Any]] = []
        self.create_record_calls: List[Dict[str, Any]] = []

    def list_records(self, table_name: str, filter_str: str = None, field_names: List[str] = None, page_size: int = 500):
        self.list_records_calls.append(
            {
                "table_name": table_name,
                "filter_str": filter_str,
                "field_names": list(field_names or []),
                "page_size": page_size,
            }
        )
        assert table_name == "holdings"

        # Very small filter parser for tests: CurrentValue.[account] = "xxx"
        account = None
        if filter_str and 'CurrentValue.[account] = "' in filter_str:
            account = filter_str.split('CurrentValue.[account] = "', 1)[1].split('"', 1)[0]

        out = []
        for r in self._records:
            if account and (r.get("fields") or {}).get("account") != account:
                continue
            out.append({"record_id": r["record_id"], "fields": dict(r.get("fields") or {})})
        return out

    def update_record(self, table_name: str, record_id: str, fields: Dict[str, Any]):
        assert table_name == "holdings"
        self.update_record_calls.append({"record_id": record_id, "fields": dict(fields)})
        # mutate backing record to emulate server state
        for r in self._records:
            if r["record_id"] == record_id:
                r.setdefault("fields", {}).update(fields)
                break
        return {"record_id": record_id, "fields": dict(fields)}

    def create_record(self, table_name: str, fields: Dict[str, Any]):
        assert table_name == "holdings"
        self.create_record_calls.append({"fields": dict(fields)})
        new_id = f"rec_new_{len(self.create_record_calls)}"
        self._records.append({"record_id": new_id, "fields": dict(fields)})
        return {"record_id": new_id, "fields": dict(fields)}

    def get_record_strict(self, table_name: str, record_id: str):
        assert table_name == "holdings"
        for record in self._records:
            if record["record_id"] == record_id:
                return {
                    "record_id": record_id,
                    "fields": dict(record.get("fields") or {}),
                }
        raise ValueError(f"record not found: {record_id}")



def test_preload_builds_index_and_projection_and_avoids_refetch():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_1",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_name": "Apple",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "futu",
                    "quantity": 10,
                    "currency": "USD",
                    "avg_cost": 150,
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)

    result = storage.preload_holdings_index(account="lx")
    assert result["loaded"] == 1
    assert len(client.list_records_calls) == 1
    call = client.list_records_calls[0]
    assert call["field_names"] == storage.HOLDING_PROJECTION_FIELDS
    assert 'CurrentValue.[account] = "lx"' in (call["filter_str"] or "")

    # hit cache, no extra list_records
    h = storage.get_holding("AAPL", "lx", "futu")
    assert h is not None
    assert h.record_id == "rec_1"
    assert len(client.list_records_calls) == 1

    # missing under preloaded account should return None directly
    missing = storage.get_holding("MSFT", "lx", "futu")
    assert missing is None
    assert len(client.list_records_calls) == 1


def test_raw_holdings_read_preserves_blank_fields_and_record_id():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_blank",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": "",
                    "currency": "",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)

    records = storage.get_raw_holdings(record_id="rec_blank")

    assert len(records) == 1
    assert records[0].record_id == "rec_blank"
    assert records[0].raw_fields["quantity"] == ""
    assert records[0].raw_fields["currency"] == ""
    assert records[0].source == "feishu"


def test_failed_preload_aggregates_invalid_rows_and_publishes_nothing():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_missing_currency",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": 1,
                    "currency": "",
                },
            },
            {
                "record_id": "rec_missing_quantity",
                "fields": {
                    "asset_id": "MSFT",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": "",
                    "currency": "USD",
                },
            },
        ]
    )
    storage = FeishuStorage(client=client)

    try:
        storage.preload_holdings_index(account="lx")
    except HoldingsIntegrityError as exc:
        assert {item["record_id"] for item in exc.errors} == {
            "rec_missing_currency",
            "rec_missing_quantity",
        }
    else:
        raise AssertionError("expected aggregate holdings integrity failure")

    assert storage._holding_id_cache == {}
    assert storage._holding_fields_cache == {}
    assert "lx" not in storage._holdings_index_loaded_accounts


def test_failed_preload_rejects_duplicates_before_replacing_existing_cache():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_original",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": 1,
                    "currency": "USD",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")
    client._records.append(
        {
            "record_id": "rec_duplicate",
            "fields": {
                "asset_id": "AAPL",
                "asset_type": "us_stock",
                "account": "lx",
                "broker": "IBKR",
                "quantity": 99,
                "currency": "USD",
            },
        }
    )

    try:
        storage.preload_holdings_index(account="lx")
    except HoldingsIntegrityError as exc:
        assert {item["record_id"] for item in exc.errors} == {
            "rec_original",
            "rec_duplicate",
        }
    else:
        raise AssertionError("expected duplicate identity failure")

    cached = storage.get_holding("AAPL", "lx", "IBKR")
    assert cached is not None
    assert cached.record_id == "rec_original"
    assert cached.quantity == 1


def test_failed_preload_does_not_loose_parse_invalid_optional_fields():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_bad_tag",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": 1,
                    "currency": "USD",
                    "tag": "not-json",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)

    try:
        storage.preload_holdings_index(account="lx")
    except HoldingsIntegrityError as exc:
        assert exc.errors[0]["record_id"] == "rec_bad_tag"
        assert "invalid tag" in exc.errors[0]["error"]
    else:
        raise AssertionError("expected invalid tag failure")


def test_preload_accepts_incident_and_predecessor_holding_date_formats():
    incident_dates = [
        "2026/03/30",
        "2026/04/08",
        "2026/04/20",
        "2026/05/12",
        "2026/07/16",
        "2026/07/17",
        "2026/07/20",
        "2026/07/22",
    ]
    records = []
    for index in range(17):
        fields = {
            "asset_id": f"ASSET-{index:02d}",
            "asset_name": f"Asset {index}",
            "asset_type": "us_stock",
            "account": "lx",
            "broker": "富途",
            "quantity": index + 1,
            "currency": "USD",
        }
        if index == 16:
            fields["updated_at"] = "2026-08-01 07:42:30"
        elif index % 2:
            fields["updated_at"] = incident_dates[index % len(incident_dates)]
        else:
            fields["created_at"] = incident_dates[index % len(incident_dates)]
        records.append({"record_id": f"rec_{index:02d}", "fields": fields})

    storage = FeishuStorage(client=StubHoldingsClient(initial_records=records))

    result = storage.preload_holdings_index(account="lx")

    assert result["loaded"] == 17
    holdings = storage.get_holdings(account="lx", include_empty=True)
    assert len(holdings) == 17
    predecessor = storage.get_holding("ASSET-16", "lx", "富途")
    assert predecessor is not None
    assert predecessor.updated_at == datetime(2026, 8, 1)
    assert storage._holding_fields_cache["ASSET-16:lx:富途"]["updated_at"] == "2026/08/01"


def test_preload_rejects_malformed_holding_date_without_publishing_cache():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_bad_date",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "USD",
                    "updated_at": "2026-08-01",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)

    try:
        storage.preload_holdings_index(account="lx")
    except HoldingsIntegrityError as exc:
        assert exc.errors == [
            {"record_id": "rec_bad_date", "error": "invalid updated_at: 2026-08-01"}
        ]
    else:
        raise AssertionError("expected malformed holdings date failure")

    assert storage._holding_id_cache == {}
    assert storage._holding_fields_cache == {}


def test_raw_account_scope_rejects_out_of_scope_source_rows():
    class CrossAccountClient(StubHoldingsClient):
        def list_records(self, *args, **kwargs):
            return [
                {
                    "record_id": "rec_sy",
                    "fields": {
                        "asset_id": "AAPL",
                        "asset_type": "us_stock",
                        "account": "sy",
                        "broker": "IBKR",
                        "quantity": 1,
                        "currency": "USD",
                    },
                }
            ]

    storage = FeishuStorage(client=CrossAccountClient())

    try:
        storage.get_raw_holdings(account="lx")
    except RuntimeError as exc:
        assert "outside the requested account" in str(exc)
    else:
        raise AssertionError("expected account scope integrity failure")


def test_raw_record_scope_rejects_mismatched_source_record_id():
    class WrongRecordClient(StubHoldingsClient):
        def get_record_strict(self, table_name, record_id):
            return {"record_id": "rec_other", "fields": {}}

    storage = FeishuStorage(client=WrongRecordClient())

    try:
        storage.get_raw_holdings(record_id="rec_expected")
    except RuntimeError as exc:
        assert "returned a different record" in str(exc)
    else:
        raise AssertionError("expected record scope integrity failure")


def test_reconciliation_patch_is_narrow_and_fresh_reads_back():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_patch",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_name": "Apple",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "IBKR",
                    "quantity": 1,
                    "currency": "",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)

    readback = storage.patch_holding_record(
        record_id="rec_patch",
        fields={"currency": "USD"},
    )

    assert readback.raw_fields["currency"] == "USD"
    assert client.update_record_calls[0]["fields"]["currency"] == "USD"
    _assert_canonical_holding_date(
        client.update_record_calls[0]["fields"]["updated_at"]
    )
    assert len(client.update_record_calls[0]["fields"]) == 2
    try:
        storage.patch_holding_record(
            record_id="rec_patch",
            fields={"quantity": 2},
        )
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("expected reconciliation patch allowlist failure")


def test_upsert_uses_preloaded_cache_for_batch_updates():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_1",
                "fields": {
                    "asset_id": "000001",
                    "asset_name": "平安银行",
                    "asset_type": "a_stock",
                    "account": "lx",
                    "broker": "manual",
                    "quantity": 100,
                    "currency": "CNY",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")

    h1 = Holding(
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="lx",
        broker="manual",
        quantity=20,
        currency="CNY",
    )
    h2 = Holding(
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="lx",
        broker="manual",
        quantity=30,
        currency="CNY",
    )

    storage.upsert_holding(h1)
    storage.upsert_holding(h2)

    # only preload triggered one list; each upsert should update by cache (no re-list)
    assert len(client.list_records_calls) == 1
    assert len(client.update_record_calls) == 2
    assert client.update_record_calls[0]["fields"]["quantity"] == 120
    assert client.update_record_calls[1]["fields"]["quantity"] == 150
    _assert_canonical_holding_date(client.update_record_calls[0]["fields"]["updated_at"])
    _assert_canonical_holding_date(client.update_record_calls[1]["fields"]["updated_at"])


def test_upsert_create_after_preload_missing_key_without_refetch():
    client = StubHoldingsClient(initial_records=[])
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")

    h = Holding(
        asset_id="00700",
        asset_name="腾讯控股",
        asset_type=AssetType.HK_STOCK,
        account="lx",
        broker="futu",
        quantity=50,
        currency="HKD",
    )
    created = storage.upsert_holding(h)

    assert created.record_id == "rec_new_1"
    assert len(client.list_records_calls) == 1  # preload only
    assert len(client.create_record_calls) == 1
    created_fields = client.create_record_calls[0]["fields"]
    _assert_canonical_holding_date(created_fields["created_at"])
    _assert_canonical_holding_date(created_fields["updated_at"])


def test_upsert_rejects_missing_broker_before_remote_access():
    client = StubHoldingsClient(initial_records=[])
    storage = FeishuStorage(client=client)
    holding = Holding(
        asset_id="AAPL",
        asset_name="Apple",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="",
        quantity=1,
        currency="USD",
    )

    try:
        storage.upsert_holding(holding)
    except ValueError as exc:
        assert "broker" in str(exc)
    else:
        raise AssertionError("expected missing broker validation failure")

    assert client.list_records_calls == []
    assert client.create_record_calls == []


def test_replace_holding_updates_absolute_quantity_and_descriptor_fields():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_cash",
                "fields": {
                    "asset_id": "CNY-CASH",
                    "asset_name": "旧现金名",
                    "asset_type": "other",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 20,
                    "currency": "USD",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")

    replaced = storage.replace_holding(Holding(
        asset_id="CNY-CASH",
        asset_name="人民币现金",
        asset_type=AssetType.CASH,
        account="lx",
        broker="富途",
        quantity=100.13,
        currency="CNY",
    ))

    assert replaced.record_id == "rec_cash"
    assert len(client.update_record_calls) == 1
    fields = client.update_record_calls[0]["fields"]
    assert fields["quantity"] == 100.13
    assert fields["asset_name"] == "人民币现金"
    assert fields["asset_type"] == "cash"
    assert fields["currency"] == "CNY"
    _assert_canonical_holding_date(fields["updated_at"])


def test_sync_mmf_balance_serializes_text_timestamp():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_mmf",
                "fields": {
                    "asset_id": "CNY-MMF",
                    "asset_name": "货币基金",
                    "asset_type": "mmf",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 644978.88,
                    "currency": "CNY",
                    "asset_class": "现金",
                    "industry": "现金",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")

    result = CashService(storage).sync_cash_like_balance(
        account="lx",
        asset_id="CNY-MMF",
        asset_name="货币基金",
        asset_type=AssetType.MMF,
        target=746470.86,
        broker="富途",
        dry_run=False,
    )

    assert result["updated"] is True
    fields = client.update_record_calls[0]["fields"]
    assert fields["quantity"] == 746470.86
    _assert_canonical_holding_date(fields["updated_at"])


def test_update_holding_quantity_writes_canonical_date():
    client = StubHoldingsClient(
        initial_records=[
            {
                "record_id": "rec_quantity",
                "fields": {
                    "asset_id": "AAPL",
                    "asset_name": "Apple",
                    "asset_type": "us_stock",
                    "account": "lx",
                    "broker": "富途",
                    "quantity": 1,
                    "currency": "USD",
                    "updated_at": "2026/07/31",
                },
            }
        ]
    )
    storage = FeishuStorage(client=client)
    storage.preload_holdings_index(account="lx")

    updated = storage.update_holding_quantity("AAPL", "lx", 2, broker="富途")

    assert updated.quantity == 3
    _assert_canonical_holding_date(client.update_record_calls[0]["fields"]["updated_at"])
