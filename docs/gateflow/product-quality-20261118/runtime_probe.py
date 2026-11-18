"""Isolated HTTP startup/resident-memory probe, no external clients or workload."""
from __future__ import annotations
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time


def child(source):
    sys.path.insert(0, str(source))
    started = time.perf_counter()
    from fastapi.testclient import TestClient
    from src.service.application import PortfolioService
    from src.service.http import create_app
    from src.app.operation_state import OperationStateStore

    service = PortfolioService(storage=object())
    service._operation_state_store = OperationStateStore()
    def no_refresh(**kwargs):
        raise AssertionError("Synthetic probe must not request broker work")
    service.refresh_futu_holdings = no_refresh
    samples = []
    # Enter the real lifespan: worker owns an isolated empty SQLite queue.
    with TestClient(create_app(service), client=("127.0.0.1", 50000)) as client:
        startup = (time.perf_counter() - started) * 1000
        for i in range(250):
            tick = time.perf_counter()
            response = client.get('/health')
            assert response.status_code == 200 and response.json()['status'] == 'ok'
            if i >= 25:
                samples.append((time.perf_counter()-tick)*1000)
        rss = int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())],text=True).strip()) / 1024
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes; Linux reports KiB.
    peak_mib = peak / (1024*1024 if sys.platform == 'darwin' else 1024)
    return dict(startup_ms=round(startup,2), health_median_ms=round(statistics.median(samples),3),
                health_max_ms=round(max(samples),3), resident_after_250_requests_mib=round(rss,2),
                process_peak_rss_mib=round(peak_mib,2), network='none (ASGI TestClient; real empty-queue lifespan)')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        print(json.dumps(child(Path(sys.argv[2]).resolve())))
    else:
        root = Path(__file__).resolve().parents[3]
        results = {}
        for label, source in [('before',root/'reports/product-quality/baseline-source'),('after',root)]:
            runs = []
            with tempfile.TemporaryDirectory(prefix='pm-quality-runtime-') as temp:
                config = Path(temp)/'config.yaml'
                config.write_text('data:' + chr(10) + '  dir: ' + temp + '/data' + chr(10))
                environment = {'PATH':os.environ['PATH'],'PORTFOLIO_CONFIG_FILE':str(config)}
                for _ in range(5):
                    result = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',str(source)],
                                            env=environment,cwd=source,text=True,capture_output=True,check=True)
                    runs.append(json.loads(result.stdout))
            results[label] = {'runs':runs, 'note':'Five cold processes; same Python/dependencies and synthetic input. No memory optimization claimed.'}
        print(json.dumps(results,indent=2))
