"""Firestore persistence helpers for Project Sentinel."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import firebase_admin
from firebase_admin import credentials, firestore

PostData = Dict[str, Any]


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def get_post_id(url: str) -> str:
    """Generate deterministic document id from post URL."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _get_collection_name() -> str:
    return os.getenv("FIREBASE_COLLECTION", "raw_posts").strip() or "raw_posts"


def init_db() -> firestore.Client:
    """Initialize and return Firestore client."""
    creds_path = os.getenv("FIREBASE_CREDENTIALS")
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()

    if not firebase_admin._apps:
        if creds_path:
            cred = credentials.Certificate(creds_path)
            app_options: Dict[str, Any] = {"projectId": project_id} if project_id else {}
            firebase_admin.initialize_app(cred, app_options)
        else:
            # Fallback path for environments that already have ADC configured.
            app_options = {"projectId": project_id} if project_id else {}
            firebase_admin.initialize_app(options=app_options)

    return firestore.client()


def upsert_post(db: firestore.Client, post_data: PostData) -> str:
    """
    Insert a post if it does not already exist.

    Returns:
        str: Document ID for the post.
    """
    url = post_data.get("url")
    if not url:
        raise ValueError("post_data must include a non-empty 'url'.")

    doc_id = get_post_id(url)
    doc_ref = db.collection(_get_collection_name()).document(doc_id)
    existing = doc_ref.get()

    if existing.exists:
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

    db.collection(_get_collection_name()).document(doc_id).set(update_payload, merge=True)

