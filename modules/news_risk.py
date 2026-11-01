"""Saved Google News scans and the border-tension risk charts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from modules.database import _get_collection_name
from modules.google_news import RISK_LEVELS

LEVEL_COLORS = {"Low": "#16a34a", "Elevated": "#d97706", "High": "#dc2626"}
FLAG_COLORS = {"Escalation": "#dc2626", "Border tension": "#d97706", "Boycott": "#7c3aed"}


def news_history_collection() -> str:
    return f"{_get_collection_name()}_news_scans"


def persist_news_scan(db: Any, snapshot: dict[str, Any]) -> None:
    # The preallocated scan ID keeps a retried write from adding a second point.
    db.collection(news_history_collection()).document(snapshot["scan_id"]).set(snapshot, timeout=30)


def load_news_history(db: Any) -> list[dict[str, Any]]:
    rows = [doc.to_dict() for doc in db.collection(news_history_collection())
            .order_by("scanned_at").stream(timeout=30)]
    return [row for row in rows if row]


def _bands() -> list[dict[str, Any]]:
    bounds = sorted(threshold for threshold, _ in RISK_LEVELS) + [100]
    labels = {threshold: label for threshold, label in RISK_LEVELS}
    return [{"from": low, "to": high, "level": labels[low]} for low, high in zip(bounds, bounds[1:])]


def risk_chart_spec(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Risk per scan on a fixed 0–100 scale over shaded Low/Elevated/High bands; rising scans are marked."""
    points = [{**row, "scanned_label": datetime.fromisoformat(row["scanned_at"]).strftime("%d %b %Y %H:%M UTC"),
               "delta_label": "First scan" if row.get("risk_delta") is None else f"{row['risk_delta']:+.1f}"}
              for row in rows]
    y = {"type": "quantitative", "scale": {"domain": [0, 100]}, "axis": {"values": [0, 20, 45, 100]}}
    x = {"field": "scanned_at", "type": "temporal", "scale": {"type": "utc"}, "title": "Scan completed (UTC)",
         "axis": {"format": "%d %b %H:%M", "tickCount": 5, "labelOverlap": "greedy", "grid": False}}
    level_scale = {"domain": list(LEVEL_COLORS), "range": list(LEVEL_COLORS.values())}
    tooltip = [
        {"field": "scanned_label", "type": "nominal", "title": "Scanned"},
        {"field": "risk_score", "type": "quantitative", "title": "Border tension risk", "format": ".1f"},
        {"field": "risk_level", "type": "nominal", "title": "Level"},
        {"field": "delta_label", "type": "nominal", "title": "Change"},
        {"field": "escalation_count", "type": "quantitative", "title": "Escalation articles"},
        {"field": "tension_count", "type": "quantitative", "title": "Tension articles"},
        {"field": "boycott_count", "type": "quantitative", "title": "Boycott articles"},
        {"field": "article_count", "type": "quantitative", "title": "Articles scanned"},
    ]
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "Border tension risk from each Google News scan, over shaded risk bands, with the boycott index.",
        "height": 300,
        "config": {"view": {"stroke": None}, "axis": {"gridOpacity": 0.2, "labelOverlap": True}},
        "layer": [
            {"data": {"values": _bands()}, "mark": {"type": "rect", "opacity": 0.12},
             "encoding": {"y": {"field": "from", **y, "title": "Risk (0–100)"}, "y2": {"field": "to"},
                          "color": {"field": "level", "type": "nominal", "scale": level_scale,
                                    "legend": {"title": "Risk level", "orient": "top", "symbolOpacity": 0.9}}}},
            {"data": {"values": points},
             "mark": {"type": "line", "color": "#0f172a", "strokeWidth": 2.5},
             "encoding": {"x": x, "y": {"field": "risk_score", **y},
                          "order": {"field": "scanned_at", "type": "temporal"}}},
            {"data": {"values": points},
             "mark": {"type": "line", "color": FLAG_COLORS["Boycott"],
                      "strokeWidth": 2, "strokeDash": [6, 4]},
             "encoding": {"x": x, "y": {"field": "boycott_index", **y},
                          "order": {"field": "scanned_at", "type": "temporal"},
                          "tooltip": [{"field": "scanned_label", "type": "nominal", "title": "Scanned"},
                                      {"field": "boycott_index", "type": "quantitative",
                                       "title": "Boycott index", "format": ".1f"}]}},
            {"data": {"values": points},
             "mark": {"type": "point", "filled": True, "stroke": "#0f172a", "strokeWidth": 1},
             "encoding": {"x": x, "y": {"field": "risk_score", **y},
                          "color": {"field": "risk_level", "type": "nominal", "scale": level_scale, "legend": None},
                          "size": {"condition": {"test": "datum.rising", "value": 260}, "value": 90},
                          "tooltip": tooltip}},
            {"data": {"values": [p for p in points if p.get("rising")]},
             "mark": {"type": "text", "text": "▲ Rising", "dy": -16, "fontWeight": 700, "fontSize": 12,
                      "color": LEVEL_COLORS["High"]},
             "encoding": {"x": x, "y": {"field": "risk_score", **y}}},
        ],
    }


def daily_chart_spec(counts: list[dict[str, Any]]) -> dict[str, Any]:
    """Flagged articles per publication day within one scan."""
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "Flagged Google News articles per publication day.",
        "height": 220,
        "data": {"values": counts},
        "mark": {"type": "bar", "cornerRadiusTopLeft": 3, "cornerRadiusTopRight": 3},
        "config": {"view": {"stroke": None}, "axis": {"gridOpacity": 0.2}},
        "encoding": {
            "x": {"field": "day", "type": "temporal", "timeUnit": "utcyearmonthdate", "title": "Published (UTC)",
                  "axis": {"format": "%d %b", "labelOverlap": "greedy", "grid": False}},
            "y": {"field": "articles", "type": "quantitative", "aggregate": "sum", "title": "Flagged articles",
                  "axis": {"tickMinStep": 1, "format": "d"}},
            "color": {"field": "flag", "type": "nominal", "title": "Flag",
                      "scale": {"domain": list(FLAG_COLORS), "range": list(FLAG_COLORS.values())},
                      "legend": {"orient": "top"}},
            "tooltip": [{"field": "day", "type": "temporal", "timeUnit": "utcyearmonthdate", "title": "Published"},
                        {"field": "flag", "type": "nominal", "title": "Flag"},
                        {"field": "articles", "type": "quantitative", "aggregate": "sum", "title": "Articles"}],
        },
    }
