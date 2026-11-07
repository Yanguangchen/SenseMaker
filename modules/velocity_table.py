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


def view_change(values: list[int | None]) -> tuple[int, float | None] | None:
    """Percent change from a post's earliest shown view count to the latest run.

    Returns the earliest count's index with the change. The latest run must have a
    count so an older figure never looks current. Growth from zero has no percentage.
    """
    if not values or values[-1] is None:
        return None
    first = next(index for index, value in enumerate(values) if value is not None)
    if first == len(values) - 1:
        return None
    start, end = values[first], values[-1]
    if start == 0:
        return first, (0.0 if end == 0 else None)
    return first, (end - start) / start * 100


def format_percent(value: float) -> str:
    digits = 0 if abs(value) >= 10 else 1
    return "0%" if round(value, digits) == 0 else f"{value:+,.{digits}f}%"


def _run_heading(run: dict, index: int) -> str:
    """Head each run column with when it ran, rather than a check number."""
    number = run.get("number", index + 1)
    stamp = format_time(run.get("at"))
    title = f"Run {number} · {stamp}" if stamp else f"Run {number}"
    date, _, clock = stamp.partition(" · ")
    label = (f'<span class="crt-run-date">{escape(date)}</span> <span class="crt-run-time">{escape(clock)}</span>'
             if stamp else escape(title))
    return f'<th scope=col title="{escape(title, quote=True)}">{label}</th>'


def _change_cell(values: list[int | None], runs: list[dict]) -> str:
    change = view_change(values)
    if change is None:
        return '<td title="Needs a view count in the latest run and in an earlier run."><span class="crt-muted">—</span></td>'
    first, percent = change
    if percent is None:
        return '<td title="Views grew from 0, so there is no percentage."><span class="crt-muted">—</span></td>'
    tone = "crt-up" if percent > 0 else "crt-down" if percent < 0 else "crt-muted"
    title = (f"From {values[first]:,} views ({format_time(runs[first]['at'])}) "
             f"to {values[-1]:,} views ({format_time(runs[-1]['at'])})")
    return f'<td title="{escape(title, quote=True)}"><span class="crt-change {tone}">{format_percent(percent)}</span></td>'


def velocity_card_sheet(card: dict) -> str:
    runs = card.get("runs", [])[-SHOWN_RUNS:]
    head = "<tr><th scope=col>Provoker's post</th>"
    head += "".join(_run_heading(run, index) for index, run in enumerate(runs))
    head += '<th scope=col class="crt-run-empty" title="Not run yet">Not run yet</th>' * (SHOWN_RUNS - len(runs))
    head += ('<th scope=col>Status</th><th scope=col title="Change in views from the earliest count shown '
             'to the latest run">% change</th></tr>')
    body = []
    for post in card_posts(card):
        cells, values, latest = [], [], None
        for run in runs:
            result = next((row for row in run["results"] if row["post_id"] == post["id"]), None)
            count = result.get("views") if result else None
            values.append(count)
            latest = result
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
        # Say why the latest run has no count without making the reader hover.
        reason = ""
        if latest is not None and latest.get("views") is None:
            reason = (f'<div class="crt-reason">Latest run: '
                      f'{escape(latest.get("reason", "Facebook did not display a view count."))}</div>')
        url = escape(post["url"], quote=True)
        body.append(f'<tr><td><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
                    f'<div class="crt-muted crt-small">Views</div>{reason}</td>' + "".join(cells)
                    + f"<td>{badge}</td>" + _change_cell(values, runs) + "</tr>")
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
        '<p class="crt-note">Each column is one Run of this card, headed by the date and time it ran (UTC). '
        'Counts are the views Facebook displays; rounded counts remain approximate. '
        'Unavailable means no view count could be read. '
        'Status compares view gains between consecutive runs and needs at least two view counts. '
        '% change compares the earliest view count shown with the latest run\'s count.' + note + '</p></section>'
    )
