"""A velocity card as a printable document: its run history, then its latest comment analysis."""

import re
from html import escape
from pathlib import Path

from modules.client_report import format_date, format_time, stylesheet
from modules.velocity_analysis_table import ANALYSIS_NOTE, analysis_table
from modules.velocity_table import velocity_card_sheet
from modules.velocity_tracking import card_posts

PAPER_CSS = Path(__file__).parent / "theme" / "velocity-notepad.css"
# A wider post column keeps Facebook links to about two lines, so ten posts fit a page;
# five run columns then fill the A4 landscape width report_pdf prints at.
RUNS_PER_PAGE = 5
POST_WIDTH = 320


def card_pdf_name(card: dict, name: str) -> str:
    runs = card.get("runs", [])
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "card"
    date = format_date(runs[-1]["at"]) if runs else ""
    stamp = re.sub(r"\s+", "-", date.lower()) if date else "no-checks"
    return f"velocity-{slug}-{stamp}.pdf"


def card_document(card: dict, name: str) -> str:
    """The same for unchanged results, so a printed PDF can be reused until the card changes."""
    runs = card.get("runs", [])
    latest = f"Latest check {format_time(runs[-1]['at'])}" if runs else "No saved checks yet"
    meta = f"{len(card_posts(card))} posts · {len(runs)} saved checks · {latest}"
    sheets = []
    starts = range(0, max(len(runs), 1), RUNS_PER_PAGE)
    for start in starts:
        heading = name
        if len(runs) > RUNS_PER_PAGE:
            heading += f" · runs {start + 1}–{min(start + RUNS_PER_PAGE, len(runs))} of {len(runs)}"
        sheets.append(velocity_card_sheet(card, columns=slice(start, start + RUNS_PER_PAGE), heading=heading,
                                          subheading=meta, legend_open=start == starts[-1], post_width=POST_WIDTH))
    sheets.append(
        '<section class="crt-sheet crt-sheet--compact" aria-label="Comment Analysis">'
        f'<div class="crt-title" role="heading" aria-level="2">{escape(name)} · comment analysis</div>'
        f'<p class="crt-note">{escape(latest)}</p>'
        + analysis_table(card) + f'<p class="crt-note">{escape(ANALYSIS_NOTE)}</p></section>'
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{escape(name)} · Velocity card</title>"
        '<link rel=preconnect href="https://fonts.googleapis.com">'
        '<link rel=stylesheet href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800'
        '&family=Noto+Sans+Khmer:wght@400;700&family=Noto+Sans+Thai:wght@400;700&display=swap">'
        f"<style>{stylesheet()}{PAPER_CSS.read_text(encoding='utf-8')}"
        "body{margin:0;padding:24px;background:#fff;color:#35423e}"
        ".sm-analysis-scroll{overflow:visible}"
        ".crt-legend:not([open]),.crt-legend>summary{display:none}"
        "html main.st-key-sm_velocity_modal .crt-table td{padding:7px 6px}"
        "html main.st-key-sm_velocity_modal .crt-table :is(th,td):first-child"
        f"{{width:{POST_WIDTH}px;min-width:{POST_WIDTH}px;max-width:{POST_WIDTH}px}}"
        "html main.st-key-sm_velocity_modal .crt-table-wrap{margin-bottom:12px}"
        "@page{size:A4 landscape;margin:8mm 8mm 12mm}"
        "@media print{body{padding:0}}</style></head><body>"
        # The notepad's dialog styles are scoped to this Streamlit container key.
        '<main class="st-key-sm_velocity_modal">' + "".join(sheets) + "</main></body></html>"
    )
