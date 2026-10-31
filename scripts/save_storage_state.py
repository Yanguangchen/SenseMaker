"""Capture authenticated Playwright storage state for Facebook scraping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.config import load_environment
from modules.facebook_session import capture_facebook_session, facebook_storage_path


def main() -> None:
    load_environment()
    output_path = facebook_storage_path()
    print("Opening Facebook with your saved session, if available...")
    print("Complete login or any Facebook verification in the browser. The session saves automatically.")
    capture_facebook_session(output_path)
    print(f"Saved storage state to: {output_path}")


if __name__ == "__main__":
    main()

