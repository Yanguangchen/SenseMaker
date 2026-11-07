"""Keep the exact membership of a completed scrape, independently of saved titles."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from modules.database import get_post_id
from modules.signals import is_analyzable


def standard_dataset_title(source: str, completed_at: float | None = None) -> str:
    """Standardized dataset nomenclature: '<Source> · YYYY-MM-DD HH:MM:SS UTC'."""
    when = (datetime.fromtimestamp(completed_at, timezone.utc)
            if completed_at is not None else datetime.now(timezone.utc))
    timestamp = when.strftime("%Y-%m-%d %H:%M:%S UTC")
    label = str(source or "Dataset").strip() or "Dataset"
    return f"{label} · {timestamp}"


def collection_snapshot(rows: list[dict[str, Any]], source: str, completed_at: float) -> dict[str, Any]:
    records = {}
    for row in rows:
        url = str(row.get("url") or "").strip()
        if url and is_analyzable(row):
            ident = get_post_id(url)
            records[ident] = {**deepcopy(row), "url": url, "_id": ident}
    return {"id": uuid4().hex, "source": source, "completed_at": completed_at,
            "title": standard_dataset_title(source, completed_at), "rows": list(records.values()), "saved": False}
