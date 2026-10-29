"""Per-user source libraries, stored through the server Firestore client."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def normalize_link(value: str) -> str:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Use one web link per line.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or "." not in parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("Enter a valid http or https web link.")
        # Accessing .port also validates malformed ports.
        port = parts.port
        host = parts.hostname.lower()
        if port is not None and (parts.scheme.lower(), port) not in {("https", 443), ("http", 80)}:
            host += f":{port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", parts.query, ""))
    except ValueError as exc:
        raise ValueError("Enter a valid http or https web link.") from exc


def parse_links(text: str) -> tuple[list[str], list[str]]:
    urls, invalid = [], []
    seen = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            url = normalize_link(line)
        except ValueError:
            invalid.append(line.strip())
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls, invalid


def library_owner_id(email: str) -> str:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A signed-in user's email is required.")
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _collection(db: Any, email: str) -> Any:
    # The dashboard supplies the email returned by server-side authentication.
    return db.collection("sensemaker_saved_links").document(library_owner_id(email)).collection("links")


def _link_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def load_saved_links(db: Any, email: str) -> list[str]:
    records = [doc.to_dict() or {} for doc in _collection(db, email).stream(timeout=15, retry=None)]
    records.sort(key=lambda row: (str(row.get("saved_at", "")), str(row.get("url", ""))))
    urls, _ = parse_links("\n".join(str(row.get("url", "")) for row in records))
    return urls


def save_links(db: Any, email: str, urls: list[str]) -> None:
    _write_links(db, email, urls, remove=False)


def remove_saved_links(db: Any, email: str, urls: list[str]) -> None:
    _write_links(db, email, urls, remove=True)


def _write_links(db: Any, email: str, urls: list[str], *, remove: bool) -> None:
    normalized = list(dict.fromkeys(normalize_link(url) for url in urls))
    collection = _collection(db, email)
    timestamp = datetime.now(timezone.utc).isoformat()
    # Separate documents and bounded writes allow libraries larger than one document/batch.
    for offset in range(0, len(normalized), 400):
        batch = db.batch()
        for url in normalized[offset:offset + 400]:
            ref = collection.document(_link_id(url))
            if remove:
                batch.delete(ref)
            else:
                batch.set(ref, {"url": url, "saved_at": timestamp})
        batch.commit(timeout=15, retry=None)
