"""Creator-owned Facebook cards with shared viewing for approved users."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from secrets import token_hex
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from modules.database import _get_collection_name
from modules.engagement import checks_for
from modules.saved_links import library_owner_id, normalize_link

MAX_CARDS = 10
MAX_POSTS_PER_CARD = 10
MAX_NAME_CHARS = 120
MAX_REMARK_CHARS = 1000
SHOWN_RUNS = 8


def normalize_post_url(value: str) -> str:
    """Accept a post permalink, retaining identifiers but removing tracking parameters."""
    url = normalize_link(value)
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com",
                    "web.facebook.com", "fb.com", "www.fb.com"} or parts.port is not None:
        raise ValueError("Enter a Facebook post link.")
    path = parts.path.rstrip("/")
    query = parse_qs(parts.query)
    post_path = re.fullmatch(
        r"/(?:[^/]+/posts/[^/]+|groups/[^/]+/(?:posts|permalink)/[^/]+|"
        r"(?:[^/]+/)?videos/[^/]+|reel/[^/]+|share/(?:p|v|r)/[^/]+|[^/]+/photos/(?:[^/]+/)*[^/]+)", path)
    query_post = ((path in {"/story.php", "/permalink.php"} and query.get("story_fbid") and query.get("id"))
                  or (path in {"/photo.php", "/photo"} and query.get("fbid"))
                  or (path == "/watch" and query.get("v")))
    if not post_path and not query_post:
        raise ValueError("Use a link to one Facebook post, photo, reel or video, rather than a page or group.")
    kept = {key: query[key][0] for key in ("fbid", "id", "story_fbid", "v") if query.get(key)}
    canonical = urlunsplit(("https", "www.facebook.com", path, urlencode(kept), ""))
    if len(canonical) > 2048:
        raise ValueError("This post link is too long. Use its direct Facebook permalink.")
    return canonical


def _ref(db: Any, email: str) -> Any:
    return db.collection("sensemaker_velocity_tracking").document(library_owner_id(email))


def load_cards(db: Any, email: str) -> list[dict[str, Any]]:
    return (_ref(db, email).get(timeout=15, retry=None).to_dict() or {}).get("cards", [])


def load_workspace_cards(db: Any, email: str) -> list[dict[str, Any]]:
    """Read approved teammates' cards without moving records or sharing logins.

    Mutations and workers still use load_cards, which reads only the creator's
    records. Shared display IDs also separate legacy cards with identical IDs.
    """
    from modules.auth import allowed_emails

    owners = {library_owner_id(owner): owner for owner in allowed_emails()}
    viewer = library_owner_id(email)
    if viewer not in owners:
        raise PermissionError("Sign in with an approved account to view velocity cards.")
    cards = list(load_cards(db, email))
    for owner_id in sorted(owners):
        if owner_id == viewer:
            continue
        for index, card in enumerate(load_cards(db, owners[owner_id]), 1):
            shared = {**card, "id": hashlib.sha256(f"{owner_id}:{card['id']}".encode()).hexdigest(),
                      "shared": True, "owner_id": owner_id}
            if card.get("name", "Velocity tracking card") == "Velocity tracking card":
                shared["name"] = f"Velocity tracking card {index}"
            cards.append(shared)
    return cards


def _post(url: str) -> dict[str, str]:
    canonical = normalize_post_url(url)
    return {"id": hashlib.sha256(canonical.encode()).hexdigest(), "url": canonical,
            "source_url": normalize_link(url)}


def card_posts(card: dict[str, Any]) -> list[dict[str, str]]:
    """Read both new multi-post cards and the original single-post presets."""
    if isinstance(card.get("posts"), list):
        return card["posts"]
    if card.get("url"):
        return [_post(card.get("source_url") or card["url"])]
    return []


def add_card(db: Any, email: str, name: str = "", url: str = "") -> list[dict[str, Any]]:
    name = " ".join(name.split()) or "Velocity tracking card"
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"Use a card name of at most {MAX_NAME_CHARS} characters.")
    row = {"id": token_hex(32), "name": name, "posts": [_post(url)] if url.strip() else [],
           "created_at": datetime.now(timezone.utc).isoformat()}
    ref = _ref(db, email)

    @firestore.transactional
    def save(transaction: Any) -> list[dict[str, Any]]:
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        if len(cards) >= MAX_CARDS:
            raise ValueError(f"You can create up to {MAX_CARDS} cards. Remove a card before adding another.")
        updated = [*cards, row]
        transaction.set(ref, {"cards": updated})
        return updated

    return save(db.transaction(max_attempts=3))


def rename_card(db: Any, email: str, card_id: str, name: str) -> list[dict[str, Any]]:
    """Rename an owned card while preserving its current posts and saved runs."""
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME_CHARS:
        raise ValueError(f"Enter a card name between 1 and {MAX_NAME_CHARS} characters.")
    if not re.fullmatch(r"[a-f0-9]{64}", card_id):
        raise ValueError("Choose a saved velocity card.")
    ref = _ref(db, email)

    @firestore.transactional
    def save(transaction: Any) -> list[dict[str, Any]]:
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        if not any(card["id"] == card_id for card in cards):
            raise ValueError("This card no longer exists. Refresh cards & checks.")
        updated = [{**card, "name": name} if card["id"] == card_id else card for card in cards]
        transaction.set(ref, {"cards": updated})
        return updated

    return save(db.transaction(max_attempts=3))


def add_post(db: Any, email: str, card_id: str, url: str) -> list[dict[str, Any]]:
    return _edit_posts(db, email, card_id, post=_post(url))


def remove_post(db: Any, email: str, card_id: str, post_id: str) -> list[dict[str, Any]]:
    """Remove a table row without deleting its collected record or checks."""
    if not re.fullmatch(r"[a-f0-9]{64}", post_id):
        raise ValueError("Choose a post from this card.")
    return _edit_posts(db, email, card_id, remove_id=post_id)


def save_remark(db: Any, email: str, card_id: str, post_id: str, remark: str) -> list[dict[str, Any]]:
    """Save a note on one owned card's post, retaining concurrent checks and edits."""
    if not re.fullmatch(r"[a-f0-9]{64}", post_id):
        raise ValueError("Choose a post from this card.")
    if not isinstance(remark, str) or len(remark) > MAX_REMARK_CHARS:
        raise ValueError(f"Use a remark of at most {MAX_REMARK_CHARS} characters.")
    return _edit_posts(db, email, card_id, remark_post_id=post_id, remark=remark.strip())


