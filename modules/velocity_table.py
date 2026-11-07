"""Views-only card tables with one aligned column per saved run."""

from html import escape

from modules.client_report import format_time
from modules.velocity_tracking import SHOWN_RUNS, card_posts


def view_status(values: list[int | None]) -> str | None:
    # Require consecutive successful checks. Missing views must never imply death
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
        return "alive" if earlier is None or delta >= earlier else "dying"
    return "dying" if earlier is not None and earlier > 0 else "dead"


def views_change_percent(values: list[int | None]) -> float | None:
    """Compare the latest two runs; missing or zero baselines are undefined."""
    if len(values) < 2 or values[-1] is None or values[-2] in (None, 0):
        return None
    return 100 * (values[-1] - values[-2]) / values[-2]


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
        else:
            title, label = "Not run yet", "—"
        head += f'<th scope=col title="{escape(title, quote=True)}">{label}</th>'
    head += ('<th scope=col>Status</th><th scope=col title="Latest run versus the previous run">'
             'Views change (%)</th></tr>')
    body = []
    for post in card_posts(card):
        cells, values = [], []
        for run in runs:
            result = next((row for row in run["results"] if row["post_id"] == post["id"]), None)
            count = result.get("views") if result else None
            values.append(count)
            if result is None:
                label, title = "—", "Post was not included in this run."
            elif count is None:
                label = "Failed" if result.get("outcome") == "error" else "Unavailable"
                title = result.get("reason", "Facebook did not display a view count.")
            else:
                label = f"{count:,}"
                title = result.get("label", f"{count:,} views")
            title += f" · {format_time(result.get('at', run['at']) if result else run['at'])}"
            cells.append(f'<td title="{escape(title, quote=True)}">{escape(label)}</td>')
        cells += ["<td></td>"] * (SHOWN_RUNS - len(cells))
        status = view_status(values)
        classes = {"alive": "live", "dying": "slowing", "dead": "dead"}
        badge = (f'<span class="crt-status crt-status--{classes[status]}">{status}</span>' if status else
                 '<span class="crt-muted" title="Needs two consecutive view counts">—</span>')
        change = views_change_percent(values)
        if change is None:
            change_label = "—"
            change_title = ("Percentage change is undefined because the previous view count is zero."
                            if len(values) >= 2 and values[-2] == 0 and values[-1] is not None else
                            "Needs view counts from both the latest and previous runs.")
        else:
            change_label = f"{change:+.1f}%" if change else "0.0%"
            change_title = (f"{values[-2]:,} → {values[-1]:,} views · "
                            f"{format_time(runs[-2]['at'])} → {format_time(runs[-1]['at'])}")
        change_cell = f'<td title="{escape(change_title, quote=True)}">{change_label}</td>'
        url = escape(post["url"], quote=True)
        body.append(f'<tr><td><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
                    '<div class="crt-muted crt-small">Views</div></td>' + "".join(cells)
                    + f"<td>{badge}</td>{change_cell}</tr>")
    if not body:
        body.append(f'<tr class="crt-empty"><td colspan="{SHOWN_RUNS + 3}">'
                    'No posts added yet. Add a Facebook post when you\'re ready.</td></tr>')
    note = ""
    if card.get("run_count", 0) > SHOWN_RUNS:
        note = f" Showing the latest {SHOWN_RUNS} runs; earlier runs remain saved."
    return (
        '<section class="crt-sheet crt-sheet--compact" aria-label="Velocity Tracker">'
        '<div class="crt-table-wrap"><table class="crt-table"><thead>' + head + '</thead><tbody>'
        + "".join(body) + '</tbody></table></div>'
        '<div class="crt-key"><span><b>alive</b> views growing</span><span><b>dying</b> view gains shrinking</span>'
        '<span><b>dead</b> no view growth</span></div>'
        '<p class="crt-note">Column headings show when each run completed (UTC). Counts are the views Facebook displays; '
        'rounded counts remain approximate. Unavailable means no view count could be read. '
        'Status compares view gains between consecutive runs and needs at least two view counts. '
        'Views change (%) = (latest − previous) ÷ previous × 100, using the latest two runs. '
        'A missing count or zero previous count shows —.' + note + '</p></section>'
    )
