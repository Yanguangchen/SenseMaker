"""Firestore persistence helpers for CrisisRadar."""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Use macOS's configured DNS routes (including VPN/split DNS) before gRPC loads.
# Its default c-ares resolver can select addresses this machine cannot reach.
if sys.platform == "darwin":
    os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

import firebase_admin
from firebase_admin import credentials, firestore

from modules.config import firebase_credentials, get_setting
from modules.analysis_library import dataset_key
from modules.engagement import add_check, is_tracked
from modules.signals import NON_CONTENT_TYPES

PostData = Dict[str, Any]
NEWS_FIELDS = ("platform", "created_at", "title", "publisher", "publisher_url", "news_category",
               "news_categories", "news_query", "news_edition", "news_assessment")


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
    previous = (existing.to_dict() or {}) if existing.exists else {}
    # Each collection is a velocity check; saving the same collection again replaces it.
    observed = str(post_data.get("scraped_at") or _now_iso())
    checks = add_check(previous, post_data, observed) if is_tracked(post_data) else None

    source_type = str(post_data.get("source_type", "")).strip()
    is_reddit = source_type in {"reddit_post", "reddit_comment"}
    is_x = source_type in {"x_post", "x_reply"}
    is_website = source_type == "website_page"
    is_news = source_type == "news_article"
    if existing.exists and not is_reddit and not is_x and not is_website and not is_news:
        # Saved posts keep their original text and analysis; only engagement is followed.
        if checks:
            doc_ref.set({"checks": checks}, merge=True)
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
    if checks:
        payload["checks"] = checks
    if is_reddit or is_x or is_website or is_news:
        fields = ("platform", "x_id", "created_at", "conversation_id", "lang", "x_query", "x_category",
                  "x_categories", "parent_url", "parent_text", "reply_sample_limit", "content_removed",
                  "reported_comment_count") if is_x else (
            "platform", "subreddit", "reddit_id", "created_at", "parent_url", "parent_text",
            "thread_id", "comment_sample_limit", "content_removed", "reported_comment_count")
        if is_website:
            fields = ("platform", "title", "publisher", "created_at", "text_truncated")
        elif is_news:
            fields = NEWS_FIELDS
        for key in fields:
            if key in post_data:
                payload[key] = post_data[key]
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

    # Error handling must not recreate a source deleted while analysis was running.
    db.collection(_get_collection_name()).document(doc_id).update(update_payload)



def delete_processed_posts(db: firestore.Client, doc_ids: List[str]) -> Dict[str, List[str]]:
    """Atomically delete selected processed posts after rechecking their current status."""
    if any(not isinstance(doc_id, str) or not doc_id or "/" in doc_id
           or doc_id in {".", ".."} or len(doc_id.encode("utf-8")) > 1500 for doc_id in doc_ids):
        raise ValueError("Select valid record IDs.")
    unique_ids = list(dict.fromkeys(doc_ids))
    if len(unique_ids) > 200:
        raise ValueError("Delete at most 200 records at a time.")
    if not unique_ids:
        return {"deleted": [], "skipped": [], "missing": []}
    collection = db.collection(_get_collection_name())
    refs = {doc_id: collection.document(doc_id) for doc_id in unique_ids}

    @firestore.transactional
    def remove(transaction: Any) -> Dict[str, List[str]]:
        # Complete all reads before any writes; a concurrent edit retries the transaction.
        snapshots = {doc.id: doc for doc in db.get_all(
            list(refs.values()), field_paths=["status"], transaction=transaction, timeout=30)}
        result: Dict[str, List[str]] = {"deleted": [], "skipped": [], "missing": []}
        for doc_id, ref in refs.items():
            snapshot = snapshots[doc_id]
            if not snapshot.exists:
                result["missing"].append(doc_id)
            elif (snapshot.to_dict() or {}).get("status") != "processed":
                result["skipped"].append(doc_id)
            else:
                transaction.delete(ref)
                result["deleted"].append(doc_id)
        return result

    return remove(db.transaction(max_attempts=3))


def load_record_catalog(db: firestore.Client) -> List[PostData]:
    """Load management metadata and server versions without analysis or source bodies."""
    query = db.collection(_get_collection_name()).select([
        "custom_title", "title", "url", "source_type", "platform", "status",
        "scraped_at", "saved_at", "created_at",
    ])
    return [{**(doc.to_dict() or {}), "_id": doc.id, "_version": doc.update_time.isoformat()}
            for doc in query.stream(timeout=30)]


def _validate_record_versions(expected_versions: Dict[str, str]) -> None:
    if any(not isinstance(ident, str) or not ident or "/" in ident or ident in {".", ".."}
           or len(ident.encode("utf-8")) > 1500 or not isinstance(version, str) or not version
           for ident, version in expected_versions.items()):
        raise ValueError("Reload records and select valid record versions.")


def delete_records(db: firestore.Client, expected_versions: Dict[str, str], *,
                   dataset: str | None = None) -> Dict[str, List[str]]:
    """Delete the exact reviewed versions atomically; keep concurrently changed records."""
    _validate_record_versions(expected_versions)
    if len(expected_versions) > 200:
        raise ValueError("Delete at most 200 records at a time.")
    if not expected_versions:
        return {"deleted": [], "skipped": [], "missing": []}
    collection = db.collection(_get_collection_name())
    refs = {ident: collection.document(ident) for ident in expected_versions}

    @firestore.transactional
    def remove(transaction: Any) -> Dict[str, List[str]]:
        snapshots = {doc.id: doc for doc in db.get_all(
            list(refs.values()), field_paths=["custom_title"] if dataset is not None else [],
            transaction=transaction, timeout=30)}
        result: Dict[str, List[str]] = {"deleted": [], "skipped": [], "missing": []}
        for ident, ref in refs.items():
            snapshot = snapshots[ident]
            if not snapshot.exists:
                result["missing"].append(ident)
            elif snapshot.update_time.isoformat() != expected_versions[ident]:
                result["skipped"].append(ident)
            elif dataset is not None and dataset_key(snapshot.to_dict() or {}) != dataset:
                result["skipped"].append(ident)
            else:
                transaction.delete(ref)
                result["deleted"].append(ident)
        return result

    return remove(db.transaction(max_attempts=3))


def delete_dataset_records(db: firestore.Client, dataset: str, expected_versions: Dict[str, str],
                           on_progress: Any = None) -> Dict[str, List[str]]:
    """Delete a reviewed dataset in bounded transactions, reporting partial progress."""
    if not isinstance(dataset, str) or not (dataset == "untitled:" or
                                          (dataset.startswith("title:") and dataset[6:].strip())):
        raise ValueError("Choose a single saved dataset.")
    # Validate the entire review before the first write, including later chunks.
    _validate_record_versions(expected_versions)
    result: Dict[str, List[str]] = {"deleted": [], "skipped": [], "missing": [], "unconfirmed": []}
    ids = list(expected_versions)
    for start in range(0, len(ids), 200):
        batch_ids = ids[start:start + 200]
        try:
            part = delete_records(db, {ident: expected_versions[ident] for ident in batch_ids}, dataset=dataset)
        except Exception:
            # Earlier transactions may have committed; never hide that progress.
            result["unconfirmed"] = ids[start:]
            break
        for key in ("deleted", "skipped", "missing"):
            result[key].extend(part[key])
        if on_progress:
            on_progress(min(start + 200, len(ids)), len(ids))
    return result
