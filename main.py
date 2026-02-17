"""Entry point for running ingestion and processing."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from modules.database import init_db, upsert_post
from modules.processor import process_pending_posts
from modules.scraper import scrape_group


def _persist_factory():
    db = init_db()

    def persist(post: dict) -> None:
        upsert_post(db, post)

    return persist


async def run() -> None:
    load_dotenv()
    target_url = os.getenv("TARGET_GROUP_URL")
    if not target_url:
        raise ValueError("TARGET_GROUP_URL is not set.")

    persist = _persist_factory()
    scraped = await scrape_group(target_url=target_url, persist_cb=persist)
    print(f"[main] scraped posts: {len(scraped)}")

    db = init_db()
    processed = process_pending_posts(db, limit=25)
    print(f"[main] processed posts: {processed}")


if __name__ == "__main__":
    asyncio.run(run())

