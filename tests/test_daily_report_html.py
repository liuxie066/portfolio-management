"""Offline user-visible daily report behavior, independent of live portfolio data."""
import copy
import json
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scripts.generate_daily_report_html import build_market_breakdown, fmt_money, render_html
from scripts.publish_daily_report import PublishConfig, render_daily_report_html


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.text = []
        self.tags = []
        self.feed(html)

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def bundle():
    return json.loads((Path(__file__).parent / "fixtures/product_quality/daily_report.json").read_text())


def test_publisher_retains_account_mode_label_and_all_warning_sources(bundle, tmp_path):
    bundle["nav_result"]["warnings"] = ["NAV source warning"]
    config = PublishConfig(tmp_path, tmp_path, tmp_path, tmp_path, "演示组合")
    day, html = render_daily_report_html(bundle, config)
    page = Page(html)
    text = " ".join(page.text)
    assert day == "2026-11-17"
    assert "演示组合 · demo" in text
    assert "预览 · NAV 未写入" in text
    assert "NAV source warning" in text
    assert "本页不是生产账本" in text
    assert html.index("估值与处理提示") < html.index('id="overview"')
    assert "snapshot_time: 2026-11-18T08:00:00+08:00" in text
    assert "run_id: synthetic-report-20261118" in text
    assert not any(tag == "script" or (tag in {"link", "img"} and attrs.get("src", attrs.get("href", "")).startswith("http")) for tag, attrs in page.tags)


@pytest.mark.parametrize(("result", "status"), [
    ({"success": True, "dry_run": False, "status": "recorded"}, "NAV 已记录"),
    ({"success": True, "status": "recorded"}, "NAV 记录状态未提供"),
    ({"success": False, "error": "快照写入失败"}, "NAV 处理失败"),
    ({}, "NAV 记录状态未提供"),
])
def test_recording_status_requires_explicit_evidence(bundle, result, status):
    bundle["nav_result"] = result
    assert status in " ".join(Page(render_html(bundle)).text)


def test_missing_is_not_zero_or_today_and_empty_lists_are_explicit():
    page = Page(render_html({"report": {}, "snapshot": {}}))
    text = " ".join(page.text)
    assert "日期未提供" in text
    assert "¥0.00" not in text
    assert "没有可展示的持仓" in text
    assert "快照未提供券商分布" in text
    assert "NAV 记录状态未提供" in text
    assert fmt_money(0) == "¥0.00"
    assert fmt_money(Decimal("123456789012345.67")) == "¥123,456,789,012,345.67"
    for missing in (None, "invalid", float("nan"), float("inf")):
        assert fmt_money(missing) == "—"


def test_broker_breakdown_preserves_unknown_subtotals_and_denominator():
    rows = [{"broker": "A", "market_value": 10}, {"broker": "A", "market_value": None},
            {"broker": "B", "market_value": 0}]
    result = build_market_breakdown(rows, None)
    assert result == [{"broker": "B", "value": 0, "ratio": None},
                      {"broker": "A", "value": None, "ratio": None}]
    assert build_market_breakdown([{"market_value": 5}], 10)[0]["ratio"] == Decimal("0.5")


def test_mobile_content_and_accessible_semantics_preserve_financial_fields(bundle):
    page = Page(render_html(bundle))
    text = " ".join(page.text)
    assert "券商分布" in text and "Market Breakdown" not in text
    assert "12,345.6789" in text and "多券商汇总" in text
    assert "不适用（合并）" in text and "999,999" not in text
    assert "¥289,123.45" in text and "¥-153,456.78" in text
    assert any(tag == "main" for tag, _ in page.tags)
    assert any(tag == "caption" for tag, _ in page.tags)
    assert any(tag == "th" and attrs.get("scope") == "col" for tag, attrs in page.tags)
    ids = {attrs["id"] for _, attrs in page.tags if "id" in attrs}
    assert all(attrs["href"][1:] in ids for tag, attrs in page.tags if tag == "a" and attrs.get("href", "").startswith("#"))


def test_untrusted_names_notes_and_trace_are_text_not_markup(bundle):
    attack = '<script>alert("test")</script>'
    bundle["account"] = attack
    bundle["report"]["warnings"] = [attack]
    bundle["report"]["top_holdings"][0]["name"] = attack
    bundle["snapshot"]["run_id"] = attack
    bundle.pop("run_id")
    page = Page(render_html(bundle))
    assert not any(tag == "script" for tag, _ in page.tags)
    assert attack in " ".join(page.text)


def test_renderer_does_not_mutate_bundle_or_invent_merged_fallback(bundle):
    bundle["report"].pop("top_holdings")
    expected = copy.deepcopy(bundle)
    page = Page(render_html(bundle))
    assert "保留快照顺序，未合并" in " ".join(page.text)
    assert bundle == expected


def test_account_override_does_not_inherit_another_accounts_default_label(monkeypatch):
    import sys
    from scripts import publish_daily_report as publisher

    monkeypatch.setattr(publisher.app_config, "get_account", lambda: "alice")
    monkeypatch.setattr(publisher.app_config, "get", lambda key, default=None: "Alice's portfolio" if key == "report.account_label" else default)
    monkeypatch.setattr(sys, "argv", ["publisher", "--account", "bob"])
    assert publisher.parse_args().account_label is None
    monkeypatch.setattr(sys, "argv", ["publisher"])
    assert publisher.parse_args().account_label == "Alice's portfolio"
    monkeypatch.setattr(sys, "argv", ["publisher", "--account", "bob", "--account-label", "Bob's portfolio"])
    assert publisher.parse_args().account_label == "Bob's portfolio"


def test_service_resolved_account_does_not_receive_local_default_alias(bundle, tmp_path):
    bundle["account"] = "bob"
    config = PublishConfig(tmp_path, tmp_path, tmp_path, tmp_path, "Alice's portfolio", account_label_account="alice")
    _, html = render_daily_report_html(bundle, config)
    text = " ".join(Page(html).text)
    assert "bob" in text
    assert "Alice's portfolio" not in text
    bundle["account"] = "alice"
    _, html = render_daily_report_html(bundle, config)
    assert "Alice's portfolio · alice" in " ".join(Page(html).text)
