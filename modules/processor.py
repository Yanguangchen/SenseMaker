"""Gemini-based post processing scaffold."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import google.generativeai as genai
from firebase_admin.firestore import Client

from modules.database import get_pending_posts, mark_post_processed

Analysis = Dict[str, Any]
log = logging.getLogger(__name__)

MAX_RETRIES = 5
INITIAL_BACKOFF_S = 2.0


def _build_batch_prompt(posts: List[Dict[str, Any]], index_map: Dict[str, str]) -> str:
    """Build the Gemini prompt using simple index keys (post_1, post_2, ...).

    ``index_map`` maps ``post_N`` -> real Firestore ``_id``.
    The reverse map (real_id -> index_key) is used to build the payload.
    """
    reverse = {v: k for k, v in index_map.items()}
    batch = []
    for p in posts:
        key = reverse.get(p.get("_id", ""), p.get("_id", ""))
        batch.append({"_id": key, "raw_text": p.get("raw_text", "")})

    return (
        "You are a multilingual analyst specialising in Singaporean social media. "
        "For EACH post object below:\n"
        "1. **translation** – translate the text to English (keep as-is if already English).\n"
        "2. **sentiment** – one of: Anxiety, Anger, Joy, Neutral.\n"
        "3. **risk_score** – integer 1-10 (10 = most urgent / concerning).\n"
        "4. **topics** – short array of topic strings.\n\n"
        "Return **strictly valid JSON** with this exact shape:\n"
        '{"results": [\n'
        '  {"_id": "<same _id from input>", "translation": "...", '
        '"sentiment": "...", "risk_score": N, "topics": [...]}\n'
        "]}\n\n"
        f"Posts:\n{json.dumps(batch, ensure_ascii=False, indent=2)}"
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Parse JSON from raw model text, tolerating fenced code blocks and pre/post text."""
    candidate = (text or "").strip()
    if not candidate:
        raise ValueError("Gemini returned empty text.")

    if candidate.startswith("```"):
        candidate = candidate.strip("`").strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Gemini response does not contain a JSON object.")

    sliced = candidate[start : end + 1]
    parsed = json.loads(sliced)
    if not isinstance(parsed, dict):
        raise ValueError("Gemini JSON payload is not an object.")
    return parsed


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if the exception is a retryable 429 / ResourceExhausted error."""
    msg = str(exc).lower()
    return "429" in msg or "resource exhausted" in msg or "resourceexhausted" in msg


def analyze_posts_with_gemini(
    posts: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    model_name: str = "models/gemini-2.0-flash",
    on_status: Optional[Any] = None,
) -> Dict[str, Analysis]:
    """Analyze posts and return analysis keyed by **real** Firestore document ID.

    Args:
        on_status: optional callable(str) invoked with progress messages
                   (e.g. a Streamlit status updater).
    """
    gemini_key = (api_key or os.getenv("GEMINI_KEY") or "").strip()
    if not gemini_key:
        raise ValueError("GEMINI_KEY is not set.")
    if not posts:
        return {}

    def _status(msg: str) -> None:
        log.info(msg)
        if callable(on_status):
            try:
                on_status(msg)
            except Exception:
                pass

    # Build simple index keys so Gemini can echo them back reliably.
    index_map: Dict[str, str] = {}
    for idx, p in enumerate(posts, start=1):
        index_map[f"post_{idx}"] = p.get("_id", f"unknown_{idx}")

    genai.configure(api_key=gemini_key)
    prompt = _build_batch_prompt(posts, index_map)
    target_model = (model_name or "").strip() or "models/gemini-2.0-flash"

    model = genai.GenerativeModel(target_model)

    # Retry loop with truncated exponential backoff for 429 errors.
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _status(f"Calling Gemini ({target_model}), attempt {attempt}/{MAX_RETRIES}...")
            response = model.generate_content(prompt)
            break
        except Exception as exc:
            last_exc = exc
            if _is_rate_limit_error(exc) and attempt < MAX_RETRIES:
                wait = INITIAL_BACKOFF_S * (2 ** (attempt - 1))
                _status(
                    f"Rate limited (429). Waiting {wait:.0f}s before retry "
                    f"({attempt}/{MAX_RETRIES})..."
                )
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError(
            f"Gemini API still rate-limited after {MAX_RETRIES} retries."
        ) from last_exc

    raw_text = (getattr(response, "text", "") or "").strip()
    parsed = _extract_json_object(raw_text)
    results = parsed.get("results", [])

    # Map the simple keys back to real Firestore _id values.
    out: Dict[str, Analysis] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        returned_key = item.get("_id", "")
        real_id = index_map.get(returned_key, returned_key)
        item["_id"] = real_id
        out[real_id] = item

    # Positional fallback: if Gemini returned the right count but
    # mangled the keys, zip by position.
    if not out and len(results) == len(posts):
        for item, post in zip(results, posts):
            real_id = post.get("_id", "")
            if isinstance(item, dict):
                item["_id"] = real_id
                out[real_id] = item

    _status(f"Gemini returned {len(out)} analysis result(s).")
    return out


def process_pending_posts(db: Client, limit: int = 25) -> int:
    """
    Fetch pending posts, process with Gemini, and update Firestore.

    Returns:
        int: Number of posts marked processed or error.
    """
    pending = get_pending_posts(db, limit=limit)
    if not pending:
        return 0

    updated = 0
    try:
        by_id = analyze_posts_with_gemini(pending)

        for post in pending:
            doc_id = post["_id"]
            analysis: Analysis = by_id.get(doc_id, {})
            if analysis:
                mark_post_processed(db, doc_id=doc_id, analysis=analysis, status="processed")
            else:
                mark_post_processed(db, doc_id=doc_id, analysis=None, status="error")
            updated += 1
    except Exception:
        for post in pending:
            mark_post_processed(db, doc_id=post["_id"], analysis=None, status="error")
            updated += 1

    return updated

