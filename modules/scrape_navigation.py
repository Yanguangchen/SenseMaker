"""Branded source cards with mounted collector panels to preserve unfinished work."""

from collections.abc import Callable
import base64
from pathlib import Path

import streamlit as st


THEME_DIR = Path(__file__).parent / "theme"
FACEBOOK_USAGE_WARNING = (
    "This Facebook scraper consumes a lot of Browserbase usage and is not recommended. "
    "Use Oyoshi on Facebook for a shorter search."
)
SOURCE_CARDS = (
    {"key": "facebook", "title": "Facebook", "label": "GROUPS & PAGES",
     "description": "Collect posts and comments from the Facebook links you follow."},
    {"key": "facebook_search", "icon": "facebook", "title": "Oyoshi on Facebook", "label": "DIRECT SEARCH",
     "description": "Open Oyoshi search results directly and collect posts with 2–3 scrolls."},
    {"key": "x", "title": "X", "label": "RECENT POSTS & REPLIES",
     "description": "Find recent posts about your brands, topics and search terms."},
    {"key": "news", "title": "Online news", "label": "NEWS & HEADLINES",
     "description": "Search Google News for coverage across publishers and watch topics."},
    {"key": "website", "title": "Custom website", "label": "ARTICLES & WEB PAGES",
     "description": "Add links to articles, blogs or company pages and collect their text."},
)
SOURCE_KEYS = {source["key"] for source in SOURCE_CARDS} | {"reddit"}


def render_scrape_navigation(on_select: Callable[[str | None], None], *,
                             on_inspect_facebook_links: Callable[[], None] | None = None) -> str | None:
    selected = st.session_state.get("scrape_source")
    if selected not in SOURCE_KEYS:
        selected = None
        st.session_state["scrape_source"] = None
    st.html(THEME_DIR / "source-colors.css")
    st.html(THEME_DIR / "source-cards.css")
    # Keep the existing collectors mounted, as the old source tabs did. Hiding
    # their containers preserves widget drafts, unsaved results and active jobs.
    # The direct-search preset shares Facebook's authenticated collector and results.
    selected_panel = "facebook" if selected == "facebook_search" else selected
    hidden = ", ".join(f".st-key-sm_scrape_panel_{key}" for key in sorted(SOURCE_KEYS - {"facebook_search"})
                       if key != selected_panel)
    st.html(f"<style>{hidden} {{ display: none !important; }}</style>")
    if selected:
        st.button("← All sources", key="scrape_all_sources", on_click=on_select, args=(None,))
        if selected == "facebook" and on_inspect_facebook_links:
            st.button("Saved Facebook links", key="scrape_inspect_facebook_links", on_click=on_inspect_facebook_links)
        return selected

    st.subheader("Scrape Data")
    st.caption("Choose a source to start collecting. Your settings and collected data stay here when you switch sources.")
    if on_inspect_facebook_links:
        st.button("Saved Facebook links", key="scrape_inspect_facebook_links", on_click=on_inspect_facebook_links)
    with st.container(key="sm_source_picker_grid"):
        for source in SOURCE_CARDS:
            with st.container(border=True, key=f"sm_source_card_{source['key']}"):
                logo = (THEME_DIR / "source-icons" / f"{source.get('icon', source['key'])}.svg").read_text(encoding="utf-8")
                # Streamlit sanitizes inline SVG; an image keeps the brand mark visible.
                logo_uri = "data:image/svg+xml;base64," + base64.b64encode(logo.encode("utf-8")).decode("ascii")
                st.html(
                    f'<div class="sm-source-card"><div class="sm-source-logo"><img src="{logo_uri}" alt="" /></div>'
                    f'<p class="sm-source-label">{source["label"]}</p>'
                    f'<h3>{source["title"]}</h3><p class="sm-source-description">{source["description"]}</p></div>'
                )
                st.button(f"Open {source['title']} →", key=f"scrape_open_{source['key']}", width="stretch",
                          on_click=on_select, args=(source["key"],))
                if source["key"] == "facebook":
                    st.warning(FACEBOOK_USAGE_WARNING, icon="⚠️")
    with st.expander("Other sources"):
        details, action = st.columns([4, 1], vertical_alignment="center")
        logo = (THEME_DIR / "source-icons" / "reddit.svg").read_text(encoding="utf-8")
        with details:
            st.html(f'<div class="sm-other-source-reddit"><div class="sm-source-logo">{logo}</div>'
                    '<div><h4>Reddit</h4><p>Collect posts and comments from r/cambodia.</p></div></div>')
        action.button("Open Reddit", key="scrape_open_reddit", width="stretch", on_click=on_select, args=("reddit",))
    return None
