"""Firestore persistence helpers for Project Sentinel."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import firebase_admin
from firebase_admin import credentials, firestore

from modules.config import firebase_credentials, get_setting
from modules.signals import NON_CONTENT_TYPES

PostData = Dict[str, Any]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def get_post_id(url: str) -> str:
    """Generate deterministic document id from post URL."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _get_collection_name() -> str:
    return get_setting("FIREBASE_COLLECTION", "raw_posts") or "raw_posts"


def init_firebase() -> firebase_admin.App:
    """Initialize the server Admin SDK; client Firestore rules stay untouched."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    cred: Optional[credentials.Certificate] = None
    fb_dict = firebase_credentials()
    project_id = get_setting("FIREBASE_PROJECT_ID") or fb_dict.get("project_id", "")
    if fb_dict:
        cred = credentials.Certificate(fb_dict)
    elif get_setting("FIREBASE_CREDENTIALS"):
        cred = credentials.Certificate(get_setting("FIREBASE_CREDENTIALS"))
    elif get_setting("FIREBASE_CLIENT_EMAIL") and get_setting("FIREBASE_PRIVATE_KEY"):
        # Also accept OYOSHI's server environment format.
        cred = credentials.Certificate({
            "type": "service_account",
            "project_id": project_id,
            "client_email": get_setting("FIREBASE_CLIENT_EMAIL"),
            "private_key": get_setting("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
            "token_uri": "https://oauth2.googleapis.com/token",
        })

    options: Dict[str, Any] = {"projectId": project_id} if project_id else {}
    return firebase_admin.initialize_app(cred, options)


def init_db() -> firestore.Client:
    """Return a server Firestore client authenticated through IAM."""
    return firestore.client(app=init_firebase())


def upsert_post(db: firestore.Client, post_data: PostData) -> str:
    """
    Insert a post if it does not already exist.

    Returns:
        str: Document ID for the post.
    """
    url = post_data.get("url")
    if not url:
        raise ValueError("post_data must include a non-empty 'url'.")
    if post_data.get("source_type") in NON_CONTENT_TYPES:
        raise ValueError("Collection errors and fallback pages are not source posts.")

    doc_id = get_post_id(url)
    doc_ref = db.collection(_get_collection_name()).document(doc_id)
    existing = doc_ref.get()

    is_reddit = post_data.get("source_type") in {"reddit_post", "reddit_comment"}
    if existing.exists and not is_reddit:
        return doc_id

    payload: PostData = {
        "_id": doc_id,
        "url": url,
        "scraped_at": post_data.get("scraped_at", _now_iso()),
        "raw_text": post_data.get("raw_text", ""),
        "status": "pending",
    }
    if "comments" in post_data:
        payload["comments"] = post_data.get("comments", [])
    if "comment_count" in post_data:
        payload["comment_count"] = int(post_data.get("comment_count", 0) or 0)
    if "source_type" in post_data:
        payload["source_type"] = str(post_data.get("source_type", "")).strip()
    if "custom_title" in post_data:
        payload["custom_title"] = str(post_data.get("custom_title", "")).strip()
    if "saved_at" in post_data:
        payload["saved_at"] = str(post_data.get("saved_at", "")).strip()
    if "target_url" in post_data:
        payload["target_url"] = str(post_data.get("target_url", "")).strip()

    if is_reddit:
        for key in ("platform", "subreddit", "reddit_id", "created_at", "parent_url", "parent_text",
                    "thread_id", "comment_sample_limit", "content_removed"):
            if key in post_data:
                payload[key] = post_data[key]
        previous = (existing.to_dict() or {}) if existing.exists else {}
        changed = any(previous.get(k, "") != payload.get(k, "")
                      for k in ("raw_text", "parent_text", "content_removed"))
        if existing.exists and not changed:
            payload["status"] = previous.get("status", "pending")
        elif existing.exists:
            payload.update(analysis=firestore.DELETE_FIELD, processed_at=firestore.DELETE_FIELD)
        if post_data.get("content_removed"):
            payload["status"] = "removed"
            payload["raw_text"] = ""
            if existing.exists:
                payload.update(parent_text=firestore.DELETE_FIELD, analysis=firestore.DELETE_FIELD,
                               processed_at=firestore.DELETE_FIELD)
        doc_ref.set(payload, merge=True)
        return doc_id

    doc_ref.set(payload)
    return doc_id


def get_pending_posts(db: firestore.Client, limit: int = 50) -> List[PostData]:
    """Return pending posts up to limit."""
    docs = (
        db.collection(_get_collection_name())
        .where("status", "==", "pending")
        .limit(limit)
        .stream()
    )
    return [doc.to_dict() for doc in docs if doc.to_dict()]


def mark_post_processed(
    db: firestore.Client,
    doc_id: str,
    analysis: Optional[Dict[str, Any]],
    status: str = "processed",
) -> None:
    """Write analysis back to a post document."""
    update_payload: Dict[str, Any] = {
        "status": status,
        "processed_at": _now_iso(),
    }
    if analysis is not None:
        update_payload["analysis"] = analysis
    elif status == "error":
        update_payload["analysis"] = firestore.DELETE_FIELD

    db.collection(_get_collection_name()).document(doc_id).set(update_payload, merge=True)

