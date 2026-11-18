"""Offline before/after NAV report benchmark; run from repository root."""
from __future__ import annotations

import gc
import json
import platform
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.app import daily_report_payload_service as report_module  # noqa: E402
from src.domain.nav import performance as current  # noqa: E402
from src.models import NAVHistory  # noqa: E402

BASE = "43def4c"
legacy = types.ModuleType("pm_benchmark_legacy")
sys.modules[legacy.__name__] = legacy
source = subprocess.check_output(["git", "show", f"{BASE}:src/domain/nav/performance.py"], cwd=ROOT, text=True)
exec(compile(source, "baseline-performance.py", "exec"), legacy.__dict__)


def measure(call):
    for _ in range(3):
        call()
    samples = []
    for _ in range(21):
        start = time.perf_counter_ns()
        call()
        samples.append((time.perf_counter_ns() - start) / 1e6)
    gc.collect()
    tracemalloc.start()
    call()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(median_ms=round(statistics.median(samples), 3), max_ms=round(max(samples), 3),
                peak_allocated_kib=round(peak / 1024, 1), repetitions=len(samples))


def scenario(count):
    navs = [NAVHistory(record_id=f"rec{i}", account="bench", date=date(2019, 12, 31)+timedelta(days=i),
                       total_value=10000+i, nav=1+i/10000, shares=10000,
                       details={"source":"synthetic", "holdings_count":80}) for i in range(count)]
    latest = navs[-1]
    random.Random(1985).shuffle(navs)
    reads = []
    def read(account, days):
        reads.append((account, days))
        return navs
    service = report_module.DailyReportPayloadService(
        account="bench", storage=types.SimpleNamespace(get_nav_history=read), portfolio=None,
        read_service=types.SimpleNamespace(get_distribution=lambda **_: {"success":True}))
    snapshot = {"snapshot_time":"2026-11-18T08:00:00+08:00", "holdings_data":{"holdings":[]}}
    def report():
        return service.build(snapshot=snapshot, nav_record=latest, nav_result={"success":True}, run_id="bench")
    results, payloads = {}, []
    for label, module in (("before", legacy), ("after", current)):
        with patch.object(report_module, "NavPerformanceCalculator", module.NavPerformanceCalculator), patch.object(report_module, "sort_navs", module.sort_navs), patch.object(report_module.config, "get_start_year", return_value=2021):
            payload = report()
            payload.pop("stage_timings")
            payloads.append(payload)
            results[label] = measure(report)
    assert payloads[0] == payloads[1], "Report financial output changed"
    assert all(read == ("bench", 9999) for read in reads)
    results["identical_financial_payload"] = True
    results["source_reads_per_build"] = 1
    results["rows"] = count
    return results


if __name__ == "__main__":
    result = {"base": BASE, "python": platform.python_version(), "platform": platform.platform(),
              "method":"3 warmups; 21 sequential samples; separate tracemalloc peak; synthetic in-memory storage; no network",
              "scenarios":[scenario(2500), scenario(10000)]}
    print(json.dumps(result, indent=2))
