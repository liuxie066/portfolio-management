"""Shared flat-markdown shells for outbound PM notifications (aligned with OM)."""
from __future__ import annotations

from typing import Any, Sequence


def _flat_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return " · ".join(part.strip() for part in text.splitlines() if part.strip())


def render_receipt(
    *,
    title: str,
    receipt_type: str,
    status: str,
    fields: Sequence[tuple[str, Any]] = (),
    sections: Sequence[tuple[str, Sequence[Any]]] = (),
) -> str:
    title_text = _flat_text(title) or "-"
    lines = [
        f"# PM · 回执 · {title_text}",
        "",
        f"类型｜{_flat_text(receipt_type) or '-'}",
        f"状态｜{_flat_text(status) or '-'}",
    ]

    for label, value in fields:
        label_text = _flat_text(label)
        if label_text:
            lines.append(f"{label_text}｜{_flat_text(value) or '-'}")

    for section_title, rows in sections:
        section_text = _flat_text(section_title)
        flat_rows = [_flat_text(row) for row in rows]
        flat_rows = [row for row in flat_rows if row]
        if section_text and flat_rows:
            lines.extend(["", f"## {section_text}", *flat_rows])

    return "\n".join(lines).strip()


__all__ = ["render_receipt"]