def _edit_posts(db: Any, email: str, card_id: str, *, post: dict[str, str] | None = None,
                remove_id: str | None = None, remark_post_id: str | None = None,
                remark: str = "") -> list[dict[str, Any]]:
    if not re.fullmatch(r"[a-f0-9]{64}", card_id):
        raise ValueError("Choose a saved velocity card.")
    ref = _ref(db, email)

    @firestore.transactional
    def save(transaction: Any) -> list[dict[str, Any]]:
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        card = next((row for row in cards if row["id"] == card_id), None)
        if card is None:
            raise ValueError("This card no longer exists. Refresh cards & checks.")
        posts = card_posts(card)
        if post is not None:
            if any(row["id"] == post["id"] for row in posts):
                raise ValueError("This Facebook post is already in this card.")
            if len(posts) >= MAX_POSTS_PER_CARD:
                raise ValueError(f"Each card can track up to {MAX_POSTS_PER_CARD} posts. Remove a post before adding another.")
            posts = [*posts, post]
        elif remark_post_id is not None:
            if not any(row["id"] == remark_post_id for row in posts):
                raise ValueError("This post is no longer in the card. Refresh cards & checks.")
            posts = [{**row, "remark": remark} if row["id"] == remark_post_id else row for row in posts]
        else:
            posts = [row for row in posts if row["id"] != remove_id]
        changed = {key: value for key, value in card.items() if key not in {"url", "source_url"}}
        changed["posts"] = posts
        updated = [changed if row["id"] == card_id else row for row in cards]
        transaction.set(ref, {"cards": updated})
        return updated

    return save(db.transaction(max_attempts=3))


