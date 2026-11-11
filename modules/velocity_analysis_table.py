"""Escaped HTML for the latest comment analysis in a velocity notepad."""

from html import escape

from modules.velocity_tracking import card_posts

ANALYSIS_NOTE = ("Sentiment of up to 20 readable comments per post. Percentages use successfully analyzed comments, "
                 "not all comments on Facebook. Negative includes anger and anxiety; positive includes joy. "
                 "Word size shows how many analyzed comments mention each topic.")

def analysis_table(card: dict) -> str:
    runs = card.get("runs", [])
    results = {row["post_id"]: row for row in runs[-1].get("results", [])} if runs else {}
    rows = []
    for index, post in enumerate(card_posts(card), 1):
        analysis = results.get(post["id"], {}).get("comment_analysis", {})
        analyzed = analysis.get("analyzed_count", 0)
        collected = analysis.get("collected_count", 0)
        status = analysis.get("status")
        note = {
            "ok": f"{analyzed} comments analyzed",
            "partial": f"{analyzed} of {collected} comments analyzed · Run again to retry",
            "empty": "No comments on this post",
            "unavailable": "Comments could not be read · Run again to retry",
            "error": "Sentiment analysis failed · Check AI settings and run again",
        }.get(status, "Run this card to analyze comments")
        if runs and post["id"] not in results:
            note = "Post was not included in the latest run"
        cells = [f'<td><a href="{escape(post["url"], quote=True)}" target="_blank" rel="noopener noreferrer">Post {index} ↗</a>'
                 f'<small>{escape(note)}</small></td>']
        for bucket in ("negative", "neutral", "positive"):
            count = analysis.get("counts", {}).get(bucket, 0)
            value = f'<b>{count}</b><small>{count / analyzed:.1%}</small>' if analyzed else '—'
            cells.append(f'<td class="sm-analysis-{bucket}">{value}</td>')
        cells.append(f'<td class="sm-analysis-remark">{escape(post.get("remark", "")) or "—"}</td>')
        keywords = analysis.get("keywords", [])[:12]
        maximum = max((item["count"] for item in keywords), default=1)
        cloud = " ".join(f'<span style="font-size:{12 + 12 * item["count"] / maximum:.1f}px" '
                         f'title="{item["count"]} analyzed comments">{escape(item["word"])}</span>' for item in keywords)
        cells.append(f'<td><div class="sm-analysis-cloud" aria-label="Top comment topics">{cloud or "—"}</div></td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    if not rows:
        rows = ['<tr><td colspan="6">Add a Facebook post to start comment analysis.</td></tr>']
    return ('<div class="sm-analysis-scroll" tabindex="0" role="region" aria-label="Comment analysis table">'
            '<table class="sm-analysis-table"><thead><tr><th scope="col">Post</th>'
            '<th scope="col" class="sm-analysis-negative">Negative</th>'
            '<th scope="col" class="sm-analysis-neutral">Neutral</th>'
            '<th scope="col" class="sm-analysis-positive">Positive</th>'
            '<th scope="col">Remarks</th><th scope="col">Word cloud</th></tr></thead>'
            '<tbody>' + ''.join(rows) + '</tbody></table></div>')
