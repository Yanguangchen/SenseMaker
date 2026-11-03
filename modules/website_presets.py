"""Named website URL lists, private to the account verified by the dashboard."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists, NotFound

from modules.saved_links import library_owner_id
from modules.websites import parse_website_links

MAX_NAME_CHARS = 120


def _collection(db, email: str):
    return db.collection("sensemaker_website_presets").document(library_owner_id(email)).collection("presets")


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > MAX_NAME_CHARS:
        raise ValueError(f"Enter a preset name between 1 and {MAX_NAME_CHARS} characters.")
    return name


def _urls(text: str) -> list[str]:
    urls = parse_website_links(text)
    if sum(len(url.encode("utf-8")) for url in urls) > 200_000:
        raise ValueError("These URLs are too long to save as a preset. Use shorter page links.")
    return urls


def _ref(db, email: str, preset_id: str):
    if not re.fullmatch(r"[a-f0-9]{64}", preset_id):
        raise ValueError("Choose a saved preset.")
    return _collection(db, email).document(preset_id)


def list_presets(db, email: str) -> list[dict]:
    rows = []
    for doc in _collection(db, email).stream(timeout=15, retry=None):
        row = doc.to_dict() or {}
        if (not re.fullmatch(r"[a-f0-9]{64}", doc.id)
                or not isinstance(row.get("name"), str) or not isinstance(row.get("urls"), list)):
            continue
        try:
            name, urls = _name(row["name"]), _urls("\n".join(row["urls"]))
        except (TypeError, ValueError):
            continue
        rows.append({**row, "id": doc.id, "name": name, "urls": urls})
    return sorted(rows, key=lambda row: row["name"].casefold())


def save_preset(db, email: str, name: str, text: str) -> dict:
    name, urls = _name(name), _urls(text)
    preset_id = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    row = {"name": name, "urls": urls, "created_at": now, "updated_at": now}
    try:
        _ref(db, email, preset_id).create(row, timeout=15, retry=None)
    except AlreadyExists:
        raise ValueError("A preset with this name already exists. Choose it and use Update preset, or enter a new name.") from None
    return {**row, "id": preset_id}


def update_preset(db, email: str, preset_id: str, text: str) -> None:
    urls = _urls(text)
    try:
        _ref(db, email, preset_id).update(
            {"urls": urls, "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=15, retry=None)
    except NotFound:
        raise ValueError("This preset no longer exists. Refresh the presets and save a new one.") from None


def delete_preset(db, email: str, preset_id: str) -> None:
    _ref(db, email, preset_id).delete(timeout=15, retry=None)