def remove_card(db: Any, email: str, card_id: str) -> list[dict[str, Any]]:
    """Remove only the preset; collected posts and engagement history stay intact."""
    if not re.fullmatch(r"[a-f0-9]{64}", card_id):
        raise ValueError("Choose a saved velocity card.")
    ref = _ref(db, email)

    @firestore.transactional
    def remove(transaction: Any) -> list[dict[str, Any]]:
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        updated = [card for card in cards if card["id"] != card_id]
        transaction.set(ref, {"cards": updated})
        return updated

    return remove(db.transaction(max_attempts=3))


def save_run(db: Any, email: str, card_id: str, run: dict[str, Any]) -> list[dict[str, Any]]:
    """Archive a run once and atomically update the card's latest eight checks.

    Retrying a save never creates another column. Read the current card inside the
    transaction so concurrent post edits are preserved. Deleted cards stay deleted.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", card_id) or not re.fullmatch(r"[a-f0-9]{64}", run["id"]):
        raise ValueError("Choose a saved velocity card.")
    ref = _ref(db, email)
    history = ref.collection("runs").document(run["id"])

    @firestore.transactional
    def save(transaction: Any) -> list[dict[str, Any]]:
        previous = history.get(transaction=transaction, timeout=15, retry=None).to_dict()
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        if previous:
            return cards
        card = next((row for row in cards if row["id"] == card_id), None)
        number = int(card.get("run_count", 0)) + 1 if card else 1
        saved = {**run, "card_id": card_id, "number": number}
        transaction.set(history, saved)
        if card:
            # URLs are already on the post rows and in the full archive. Avoid
            # duplicating long permalinks in every cell of the account document.
            summary = {**saved, "results": [{key: value for key, value in row.items()
                                            if key not in {"url", "comment_sample", "comment_analysis_details"}}
                                           for row in saved["results"]]}
            # The Analysis tab displays the latest run. Keep older comment data
            # in the run archive, so ten full cards fit Firestore's document limit.
            earlier = [{**old, "results": [{key: value for key, value in row.items()
                                           if key not in {"comment_analysis", "comment_collection",
                                                          "comment_sample", "comment_analysis_details"}}
                                          for row in old.get("results", [])]}
                       for old in card.get("runs", [])]
            changed = {**card, "run_count": number, "runs": [*earlier, summary][-SHOWN_RUNS:]}
            cards = [changed if row["id"] == card_id else row for row in cards]
            transaction.set(ref, {"cards": cards})
        return cards

    return save(db.transaction(max_attempts=3))


def _url_variants(card: dict[str, Any]) -> set[str]:
    """Match permalinks saved by both mobile and desktop collectors, with or without a slash."""
    parts = urlsplit(card["url"])
    variants = {card["url"], card.get("source_url", card["url"])}
    for host in ("www.facebook.com", "facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com",
                 "fb.com", "www.fb.com"):
        for scheme in ("https", "http"):
            for path in (parts.path, parts.path + "/"):
                variants.add(urlunsplit((scheme, host, path, parts.query, "")))
    return variants


def load_card_records(db: Any, cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read only the pinned posts; analysis and dataset selection are not required."""
    posts = [post for card in cards for post in card_posts(card)]
    if not posts:
        return {}
    urls = sorted({url for post in posts for url in _url_variants(post)})
    by_url = {post["url"]: post["id"] for post in posts}
    found: dict[str, dict[str, Any]] = {}
    collection = db.collection(_get_collection_name())
    # Bound each query instead of scanning the full collected-post library.
    for start in range(0, len(urls), 30):
        query = collection.where(filter=FieldFilter("url", "in", urls[start:start + 30]))
        for doc in query.stream(timeout=15, retry=None):
            row = doc.to_dict() or {}
            try:
                ident = by_url.get(normalize_post_url(row.get("url", "")))
            except ValueError:
                continue
            if ident is None:
                continue
            previous = found.get(ident, {})
            checks = {check["at"]: check for check in [*checks_for(previous), *checks_for(row)]}
            found[ident] = {**row, "_id": doc.id, "checks": sorted(checks.values(), key=lambda check: check["at"])}
    return found
