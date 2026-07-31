from __future__ import annotations

import json

from src.local_cache import LocalHoldingsIndexCache


def test_local_holdings_index_ignores_legacy_and_v1_payloads(tmp_path):
    cache_file = tmp_path / "holdings-index.json"

    cache_file.write_text(json.dumps({"legacy:key": {"record_id": "rec_legacy"}}))
    legacy = LocalHoldingsIndexCache(cache_file=cache_file)
    assert legacy.load_all() == {}
    legacy.close()

    cache_file.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {"v1:key": {"record_id": "rec_v1"}},
            }
        )
    )
    version_one = LocalHoldingsIndexCache(cache_file=cache_file)
    assert version_one.load_all() == {}
    version_one.close()


def test_local_holdings_index_restores_only_current_version(tmp_path):
    cache_file = tmp_path / "holdings-index.json"
    expected = {
        "AAPL:lx:IBKR": {
            "validation_policy_version": "holdings-validation.v1",
            "record_id": "rec_current",
        }
    }
    cache_file.write_text(json.dumps({"version": 2, "items": expected}))

    current = LocalHoldingsIndexCache(cache_file=cache_file)

    assert current.load_all() == expected
    current.close()
