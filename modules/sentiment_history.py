"""Durable analysis-run snapshots and the sentiment-over-time chart."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from firebase_admin import firestore

from modules.database import _get_collection_name

SENTIMENT_VALUES = {"Joy": 1, "Neutral": 0, "Anger": -1, "Anxiety": -1}
RISK_SIGNALS = ("tension", "boycott_call", "boycott_report")


def history_collection() -> str:
    return f"{_get_collection_name()}_analysis_runs"


def build_snapshot(
    analyses: list[dict[str, Any]], *, run_id: str, completed_at: str,
    selected_count: int, provider: str, targets: list[str],
) -> dict[str, Any] | None:
    counts = {sentiment: 0 for sentiment in SENTIMENT_VALUES}
    for analysis in analyses:
        sentiment = analysis.get("sentiment")
        if sentiment in counts:
            counts[sentiment] += 1
    scored_count = sum(counts.values())
    if not scored_count:
        return None
    signals = {signal: 0 for signal in RISK_SIGNALS}
    for analysis in analyses:
        monitor = analysis.get("monitor") or {}
        if monitor.get("signal_type") in signals and monitor.get("thailand_relevance") in {"explicit", "watchlist"}:
            signals[monitor["signal_type"]] += 1
    return {
        "run_id": run_id,
        "completed_at": completed_at,
        "score": 100 * sum(SENTIMENT_VALUES[key] * count for key, count in counts.items()) / scored_count,
        "counts": counts,
        "scored_count": scored_count,
        "processed_count": len(analyses),
        "selected_count": selected_count,
        "error_count": selected_count - len(analyses),
        "provider": provider,
        "models": sorted({str(row["model"]) for row in analyses if row.get("model")}),
        "targets": targets,
        "signal_counts": signals,
        "score_version": 1,
    }


def persist_analysis_run(
    db: Any, posts: list[dict[str, Any]], analyses_by_id: dict[str, Any], *,
    provider: str, targets: list[str],
) -> dict[str, Any] | None:
    """Commit post results and their timeline point together, or neither.

    Every invocation is a new run, including reanalysis of the same records.
    The preallocated ID also keeps Firestore commit retries from adding points.
    """
    if not posts:
        return None
    if len(posts) > 499:
        raise ValueError("Analyze at most 499 records in one run.")
    ids = [post.get("_id") for post in posts]
    if any(not isinstance(key, str) or not key or "/" in key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Analysis requires unique, valid record IDs.")
    completed_at = datetime.now(timezone.utc).isoformat()
    snapshot = build_snapshot(
        [analyses_by_id[key] for key in ids if analyses_by_id.get(key)],
        run_id=uuid4().hex, completed_at=completed_at, selected_count=len(posts),
        provider=provider, targets=targets,
    )
    batch = db.batch()
    collection = db.collection(_get_collection_name())
    for key in ids:
        analysis = analyses_by_id.get(key)
        batch.set(collection.document(key), {
            "status": "processed" if analysis else "error",
            "processed_at": completed_at,
            "analysis": analysis if analysis else firestore.DELETE_FIELD,
        }, merge=True)
    if snapshot:
        batch.set(db.collection(history_collection()).document(snapshot["run_id"]), snapshot)
    batch.commit(timeout=30)
    return snapshot


def load_history(db: Any) -> list[dict[str, Any]]:
    """Read all saved run summaries; never infer historical runs from post edits."""
    rows = [doc.to_dict() for doc in db.collection(history_collection())
            .order_by("completed_at").stream(timeout=30)]
    return [row for row in rows if row]


def chart_spec(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One point per run; fixed polarity scale and an equally weighted run mean."""
    average = sum(row["score"] for row in rows) / len(rows)
    points = []
    for row in rows:
        signals = row.get("signal_counts") or {}
        points.append({**row, "completed_label": datetime.fromisoformat(row["completed_at"]).strftime("%d %b %Y %H:%M:%S UTC"),
                       "tension_signals": signals.get("tension", 0),
                       "boycott_signals": signals.get("boycott_call", 0) + signals.get("boycott_report", 0)})
    flagged = [p for p in points if p["tension_signals"] or p["boycott_signals"]]
    y = {"type": "quantitative", "scale": {"domain": [-100, 100]},
         "axis": {"values": [-100, -50, 0, 50, 100]}, "title": "Sentiment"}
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "Sentiment of each completed analysis over time, with a dashed average line.",
        "height": 300,
        "config": {"view": {"stroke": None}, "axis": {"gridOpacity": 0.2, "labelOverlap": True}},
        "layer": [
            {"data": {"values": [{"zero": 0}]}, "mark": {"type": "rule", "color": "#94a3b8", "opacity": 0.5},
             "encoding": {"y": {"field": "zero", **y}}},
            {"data": {"values": points},
             "mark": {"type": "line", "color": "#2563eb", "strokeWidth": 2.5,
                      "point": {"filled": True, "size": 75}},
             "encoding": {
                 "x": {"field": "completed_at", "type": "temporal", "scale": {"type": "utc"},
                       "title": "Analysis completed (UTC)",
                       "axis": {"format": "%d %b %H:%M", "tickCount": 5, "labelOverlap": "greedy", "grid": False}},
                 "y": {"field": "score", **y},
                 "order": {"field": "completed_at", "type": "temporal"},
                 "tooltip": [
                     {"field": "completed_label", "type": "nominal", "title": "Completed"},
                     {"field": "score", "type": "quantitative", "title": "Sentiment", "format": ".1f"},
                     {"field": "processed_count", "type": "quantitative", "title": "Analyzed records"},
                     {"field": "error_count", "type": "quantitative", "title": "Failed records"},
                     {"field": "provider", "type": "nominal", "title": "Provider"},
                 ],
             }},
            {"data": {"values": [{"average": average}]},
             "mark": {"type": "rule", "color": "#d97706", "strokeWidth": 2, "strokeDash": [6, 4]},
             "encoding": {"y": {"field": "average", **y}}},
            {"data": {"values": [{"average": average, "label": f"Average {average:+.1f}"}]},
             "mark": {"type": "text", "color": "#f8fafc", "align": "left", "fontWeight": 600, "fontSize": 12,
                      "dy": 14 if average > 85 else -10},
             "encoding": {"x": {"value": 8}, "y": {"field": "average", **y}, "text": {"field": "label"}}},
            {"data": {"values": flagged},
             "mark": {"type": "point", "shape": "circle", "size": 320, "strokeWidth": 3, "color": "#dc2626",
                      "filled": False},
             "encoding": {
                 "x": {"field": "completed_at", "type": "temporal", "scale": {"type": "utc"}},
                 "y": {"field": "score", **y},
                 "tooltip": [
                     {"field": "completed_label", "type": "nominal", "title": "Completed"},
                     {"field": "tension_signals", "type": "quantitative", "title": "Border tension signals"},
                     {"field": "boycott_signals", "type": "quantitative", "title": "Boycott signals"},
                 ],
             }},
        ],
    }
