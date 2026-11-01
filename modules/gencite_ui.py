"""GenCite workspace: measure and grow a brand's share of voice in AI answers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from modules import database
from modules import gencite
from modules import gencite_store as store
from modules.config import AI_PROVIDERS, get_ai_key

log = logging.getLogger(__name__)

RESULT = "gencite_result"
ROWS = "gencite_rows"
EDITOR_VERSION = "gencite_editor_version"
VARIATION_META = "gencite_variation_meta"
VIEWING = "gencite_viewing_saved"
RECS = "gencite_recs"
KEYWORDS = "gencite_keywords"
FLASH = "gencite_flash"
QUEUED = "gencite_queued_keywords"


def _saved_key(email: str) -> str:
    return f"gencite_saved_{store.library_owner_id(email)}"


def _published_key(email: str) -> str:
    return f"gencite_published_{store.library_owner_id(email)}"


def _flash(kind: str, message: str) -> None:
    st.session_state.setdefault(FLASH, []).append((kind, message))


def _show_flash() -> None:
    for kind, message in st.session_state.pop(FLASH, []):
        getattr(st, kind)(message)


def _date_label(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def _set_rows(rows: List[Dict[str, Any]]) -> None:
    st.session_state[ROWS] = rows
    # A new editor key discards the previous table's pending edits.
    st.session_state[EDITOR_VERSION] = st.session_state.get(EDITOR_VERSION, 0) + 1


def _set_result(result: Optional[Dict[str, Any]], viewing: Optional[Dict[str, str]] = None) -> None:
    st.session_state[RESULT] = result
    st.session_state[VIEWING] = viewing
    st.session_state.pop(QUEUED, None)


def _context() -> Dict[str, Any]:
    return {
        "aliases": gencite.parse_name_list(st.session_state.get("gencite_aliases", "")),
        "competitors": gencite.parse_name_list(st.session_state.get("gencite_competitors", "")),
        "market": st.session_state.get("gencite_market", "").strip()[:200],
        "region": st.session_state.get("gencite_region", "").strip()[:120],
    }


def _providers() -> List[str]:
    return list(st.session_state.get("gencite_providers") or [])


def _run(brand: str, queries: List[str], previous: Optional[Dict[str, Any]], context: Dict[str, Any],
         label: str) -> Optional[Dict[str, Any]]:
    """Run one sampling round under a live status panel; returns the merged result."""
    status = st.status(label, expanded=True)
    try:
        latest = gencite.run_analysis(brand, queries, _providers(), competitors=context.get("competitors"),
                                      aliases=context.get("aliases"), on_status=status.write)
    except (gencite.GenCiteError, ValueError) as exc:
        status.update(label=f"Analysis failed: {exc}", state="error")
        return None
    except Exception:
        log.exception("GenCite analysis failed unexpectedly")
        status.update(label="Analysis failed. Please try again or contact the app administrator.", state="error")
        return None
    latest["context"] = {**latest.get("context", {}), "market": context.get("market", ""),
                         "region": context.get("region", "")}
    result = gencite.merge_analysis_results(previous, latest)
    degraded = bool(result.get("providerErrors"))
    status.update(label=f"Done: {result['overallPct']}% of {result['totalRuns']} answers mention {brand}",
                  state="error" if degraded else "complete", expanded=degraded)
    return result


# --- Analyze ----------------------------------------------------------------------

def _render_provider_status() -> None:
    parts = []
    for provider, label in AI_PROVIDERS.items():
        model = gencite.provider_model(provider)
        parts.append(f"{label} · {model} · {'ready' if get_ai_key(provider) else 'not configured'}")
    st.caption("Providers: " + "  |  ".join(parts))


def _render_topic() -> None:
    with st.container(border=True, key="gencite_topic"):
        st.markdown("**1 · Topic**")
        query = st.text_input("Query or topic", key="gencite_query", max_chars=gencite.MAX_QUERY_CHARS,
                              placeholder="e.g. project management software for agencies")
        if st.button("Generate query variations", key="gencite_generate_variations",
                     disabled=not query.strip()):
            with st.spinner("Generating query variations…"):
                outcome = gencite.generate_variations(query)
            _set_rows([{"Test": True, "Query": v["text"], "Intent": v["tag"]} for v in outcome["variations"]])
            st.session_state[VARIATION_META] = {key: outcome[key] for key in ("provider", "model", "fallback", "error")}
        meta = st.session_state.get(VARIATION_META)
        if meta and meta["fallback"]:
            st.warning("AI variation generation was unavailable, so standard templates were used. "
                       f"{meta['error']}")
        elif meta:
            st.caption(f"Generated by {AI_PROVIDERS[meta['provider']]} · {meta['model']}.")


def _select_all(value: bool) -> None:
    edited = st.session_state.get("gencite_current_rows") or st.session_state.get(ROWS, [])
    _set_rows([{**row, "Test": value} for row in edited])


def _render_queries() -> List[str]:
    with st.container(border=True, key="gencite_queries"):
        st.markdown("**2 · Queries to test**")
        rows = st.session_state.get(ROWS, [])
        st.caption("Tick the queries to test, edit any wording, or add your own rows. "
                   f"Up to {gencite.MAX_QUERIES} queries; each is answered {gencite.ANSWERS_PER_QUERY} "
                   "times by every selected provider.")
        frame = pd.DataFrame(rows, columns=["Test", "Query", "Intent"])
        edited = st.data_editor(
            frame, key=f"gencite_query_editor_{st.session_state.get(EDITOR_VERSION, 0)}",
            num_rows="dynamic", hide_index=True, use_container_width=True,
            column_config={
                "Test": st.column_config.CheckboxColumn("Test", default=True, width="small"),
                "Query": st.column_config.TextColumn("Query", max_chars=gencite.MAX_QUERY_CHARS, width="large"),
                "Intent": st.column_config.SelectboxColumn("Intent", options=[*gencite.ALLOWED_TAGS, "Custom"],
                                                           default="Custom"),
            })
        current = [{"Test": bool(r.get("Test")) if pd.notna(r.get("Test")) else False,
                    "Query": str(r.get("Query") or "") if pd.notna(r.get("Query")) else "",
                    "Intent": r.get("Intent") if isinstance(r.get("Intent"), str) else "Custom"}
                   for r in edited.to_dict("records")]
        st.session_state["gencite_current_rows"] = current
        selected = list(dict.fromkeys(r["Query"].strip() for r in current if r["Test"] and r["Query"].strip()))
        left, right, count = st.columns([1, 1, 2], vertical_alignment="center")
        left.button("Select all", key="gencite_select_all", on_click=_select_all, args=(True,),
                    disabled=not current, use_container_width=True)
        right.button("Clear selection", key="gencite_clear_selection", on_click=_select_all, args=(False,),
                     disabled=not current, use_container_width=True)
        count.caption(f"{len(selected)} selected")
        if len(selected) > gencite.MAX_QUERIES:
            st.warning(f"Select at most {gencite.MAX_QUERIES} queries.")
        return selected


def _render_brand() -> None:
    with st.container(border=True, key="gencite_brand_settings"):
        st.markdown("**3 · Brand**")
        brand_col, alias_col = st.columns(2)
        brand_col.text_input("Brand to track", key="gencite_brand", max_chars=gencite.MAX_BRAND_CHARS)
        alias_col.text_input("Aliases (optional, comma-separated)", key="gencite_aliases",
                             help=f"Other names that count as a brand mention, up to {gencite.MAX_NAMES}.")
        st.text_input("Competitors (optional, comma-separated)", key="gencite_competitors",
                      help=f"Up to {gencite.MAX_NAMES}. Their mentions are counted the same way to find gaps.")
        market_col, region_col = st.columns(2)
        market_col.text_input("Market (optional)", key="gencite_market", max_chars=200,
                              help="Used to ground recommendations, e.g. 'B2B SaaS for agencies'.")
        region_col.text_input("Region (optional)", key="gencite_region", max_chars=120)
        configured = gencite.configured_providers()
        if "gencite_providers" not in st.session_state:
            st.session_state["gencite_providers"] = configured or list(AI_PROVIDERS)
        st.multiselect("Providers", list(AI_PROVIDERS), key="gencite_providers", format_func=AI_PROVIDERS.get,
                       help="GenCite runs the selected providers side by side and combines their answers.")
        missing = [AI_PROVIDERS[p] for p in _providers() if p not in configured]
        if missing:
            st.warning(f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not configured and "
                       "will be skipped. Contact the app administrator.")


def _render_result(email: str) -> None:
    result = st.session_state.get(RESULT)
    if not result:
        return
    st.divider()
    viewing = st.session_state.get(VIEWING)
    st.subheader(f"Share of voice · {result['brand']}")
    if viewing:
        st.info(f"Opened from saved analysis **{viewing['name']}** · saved {_date_label(viewing['savedAt'])}. "
                "Run again to add new answers; saving keeps a new copy.")
    for error in result.get("providerErrors") or []:
        st.warning(f"{error['message']} The available answers are shown; run again to retry.")
    sov, mentions, queries, rounds = st.columns(4)
    sov.metric("Share of voice", f"{result['overallPct']}%", gencite.delta_label(result) or None,
               help=f"Share of sampled AI answers that mention {result['brand']} or an alias.")
    mentions.metric("Answers mentioning brand", f"{result['totalCitations']} / {result['totalRuns']}")
    queries.metric("Queries", len(result["queries"]))
    rounds.metric("Sampling rounds", result.get("runCount", 1),
                  help="Run again adds new answers to the totals instead of replacing them.")
    breakdown = gencite.model_breakdown(result)
    if breakdown:
        for column, row in zip(st.columns(len(breakdown)), breakdown):
            column.metric(row["name"], f"{row['pct']}%", help=f"{row['count']} of {row['runs']} answers")
        note = gencite.model_leader_note(result)
        if note:
            st.caption(note)
    st.vega_lite_chart(spec=gencite.query_chart_spec(result), use_container_width=True)
    st.dataframe(pd.DataFrame(gencite.query_rows(result)), use_container_width=True, hide_index=True,
                 column_config={"Share of voice": st.column_config.ProgressColumn(
                     "Share of voice", format="%d%%", min_value=0, max_value=100)})
    st.caption("A mention is a case-insensitive match of the brand, an alias or a competitor in a sampled "
               "model answer. It is not a source citation, and samples vary between runs.")

    again, name_col, save = st.columns([1, 2, 1], vertical_alignment="bottom")
    if again.button("Run again", key="gencite_run_again", use_container_width=True,
                    disabled=not gencite.configured_providers()):
        context = {**(result.get("context") or {}), **{k: v for k, v in _context().items()
                                                       if k in ("market", "region") and v}}
        merged = _run(result["brand"], [q["text"] for q in result["queries"]], result, context,
                      "Sampling another round…")
        if merged:
            st.session_state[RESULT] = merged
            st.rerun()
    default_name = f"{result['brand']} — {datetime.now().strftime('%d %b %Y')}"
    name = name_col.text_input("Analysis name", value=viewing["name"] if viewing else default_name,
                               key=f"gencite_save_name_{viewing['savedAt'] if viewing else 'new'}",
                               max_chars=store.MAX_NAME_CHARS)
    if save.button("Save analysis", key="gencite_save", use_container_width=True):
        try:
            record = store.save_analysis(database.init_db(), email, name, result)
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            log.exception("GenCite analysis could not be saved")
            st.error("The analysis could not be saved. Try again.")
        else:
            saved = st.session_state.get(_saved_key(email))
            st.session_state[_saved_key(email)] = {"rows": [record, *(saved or {}).get("rows", [])], "error": False}
            st.session_state[VIEWING] = {"name": record["name"], "savedAt": record["savedAt"]}
            st.success(f"Saved as “{record['name']}”.")


def _render_analyze(email: str) -> None:
    _render_topic()
    selected = _render_queries()
    _render_brand()
    brand = st.session_state.get("gencite_brand", "").strip()
    ready = bool(brand and selected and len(selected) <= gencite.MAX_QUERIES and gencite.configured_providers()
                 and set(_providers()) & set(gencite.configured_providers()))
    label = f"Run analysis on {len(selected)} quer{'y' if len(selected) == 1 else 'ies'}"
    if st.button(label, key="gencite_run", type="primary", use_container_width=True, disabled=not ready):
        result = _run(brand, selected, None, _context(), f"Sampling {len(selected)} queries…")
        if result:
            _set_result(result)
    if not gencite.configured_providers():
        st.error("No AI provider is configured. Ask the app administrator to set GEMINI_KEY or OPENAI_API_KEY.")
    elif not brand or not selected:
        st.caption("Enter a brand and select at least one query to run the analysis.")
    _render_result(email)


# --- Recommendations ----------------------------------------------------------------

def _load_published(email: str) -> Dict[str, str]:
    key = _published_key(email)
    if key not in st.session_state:
        try:
            st.session_state[key] = store.load_published(database.init_db(), email)
        except Exception:
            log.exception("Published GenCite recommendations could not be loaded")
            st.session_state[key] = {}
    return st.session_state[key]


def _toggle_published(email: str, title: str) -> None:
    published = dict(_load_published(email))
    marking = title not in published
    try:
        store.set_published(database.init_db(), email, title, marking)
    except Exception:
        log.exception("GenCite publish mark could not be saved")
        _flash("error", "The published mark could not be saved. Try again.")
        return
    if marking:
        published[title] = datetime.now().isoformat()
        _flash("success", "Marked as published. Run the analysis again later to measure its impact.")
    else:
        published.pop(title, None)
    st.session_state[_published_key(email)] = published


def _idea_context(result: Dict[str, Any]) -> Dict[str, Any]:
    current = _context()
    return {**(result.get("context") or {}), **{k: current[k] for k in ("market", "region") if current[k]}}


def _generate(kind: str, result: Dict[str, Any]) -> None:
    key, generate = (RECS, gencite.generate_recommendations) if kind == "recs" else (KEYWORDS, gencite.generate_keywords)
    try:
        with st.spinner("Generating recommendations…" if kind == "recs" else "Generating keyword ideas…"):
            outcome = generate(result, _idea_context(result))
    except (gencite.GenCiteError, ValueError) as exc:
        st.session_state[key] = {"fingerprint": gencite.result_fingerprint(result), "error": str(exc)}
    else:
        st.session_state[key] = {"fingerprint": gencite.result_fingerprint(result), "error": "", **outcome}
        if kind == "keywords":
            st.session_state.pop(QUEUED, None)


def _cached(key: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cached = st.session_state.get(key)
    return cached if cached and cached.get("fingerprint") == gencite.result_fingerprint(result) else None


def _render_recommendations(email: str, result: Dict[str, Any]) -> None:
    with st.container(border=True, key="gencite_recommendations"):
        st.markdown("**Content recommendations**")
        st.caption("Five content briefs for the topics where the brand is mentioned least, grounded in the "
                   "measured gaps. Ranked by 35% gap, 25% relevance, 20% cross-provider consistency and 10% freshness.")
        cached = _cached(RECS, result)
        if st.button("Regenerate" if cached and not cached["error"] else "Generate content recommendations",
                     key="gencite_generate_recs", disabled=not gencite.configured_providers()):
            _generate("recs", result)
            cached = _cached(RECS, result)
        if not cached:
            return
        if cached["error"]:
            st.error(cached["error"])
            return
        st.caption(f"Generated by {AI_PROVIDERS[cached['provider']]} · {cached['model']}.")
        published = _load_published(email)
        for rank, rec in enumerate(gencite.ranked(cached["recommendations"], gencite.score_recommendation, result), 1):
            with st.container(border=True):
                title_col, score_col = st.columns([5, 1])
                title_col.markdown(f"**{rank}. {rec['title']}**" + ("  ✓ Published" if rec["title"] in published else ""))
                title_col.caption(f"{rec['cluster']} · confidence {rec['confidence']}")
                score_col.metric("Score", rec["score"], help=rec["detail"])
                st.markdown(f"*{rec['question']}*")
                line = gencite.gap_line(rec["used"], result)
                if line:
                    st.caption(line)
                with st.expander("View brief"):
                    st.markdown(f"**Audience:** {rec['audience']}\n\n**Angle:** {rec['angle']}")
                    st.markdown("**Cover these terms:** " + ", ".join(rec["supportingTerms"]))
                    st.markdown("**Requirements**\n" + "\n".join(f"- {item}" for item in rec["requirements"]))
                    st.markdown("**Evidence**")
                    st.dataframe(pd.DataFrame(gencite.evidence_rows(rec["used"], result)).rename(
                        columns={"query": "Tested query", "stats": "Mentions"}),
                        use_container_width=True, hide_index=True)
                    st.caption(f"Score: {rec['detail']}")
                st.button("Unmark as published" if rec["title"] in published else "Mark as published",
                          key=f"gencite_publish_{rank}", on_click=_toggle_published, args=(email, rec["title"]))


def _toggle_queued(keyword: str) -> None:
    queued = set(st.session_state.get(QUEUED, []))
    queued.symmetric_difference_update({keyword})
    st.session_state[QUEUED] = sorted(queued)


def _render_keywords(result: Dict[str, Any]) -> None:
    with st.container(border=True, key="gencite_keyword_ideas"):
        st.markdown("**Keyword ideas**")
        st.caption("Eight new queries to test next, prioritizing untested intents and weak spots. Ranked by 35% "
                   "opportunity, 25% relevance, 25% novelty versus tested queries and 15% coverage type.")
        cached = _cached(KEYWORDS, result)
        if st.button("Regenerate" if cached and not cached["error"] else "Generate keyword ideas",
                     key="gencite_generate_keywords", disabled=not gencite.configured_providers()):
            _generate("keywords", result)
            cached = _cached(KEYWORDS, result)
        if not cached:
            return
        if cached["error"]:
            st.error(cached["error"])
            return
        st.caption(f"Generated by {AI_PROVIDERS[cached['provider']]} · {cached['model']}.")
        queued = set(st.session_state.get(QUEUED, []))
        for rank, kw in enumerate(gencite.ranked(cached["keywords"], gencite.score_keyword, result), 1):
            with st.container(border=True):
                text_col, score_col = st.columns([5, 1])
                text_col.markdown(f"**{rank}. {kw['keyword']}**")
                text_col.caption(f"{kw['tag']} · {gencite.COVERAGE_LABELS[kw['coverage']]} · "
                                 f"confidence {kw['confidence']}")
                score_col.metric("Score", kw["score"], help=kw["detail"])
                st.markdown(kw["rationale"])
                evidence = gencite.evidence_rows(kw["relatedQueryIndexes"], result)
                if evidence:
                    with st.expander("View evidence"):
                        st.dataframe(pd.DataFrame(evidence).rename(columns={"query": "Tested query",
                                                                            "stats": "Mentions"}),
                                     use_container_width=True, hide_index=True)
                st.button("✓ Queued" if kw["keyword"] in queued else "Queue for testing",
                          key=f"gencite_queue_{rank}", on_click=_toggle_queued, args=(kw["keyword"],))
        queued_now = [kw["keyword"] for kw in cached["keywords"] if kw["keyword"] in queued]
        if st.button(f"Test {len(queued_now)} queued keyword{'s' if len(queued_now) != 1 else ''}",
                     key="gencite_test_keywords", type="primary", disabled=not queued_now):
            outcome = _run(result["brand"], queued_now, None, _idea_context(result),
                           f"Testing {len(queued_now)} keywords…")
            if outcome:
                _set_rows([{"Test": True, "Query": kw, "Intent": next(
                    k["tag"] for k in cached["keywords"] if k["keyword"] == kw)} for kw in queued_now])
                _set_result(outcome)
                _flash("success", "The queued keywords were tested. See the new results in Analyze.")
                st.rerun()


def _render_ideas(email: str) -> None:
    result = st.session_state.get(RESULT)
    if not result:
        st.info("Run an analysis in Analyze, or open a saved one, to get recommendations from its measured gaps.")
        return
    if not any(q.get("competitors") for q in result["queries"]):
        st.caption("Add competitors before running the analysis to measure mention gaps against them.")
    _render_recommendations(email, result)
    _render_keywords(result)


# --- Saved analyses ----------------------------------------------------------------

def _refresh_saved(email: str) -> None:
    key = _saved_key(email)
    try:
        st.session_state[key] = {"rows": store.list_analyses(database.init_db(), email), "error": False}
    except Exception:
        log.exception("Saved GenCite analyses could not be loaded")
        st.session_state[key] = {"rows": (st.session_state.get(key) or {}).get("rows", []), "error": True}


def _open_saved(email: str, analysis_id: str) -> None:
    record = next((r for r in st.session_state[_saved_key(email)]["rows"] if r["id"] == analysis_id), None)
    if record:
        _set_result({k: v for k, v in record.items() if k not in ("id", "name", "savedAt", "queryCount")},
                    {"name": record["name"], "savedAt": record["savedAt"]})
        context = record.get("context") or {}
        st.session_state["gencite_brand"] = record["brand"]
        st.session_state["gencite_aliases"] = ", ".join(context.get("aliases", []))
        st.session_state["gencite_competitors"] = ", ".join(context.get("competitors", []))
        _set_rows([{"Test": True, "Query": q["text"], "Intent": "Custom"} for q in record["queries"]])
        _flash("success", f"Opened “{record['name']}”. Its results are in Analyze.")


def _rename_saved(email: str, analysis_id: str) -> None:
    name = st.session_state.get(f"gencite_rename_{analysis_id}", "")
    try:
        store.rename_analysis(database.init_db(), email, analysis_id, name)
    except ValueError as exc:
        _flash("error", str(exc))
        return
    except Exception:
        log.exception("GenCite analysis could not be renamed")
        _flash("error", "The analysis could not be renamed. Try again.")
        return
    clean = store.clean_name(name)
    for row in st.session_state[_saved_key(email)]["rows"]:
        if row["id"] == analysis_id:
            row["name"] = clean
    _flash("success", f"Renamed to “{clean}”.")


def _delete_saved(email: str, analysis_id: str) -> None:
    try:
        store.delete_analysis(database.init_db(), email, analysis_id)
    except Exception:
        log.exception("GenCite analysis could not be deleted")
        _flash("error", "The analysis could not be deleted. Try again.")
        return
    saved = st.session_state[_saved_key(email)]
    saved["rows"] = [row for row in saved["rows"] if row["id"] != analysis_id]
    st.session_state.pop("gencite_confirm_delete", None)
    _flash("success", "Analysis deleted.")


def _render_saved(email: str) -> None:
    key = _saved_key(email)
    if key not in st.session_state:
        _refresh_saved(email)
    saved = st.session_state[key]
    st.button("Refresh", key="gencite_refresh_saved", on_click=_refresh_saved, args=(email,))
    if saved["error"]:
        st.warning("Saved analyses could not be loaded. Refresh to try again.")
    rows = saved["rows"]
    if not rows:
        st.info("No saved analyses yet. Run an analysis and click Save analysis to keep it here.")
        return
    st.caption("Your saved analyses are private to your account and available on any device.")
    st.dataframe(pd.DataFrame([{
        "Name": row["name"], "Brand": row.get("brand", ""), "Share of voice": row.get("overallPct", 0),
        "Mentions": f"{row.get('totalCitations', 0)}/{row.get('totalRuns', 0)}",
        "Queries": row.get("queryCount", len(row["queries"])), "Rounds": row.get("runCount", 1),
        "Providers": " + ".join(dict.fromkeys(m["name"] for q in row["queries"] for m in q.get("models") or [])),
        "Saved": _date_label(row.get("savedAt", "")),
    } for row in rows]), use_container_width=True, hide_index=True, column_config={
        "Share of voice": st.column_config.ProgressColumn("Share of voice", format="%d%%", min_value=0, max_value=100),
    })
    by_id = {row["id"]: row for row in rows}
    choice = st.selectbox("Analysis", list(by_id), key="gencite_saved_choice",
                          format_func=lambda i: f"{by_id[i]['name']} · {_date_label(by_id[i].get('savedAt', ''))}")
    if choice not in by_id:
        return
    open_col, rename_col, rename_btn, delete_col = st.columns([1, 2, 1, 1], vertical_alignment="bottom")
    open_col.button("Open", key="gencite_open_saved", type="primary", use_container_width=True,
                    on_click=_open_saved, args=(email, choice))
    rename_col.text_input("New name", value=by_id[choice]["name"], key=f"gencite_rename_{choice}",
                          max_chars=store.MAX_NAME_CHARS)
    rename_btn.button("Rename", key="gencite_rename_saved", use_container_width=True,
                      on_click=_rename_saved, args=(email, choice))
    if delete_col.button("Delete", key="gencite_delete_saved", use_container_width=True):
        st.session_state["gencite_confirm_delete"] = choice
    if st.session_state.get("gencite_confirm_delete") == choice:
        st.warning(f"Permanently delete “{by_id[choice]['name']}”?")
        confirm, cancel = st.columns(2)
        confirm.button("Delete permanently", key="gencite_confirm_delete_btn", type="primary",
                       on_click=_delete_saved, args=(email, choice))
        cancel.button("Cancel", key="gencite_cancel_delete",
                      on_click=lambda: st.session_state.pop("gencite_confirm_delete", None))


def render_gencite_workspace(email: str) -> None:
    st.subheader("GenCite · AI share of voice")
    st.caption("Measure how often Gemini and ChatGPT mention your brand when answering the questions your "
               "customers ask, then close the gaps with content and keyword recommendations.")
    _render_provider_status()
    _show_flash()
    analyze_tab, ideas_tab, saved_tab = st.tabs(["Analyze", "Recommendations", "Saved analyses"])
    with analyze_tab:
        _render_analyze(email)
    with ideas_tab:
        _render_ideas(email)
    with saved_tab:
        _render_saved(email)
