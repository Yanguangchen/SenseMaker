"""Likes + comments tables with one aligned column per saved run."""

from html import escape

from modules.client_report import format_time
from modules.velocity_tracking import SHOWN_RUNS, card_posts
from modules.velocity_metrics import METRIC, engagement_count


VIRAL_COUNT_THRESHOLD = 500_000
VIRAL_DELTA_THRESHOLD = 10_000
VIRAL_GROWTH_MULTIPLIER = 2


def _is_viral_velocity(prior: int, current: int, delta: int, earlier: int | None) -> bool:
    if current > VIRAL_COUNT_THRESHOLD or delta >= VIRAL_DELTA_THRESHOLD:
        return True
    if prior > 0 and delta >= prior * VIRAL_GROWTH_MULTIPLIER:
        return True
    if earlier is not None and earlier > 0 and delta >= earlier * VIRAL_GROWTH_MULTIPLIER and delta >= 50:
        return True
    return False


def engagement_status(values: list[int | None]) -> str | None:
    # Require consecutive successful checks. Missing counts must never imply death
    # or leave an old status looking current.
    recent = []
    for value in reversed(values):
        if value is None:
            break
        recent.insert(0, value)
        if len(recent) == 3:
            break
    if len(recent) < 2:
        return None
    delta = recent[-1] - recent[-2]
    earlier = recent[-2] - recent[-3] if len(recent) == 3 else None
    if delta > 0:
        if earlier is None or delta >= earlier:
            return "Viral" if _is_viral_velocity(recent[-2], recent[-1], delta, earlier) else "alive"
        return "dying"
    return "dying" if earlier is not None and earlier > 0 else "dead"


def engagement_change_percent(values: list[int | None]) -> float | None:
    """Compare the latest two runs; missing or zero baselines are undefined."""
    if len(values) < 2 or values[-1] is None or values[-2] in (None, 0):
        return None
    return 100 * (values[-1] - values[-2]) / values[-2]


view_status = engagement_status
views_change_percent = engagement_change_percent


def velocity_card_sheet(card: dict) -> str:
    runs = card.get("runs", [])[-SHOWN_RUNS:]
    head = "<tr><th scope=col>Provoker's post</th>"
    for index in range(SHOWN_RUNS):
        if index < len(runs):
            timestamp = format_time(runs[index]["at"])
            date, _, time = timestamp.partition(" · ")
            title = f"Run {runs[index].get('number', index + 1)} · {timestamp}"
            label = (f'<span class="crt-run-date">{escape(date)}</span>'
                     f'<span class="crt-run-time">{escape(time)}</span>')
            if runs[index].get("metric") != METRIC:
                label += '<span class="crt-muted crt-small">Earlier views check</span>'
        else:
            title, label = "Not run yet", "—"
        head += f'<th scope=col title="{escape(title, quote=True)}">{label}</th>'
    head += ('<th scope=col>Status</th><th scope=col title="Latest run versus the previous run">'
             'Change (%)</th><th scope=col class="crt-remark">Remark</th></tr>')
    body = []
    for post in card_posts(card):
        cells, values = [], []
        for run in runs:
            result = next((row for row in run["results"] if row["post_id"] == post["id"]), None)
            current_metric = run.get("metric") == METRIC
            count = engagement_count(result) if current_metric else None
            values.append(count)
            if result is None:
                label, title = "—", "Post was not included in this run."
            elif not current_metric:
                label = f"{result['views']:,} views" if result.get("views") is not None else "Not collected"
                title = "This earlier check tracked views. Likes + comments were not collected."
            elif count is None:
                label = "Failed" if result.get("outcome") == "error" else "Unavailable"
                title = result.get("reason", "Both likes/reactions and comment totals are needed.")
            else:
                label = f"{count:,}"
                title = f"{result['reactions']:,} likes/reactions + {result['comments']:,} comments"
            title += f" · {format_time(result.get('at', run['at']) if result else run['at'])}"
            cells.append(f'<td title="{escape(title, quote=True)}">{escape(label)}</td>')
        cells += ["<td></td>"] * (SHOWN_RUNS - len(cells))
        status = engagement_status(values)
        classes = {"Viral": "viral", "alive": "live", "dying": "slowing", "dead": "dead"}
        badge = (f'<span class="crt-status crt-status--{classes[status]}">{status}</span>' if status else
                 '<span class="crt-muted" title="Needs two consecutive likes + comments totals">—</span>')
        change = engagement_change_percent(values)
        if change is None:
            change_label = "—"
            change_title = ("Percentage change is undefined because the previous total is zero."
                            if len(values) >= 2 and values[-2] == 0 and values[-1] is not None else
                            "Needs likes + comments totals from both the latest and previous runs.")
        else:
            change_label = f"{change:+.1f}%" if change else "0.0%"
            change_title = (f"{values[-2]:,} → {values[-1]:,} likes + comments · "
                            f"{format_time(runs[-2]['at'])} → {format_time(runs[-1]['at'])}")
        change_cell = f'<td title="{escape(change_title, quote=True)}">{change_label}</td>'
        remark = escape(str(post.get("remark") or "")) or '<span class="crt-muted">—</span>'
        remark_cell = f'<td class="crt-remark">{remark}</td>'
        url = escape(post["url"], quote=True)
        body.append(f'<tr><td><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
                    '<div class="crt-muted crt-small">Likes + comments</div></td>' + "".join(cells)
                    + f"<td>{badge}</td>{change_cell}{remark_cell}</tr>")
    if not body:
        body.append(f'<tr class="crt-empty"><td colspan="{SHOWN_RUNS + 4}">'
                    'No posts added yet. Add a Facebook post when you\'re ready.</td></tr>')
    note = ""
    if card.get("run_count", 0) > SHOWN_RUNS:
        note = f" Showing the latest {SHOWN_RUNS} runs; earlier runs remain saved."
    return (
        '<section class="crt-sheet crt-sheet--compact" aria-label="Velocity Tracker">'
        '<div class="crt-table-wrap"><table class="crt-table"><thead>' + head + '</thead><tbody>'
        + "".join(body) + '</tbody></table></div>'
        '<div class="crt-key"><span><b>Viral</b> engagement surging significantly</span><span><b>alive</b> engagement growing</span>'
        '<span><b>dying</b> gains shrinking</span><span><b>dead</b> no growth</span></div>'
        '<p class="crt-note">Each check = likes + comments. Likes includes all Facebook reactions. '
        'Photo attachments use their linked full post. '
        'Hover over a total to see both counts. Column headings show when each run completed (UTC). '
        'Rounded counts remain approximate. Both counts must be available; missing counts are never treated as zero. '
        'Earlier views checks remain labelled and are excluded from engagement comparisons. '
        'Status compares gains between consecutive runs and needs at least two likes + comments totals. '
        'Change (%) = (latest − previous) ÷ previous × 100, using the latest two runs. '
        'A missing count or zero previous count shows —.' + note + '</p></section>'
    )
