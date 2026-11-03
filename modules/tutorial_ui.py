"""Searchable in-app user guide, also available as a complete Markdown download."""

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


def render_tutorial_workspace() -> None:
    document = GUIDE_PATH.read_text(encoding="utf-8")
    sections = guide_sections(document)
    st.header("Platform tutorial")
    st.write("Learn how to collect sources, analyze sentiment, review evidence and plan content. "
             "Start with the first-analysis walkthrough or look up a specific task.")
    st.info("Your workflow: Collect sources → Save to cloud → Analyze → Review evidence → Plan content")
    st.download_button("Download complete tutorial (Markdown)", document.encode("utf-8"),
                       file_name="crisisradar-tutorial.md", mime="text/markdown", key="tutorial_download")
    query = st.text_input("Search tutorial", key="tutorial_search", max_chars=160,
                          placeholder="Try: save records, Facebook, PDF, keywords",
                          help="Searches every topic. All search words must appear in a matching topic.")
    topic = st.selectbox("Choose a topic", [title for title, _ in sections] + ["All topics"],
                         key="tutorial_topic", disabled=bool(query.strip()))
    if query.strip():
        shown = search_sections(sections, query)
        st.caption(f"{len(shown)} of {len(sections)} topics match your search.")
        if not shown:
            st.info("No topics match. Try fewer words or a workspace name such as GenCite.")
        st.button("Clear search", key="tutorial_clear_search",
                  on_click=lambda: st.session_state.update(tutorial_search=""))
    else:
        shown = sections if topic == "All topics" else [(title, body) for title, body in sections if title == topic]
        st.caption(f"{len(sections)} topics · You can return to another workspace using the sidebar.")
    for title, body in shown:
        st.divider()
        st.subheader(title, anchor="tutorial-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))
        st.markdown(body)
