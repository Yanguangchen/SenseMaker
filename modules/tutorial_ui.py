"""Searchable, chapter-by-chapter user guide inside the dashboard."""

from html import escape
from pathlib import Path
import re

import streamlit as st

GUIDE_PATH = Path(__file__).with_name("tutorial.md")
TUTORIAL_CSS = Path(__file__).parent / "theme" / "tutorial.css"

_WORKFLOW_STEPS = (
    ("1", "Scrape Data", "Facebook, Oyoshi, X, News, Web, Reddit"),
    ("2", "Clean & Manage", "Browse cards, filter & review deletions"),
    ("3", "Sentiment Analysis", "Curate rows, run AI & export PDF/HTML"),
    ("4", "Velocity & Trends", "Track post views & timeline risk"),
    ("5", "Content & GenCite", "Plan briefs & measure AI share of voice"),
)


def guide_sections(document: str) -> list[tuple[str, str]]:
    sections = []
    for block in re.split(r"(?m)^## ", document)[1:]:
        title, _, body = block.partition("\n")
        sections.append((title.strip(), body.strip()))
    return sections


def chapter_subsections(body: str) -> list[str]:
    """Return ### subheadings inside a chapter in reading order."""
    return [match.group(1).strip() for match in re.finditer(r"(?m)^###\s+(.+)$", body) if match.group(1).strip()]


def search_sections(sections: list[tuple[str, str]], query: str) -> list[tuple[str, str]]:
    words = query.casefold().split()
    return [(title, body) for title, body in sections
            if all(word in f"{title}\n{body}".casefold() for word in words)]


def search_preview(body: str, query: str = "", limit: int = 240) -> str:
    """Pick the most relevant prose paragraph for a search result preview."""
    blocks = [
        re.sub(r"^>\s*", "", block.strip())
        for block in body.split("\n\n")
        if block.strip() and not block.lstrip().startswith(("#", "|"))
    ]
    if not blocks:
        return ""
    words = [w for w in query.casefold().split() if w]
    chosen = next(
        (block for block in blocks if words and any(word in block.casefold() for word in words)),
        blocks[0],
    )
    compact = " ".join(chosen.split())
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _subtopics_markup(subtopics: list[str], label: str = "In this chapter") -> str:
    if not subtopics:
        return ""
    chips = "".join(f'<span class="sm-tutorial-chip">{escape(item)}</span>' for item in subtopics)
    return (
        '<div class="sm-tutorial-subtopics" role="region" '
        f'aria-label="{escape(label, quote=True)}">'
        f'<span class="sm-tutorial-subtopics__label">{escape(label)}</span>{chips}</div>'
    )


def _banner_markup(chapter_count: int, topic_count: int) -> str:
    steps = "".join(
        '<div class="sm-tutorial-flow__step">'
        f'<span class="sm-tutorial-flow__num">{escape(num)}</span>'
        f'<div class="sm-tutorial-flow__text"><strong>{escape(name)}</strong>'
        f'<span>{escape(detail)}</span></div></div>'
        for num, name, detail in _WORKFLOW_STEPS
    )
    return (
        '<div class="sm-tutorial-banner">'
        '<div class="sm-tutorial-banner__top">'
        '<span class="sm-tutorial-eyebrow">CrisisRadar User Guide</span>'
        f'<span class="sm-tutorial-meta-pill">{chapter_count} chapters · {topic_count} topics · Searchable reference</span>'
        '</div>'
        f'<div class="sm-tutorial-flow" aria-label="Platform workflow">{steps}</div>'
        '</div>'
    )


def _open_topic(title: str) -> None:
    st.session_state.tutorial_topic = title
    _clear_search()


def _clear_search() -> None:
    st.session_state.tutorial_search = ""


def render_tutorial_workspace() -> None:
    document = GUIDE_PATH.read_text(encoding="utf-8")
    sections = guide_sections(document)
    st.subheader("Platform tutorial")
    st.caption(
        "Step-by-step guide for CrisisRadar · Collect sources → Clean & curate → Analyze sentiment & signals → "
        "Track velocity & trends → Plan content & AI visibility"
    )
    if not sections:
        st.info("The tutorial is being updated. Please check back soon.")
        return

    st.html(TUTORIAL_CSS)
    titles = [title for title, _ in sections]
    if st.session_state.get("tutorial_topic") not in titles:
        st.session_state.tutorial_topic = titles[0]

    total_subtopics = sum(len(chapter_subsections(body)) for _, body in sections)
    st.html(_banner_markup(len(sections), total_subtopics))

    contents, reader = st.columns([1, 2.6], gap="large")
    with contents:
        query = st.text_input(
            "Search tutorial", key="tutorial_search", max_chars=160,
            placeholder="Search: Facebook, Oyoshi, PDF, velocity, GenCite…",
            help="Searches every chapter. All search words must appear in a matching chapter.",
        )
        with st.container(border=True, key="tutorial_contents"):
            topic = st.radio(
                "Contents", titles, key="tutorial_topic",
                format_func=lambda title: f"{titles.index(title) + 1:02d} · {title}",
                on_change=_clear_search,
            )

    with reader:
        if query.strip():
            shown = search_sections(sections, query)
            header_col, action_col = st.columns([3, 1], vertical_alignment="center")
            header_col.markdown(f"**Search results** · {len(shown)} of {len(sections)} chapters match")
            action_col.button("Clear search", key="tutorial_clear_search", width="stretch", on_click=_clear_search)
            if not shown:
                st.info("No chapters match. Try fewer words or choose a chapter from Contents.")
            for title, body in shown:
                chapter_idx = titles.index(title)
                with st.container(border=True):
                    st.markdown(f"#### {chapter_idx + 1:02d} · {title}")
                    subtopics = chapter_subsections(body)
                    if subtopics:
                        st.html(_subtopics_markup(subtopics, label="Topics"))
                    preview = search_preview(body, query)
                    if preview:
                        st.markdown(preview)
                    st.button("Read chapter →", key=f"tutorial_result_{chapter_idx}",
                              on_click=_open_topic, args=(title,))
            return

        index = titles.index(topic)
        title, body = sections[index]
        subtopics = chapter_subsections(body)
        with st.container(border=True, key="tutorial_reader"):
            st.caption(f"Chapter {index + 1} of {len(sections)}")
            st.subheader(title, anchor="tutorial-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))
            if subtopics:
                st.html(_subtopics_markup(subtopics))
            st.markdown(body)
            st.divider()
            prev_label = f"{index:02d} · {titles[index - 1]}" if index > 0 else "Start of guide"
            next_label = f"{index + 2:02d} · {titles[index + 1]}" if index < len(sections) - 1 else "End of guide"
            st.html(
                '<div class="sm-tutorial-nav-hints">'
                f'<div>{escape(prev_label)}</div>'
                f'<div>{escape(next_label)}</div></div>'
            )
            previous, next_chapter = st.columns(2)
            previous.button(
                "← Previous chapter", key="tutorial_previous", width="stretch",
                disabled=index == 0, on_click=_open_topic,
                args=(titles[max(0, index - 1)],),
            )
            next_chapter.button(
                "Next chapter →", key="tutorial_next", width="stretch",
                disabled=index == len(sections) - 1, on_click=_open_topic,
                args=(titles[min(len(sections) - 1, index + 1)],),
            )
