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


def velocity_card_sheet(card: dict) -> str:
    runs = card.get("runs", [])[-SHOWN_RUNS:]
    head = "<tr><th scope=col>Provoker's post</th>"
    for index in range(SHOWN_RUNS):
        title = (f"Run {runs[index].get('number', index + 1)} · {format_time(runs[index]['at'])}"
                 if index < len(runs) else "Not run yet")
        head += f'<th scope=col title="{escape(title, quote=True)}">Check {index + 1}</th>'
    head += "<th scope=col>Status</th></tr>"
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
        url = escape(post["url"], quote=True)
        body.append(f'<tr><td><a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'
                    '<div class="crt-muted crt-small">Views</div></td>' + "".join(cells) + f"<td>{badge}</td></tr>")
    if not body:
        body.append('<tr class="crt-empty"><td colspan="10">No posts added yet. Add a Facebook post when you\'re ready.</td></tr>')
    note = ""
    if card.get("run_count", 0) > SHOWN_RUNS:
        note = f" Showing the latest {SHOWN_RUNS} runs; earlier runs remain saved."
    return (
        '<section class="crt-sheet crt-sheet--compact" aria-label="Velocity Tracker">'
        '<div class="crt-table-wrap"><table class="crt-table"><thead>' + head + '</thead><tbody>'
        + "".join(body) + '</tbody></table></div>'
        '<div class="crt-key"><span><b>alive</b> views growing</span><span><b>dying</b> view gains shrinking</span>'
        '<span><b>dead</b> no view growth</span></div>'
        '<p class="crt-note">Each check is one Run of this card. Counts are the views Facebook displays; '
        'rounded counts remain approximate. Unavailable means no view count could be read. '
        'Status compares view gains between consecutive runs and needs at least two view counts.' + note + '</p></section>'
    )
