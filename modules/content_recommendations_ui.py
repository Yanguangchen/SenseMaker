"""Streamlit content plans grounded in the current sentiment workspace."""

import json

import streamlit as st

from modules import content_recommendations as recommendations
from modules.config import AI_PROVIDERS, get_ai_key, get_ai_model, get_ai_provider

PLAN = "content_recommendation_plan"


def render_content_workspace() -> None:
    st.subheader("Content recommendations")
    st.caption("Turn sentiment findings into specific keywords, useful answers and content that addresses people's concerns.")
    source = st.radio("Use sentiment records from", ["Loaded processed records", "Latest analysis"],
                      horizontal=True, key="content_source")
    rows = (st.session_state.get("processed_rows", []) if source == "Loaded processed records" else
            (st.session_state.get("analysis_last_results") or {}).get("analyses", []))
    records = recommendations.source_records(rows)
    if not records:
        st.session_state.pop(PLAN, None)
        st.info("In Sentiment Analysis, load processed records or run an analysis. Then choose those records here.")
        return

    options = {record["id"]: record for record in records}
    # A changed source set must not leave a widget referring to deleted records.
    if "content_records" in st.session_state:
        st.session_state["content_records"] = [ident for ident in st.session_state["content_records"] if ident in options]
    selected = st.multiselect("Records to use", list(options), default=list(options)[:recommendations.MAX_RECORDS],
        max_selections=recommendations.MAX_RECORDS, key="content_records",
        format_func=lambda ident: f"{options[ident]['sentiment']} · {options[ident]['excerpts']['raw_text'][:80]} · {ident[:8]}")
    selected_records = [record for record in records if record["id"] in selected]
    st.caption(f"{len(selected_records)} of {len(records)} analyzed records selected · up to {recommendations.MAX_RECORDS} per plan.")
    if any(record["excerpted"] for record in selected_records):
        st.caption("Long records are excerpted; the plan uses the first 1,200 characters and up to two comment excerpts.")

    brand = st.text_input("Brand or topic", max_chars=160, key="content_brand", placeholder="e.g. OYOSHI customer service")
    goal = st.text_area("What should the content improve?", max_chars=600, key="content_goal",
                        value="Address recurring concerns, clarify misunderstandings and build trust with useful, verifiable answers.")
    focus = st.text_input("Focus keyword or concern (optional)", max_chars=200, key="content_focus",
                          placeholder="e.g. delivery delays, product quality, refund policy")
    left, right = st.columns(2)
    language = left.selectbox("Content language", recommendations.LANGUAGES, key="content_language")
    available = [provider for provider in AI_PROVIDERS if get_ai_key(provider)]
    default = get_ai_provider()
    if "content_provider" not in st.session_state:
        st.session_state["content_provider"] = default if default in available else (available or [default])[0]
    provider = right.selectbox("Recommendation provider", list(AI_PROVIDERS), format_func=AI_PROVIDERS.get,
                               key="content_provider")
    configured = provider in available
    if not configured:
        st.info(f"{AI_PROVIDERS[provider]} needs its server API key. This feature reuses the Sentiment Analysis credentials.")
    st.caption("Keywords are suggested phrases, with no measured search volume. Compare sentiment on the same sources "
               "after publishing; a recommendation does not guarantee improvement.")
    context = {"brand": brand.strip(), "goal": goal.strip(), "language": language, "focus": focus.strip()}
    signature = recommendations.fingerprint(selected_records, context, provider, get_ai_model(provider))
    plan = st.session_state.get(PLAN)
    if plan and plan.get("fingerprint") != signature:
        st.session_state.pop(PLAN, None)
        plan = None
    if st.button("Generate content plan", key="content_generate", type="primary",
                 disabled=not (configured and selected and brand.strip() and goal.strip())):
        st.session_state.pop(PLAN, None)
        plan = None
        with st.spinner("Finding concerns and writing keyword-led content briefs…"):
            try:
                plan = recommendations.generate_plan([row for row in rows if str(row.get("_id")) in selected],
                    provider=provider, brand=brand, goal=goal, language=language, focus=focus)
                st.session_state[PLAN] = plan
            except (recommendations.RecommendationError, ValueError) as exc:
                st.error(str(exc))
    if not plan:
        return
    if plan.get("warnings"):
        st.warning("Some suggestions were omitted because they were incomplete, duplicated another keyword, "
                   "or referenced unavailable sources. The validated suggestions are shown below.")
        with st.expander("Details about omitted suggestions"):
            for warning in plan["warnings"]:
                st.text(warning)
    st.caption(f"{AI_PROVIDERS[plan['provider']]} · {plan['model']} · {plan['record_count']} records · "
               f"Generated {plan['generated_at'][:16].replace('T', ' ')} UTC. Plans remain in this session; export to keep a copy.")
    st.markdown("**Sentiment baseline in the selected sample**")
    for column, sentiment in zip(st.columns(4), recommendations.SENTIMENTS):
        column.metric(sentiment, plan["baseline"].get(sentiment, 0))
    st.caption("Ordered by the number of cited Anger/Anxiety records, then total cited records. "
               "These counts describe the supporting sample, not predicted impact.")
    download_keywords, download_plan = st.columns(2)
    download_keywords.download_button("Download keywords (CSV)", recommendations.keyword_csv(plan),
        file_name="sentiment-keywords.csv", mime="text/csv", key="content_download_keywords")
    download_plan.download_button("Download content plan (JSON)", json.dumps(plan, ensure_ascii=False, indent=2),
        file_name="sentiment-content-plan.json", mime="application/json", key="content_download_plan")
    for rank, rec in enumerate(plan["recommendations"], 1):
        with st.container(border=True):
            st.subheader(f"{rank}. {rec['title']}")
            st.write(f"**Primary keyword:** {rec['primary_keyword']}")
            st.write("**Supporting keywords:** " + " · ".join(rec["supporting_keywords"]))
            st.write(f"**Concern to address:** {rec['concern']}")
            st.write(f"**Format:** {rec['format']} · **Search intent:** {rec['search_intent']}")
            st.write(f"**Content angle:** {rec['angle']}")
            st.markdown("**Content outline**")
            for point in rec["outline"]:
                st.write("• " + point)
            st.markdown("**Suggested draft**")
            st.write(rec["draft"])
            with st.expander("Source evidence and facts to verify"):
                for ref in rec["evidence"]:
                    st.text(f"{ref['sentiment']} · {ref['source']} · {ref['record_id']}")
                    st.text(ref["quote"])
                    if ref["url"]:
                        st.link_button("Open source", ref["url"])
                for fact in rec["facts_to_verify"]:
                    st.write("• " + fact)
                st.caption("Source excerpts are attached from the selected records. Check the interpretation and factual claims before publishing.")
            st.write(f"**Measure after publishing:** {rec['measurement']}")
