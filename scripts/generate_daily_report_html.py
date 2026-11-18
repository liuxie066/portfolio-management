#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PUBLIC_DIR = REPO_ROOT / "public"
OUTPUT_PATH = PUBLIC_DIR / "index.html"


def _number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def fmt_money(v: Any) -> str:
    number = _number(v)
    return "—" if number is None else f"¥{number:,.2f}"


def fmt_pct_ratio(v: Any) -> str:
    number = _number(v)
    return "—" if number is None else f"{number:.2%}"


def fmt_pct_change(v: Any) -> str:
    number = _number(v)
    return "—" if number is None else f"{number:+.2%}"


def kpi_class(v: Any) -> str:
    number = _number(v)
    return "" if number is None or number == 0 else "up" if number > 0 else "down"


def html_escape(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def build_market_breakdown(holdings: List[Dict[str, Any]], total_value: Any) -> List[Dict[str, Any]]:
    """Group the snapshot by broker; retain an unknown subtotal as unknown."""
    by_broker: dict[str, Decimal | None] = {}
    for holding in holdings:
        broker = holding.get("broker") or "未分类"
        value = _number(holding.get("market_value"))
        previous = by_broker.get(broker, Decimal(0))
        by_broker[broker] = previous + value if previous is not None and value is not None else None
    total = _number(total_value)
    return [
        {"broker": broker, "value": value,
         "ratio": value / total if value is not None and total is not None and total > 0 else None}
        for broker, value in sorted(
            by_broker.items(), key=lambda item: (item[1] is not None, item[1] or 0), reverse=True,
        )
    ]


def render_html(bundle: Dict[str, Any]) -> str:
    report = bundle["report"]
    full = bundle.get("full") or {}
    snapshot = bundle["snapshot"]
    overview = report.get("overview") or {}
    holdings = (snapshot.get("holdings_data") or {}).get("holdings") or []
    top_holdings = report.get("top_holdings")
    holdings_caption = "主要持仓（最多 10 项；同代码跨券商合并，现金与货基合并）"
    if top_holdings is None:
        top_holdings = holdings[:10]
        holdings_caption = "快照持仓（最多 10 项；保留快照顺序，未合并）"
    total_value = report.get("total_value")
    date_text = report.get("date") or "日期未提供"
    account = bundle.get("account") or snapshot.get("account") or report.get("account") or "未提供"
    account_label = bundle.get("account_label")
    account_text = str(account) if not account_label or account_label == account else f"{account_label} · {account}"
    snapshot_time = report.get("snapshot_time") or snapshot.get("snapshot_time") or "未提供"
    run_id = report.get("run_id") or bundle.get("run_id") or snapshot.get("run_id") or full.get("run_id") or "未提供"
    nav_result = bundle.get("nav_result") or {}
    if nav_result.get("success") is False:
        status = "NAV 处理失败"
    elif nav_result.get("dry_run") is True:
        status = "预览 · NAV 未写入"
    elif nav_result.get("success") is True and nav_result.get("dry_run") is False and nav_result.get("status") == "recorded":
        status = "NAV 已记录"
    else:
        status = "NAV 记录状态未提供"
    notes = []
    for source in (report, nav_result, full):
        for note in source.get("warnings") or []:
            if str(note) not in notes:
                notes.append(str(note))
    if nav_result.get("error"):
        notes.insert(0, str(nav_result["error"]))
    notes_html = ""
    if notes:
        items = "".join(f"<li>{html_escape(note)}</li>" for note in notes)
        notes_html = f"<aside class='notes' aria-labelledby='notes-title'><h2 id='notes-title'>估值与处理提示 · {len(notes)}</h2><ul>{items}</ul></aside>"

    def metric(label: str, value: str, raw: Any = None, hint: str = "") -> str:
        return (f"<div class='metric'><dt>{label}</dt>"
                f"<dd class='{kpi_class(raw)}'>{value}</dd>"
                + (f"<dd class='hint'>{hint}</dd>" if hint else "") + "</div>")

    nav = _number(report.get("nav"))
    primary = (
        metric("总资产 · CNY", fmt_money(total_value), hint="按快照估值折合人民币")
        + metric("当日盈亏 · CNY", fmt_money(report.get("pnl")), report.get("pnl"))
        + metric("单位净值", "—" if nav is None else f"{nav:.6f}")
        + metric("现金占比", fmt_pct_ratio(overview.get("cash_ratio")))
    )
    periods = (
        metric("本月收益率", fmt_pct_change(report.get("mtd_nav_change")), report.get("mtd_nav_change"))
        + metric("本月盈亏 · CNY", fmt_money(report.get("mtd_pnl")), report.get("mtd_pnl"))
        + metric("本年收益率", fmt_pct_change(report.get("ytd_nav_change")), report.get("ytd_nav_change"))
        + metric("本年盈亏 · CNY", fmt_money(report.get("ytd_pnl")), report.get("ytd_pnl"))
    )
    top_rows = []
    for holding in top_holdings[:10]:
        quantity = _number(holding.get("quantity"))
        quantity_text = "—" if quantity is None else f"{quantity:,f}"
        if holding.get("code") == "CASH+MMF":
            quantity_text = "不适用（合并）"
        top_rows.append(
            f"<tr><th scope='row'><span class='security'>{html_escape(holding.get('name') or '名称未提供')}</span>"
            f"<span class='code'>{html_escape(holding.get('code'))}</span></th>"
            f"<td data-label='券商'>{html_escape(holding.get('broker') or '未分类')}</td>"
            f"<td data-label='数量' class='number'>{quantity_text}</td>"
            f"<td data-label='市值 · CNY' class='number'>{fmt_money(holding.get('market_value'))}</td>"
            f"<td data-label='占比' class='number'>{fmt_pct_ratio(holding.get('weight'))}</td></tr>"
        )
    top_table = (f"<table class='holdings'><caption>{holdings_caption}</caption>"
                 "<thead><tr><th scope='col'>资产 / 代码</th><th scope='col'>券商</th><th scope='col' class='number'>数量</th>"
                 "<th scope='col' class='number'>市值 · CNY</th><th scope='col' class='number'>占比</th></tr></thead>"
                 f"<tbody>{''.join(top_rows)}</tbody></table>" if top_rows else
                 "<p class='empty'>没有可展示的持仓。请核对快照和估值提示；空列表不代表资产为零。</p>")
    broker_rows = "".join(
        f"<tr><th scope='row'>{html_escape(row['broker'])}</th>"
        f"<td class='number'>{fmt_money(row['value'])}</td><td class='number'>{fmt_pct_ratio(row['ratio'])}</td></tr>"
        for row in build_market_breakdown(holdings, total_value)
    )
    broker_table = ("<table><caption>按快照中的券商归属汇总</caption><thead><tr><th scope='col'>券商</th>"
                    "<th scope='col' class='number'>市值 · CNY</th><th scope='col' class='number'>占比</th></tr></thead>"
                    f"<tbody>{broker_rows}</tbody></table>" if broker_rows else "<p class='empty'>快照未提供券商分布。</p>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>投资组合日报 · {html_escape(account)} · {html_escape(date_text)}</title>
<style>
:root {{ color-scheme:light dark; --bg:#f3f5f8; --card:#fff; --text:#172638; --muted:#536477; --border:#d9e0e8; --accent:#235c94; --up:#176d4b; --down:#bb3341; --note:#fff5df; }}
* {{ box-sizing:border-box; }}
html {{ scroll-padding-top:16px; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
a {{ color:var(--accent); text-underline-offset:4px; }}
a:focus-visible,summary:focus-visible {{ outline:3px solid var(--accent); outline-offset:5px; }}
.skip {{ position:absolute; top:-100px; padding:12px; background:var(--card); }}
.skip:focus {{ top:8px; left:8px; }}
.wrap {{ max-width:1120px; margin:40px auto; padding:0 24px; }}
header {{ border-bottom:2px solid var(--text); padding-bottom:24px; }}
.eyebrow {{ color:var(--muted); font-size:13px; letter-spacing:.08em; margin:0; }}
h1 {{ font-size:32px; letter-spacing:-.03em; line-height:1.25; margin:8px 0 16px; }}
h2 {{ font-size:20px; margin:0 0 12px; }}
p {{ margin:8px 0; }}
.context {{ display:flex; flex-wrap:wrap; gap:8px 24px; overflow-wrap:anywhere; }}
.status {{ font-weight:600; }}
nav {{ display:flex; flex-wrap:wrap; gap:12px 24px; margin:16px 0 24px; }}
nav a {{ padding:6px 0; }}
section {{ margin-top:28px; }}
.panel {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:24px; }}
.grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:24px; margin:0; }}
.metric {{ min-width:0; }}
dt,.hint,caption,.muted {{ color:var(--muted); }}
dt {{ font-size:13px; margin-bottom:8px; }}
dd {{ margin:0; font-size:24px; font-weight:650; line-height:1.35; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }}
dd.hint {{ font-size:12px; font-weight:400; margin-top:8px; }}
.primary .metric:first-child {{ grid-column:span 2; }}
.primary {{ grid-template-columns:repeat(5,minmax(0,1fr)); }}
.primary .metric:first-child dd:not(.hint) {{ font-size:32px; }}
.cash-flow {{ border-top:1px solid var(--border); padding-top:16px; margin-top:20px; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
.notes {{ padding:18px 24px; background:var(--note); border-left:4px solid var(--accent); border-radius:4px; overflow-wrap:anywhere; }}
.notes h2 {{ font-size:16px; }} .notes ul {{ margin:0; padding-left:20px; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
caption {{ text-align:left; font-size:13px; padding-bottom:16px; }}
th,td {{ padding:14px 10px; border-bottom:1px solid var(--border); text-align:left; overflow-wrap:anywhere; }}
thead th {{ color:var(--muted); font-size:12px; font-weight:600; }}
tbody th {{ font-weight:500; }}
.number {{ text-align:right; font-variant-numeric:tabular-nums; }}
.security,.code {{ display:block; }} .code {{ color:var(--muted); font-size:12px; }}
.empty {{ color:var(--muted); padding:20px 0; }}
summary {{ cursor:pointer; padding:8px 0; font-weight:600; }}
.trace {{ overflow-wrap:anywhere; font-size:13px; }}
footer {{ color:var(--muted); margin-top:28px; font-size:12px; }}
@media(prefers-color-scheme:dark) {{ :root {{ --bg:#121a24; --card:#1a2634; --text:#e5edf5; --muted:#b0c0d0; --border:#39495b; --accent:#8fc3f6; --up:#7fdbb1; --down:#ff9da7; --note:#302b20; }} }}
@media(max-width:900px) {{ .grid,.primary {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .primary .metric:first-child {{ grid-column:auto; }} }}
@media(max-width:600px) {{
 .wrap {{ margin:20px auto; padding:0 16px; }} h1 {{ font-size:26px; }}
 .panel {{ padding:18px 14px; }} .grid {{ gap:22px 12px; }}
 .primary .metric:first-child {{ grid-column:span 2; }} dd {{ font-size:21px; }}
 .primary .metric:first-child dd:not(.hint) {{ font-size:30px; }}
 .holdings,.holdings tbody,.holdings tr,.holdings td,.holdings th {{ display:block; }}
 .holdings caption {{ display:block; }}
 .holdings thead {{ position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }}
 .holdings tr {{ padding:12px 0; border-bottom:1px solid var(--border); }}
 .holdings th,.holdings td {{ border:0; padding:5px 0; }}
 .holdings td {{ display:grid; grid-template-columns:90px minmax(0,1fr); gap:10px; text-align:right; }}
 .holdings td::before {{ content:attr(data-label); text-align:left; color:var(--muted); font-size:13px; }}
 th,td {{ padding:12px 5px; }}
}}
@media(max-width:480px) {{ .grid,.primary {{ grid-template-columns:minmax(0,1fr); }} .primary .metric:first-child {{ grid-column:auto; }} }}
@media print {{ :root {{ color-scheme:light; --bg:#fff; --card:#fff; --text:#111; --muted:#444; --border:#aaa; --up:#111; --down:#111; --note:#fff; }} .wrap {{ margin:0; max-width:none; }} nav,.skip {{ display:none; }} .panel {{ break-inside:avoid; }} details .trace {{ display:block; }} }}
</style>
</head>
<body>
<a class="skip" href="#overview">跳至资产概览</a>
<main class="wrap">
<header><p class="eyebrow">PORTFOLIO MANAGEMENT / DAILY REVIEW</p><h1>投资组合日报</h1>
<div class="context"><span>账户 <strong>{html_escape(account_text)}</strong></span><span>NAV 日期 <strong>{html_escape(date_text)}</strong></span><span class="status">{status}</span></div>
<p class="muted trace">快照时间：{html_escape(snapshot_time)}</p></header>
<nav aria-label="日报章节"><a href="#overview">资产概览</a><a href="#returns">收益表现</a><a href="#holdings">主要持仓</a><a href="#brokers">券商分布</a><a href="#audit">追溯信息</a></nav>
{notes_html}
<section id="overview" class="panel" aria-labelledby="overview-title"><h2 id="overview-title">资产概览</h2><dl class="grid primary">{primary}</dl><p class="cash-flow">当日现金流 · CNY <strong>{fmt_money(report.get('cash_flow'))}</strong> <span class="muted">／ 现金流与投资盈亏分列</span></p></section>
<section id="returns" class="panel" aria-labelledby="returns-title"><h2 id="returns-title">收益表现</h2><dl class="grid">{periods}</dl></section>
<section id="holdings" class="panel" aria-labelledby="holdings-title"><h2 id="holdings-title">主要持仓</h2>{top_table}</section>
<section id="brokers" class="panel" aria-labelledby="brokers-title"><h2 id="brokers-title">券商分布</h2>{broker_table}</section>
<section id="audit"><details open><summary>追溯信息与口径</summary><div class="trace"><p>snapshot_time: {html_escape(snapshot_time)}</p><p>run_id: {html_escape(run_id)}</p><p>金额统一折合人民币；“—”表示未提供或不可用，不代表零。现金与货基合并项不展示混合单位的数量。</p><p>这是静态快照，不会自动刷新。NAV 已记录仅表示本次写入结果，不等同于通过全部质量检查。</p></div></details></section>
<footer>PM · 本地日报 · 依据同一次估值快照生成</footer>
</main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renderer-only daily report HTML generator. Data must come from scripts/publish_daily_report.py."
    )
    parser.add_argument("--bundle", required=True, help="JSON bundle path produced by publish_daily_report.py or compatible caller.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="HTML output path.")
    return parser.parse_args()


def load_bundle(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        bundle = json.load(f)
    missing = [key for key in ("snapshot", "report") if key not in bundle]
    if missing:
        raise ValueError(f"bundle missing required keys: {', '.join(missing)}")
    bundle.setdefault("full", {})
    return bundle


def main() -> None:
    args = parse_args()
    html = render_html(load_bundle(args.bundle))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(str(output))


if __name__ == "__main__":
    main()
