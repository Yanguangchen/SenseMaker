"""The client's appendix templates, filled from CrisisRadar records.

Comment Sentiment Analysis, Velocity Tracker and Negative Post Report keep the layout
the client already uses. Values come only from collected records, their saved checks
and their AI analysis. Fields CrisisRadar does not collect, and decisions that belong
to the analyst, stay blank as on the paper template, so a filled report never implies
data it lacks.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from modules.engagement import checks_for, is_tracked, latest_pair, velocity
from modules.signals import SIGNAL_LABELS, is_flagged

CSS_PATH = Path(__file__).parent / "theme" / "client_report.css"
BUCKETS = ("Negative", "Neutral", "Positive")
BUCKET_OF = {"Anger": "Negative", "Anxiety": "Negative", "Neutral": "Neutral", "Joy": "Positive"}
SHOWN_CHECKS = 8
EYEBROW = "CrisisRadar — report"
NOT_COLLECTED = "Not collected"
METRIC_LABELS = {"comments": "platform total", "collected_comments": "collected sample"}
_STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each even every few for from further get gets got had has
have having he her here hers him his how i if in into is it its itself just like many may me more most much must
my no nor not now of off on once one only or other our ours out over own people said same say says she should so
some such than that the their theirs them then there these they this those through to too under until up upon us
very was we were what when where which while who whom why will with would you your yours yet still really dont
didnt doesnt cant wont isnt arent im ive youre thats theres lets let make made want know think see going go come
""".split())


