"""Per-user GenCite analyses, stored through the server Firestore client.

The GenCite web app keeps saved analyses in one browser's localStorage. Here each
signed-in user has a private library that follows them across devices:

    sensemaker_gencite/{owner_hash}/analyses/{analysis_id}
    sensemaker_gencite/{owner_hash}/published/{title_hash}

The owner is derived from the email verified by server-side authentication, as
for saved links. Separation is enforced by these queries, not Firestore rules.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from modules.saved_links import library_owner_id

ROOT_COLLECTION = "sensemaker_gencite"
MAX_NAME_CHARS = 120
SAVED_FIELDS = ("brand", "overallPct", "totalCitations", "totalRuns", "queries", "runCount",
                "previousOverallPct", "model", "context")


def _owner(db: Any, email: str) -> Any:
    return db.collection(ROOT_COLLECTION).document(library_owner_id(email))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_name(name: Any) -> str:
    name = " ".join(str(name or "").split())
    if not name or len(name) > MAX_NAME_CHARS:
        raise ValueError(f"Name must contain between 1 and {MAX_NAME_CHARS} characters.")
    return name


def save_analysis(db: Any, email: str, name: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """Save a combined result with GenCite's SavedAnalysis shape; returns the record."""
    if not result.get("queries"):
        raise ValueError("There is no analysis to save.")
    record = {key: result[key] for key in SAVED_FIELDS if key in result}
    record.update({"id": uuid.uuid4().hex, "name": clean_name(name), "savedAt": _now(),
                   "queryCount": len(result["queries"]), "runCount": result.get("runCount", 1)})
    _owner(db, email).collection("analyses").document(record["id"]).set(record, timeout=30, retry=None)
    return record


def list_analyses(db: Any, email: str) -> List[Dict[str, Any]]:
    rows = [doc.to_dict() or {} for doc in _owner(db, email).collection("analyses").stream(timeout=30, retry=None)]
    rows = [row for row in rows if row.get("id") and isinstance(row.get("queries"), list)]
    return sorted(rows, key=lambda row: str(row.get("savedAt", "")), reverse=True)


def rename_analysis(db: Any, email: str, analysis_id: str, name: Any) -> None:
    _owner(db, email).collection("analyses").document(analysis_id).update(
        {"name": clean_name(name)}, timeout=30, retry=None)


def delete_analysis(db: Any, email: str, analysis_id: str) -> None:
    _owner(db, email).collection("analyses").document(analysis_id).delete(timeout=30, retry=None)


def _title_id(title: str) -> str:
    return hashlib.sha256(title.strip().casefold().encode("utf-8")).hexdigest()


def load_published(db: Any, email: str) -> Dict[str, str]:
    """Recommendation titles marked as published, with the time they were marked."""
    rows = [doc.to_dict() or {} for doc in _owner(db, email).collection("published").stream(timeout=30, retry=None)]
    return {row["title"]: row.get("publishedAt", "") for row in rows if isinstance(row.get("title"), str)}


def set_published(db: Any, email: str, title: str, published: bool) -> None:
    ref = _owner(db, email).collection("published").document(_title_id(title))
    if published:
        ref.set({"title": title.strip(), "publishedAt": _now()}, timeout=30, retry=None)
    else:
        ref.delete(timeout=30, retry=None)
