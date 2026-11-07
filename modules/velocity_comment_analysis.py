"""Bounded, per-comment sentiment summaries for saved velocity runs."""

from collections import Counter
import hashlib
import logging

from modules.processor import analyze_posts

log = logging.getLogger(__name__)
MAX_COMMENTS = 20
MAX_COMMENT_BYTES = 2000
SENTIMENT_BUCKETS = {"Anger": "negative", "Anxiety": "negative", "Neutral": "neutral", "Joy": "positive"}


def bounded_text(value: str, limit: int = MAX_COMMENT_BYTES) -> str:
    return str(value).encode("utf-8")[:limit].decode("utf-8", errors="ignore").strip()


def analyze_comments(rows: list[dict], *, provider=None, model_name=None, on_status=None) -> None:
    """Analyze a card's sample in one bounded batch; never discard engagement.

    Percentages use successfully analyzed comments, not the post's total comment
    count. Missing results stay missing, including when the provider times out.
    """
    records, by_post = [], {}
    for row in rows:
        sample, seen = [], set()
        for comment in row.get("comment_sample", [])[:MAX_COMMENTS]:
            if not isinstance(comment, dict):
                continue
            text = bounded_text(comment.get("text", ""))
            ident = hashlib.sha256(f"{row['post_id']}:{comment.get('id') or text}".encode()).hexdigest()
            if not text or ident in seen:
                continue
            seen.add(ident)
            sample.append({"id": ident, "text": text})
            records.append({"_id": ident, "raw_text": text, "source_type": "facebook_comment"})
        row["comment_sample"] = sample
        by_post[row["post_id"]] = sample

    analyses = {}
    if records:
        try:
            analyses = analyze_posts(records, provider=provider, model_name=model_name, on_status=on_status)
        except Exception as exc:
            # Provider errors must not leak credentials or drop already-read counts.
            log.warning("Velocity comment analysis failed (%s)", type(exc).__name__)

    for row in rows:
        sample = by_post[row["post_id"]]
        counts = dict.fromkeys(("negative", "neutral", "positive"), 0)
        keywords, details = Counter(), []
        for comment in sample:
            analysis = analyses.get(comment["id"], {})
            bucket = SENTIMENT_BUCKETS.get(analysis.get("sentiment"))
            if bucket is None:
                continue
            counts[bucket] += 1
            # Count a topic once per comment; case-fold before ranking.
            topics = analysis.get("topics") or []
            topics = topics if isinstance(topics, list) else []
            topics = sorted({bounded_text(topic, 60).casefold() for topic in topics[:5]
                             if isinstance(topic, str) and topic.strip()})
            keywords.update(topics)
            details.append({"id": comment["id"], "sentiment": bucket, "topics": topics})
        analyzed = sum(counts.values())
        status = ("ok" if analyzed == len(sample) else "partial" if analyzed else "error") if sample else (
            "empty" if row.get("comment_collection", {}).get("status") == "empty" else "unavailable")
        row["comment_analysis"] = {
            "status": status, "counts": counts, "analyzed_count": analyzed,
            "collected_count": len(sample), "failed_count": len(sample) - analyzed,
            "keywords": [{"word": word, "count": count} for word, count in keywords.most_common(12)],
        }
        row["comment_analysis_details"] = details

