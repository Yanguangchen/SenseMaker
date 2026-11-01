"""Check that the server's AI keys meet GenCite's requirements.

Reads the same settings as the dashboard (.env.local, .env, environment variables
or Streamlit secrets) and sends one small GenCite request per provider. Keys are
never printed.

    python scripts/check_gencite_providers.py            # every provider
    python scripts/check_gencite_providers.py gemini     # just Gemini
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.config import AI_PROVIDERS, get_ai_key, load_environment
from modules.gencite import check_provider


def main(argv: list[str] | None = None) -> int:
    load_environment()
    requested = list(argv if argv is not None else sys.argv[1:])
    unknown = [name for name in requested if name not in AI_PROVIDERS]
    if unknown:
        print(f"Unknown provider: {', '.join(unknown)}. Choose from {', '.join(AI_PROVIDERS)}.")
        return 2
    passed = failed = 0
    for provider in requested or list(AI_PROVIDERS):
        if not requested and not get_ai_key(provider):
            print(f"{AI_PROVIDERS[provider]}: SKIPPED (no key configured)\n")
            continue
        report = check_provider(provider)
        print(f"{report['label']} · {report['model']}: {'PASS' if report['ok'] else 'FAIL'}")
        for step in report["steps"]:
            print(f"  [{'ok' if step['ok'] else 'X '}] {step['name']}: {step['detail']}")
        print()
        passed, failed = passed + report["ok"], failed + (not report["ok"])
    if not passed and not failed:
        print("No AI provider key is configured. Set GEMINI_KEY (or GEMINI_API_KEY) and/or OPENAI_API_KEY.")
        return 1
    print("A pass confirms access and request format, not remaining daily quota.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
