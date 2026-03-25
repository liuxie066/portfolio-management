"""Tencent batch quote helper.

Tencent quote API supports multiple codes per request:
  http://qt.gtimg.cn/q=sh600519,sz000651,hk00700,jj007722

This module:
- chunks codes to avoid overly long URLs
- parses response into a mapping {query_code: parts[]}

We keep it minimal (requests + stdlib) and avoid coupling to project models.
"""

from __future__ import annotations

from typing import Dict, List, Iterable, Tuple
import re


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_multi_payload(text: str) -> Dict[str, List[str]]:
    """Parse Tencent multi-line payload.

    Each line is like: v_sh600519="...~...";
    """
    out: Dict[str, List[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^v_([a-z0-9\.]+)=\"([^\"]*)\";?", line, flags=re.IGNORECASE)
        if not m:
            continue
        code = m.group(1)
        payload = m.group(2)
        out[code] = payload.split('~') if payload is not None else []
    return out


def fetch_batch(session, query_codes: List[str], timeout: int = 8, chunk_size: int = 50) -> Dict[str, List[str]]:
    """Fetch Tencent quotes in batches.

    Args:
        session: requests.Session
        query_codes: list like ['sh600519','hk00700','jj007722']
        timeout: per-request timeout
        chunk_size: number of codes per request

    Returns:
        mapping query_code -> parts list
    """
    results: Dict[str, List[str]] = {}
    if not query_codes:
        return results

    for batch in chunked(query_codes, chunk_size):
        url = "http://qt.gtimg.cn/q=" + ",".join(batch)
        resp = session.get(url, timeout=timeout)
        resp.encoding = 'gb2312'
        parsed = parse_multi_payload(resp.text)
        results.update(parsed)

    return results
