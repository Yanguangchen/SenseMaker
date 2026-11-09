"""Revocable, read-only links to one explicitly shared velocity card."""

import re
from secrets import token_hex
from urllib.parse import urlencode, urlsplit, urlunsplit

from firebase_admin import firestore

from modules import velocity_tracking as store
from modules.saved_links import library_owner_id

QUERY_KEY = "velocity_share"
TOKEN = re.compile(r"[a-f0-9]{64}")


def _shares(db):
    return db.collection("sensemaker_velocity_shares")


def set_sharing(db, email: str, card_id: str, enabled: bool) -> list[dict]:
    """Only the approved creator can enable/revoke, atomically with card state."""
    from modules.auth import allowed_emails

    if email not in allowed_emails():
        raise PermissionError("Sign in with an approved account to share a card.")
    if not TOKEN.fullmatch(card_id):
        raise ValueError("Choose a saved velocity card.")
    ref = store._ref(db, email)
    new_token = token_hex(32)

    @firestore.transactional
    def save(transaction):
        cards = (ref.get(transaction=transaction, timeout=15, retry=None).to_dict() or {}).get("cards", [])
        card = next((card for card in cards if card["id"] == card_id), None)
        if card is None:
            raise ValueError("This card no longer exists or belongs to another user.")
        changed = dict(card)
        previous = changed.pop("share_token", None)
        if enabled:
            token = previous or new_token
            changed["share_token"] = token
            transaction.set(_shares(db).document(token), {
                "owner_id": library_owner_id(email), "card_id": card_id, "active": True,
            })
        elif previous:
            transaction.set(_shares(db).document(previous), {"active": False})
        updated = [changed if row["id"] == card_id else row for row in cards]
        transaction.set(ref, {"cards": updated})
        return updated

    return save(db.transaction(max_attempts=3))


def share_url(base: str, token: str) -> str:
    if not TOKEN.fullmatch(token):
        raise ValueError("Invalid share link.")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Open the app in a browser to create a share link.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode({QUERY_KEY: token}), ""))


def load_shared_card(db, token: str) -> dict | None:
    """Resolve a capability on every read; never expose an owner/library record."""
    from modules.auth import allowed_emails

    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return None
    link = _shares(db).document(token).get(timeout=15, retry=None).to_dict() or {}
    if link.get("active") is not True:
        return None
    owner = next((email for email in allowed_emails() if library_owner_id(email) == link.get("owner_id")), None)
    if owner is None:
        return None
    card = next((card for card in store.load_cards(db, owner)
                 if card["id"] == link.get("card_id") and card.get("share_token") == token), None)
    if card is None:
        return None
    # Full history is projected to metrics by the existing archive reader. Raw
    # comment samples, browser sessions, owner identities, and tokens stay private.
    history = store.load_run_history(db, owner, card)
    latest = card.get("runs", [])[-1:] or [{"results": []}]
    analysis = [{"post_id": row["post_id"], "comment_analysis": {
        key: value for key, value in row.get("comment_analysis", {}).items()
        if key in {"status", "analyzed_count", "collected_count", "counts", "keywords"}
    }} for row in latest[0].get("results", [])]
    return {"name": card.get("name", "Velocity tracking card"),
            "posts": [{key: value for key, value in post.items() if key in {"id", "url", "remark"}}
                      for post in store.card_posts(card)],
            "runs": history, "analysis_results": analysis}
