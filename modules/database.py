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


def _read_firebase_from_secrets() -> Dict[str, Any]:
    """Read the [firebase] section from st.secrets, converting all proxy
    objects to plain Python types.  Returns empty dict when unavailable."""
    try:
        import streamlit as st
        fb = st.secrets["firebase"]
        # Force every value to a plain Python str/int/float.
        return {str(k): str(v) if isinstance(v, str) else v for k, v in fb.items()}
    except Exception:
        return {}


def _read_secret(key: str, default: str = "") -> str:
    """Read a single top-level key from st.secrets, or return default."""
    try:
        import streamlit as st
        return str(st.secrets[key])
    except Exception:
        return default


def init_db() -> firestore.Client:
    """Initialize and return Firestore client."""
    if not firebase_admin._apps:
        cred: Optional[credentials.Certificate] = None
        project_id = ""

        # 1) Try st.secrets [firebase] section  (Streamlit Cloud)
        fb_dict = _read_firebase_from_secrets()
        if fb_dict:
            cred = credentials.Certificate(fb_dict)
            project_id = fb_dict.get("project_id", "")

        # 2) Try local file path
        if cred is None:
            creds_path = os.getenv("FIREBASE_CREDENTIALS", "").strip()
            if creds_path:
                cred = credentials.Certificate(creds_path)

        # Resolve project_id: env var > st.secrets top-level > cred dict
        project_id = (
            os.getenv("FIREBASE_PROJECT_ID", "").strip()
            or _read_secret("FIREBASE_PROJECT_ID")
            or project_id
        )

        app_options: Dict[str, Any] = {"projectId": project_id} if project_id else {}
        if cred:
            firebase_admin.initialize_app(cred, app_options)
        else:
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

