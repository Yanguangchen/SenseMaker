"""Print the client report to PDF with the Chromium build Playwright already uses.

The PDF is the downloadable HTML report printed on A4 landscape, so it keeps the
templates' layout. Comment Sentiment Analysis and each Negative Post Report are fitted
to one page. A Velocity Tracker too long to fit continues onto further pages with its
header row repeated. The page loads nothing except Google Fonts.
"""

from __future__ import annotations

import asyncio
import html
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SCALE = 0.8
MARGIN = {"top": "8mm", "right": "8mm", "bottom": "12mm", "left": "8mm"}
_PX_PER_MM = 96 / 25.4
# CSS pixels inside the margins of an A4 landscape page at this scale.
LAYOUT_WIDTH = round((297 - 16) * _PX_PER_MM / SCALE)
PAGE_HEIGHT = int((210 - 20) * _PX_PER_MM / SCALE)
# A Velocity Tracker needing more shrinking than this flows onto more pages instead.
MIN_TRACKER_ZOOM = 0.72
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}
TIMEOUT_S = 60
MISSING_BROWSER = ("PDF export needs Chromium on the server. Ask the administrator to run "
                   "`python -m playwright install chromium`, or download the HTML report and print it to PDF.")

# Zoom changes layout, so measure again after each adjustment.
_FIT_SHEETS = """([height, minTracker]) => {
  for (let pass = 0; pass < 4; pass++) {
    let changed = false;
    for (const sheet of document.querySelectorAll('.crt-sheet')) {
      const zoom = Number(sheet.style.zoom || 1);
      const tall = sheet.getBoundingClientRect().height;
      if (tall <= height) continue;
      const next = zoom * height / tall * 0.99;
      if (sheet.getAttribute('aria-label') === 'Velocity Tracker' && next < minTracker) continue;
      sheet.style.zoom = String(next);
      changed = true;
    }
    if (!changed) break;
  }
}"""


class PDFExportError(RuntimeError):
    """A PDF could not be produced; the message is safe to show."""


def _footer(label: str) -> str:
    # Chromium renders header and footer templates at zero size unless styled inline.
    return ('<div style="box-sizing:border-box;width:100%;padding:0 8mm;display:flex;'
            'justify-content:space-between;font:8px Arial,sans-serif;color:#666966">'
            f'<span>{html.escape(label)}</span>'
            '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


async def _only_fonts(route: Any) -> None:
    url = urlsplit(route.request.url)
    if url.scheme in {"data", "about"} or (url.scheme == "https" and url.hostname in FONT_HOSTS):
        await route.continue_()
    else:
        await route.abort()


async def _launch(playwright: Any) -> Any:
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception as exc:
        if "Executable doesn't exist" not in str(exc):
            raise
    try:
        # Same fallback as collection: an installed Google Chrome.
        return await playwright.chromium.launch(channel="chrome", headless=True)
    except Exception:
        raise PDFExportError(MISSING_BROWSER) from None


async def _render(document: str, label: str) -> bytes:
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await _launch(playwright)
        try:
            page = await browser.new_page(viewport={"width": LAYOUT_WIDTH, "height": PAGE_HEIGHT})
            await page.route("**/*", _only_fonts)
            await page.emulate_media(media="print")
            await page.set_content(document, wait_until="load")
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeout:
                pass  # Fonts are optional; the stylesheet falls back to installed fonts.
            await page.evaluate("() => document.fonts.ready.then(() => true)")
            await page.evaluate(_FIT_SHEETS, [PAGE_HEIGHT, MIN_TRACKER_ZOOM])
            return await page.pdf(
                format="A4", landscape=True, scale=SCALE, margin=MARGIN, print_background=True,
                display_header_footer=True, header_template="<span></span>", footer_template=_footer(label),
            )
        finally:
            await browser.close()


def render_pdf(document: str, label: str = "CrisisRadar report") -> bytes:
    """Return the report as PDF bytes; runs Chromium on its own thread and event loop."""

    def run() -> bytes:
        return asyncio.run(asyncio.wait_for(_render(document, label), TIMEOUT_S))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(run).result()
    except PDFExportError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise PDFExportError(f"Creating the PDF took longer than {TIMEOUT_S} seconds. Try again.") from None
    except Exception:
        log.exception("PDF export failed")
        raise PDFExportError("The PDF could not be created. Try again, or download the HTML report "
                             "and print it to PDF.") from None
