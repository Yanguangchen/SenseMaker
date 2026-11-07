"""Interactive term frequencies across all sentiment analysis records."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st
import streamlit.components.v1 as components

from modules.client_report import BUCKET_OF, _STOPWORDS, capture_text, report_record
from modules.signals import NON_CONTENT_TYPES


def content_words(text: str) -> list[str]:
    """Keep Unicode letters/marks together, without counting URLs or UI stopwords."""
    text = re.sub(r"https?://\S+|www\.\S+", " ", html.unescape(str(text)), flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    words, current = [], []
    for char in text + " ":
        if unicodedata.category(char)[0] in {"L", "M"}:
            current.append(char)
        elif char in {"'", "’"}:
            continue
        else:
            word = "".join(current)
            if 2 <= len(word) <= 60 and word not in _STOPWORDS:
                words.append(word)
            current = []
    return words


def _safe_url(value: str) -> str:
    try:
        url = str(value or "").strip()
        parts = urlsplit(url)
        return url if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else ""
    except ValueError:
        return ""


def build_cloud(items: list[dict], *, sentiment: str = "All sentiments",
                original: bool = False, limit: int = 50, excluded: str = "") -> dict:
    """Count actual occurrences; never use NPR's relative 1–5 display weights."""
    totals, documents, seen = Counter(), [], set()
    ignored = set(content_words(excluded))
    selected, fallback = 0, 0
    for index, item in enumerate(items):
        record = report_record(item)
        if record.get("analysis_excluded") or record.get("content_removed") or record.get("source_type") in NON_CONTENT_TYPES:
            continue
        bucket = BUCKET_OF.get(record.get("sentiment"), "Unclassified")
        if sentiment != "All sentiments" and bucket != sentiment:
            continue
        identity = record.get("_id") or record.get("url") or f"row:{index}"
        if identity in seen:
            continue
        seen.add(identity)
        selected += 1
        translation = str(record.get("translation") or "").strip()
        text = capture_text(record) if original or not translation else translation
        if not text.strip():
            continue
        fallback += int(not original and not translation)
        counts = Counter(word for word in content_words(text) if word not in ignored)
        totals.update(counts)
        documents.append({"counts": counts, "text": " ".join(text.split()),
                          "url": _safe_url(record.get("url", "")), "sentiment": bucket,
                          "kind": "Original text" if original or not translation else "English translation"})
    terms = []
    for word, count in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))[:max(1, min(limit, 80))]:
        matches = [doc for doc in documents if word in doc["counts"]]
        examples = []
        for doc in matches[:4]:
            # A bounded, readable excerpt near the selected term; full text remains at its source.
            position = doc["text"].casefold().find(word)
            start = max(0, position - 90)
            excerpt = doc["text"][start:start + 420]
            examples.append({"text": ("…" if start else "") + excerpt + ("…" if start + 420 < len(doc["text"]) else ""),
                             "url": doc["url"], "sentiment": doc["sentiment"], "kind": doc["kind"]})
        terms.append({"word": word, "count": count, "records": len(matches), "examples": examples})
    return {"terms": terms, "records": selected, "with_text": len(documents),
            "tokens": sum(totals.values()), "unique": len(totals), "fallback": fallback}


def render_word_cloud(items: list[dict], key: str) -> None:
    st.markdown("#### Words across your analysis")
    st.caption("All sentiments are included by default. Word size shows frequency, not sentiment. "
               "Select a word to see its count and matching records; drag words to explore.")
    with st.expander("Cloud settings"):
        sentiment = st.selectbox("Sentiment", ["All sentiments", "Positive", "Neutral", "Negative"],
                                 key=f"{key}_cloud_sentiment")
        original = st.radio("Text to count", ["English translations", "Original text"],
                            horizontal=True, key=f"{key}_cloud_text") == "Original text"
        limit = st.slider("Maximum words", 10, 80, 50, step=10, key=f"{key}_cloud_limit")
        excluded = st.text_input("Hide words", placeholder="Separate words with spaces or commas",
                                 key=f"{key}_cloud_excluded")
    cloud = build_cloud(items, sentiment=sentiment, original=original, limit=limit, excluded=excluded)
    if not cloud["terms"]:
        st.info("No words match these settings. Choose another sentiment or text source, or clear hidden words.")
        return
    st.caption(f"{cloud['with_text']:,} of {cloud['records']:,} records have text · "
               f"{cloud['tokens']:,} word occurrences · {cloud['unique']:,} distinct terms · "
               f"Showing the top {len(cloud['terms'])} · Common English words and URLs are omitted.")
    if original or cloud["fallback"]:
        st.caption((f"{cloud['fallback']} record(s) use original text because no translation is available. "
                    if not original else "") +
                   "Text without spaces may appear as phrases. Counts use record text, not summaries or topic labels.")
    component = components.declare_component("floating_word_cloud", path=str(Path(__file__).with_name("word_cloud")))
    digest = hashlib.sha256(json.dumps(cloud, sort_keys=True).encode()).hexdigest()
    component(cloud=cloud, revision=digest, key=f"{key}_floating_cloud", default=None)
    with st.expander("Word frequencies"):
        rows = [{"Word": term["word"], "Occurrences": term["count"], "Records": term["records"]}
                for term in cloud["terms"]]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["Word", "Occurrences", "Records"])
        writer.writeheader()
        writer.writerows(rows)
        st.download_button("Download frequencies (CSV)", output.getvalue().encode("utf-8-sig"),
                           "word-frequencies.csv", "text/csv", key=f"{key}_cloud_download")
