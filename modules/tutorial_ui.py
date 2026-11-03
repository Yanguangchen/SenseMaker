"""Searchable, chapter-by-chapter user guide inside the dashboard."""

from pathlib import Path
import re

import streamlit as st

GUIDE_PATH = Path(__file__).with_name("tutorial.md")


def guide_sections(document: str) -> list[tuple[str, str]]:
    sections = []
    for block in re.split(r"(?m)^## ", document)[1:]:
        title, _, body = block.partition("\n")
        sections.append((title.strip(), body.strip()))
    return sections


def search_sections(sections: list[tuple[str, str]], query: str) -> list[tuple[str, str]]:
    words = query.casefold().split()
    return [(title, body) for title, body in sections
            if all(word in f"{title}\n{body}".casefold() for word in words)]


def _open_topic(title: str) -> None:
    st.session_state.tutorial_topic = title
    _clear_search()


def _clear_search() -> None:
    st.session_state.tutorial_search = ""


def render_tutorial_workspace() -> None:
    document = GUIDE_PATH.read_text(encoding="utf-8")
    sections = guide_sections(document)
    st.header("Platform tutorial")
    st.write("Follow the guide here in the app. Start with your first analysis, "
             "browse the chapters or search for a specific task.")
    st.info("Your workflow: Collect sources → Save to cloud → Analyze → Review evidence → Plan content")
    if not sections:
        st.info("The tutorial is being updated. Please check back soon.")
        return

    titles = [title for title, _ in sections]
    if st.session_state.get("tutorial_topic") not in titles:
        st.session_state.tutorial_topic = titles[0]

    # Keep the contents compact when Streamlit stacks the columns on small screens.
    st.html("""<style>
        @media (max-width: 900px) {
            .st-key-tutorial_contents {
                max-height: 220px;
                overflow-y: auto;
            }
        }
    </style>""")
    query = st.text_input("Search tutorial", key="tutorial_search", max_chars=160,
                          placeholder="Try: save records, Facebook, PDF, keywords",
                          help="Searches every topic. All search words must appear in a matching topic.")

    contents, reader = st.columns([1, 2.5], gap="large")
    with contents, st.container(border=True, key="tutorial_contents"):
        topic = st.radio(
            "Contents", titles, key="tutorial_topic",
            format_func=lambda title: f"{titles.index(title) + 1:02d} · {title}",
            on_change=_clear_search,
        )

    with reader:
        if query.strip():
            shown = search_sections(sections, query)
            st.subheader("Search results")
            st.caption(f"{len(shown)} of {len(sections)} chapters match your search.")
            st.button("Clear search", key="tutorial_clear_search", on_click=_clear_search)
            if not shown:
                st.info("No chapters match. Try fewer words or choose a chapter from Contents.")
            for title, body in shown:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    # A short preview keeps searches scannable, even for long chapters.
                    preview = next((block for block in body.split("\n\n")
                                    if not block.startswith(("#", "|"))), "")
                    st.markdown(preview[:240] + ("…" if len(preview) > 240 else ""))
                    st.button("Read chapter", key=f"tutorial_result_{titles.index(title)}",
                              on_click=_open_topic, args=(title,))
            return

        index = titles.index(topic)
        title, body = sections[index]
        st.caption(f"Chapter {index + 1} of {len(sections)}")
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
        st.subheader(title, anchor="tutorial-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))
        st.markdown(body)
        st.caption("Use Contents to jump to another chapter, or the sidebar to return to your work.")