def _text(value: Any) -> str:
    return html.escape(" ".join(("" if value is None else str(value)).split()), quote=True)


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _when(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def format_date(value: Any) -> str:
    moment = _when(value)
    return moment.strftime("%d %b %Y") if moment else ""


def format_time(value: Any) -> str:
    moment = _when(value)
    return moment.strftime("%d %b %Y · %H:%M UTC") if moment else ""


def _number(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _signed(value: int | float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0" + suffix
    if isinstance(value, float):
        return f"{value:+.0f}{suffix}" if abs(value) >= 10 else f"{value:+.1f}{suffix}"
    return f"{value:+,}{suffix}"


# ---------- Record helpers ----------

def report_record(item: dict[str, Any]) -> dict[str, Any]:
    """Accept a Firestore post with an ``analysis`` map or an already flattened result."""
    analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
    record = {**item, **analysis}
    topics = record.get("topics", [])
    if isinstance(topics, str):
        topics = topics.split(",")
    record["topics"] = [str(t).strip() for t in topics if str(t).strip()] if isinstance(topics, list) else []
    record["monitor"] = record.get("monitor") if isinstance(record.get("monitor"), dict) else {}
    record["checks"] = checks_for(record)
    return record


def is_negative(record: dict[str, Any]) -> bool:
    return record.get("sentiment") in {"Anger", "Anxiety"} or is_flagged(record)


def _risk(record: dict[str, Any]) -> int:
    return _int(record.get("risk_score")) or 0


def _priority(record: dict[str, Any]) -> tuple[bool, int]:
    return is_flagged(record), _risk(record)


def report_number(record: dict[str, Any]) -> str:
    key = str(record.get("_id") or hashlib.md5(str(record.get("url", "")).encode()).hexdigest())
    return "NPR-" + key[:6].upper()


def _url(record: dict[str, Any]) -> str:
    url = str(record.get("url") or "").strip()
    return url if urlsplit(url).scheme in {"http", "https"} else ""


def platform_label(record: dict[str, Any]) -> str:
    host = (urlsplit(_url(record)).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    platform = str(record.get("platform") or "").lower()
    if platform == "reddit" or host.endswith("reddit.com"):
        return "Reddit"
    if record.get("source_type") == "news_article" or platform == "google_news":
        return str(record.get("publisher") or "Online news")
    for domain, label in (("facebook.com", "Facebook"), ("fb.watch", "Facebook"), ("fb.com", "Facebook"),
                          ("tiktok.com", "TikTok"), ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
                          ("instagram.com", "Instagram"), ("x.com", "X"), ("twitter.com", "X")):
        if host == domain or host.endswith("." + domain):
            return label
    return host or "Web"


def content_type(record: dict[str, Any]) -> str:
    if record.get("source_type") == "website_page":
        return "Web page"
    if record.get("source_type") == "reddit_comment":
        return "Comment"
    if record.get("source_type") == "x_reply":
        return "Reply"
    if record.get("source_type") == "news_article":
        return "News article"
    url = _url(record).lower()
    if any(part in url for part in ("/videos/", "/video/", "/reel/", "/reels/", "/watch", "fb.watch", "/shorts/")):
        return "Video"
    return "Post"


_CHROME = re.compile(r"(?:like|comment|share|reply|follow|all reactions:|see more|see translation|"
                     r"\d[\d.,]*\s?[km]?|\d[\d.,]*\s?[km]?\s+(?:comments?|shares?|views?|plays?|replies))",
                     re.I)


def capture_text(record: dict[str, Any]) -> str:
    """The captured post text without Facebook's buttons and count lines."""
    text = str(record.get("raw_text") or record.get("title") or "")
    return " ".join(line.strip() for line in text.splitlines()
                    if line.strip() and not _CHROME.fullmatch(line.strip()))


def _headline(record: dict[str, Any], limit: int = 90) -> str:
    return _clip(record.get("title") or record.get("translation") or record.get("raw_text") or "Untitled record", limit)


def _percentages(counts: dict[str, int]) -> dict[str, int]:
    """Whole percentages that add up to 100 (largest remainder)."""
    total = sum(counts.values())
    if not total:
        return {key: 0 for key in counts}
    exact = {key: 100 * value / total for key, value in counts.items()}
    shares = {key: int(value) for key, value in exact.items()}
    for key in sorted(exact, key=lambda k: exact[k] - shares[k], reverse=True)[:100 - sum(shares.values())]:
        shares[key] += 1
    return shares


def _top(values: Iterable[str], count: int) -> list[str]:
    tally = Counter(v.strip() for v in values if v and v.strip())
    return [value for value, _ in tally.most_common(count)]


def _tone(bucket: str, records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    if bucket == "Negative":
        anger = sum(r.get("sentiment") == "Anger" for r in records)
        anxiety = sum(r.get("sentiment") == "Anxiety" for r in records)
        mood = ("Angry" if not anxiety else "Anxious" if not anger else "Mostly angry" if anger > anxiety
                else "Mostly anxious" if anxiety > anger else "Angry and anxious")
    else:
        mood = "Matter-of-fact" if bucket == "Neutral" else "Upbeat"
    topics = _top((t for r in records for t in r["topics"]), 2)
    return mood + (" — about " + ", ".join(topics) if topics else "")


# ---------- Comment Sentiment Analysis ----------

def threat_level(records: list[dict[str, Any]]) -> str:
    flagged = [r for r in records if is_flagged(r)]
    highest = max((_risk(r) for r in records), default=0)
    if highest >= 8 or any(r["monitor"].get("signal_type") == "boycott_call" for r in flagged):
        return "High"
    if flagged or highest >= 5:
        return "Medium"
    return "Low"


def sentiment_view(records: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in records if r.get("sentiment") in BUCKET_OF]
    groups = {bucket: [r for r in scored if BUCKET_OF[r["sentiment"]] == bucket] for bucket in BUCKETS}
    counts = {bucket: len(rows) for bucket, rows in groups.items()}
    flagged = [r for r in records if is_flagged(r)]
    signals = Counter(r["monitor"].get("signal_type") for r in flagged)
    negatives = [r for r in records if is_negative(r)]

    insight = []
    if flagged:
        parts = [f"{signals[s]} {label.lower()}" for s, label in
                 (("boycott_call", "explicit boycott call"), ("boycott_report", "boycott report"),
                  ("tension", "reported tension"), ("negative_sentiment", "negative sentiment"))
                 if signals[s]]
        insight.append(f"{len(flagged)} of {len(records)} records carry a Thailand signal: {', '.join(parts)}.")
    else:
        insight.append(f"No Thailand-linked criticism, tension or boycott signal in {len(records)} records.")
    focus = _top((t for r in negatives for t in r["topics"]), 3)
    if focus:
        insight.append("Negative records focus on " + ", ".join(focus) + ".")
    targets = _top((name for r in flagged for name in r["monitor"].get("target_names", [])), 2)
    if targets:
        insight.append("Most named target: " + ", ".join(targets) + ".")
    unclear = sum(r["monitor"].get("signal_type") == "unclear" for r in records)
    insight.append(f"{unclear} record{'s need' if unclear != 1 else ' needs'} human review." if unclear
                   else "All classifications are AI assessments awaiting human review.")

    types = Counter(r.get("source_type") for r in records)
    posts = [r for r in records if r.get("source_type") not in {"reddit_comment", "x_reply", "news_article", "website_page"}]
    confidence = Counter(r["monitor"].get("confidence") for r in records if r["monitor"])
    with_comments = [r for r in posts if (_int(r.get("comment_count")) or 0) > 0]
    evidenced = sum(bool(r["monitor"].get("evidence")) for r in records)
    engagement = [
        f"{len(records)} records: {len(posts)} posts · {types['reddit_comment'] + types['x_reply']} comments · "
        f"{types['news_article']} news articles · {types['website_page']} website pages.",
        f"{len(with_comments)} of {len(posts)} posts include collected comments"
        + (f" (avg {sum(_int(r.get('comment_count')) or 0 for r in with_comments) / len(with_comments):.1f})."
           if with_comments else "."),
        f"AI confidence: {confidence['high']} high · {confidence['medium']} medium · {confidence['low']} low.",
        f"{evidenced} of {len(records)} records cite exact quotes from the source.",
    ]

    lead = max(records, key=_priority, default=None)
    main_risk = ""
    if lead and (is_flagged(lead) or _risk(lead) >= 5):
        signal = SIGNAL_LABELS.get(lead["monitor"].get("signal_type", ""), "Negative sentiment")
        main_risk = (f"{signal} · priority {_risk(lead)}/10 · {report_number(lead)} — "
                     + _clip(lead["monitor"].get("summary") or lead.get("translation"), 180))
    return {
        "counts": counts, "shares": _percentages(counts), "scored": len(scored),
        "tones": {bucket: _tone(bucket, rows) for bucket, rows in groups.items()},
        "insight": insight[:4], "engagement": engagement, "threat": threat_level(records), "main_risk": main_risk,
    }


# ---------- Velocity Tracker ----------

def velocity_view(records: list[dict[str, Any]], *, negative_only: bool = True) -> dict[str, Any]:
    rows = []
    for record in sorted((r for r in records if is_tracked(r) and (not negative_only or is_negative(r))),
                         key=_priority, reverse=True):
        result = velocity(record["checks"])
        values = [value for _, value in result["points"]]
        rows.append({"record": record, "velocity": result, "values": values[-SHOWN_CHECKS:],
                     "times": [at for at, _ in result["points"]][-SHOWN_CHECKS:],
                     "truncated": len(values) > SHOWN_CHECKS})
    width = max((len(row["values"]) for row in rows), default=0)
    totals, change_abs, change_pct = [], [None], [None]
    for index in range(width):
        present = [row["values"][index] for row in rows if len(row["values"]) > index]
        totals.append(sum(present) if present else None)
        if index:
            pairs = [(row["values"][index - 1], row["values"][index]) for row in rows if len(row["values"]) > index]
            delta, base = sum(b - a for a, b in pairs), sum(a for a, _ in pairs)
            change_abs.append(delta if pairs else None)
            change_pct.append(100 * delta / base if pairs and base else None)
    moving = [row["velocity"] for row in rows if row["velocity"]["change"] is not None]
    total_change = sum(v["change"] for v in moving) if moving else None
    total_prior = sum(v["prior"] for v in moving)
    statuses = Counter(row["velocity"]["status"] or "New" for row in rows)
    times = [at for row in rows for at in row["times"]]

    if rows:
        parts = [f"{statuses[s]} {s.lower()}" for s in ("Viral", "Live", "Slowing", "Dead") if statuses[s]]
        if statuses["New"]:
            first = sum(not row["values"] for row in rows)
            if first:
                parts.append(f"{first} awaiting a first check")
            if statuses["New"] > first:
                parts.append(f"{statuses['New'] - first} awaiting a second check")
        qualifier = "negative " if negative_only else ""
        assessment = f"{len(rows)} {qualifier}post{'s' if len(rows) != 1 else ''} tracked: " + ", ".join(parts) + "."
        if total_change is not None:
            assessment += (f" Comments {_signed(total_change)}"
                           + (f" ({_signed(100 * total_change / total_prior, '%')})" if total_prior else "")
                           + " since each post's prior check.")
    else:
        assessment = "No negative Facebook or Reddit posts in these records." if negative_only else "No posts tracked yet."
    return {
        "rows": rows, "totals": totals, "change_abs": change_abs, "change_pct": change_pct,
        "total_change_pct": 100 * total_change / total_prior if total_change is not None and total_prior else None,
        "total_direction": None if total_change is None else "up" if total_change > 0
        else "down" if total_change < 0 else "flat",
        "as_of": max(times) if times else "", "assessment": assessment,
        "truncated": any(row["truncated"] for row in rows),
    }


# ---------- Negative Post Report ----------

def _thread(record: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    url = record.get("url")
    return [record] + [r for r in records if r is not record and url and r.get("parent_url") == url]


def word_cloud(texts: Iterable[str], limit: int = 22) -> list[tuple[str, int]]:
    """Most frequent content words, weighted 1–5, largest placed in the middle."""
    tokens = (token.strip("'-") for text in texts for token in re.findall(r"[a-z][a-z'-]+", str(text or "").lower()))
    words = Counter(word.replace("'", "") for word in tokens
                    if len(word) > 2 and word.replace("'", "") not in _STOPWORDS)
    common = words.most_common(limit)
    if not common:
        return []
    high, low = common[0][1], common[-1][1]
    weighted = [(word, 1 + round(4 * (count - low) / (high - low)) if high > low else 3) for word, count in common]
    return weighted[1::2][::-1] + weighted[::2]


def npr_view(record: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    thread = _thread(record, records)
    scored = [r for r in thread if r.get("sentiment") in BUCKET_OF]
    counts = {bucket: sum(BUCKET_OF[r["sentiment"]] == bucket for r in scored) for bucket in BUCKETS}
    comments = len(thread) - 1
    if comments:
        basis = f"Across the post and {comments} analysed comment{'s' if comments != 1 else ''}"
    elif (_int(record.get("comment_count")) or 0) > 0:
        basis = f"One assessment of the post with its {record['comment_count']} collected comments"
    else:
        basis = "One assessment of this record"
    checks = record["checks"]
    tracked = velocity(checks) if is_tracked(record) else {"current": None, "prior": None, "direction": None,
                                                            "metric": "collected_comments"}
    views = latest_pair(checks, "views")
    impact = "High" if _risk(record) >= 8 or record["monitor"].get("signal_type") == "boycott_call" else "Low"
    themes = _top((t for r in thread for t in r["topics"]), 4)
    return {
        "number": report_number(record),
        "date": record.get("processed_at") or "",
        "record": record,
        "comments": (tracked["current"], tracked["prior"]),
        "comments_basis": METRIC_LABELS.get(tracked["metric"], ""),
        "views": views,
        "shares": _percentages(counts),
        "sentiment_basis": basis,
        "tone": _tone("Negative", [record]) if is_negative(record) and record.get("sentiment") in BUCKET_OF
        else str(record.get("sentiment") or ""),
        "cloud": word_cloud(r.get("translation") or r.get("title") or "" for r in thread),
        "themes": themes,
        "impact": impact,
        "cadence": "6h" if impact == "High" else "24h",
        "direction": tracked["direction"],
    }


def negative_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((r for r in records if is_negative(r)), key=_priority, reverse=True)


# ---------- HTML ----------

def _fill(value: Any) -> str:
    """A value on the template's underline; a missing value leaves the blank line."""
    if value is None or value == "":
        return '<span class="crt-fill crt-fill--empty">&nbsp;</span>'
    return f'<span class="crt-fill">{_text(value)}</span>'


def _head(title: str, blank: str, suffix: str = "") -> str:
    return (f'<div class="crt-eyebrow">{_text(EYEBROW)}</div>'
            f'<div class="crt-title" role="heading" aria-level="2">{_text(title)}{suffix} {_fill(blank)}</div>')


def _pills(options: Iterable[str], selected: str | None = None, extra: str = "") -> str:
    out = []
    for option in options:
        on = option == selected
        out.append(f'<span class="crt-pill{" is-on" if on else ""}">{_text(option)}'
                   f'{"<span class=crt-sr> (selected)</span>" if on else ""}</span>')
    return f'<div class="crt-pills{extra}">' + "".join(out) + "</div>"


def _lines(items: list[str], minimum: int = 4) -> str:
    rows = [f"<li>{_text(item)}</li>" for item in items] + ["<li></li>"] * max(0, minimum - len(items))
    return '<ul class="crt-lines">' + "".join(rows) + "</ul>"


def _arrows(direction: str | None) -> str:
    up = ' is-up' if direction == "up" else ""
    down = ' is-down' if direction == "down" else ""
    label = {"up": "rising", "down": "falling", "flat": "unchanged"}.get(direction or "", "not measured yet")
    return (f'<span class="crt-arrows" role="img" aria-label="Velocity {label}">'
            f'<span class="crt-arrow{up}" aria-hidden="true">▲</span> / '
            f'<span class="crt-arrow{down}" aria-hidden="true">▼</span></span>')


def _link(record: dict[str, Any], label: str) -> str:
    url = _url(record)
    if not url:
        return _text(label)
    return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{_text(label)}</a>'


def sentiment_sheet(records: list[dict[str, Any]], report_date: str) -> str:
    view = sentiment_view(records)
    boxes = []
    for bucket in BUCKETS:
        key = bucket.lower()
        count = view["counts"][bucket]
        boxes.append(
            f'<div class="crt-box crt-box--{key} crt-share"><div>'
            f'<div class="crt-label crt-label--{key}">{bucket}</div>'
            f'<div class="crt-share__value">{_fill(view["shares"][bucket] if view["scored"] else None)}'
            '<span>%</span></div>'
            f'<div class="crt-share__count">{count} of {view["scored"]} scored records</div></div>'
            f'<div class="crt-share__tone">Tone: {_fill(view["tones"][bucket] or None)}</div></div>'
        )
    threat = (
        '<div class="crt-box crt-box--dark"><div class="crt-label crt-label--dark">Threat level</div>'
        + _pills(("Low", "Medium", "High"), view["threat"])
        + '<p class="crt-muted" style="margin-top:14px">Main risk:</p>'
        + _lines([view["main_risk"]] if view["main_risk"] else [], 2) + "</div>"
    )
    return (
        '<section class="crt-sheet" aria-label="Comment Sentiment Analysis">'
        + _head("Comment Sentiment Analysis", format_date(report_date), " · date:")
        + '<div class="crt-grid">' + "".join(boxes) + "</div>"
        + '<div class="crt-grid" style="margin-top:22px">'
        + '<div class="crt-box"><div class="crt-label">Key insight</div>' + _lines(view["insight"]) + "</div>"
        + '<div class="crt-box"><div class="crt-label">Engagement quality</div>' + _lines(view["engagement"]) + "</div>"
        + threat + "</div>"
        + '<p class="crt-note">Negative = Anger + Anxiety, Positive = Joy. Shares cover analysed posts, comments '
          "and articles in this set; each record counts once. Threat level is High for review priority 8+ or an "
          "explicit boycott call, Medium for any Thailand signal or priority 5+. AI assessments need human review.</p>"
        + "</section>"
    )


def velocity_sheet(records: list[dict[str, Any]], report_date: str, *, negative_only: bool = True,
                   compact: bool = False) -> str:
    """One post per row, eight saved checks, and its current tracking status."""
    view = velocity_view(records, negative_only=negative_only)
    head = ("<tr><th scope=col>Provoker's post</th>"
            + "".join(f"<th scope=col>Check {i}</th>" for i in range(1, SHOWN_CHECKS + 1))
            + "<th scope=col>Status</th></tr>")
    labels = {"Viral": "Viral", "Live": "alive", "Slowing": "dying", "Dead": "dead"}
    body = []
    for row in view["rows"]:
        record, result = row["record"], row["velocity"]
        cells = [f'<td title="{_text(format_time(at))}">{_number(value)}</td>'
                 for at, value in zip(row["times"], row["values"])]
        cells += ["<td></td>"] * (SHOWN_CHECKS - len(cells))
        status = result["status"]
        badge = (f'<span class="crt-status crt-status--{status.lower()}">{labels[status]}</span>' if status
                 else '<span class="crt-muted" title="Status needs at least two saved checks">—</span>')
        body.append(
            f"<tr><td>{_link(record, _headline(record, 90))}"
            f'<div class="crt-muted crt-small">{_text(METRIC_LABELS[result["metric"]])}</div></td>'
            + "".join(cells) + f"<td>{badge}</td></tr>"
        )
    if not body:
        body.append(f'<tr class="crt-empty"><td colspan="{SHOWN_CHECKS + 2}">'
                    "No posts added yet. Add a Facebook post when you're ready.</td></tr>")
    as_of = format_time(view["as_of"]) or format_date(report_date)
    heading = "" if compact else _head("Velocity Tracker", as_of, " · as of")
    note = f" Showing the latest {SHOWN_CHECKS} checks per post." if view["truncated"] else ""
    return (
        f'<section class="crt-sheet{" crt-sheet--compact" if compact else ""}" aria-label="Velocity Tracker">'
        + heading + '<div class="crt-table-wrap"><table class="crt-table"><thead>' + head + "</thead><tbody>"
        + "".join(body) + "</tbody></table></div>"
        + '<div class="crt-key"><span><b>Viral</b> surging significantly</span><span><b>alive</b> still gaining</span><span><b>dying</b> gains shrinking</span>'
          "<span><b>dead</b> no growth across checks</span></div>"
        + '<p class="crt-note">Each check is a saved collection. Counts are platform comment totals when available, '
          "otherwise collected comments (a sample), as labeled under the post. Status needs at least two checks."
        + note + "</p></section>"
    )


def npr_sheet(record: dict[str, Any], records: list[dict[str, Any]], report_date: str) -> str:
    view = npr_view(record, records)
    comments_now, comments_prior = view["comments"]
    views_now, views_prior = view["views"]

    def metric(title: str, area: str, now: int | None, prior: int | None, note: str) -> str:
        if now is None:
            value = f'<p>Current: {_fill(NOT_COLLECTED)}</p>'
            change = ""
        else:
            delta = None if prior is None else now - prior
            direction = None if delta is None else "up" if delta > 0 else "down" if delta < 0 else "flat"
            value = f"<p>Current: {_fill(_number(now))}</p>"
            change = (f'<p class="crt-muted">vs prior check: {_fill(_signed(delta) if delta is not None else None)} '
                      f"{_arrows(direction)}</p>")
        return (f'<div class="crt-box crt-box--card crt-npr__{area}"><div class="crt-label">{title}</div>'
                f'<div class="crt-metric">{value}{change}'
                + (f'<p class="crt-muted crt-small">{_text(note)}</p>' if note else "") + "</div></div>")

    shares = view["shares"]
    capture_at = format_time(record.get("scraped_at") or record.get("saved_at"))
    original = _clip(capture_text(record), 520)
    english = _clip(record.get("translation"), 360)
    summary = _clip(record["monitor"].get("summary"), 260)
    exhibit = (
        '<div class="crt-box crt-box--card crt-npr__exhibit"><div class="crt-label crt-label--negative">'
        "Exhibit — the post</div><div class=crt-capture>"
        f'<p class="crt-muted crt-small">Text capture of record{" · " + _text(capture_at) if capture_at else ""}</p>'
        + (f"<q>{_text(original)}</q>" if original else "")
        + (f"<p><b>English:</b> {_text(english)}</p>" if english and english != _clip(original, 360) else "")
        + (f'<p><b>AI summary:</b> {_text(summary)}</p>' if summary else "")
        + (f'<p style="margin-top:10px">{_link(record, "Open source ↗")}</p>' if _url(record) else "")
        + "</div></div>"
    )
    posted = format_time(record.get("created_at"))
    poster = (
        '<div class="crt-box crt-box--gold crt-npr__poster"><div class="crt-label">Post &amp; poster</div>'
        '<ul class="crt-fields">'
        f"<li><span>Platform / link</span><span>{_link(record, platform_label(record) + ' ↗')}</span></li>"
        f"<li><span>Account</span><span class=crt-muted>{NOT_COLLECTED}</span></li>"
        f"<li><span>Posted at (date · time)</span><span>{_text(posted) or '<span class=crt-muted>' + NOT_COLLECTED + '</span>'}</span></li>"
        "<li><span>Reported to Core Team at (≤ 1h)</span><span></span></li>"
        "</ul></div>"
    )
    sentiment = (
        '<div class="crt-box crt-box--card crt-npr__sentiment"><div class="crt-label">Sentiment</div>'
        '<div class="crt-metric"><p class="crt-split">'
        + "".join(f'<span>{label} {_fill(shares[bucket])}%</span>'
                  for label, bucket in (("Neg", "Negative"), ("Neu", "Neutral"), ("Pos", "Positive")))
        + f'</p><p class="crt-tone">Tone: {_fill(view["tone"] or None)}</p>'
        f'<p class="crt-muted crt-small">{_text(view["sentiment_basis"])}</p></div></div>'
    )
    words = "".join(f'<span class="crt-w crt-w--{weight}">{_text(word)}</span>' for word, weight in view["cloud"])
    cloud = (
        '<div class="crt-box crt-box--card crt-npr__cloud"><div class="crt-label">Word cloud</div>'
        f'<div class="crt-cloud" role="img" aria-label="Most frequent words in the English translation">'
        f'{words or "<span class=crt-muted>No translated text to summarise.</span>"}</div>'
        f'<p class="crt-tone" style="margin-top:12px">Dominant themes: {_fill(", ".join(view["themes"]) or None)}</p>'
        "</div>"
    )
    classification = (
        '<div class="crt-box crt-box--dark crt-npr__class"><div class="crt-label crt-label--dark">Classification</div>'
        '<div class="crt-class-group"><p>Crisis type</p>' + _pills(("Type 1", "Type 2")) + "</div>"
        '<div class="crt-class-group"><p>Impact</p>' + _pills(("High", "Low"), view["impact"]) + "</div>"
        '<div class="crt-class-group"><p>Check cadence</p>' + _pills(("6h", "24h"), view["cadence"]) + "</div>"
        '<div class="crt-class-group"><p>Issue source</p>' + _pills(("Internal", "External")) + "</div>"
        '<div class="crt-label crt-label--dark" style="margin-top:22px">Action taken</div>'
        '<ol class="crt-actions"><li></li><li></li><li></li></ol>'
        f'<p class="crt-note">Impact is pre-filled from AI review priority ({_risk(record)}/10; High at 8+ or an '
        "explicit boycott call) and sets the cadence. Confirm before acting.</p></div>"
    )
    return (
        f'<section class="crt-sheet" aria-label="Negative Post Report {view["number"]}">'
        + _head(f"Negative Post Report — {view['number']}", format_date(view["date"] or report_date), " · date:")
        + '<div class="crt-npr">' + exhibit
        + metric("Total reach", "reach", None, None, "Reach is not public on the collected platforms.")
        + metric("Total views", "views", views_now, views_prior,
                 "" if views_now is not None else "Shown when the captured page displays a view count.")
        + metric("Total comments", "comments", comments_now, comments_prior,
                 f"Basis: {view['comments_basis']}" if comments_now is not None else "")
        + sentiment + poster + cloud + classification + "</div></section>"
    )


def stylesheet() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def report_document(records: list[dict[str, Any]], report_date: str) -> str:
    """Sentiment analysis and negative post reports, ready to share or print."""
    reports = "".join(npr_sheet(r, records, report_date) for r in negative_records(records))
    title = f"CrisisRadar report · {format_date(report_date) or 'undated'}"
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{_text(title)}</title>"
        '<link rel=preconnect href="https://fonts.googleapis.com">'
        '<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
        '&family=Noto+Sans+Khmer:wght@400;700&family=Noto+Sans+Thai:wght@400;700&display=swap">'
        f"<style>{stylesheet()}"
        "body{margin:0;padding:24px;background:#e8eae5}"
        "@page{size:A4 landscape;margin:8mm 8mm 12mm}"
        "@media print{body{padding:0;background:#fff}}</style></head><body>"
        + sentiment_sheet(records, report_date) + reports
        + "</body></html>"
    )
