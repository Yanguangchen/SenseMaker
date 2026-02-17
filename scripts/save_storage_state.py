"""Capture authenticated Playwright storage state for Facebook scraping."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    output_path = Path(os.getenv("PLAYWRIGHT_STORAGE_STATE", "storage_state.json")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Opening browser for manual Facebook login...")
    print("1) Log in to Facebook in the opened browser")
    print("2) Open your target feed/profile page")
    print("3) Press ENTER here to save session state")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

        input("Press ENTER after login is complete: ")
        context.storage_state(path=str(output_path))
        browser.close()

    print(f"Saved storage state to: {output_path}")
    print("Set PLAYWRIGHT_STORAGE_STATE to this path in .env")


if __name__ == "__main__":
    main()

