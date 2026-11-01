"""Prepare the public sign-in component as a single self-contained response."""

from __future__ import annotations

import atexit
from functools import lru_cache
from pathlib import Path
import re
import shutil
import tempfile


@lru_cache(maxsize=1)
def sign_in_component_path() -> str:
    source = Path(__file__).parent / "google_sign_in"
    html = (source / "index.html").read_text(encoding="utf-8")
    javascript = (source / "sign-in.js").read_text(encoding="utf-8")
    # A second module request can land on a different replica or be deferred
    # while Streamlit hides the iframe before receiving componentReady.
    inline = "<script>" + javascript.replace("</script", "<\\/script") + "</script>"
    html, count = re.subn(
        r'<script type="module" src="\./sign-in\.js(?:\?[^\"]*)?"></script>',
        lambda _match: inline, html,
    )
    if count != 1:
        raise RuntimeError("The sign-in component must have exactly one frontend entry point.")
    output = Path(tempfile.mkdtemp(prefix="sensemaker-public-sign-in-"))
    atexit.register(shutil.rmtree, output, ignore_errors=True)
    (output / "index.html").write_text(html, encoding="utf-8")
    # Keep the source URL available for cached clients and diagnostics.
    (output / "sign-in.js").write_text(javascript, encoding="utf-8")
    return str(output)
