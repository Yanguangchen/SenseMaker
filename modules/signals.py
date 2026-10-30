"""Evidence validation for content-level Thailand monitoring, never user profiling."""

from __future__ import annotations

from typing import Any

ANALYSIS_VERSION = 2
SIGNAL_LABELS = {
    "none": "No signal detected",
    "negative_sentiment": "Negative sentiment",
    "tension": "Reported tension",
    "boycott_call": "Explicit boycott call",
    "boycott_report": "Reporting a boycott",
    "boycott_opposition": "Opposing a boycott",
    "unclear": "Needs review",
}
RELEVANCE = {"explicit", "watchlist", "unclear", "not_related"}
NON_CONTENT_TYPES = {"batch_error", "batch_warning", "emergency_fallback", "page_fallback", "permalink_fallback"}
MAX_TEXT = 16000
MAX_CONTEXT = 6000


def watch_terms(text: str) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))[:100]


def content_sources(post: dict) -> dict[str, str]:
    sources = {"raw_text": str(post.get("raw_text", ""))[:MAX_TEXT]}
    if post.get("parent_text"):
        sources["parent_text"] = str(post["parent_text"])[:MAX_CONTEXT]
    # Existing Facebook collection stores comments under their original post.
    comments = post.get("comments") if isinstance(post.get("comments"), list) else []
    for i, comment in enumerate(comments[:20], 1):
        if isinstance(comment, str) and comment.strip():
            sources[f"comment_{i}"] = comment[:MAX_CONTEXT]
    return sources


def is_analyzable(post: dict) -> bool:
    return (post.get("source_type") not in NON_CONTENT_TYPES and not post.get("content_removed")
            and bool(str(post.get("raw_text", "")).strip()))


def normalize_analysis(item: dict, post: dict, targets: list[str], model: str) -> dict[str, Any]:
    """Reject unsupported model fields and downgrade findings without literal evidence.

    Literal matching proves a quote exists, not that the interpretation is correct.
    Every finding remains an unreviewed model assessment.
    """
    sources = content_sources(post)
    monitor = item.get("monitor")
    if not isinstance(monitor, dict):
        raise ValueError("Missing Thailand monitoring assessment.")
    signal = monitor.get("signal_type")
    relevance = monitor.get("thailand_relevance")
    if signal not in SIGNAL_LABELS or relevance not in RELEVANCE:
        raise ValueError("Invalid monitoring classification.")
    evidence = []
    invalid_evidence = False
    raw_evidence = monitor.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValueError("Invalid evidence list.")
    for entry in raw_evidence[:12]:
        if not isinstance(entry, dict):
            invalid_evidence = True
            continue
        source, quote, purpose = entry.get("source"), entry.get("quote"), entry.get("purpose")
        if (source not in sources or not isinstance(quote, str) or not quote.strip()
                or quote not in sources[source] or purpose not in {"signal", "relevance", "target"}
                or (purpose == "signal" and source == "parent_text")):
            invalid_evidence = True
            continue
        evidence.append({"source": source, "quote": quote, "purpose": purpose})

    notes = []
    if invalid_evidence:
        notes.append("Some model evidence could not be verified against this record.")
    if relevance in {"explicit", "watchlist"} and not any(e["purpose"] == "relevance" for e in evidence):
        relevance = "unclear"
        notes.append("The Thailand or watchlist connection needs supporting evidence.")
    if relevance == "watchlist" and not any(
        term.casefold() in e["quote"].casefold()
        for term in targets for e in evidence if e["purpose"] == "relevance"
    ):
        relevance = "unclear"
        notes.append("No configured watch term appears in the relevance evidence.")
    if signal not in {"none", "unclear"} and not any(e["purpose"] == "signal" for e in evidence):
        signal = "unclear"
        notes.append("The signal lacks an exact quote from the post or its comments.")
    if relevance in {"not_related", "unclear"} and signal not in {"none", "unclear"}:
        signal = "unclear"
        notes.append("The connection to Thailand is not established.")
    target_names = monitor.get("target_names", [])
    if not isinstance(target_names, list):
        target_names = []
    target_names = [name for name in target_names[:20] if isinstance(name, str) and name.strip()
                    and any(name.casefold() in e["quote"].casefold() for e in evidence if e["purpose"] == "target")]
    target_kind = monitor.get("target_kind", "unclear")
    if target_kind not in {"named_project", "company_brand", "country_general", "unclear"}:
        target_kind = "unclear"
    if target_kind in {"named_project", "company_brand"} and not target_names:
        target_kind = "unclear"
        notes.append("No named target could be grounded in the supplied evidence.")
    comments = post.get("comments") if isinstance(post.get("comments"), list) else []
    truncated = (len(str(post.get("raw_text", ""))) > MAX_TEXT
                 or len(str(post.get("parent_text", ""))) > MAX_CONTEXT
                 or len(comments) > 20
                 or any(len(str(c)) > MAX_CONTEXT for c in comments[:20]))
    if truncated:
        notes.append("Only an excerpt of this record was analyzed; review the full source.")
    confidence = monitor.get("confidence", "low")
    if confidence not in {"low", "medium", "high"} or notes:
        confidence = "low"
    sentiment = item.get("sentiment", "Neutral")
    if sentiment not in {"Anxiety", "Anger", "Joy", "Neutral"}:
        sentiment = "Neutral"
    try:
        score = max(1, min(10, int(item.get("risk_score", 1))))
    except (TypeError, ValueError):
        score = 1
    topics = item.get("topics", [])
    return {
        "analysis_version": ANALYSIS_VERSION,
        "model": model,
        "translation": str(item.get("translation", ""))[:24000],
        "sentiment": sentiment,
        "risk_score": score,
        "topics": [str(t)[:100] for t in topics[:12]] if isinstance(topics, list) else [],
        "monitor": {
            "thailand_relevance": relevance, "signal_type": signal,
            "target_kind": target_kind, "target_names": target_names,
            "confidence": confidence, "summary": str(monitor.get("summary", ""))[:2000],
            "evidence": evidence, "validation_notes": notes,
            "review_status": "unreviewed", "input_truncated": truncated,
            "watch_terms": targets,
        },
    }


def is_flagged(analysis: dict) -> bool:
    monitor = analysis.get("monitor", {})
    return (monitor.get("thailand_relevance") in {"explicit", "watchlist"}
            and monitor.get("signal_type") in {"negative_sentiment", "tension", "boycott_call", "boycott_report"})
