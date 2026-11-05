"""Direct Facebook search preset used by the Oyoshi collection card."""

from urllib.parse import parse_qs, urlsplit

from modules.saved_links import is_facebook_link, normalize_link

OYOSHI_SEARCH_URL = (
    "https://www.facebook.com/search/top?q=oyoshi&filters="
    "eyJyZWNlbnRfcG9zdHM6MCI6IntcIm5hbWVcIjpcInJlY2VudF9wb3N0c1wiLFwiYXJnc1wiOlwiXCJ9In0%3D"
)


def validate_facebook_search_url(value: str) -> str:
    url = normalize_link(value)
    parts = urlsplit(url)
    query = parse_qs(parts.query).get("q", [""])[0].strip()
    if not is_facebook_link(url) or not parts.path.startswith("/search/") or not query:
        raise ValueError("Use a Facebook search-results URL with a search term (q=…), such as the Oyoshi preset.")
    return url
