"""Google News search collection with a transparent keyword scan for boycott and border-tension risk.

Each query is read from the Google News search feed: the same results as Google Search's News tab,
without driving a browser that Google would challenge with CAPTCHAs. Keyword flags are a fast,
explainable first pass over headlines; saved articles can be analyzed by the AI provider afterwards.
"""

from __future__ import annotations

import html
import re
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import certifi

NEWS_CATEGORIES = {
    "oyoshi": {"label": "OYOSHI mentions", "query": "OYOSHI"},
    "border_tension": {"label": "Thailand–Cambodia border tension", "query": "Thailand Cambodia border tension"},
    "boycott": {"label": "Boycott of Thai products in Cambodia", "query": "boycott Thailand products Cambodia"},
}
EDITIONS = {
    "en": {"label": "English", "hl": "en-US", "gl": "US", "ceid": "US:en"},
    "th": {"label": "Thai", "hl": "th", "gl": "TH", "ceid": "TH:th"},
}
TIME_WINDOWS = {"1d": "Past 24 hours", "7d": "Past week", "30d": "Past month", "1y": "Past year"}
MAX_ARTICLES = 100
MAX_QUERY = 200
# Small scans are scaled as if they had this many articles so one headline cannot spike the index.
MIN_SAMPLE = 10
RISK_LEVELS = ((45, "High"), (20, "Elevated"), (0, "Low"))
RISING_DELTA = 10

THAI_TERMS = ("thailand", "thai", "bangkok", "ไทย", "ថៃ")
CAMBODIA_TERMS = ("cambodia", "khmer", "phnom penh", "กัมพูชา", "เขมร", "កម្ពុជា", "ខ្មែរ")
BOYCOTT_TERMS = ("boycott", "stop buying", "ban on thai", "bans thai", "ban thai", "คว่ำบาตร", "แบนสินค้า",
                 "ពហិការ")
TENSION_TERMS = ("tension", "dispute", "standoff", "stand-off", "border closure", "close border", "closes border",
                 "closed border", "border closed", "border crossing closed", "troops", "soldiers", "military",
                 "protest", "accuse", "condemn", "provocation", "incursion", "conflict", "sovereignty",
                 "reject", "ตึงเครียด", "ข้อพิพาท", "ปิดด่าน",
                 "ทหาร", "តានតឹង", "ជម្លោះ", "ទាហាន")
ESCALATION_TERMS = ("clash", "gunfire", "shelling", "artillery", "airstrike", "air strike", "fighting",
                    "killed", "wounded", "injured", "casualt", "landmine", "land mine", "rocket", "evacuat",
                    "war", "attack", "fire exchange", "exchange of fire", "ปะทะ", "อพยพ", "ยิงถล่ม",
                    "ប៉ះទង្គិច", "បាញ់", "ជម្លៀស")
DEESCALATION_TERMS = ("ceasefire", "cease-fire", "truce", "peace", "talks", "reopen", "re-open", "de-escalat",
                      "agree", "withdraw", "eases", "thaw", "หยุดยิง", "เจรจา", "បទឈប់បាញ់", "ចរចា")
CEASEFIRE_BREACH = ("violat", "breach", "broke", "broken", "collapse")
NEGATIVE_TERMS = ("crisis", "threat", "anger", "angry", "fear", "boycott", "dispute", "tension", "ban", "war",
                  "violence", "accuse", "warn", "hostile", "deadly", "killed", "clash", "attack", "condemn",
                  "protest", "loss", "slump", "fall", "decline", "risk", "conflict", "ตึงเครียด", "ความขัดแย้ง",
                  "ជម្លោះ")
POSITIVE_TERMS = ("peace", "agreement", "cooperat", "growth", "celebrat", "success", "boost", "welcome",
                  "partnership", "reopen", "improv", "award", "gold", "recover", "friendship", "support",
                  "สันติ", "ความร่วมมือ", "សន្តិភាព", "កិច្ចសហប្រតិបត្តិការ")


class NewsError(RuntimeError):
    pass


def _matcher(terms: Iterable[str]) -> Callable[[str], list[str]]:
    # Latin terms match at a word start ("clash" finds "clashes"); short ones must be whole words so
    # "ban" skips "Bangkok" and "war" skips "warn". Thai and Khmer are written without word spaces.
    def pattern(term: str) -> re.Pattern[str]:
        end = r"(?:s|es)?(?![a-z])" if len(term) <= 4 else ""
        return re.compile(r"(?<![a-z])" + re.escape(term) + end, re.IGNORECASE)

    compiled = [(term, pattern(term) if term.isascii() else None) for term in terms]

    def find(text: str) -> list[str]:
        return [term for term, pattern in compiled if (pattern.search(text) if pattern else term in text)]
    return find


_thai, _cambodia, _boycott = _matcher(THAI_TERMS), _matcher(CAMBODIA_TERMS), _matcher(BOYCOTT_TERMS)
_tension, _escalation, _deescalation = _matcher(TENSION_TERMS), _matcher(ESCALATION_TERMS), _matcher(DEESCALATION_TERMS)
_breach, _negative, _positive = _matcher(CEASEFIRE_BREACH), _matcher(NEGATIVE_TERMS), _matcher(POSITIVE_TERMS)


def search_url(query: str, window: str = "7d", edition: str = "en") -> str:
    ed = EDITIONS[edition]
    q = quote_plus(f"{query.strip()[:MAX_QUERY]} when:{window}" if window in TIME_WINDOWS else query.strip()[:MAX_QUERY])
    return f"https://news.google.com/rss/search?q={q}&hl={ed['hl']}&gl={ed['gl']}&ceid={ed['ceid']}"


def browse_url(query: str, window: str = "7d", edition: str = "en") -> str:
    """The equivalent Google News page, for people to open the same search themselves."""
    return search_url(query, window, edition).replace("/rss/search", "/search", 1)


def _canonical_link(link: str) -> str:
    parts = urlsplit(link.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    # Google appends tracking parameters that differ between feeds for the same article.
    if parts.hostname == "news.google.com":
        return urlunsplit(("https", parts.hostname, parts.path, "", ""))
    return link.strip()


def _plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def _published(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def assess_article(text: str) -> dict[str, Any]:
    """Keyword scan of one headline/snippet. Flags need a Thailand or Cambodia mention."""
    thai, cambodia = _thai(text), _cambodia(text)
    boycott, tension, escalation = _boycott(text), _tension(text), _escalation(text)
    deescalation = _deescalation(text)
    if any(t.startswith(("ceasefire", "cease-fire", "หยุดยิง", "បទឈប់បាញ់")) for t in deescalation) and _breach(text):
        escalation = escalation + ["ceasefire breach"]
        deescalation = [t for t in deescalation if not t.startswith(("ceasefire", "cease-fire", "หยุดยิง", "បទឈប់បាញ់"))]
    linked = bool(thai or cambodia)
    level = (2 if escalation else 1 if tension else 0) if linked else 0
    if level and deescalation:
        level -= 1
    tone_score = len(_positive(text)) - len(_negative(text))
    flags = []
    if linked and boycott:
        flags.append("Boycott")
    if level == 2:
        flags.append("Escalation")
    elif level == 1:
        flags.append("Border tension")
    if linked and deescalation:
        flags.append("De-escalation")
    return {
        "tone": "Positive" if tone_score > 0 else "Negative" if tone_score < 0 else "Neutral",
        "tone_score": tone_score,
        "thailand_cambodia": linked,
        "both_countries": bool(thai and cambodia),
        "boycott": linked and bool(boycott),
        "tension_level": level,
        "deescalation": linked and bool(deescalation),
        "flags": flags,
        "matched_terms": list(dict.fromkeys((boycott + escalation + tension + deescalation) if linked else []))[:12],
        "method": "keyword_scan_v1",
    }


def parse_feed(payload: bytes, category: str, query: str, edition: str = "en",
               limit: int = MAX_ARTICLES) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise NewsError("Google News returned an unreadable response. Try again later.") from None
    channel = root.find("channel")
    if channel is None:
        raise NewsError("Google News returned an unexpected response.")
    scraped_at = datetime.now(timezone.utc).isoformat()
    records = []
    for item in channel.findall("item")[:max(1, min(MAX_ARTICLES, int(limit)))]:
        link = _canonical_link(item.findtext("link", ""))
        title = _plain(item.findtext("title", ""))
        if not link or not title:
            continue
        source = item.find("source")
        publisher = _plain(source.text or "") if source is not None else ""
        publisher_url = (source.get("url", "") if source is not None else "")
        headline = title.removesuffix(f" - {publisher}") if publisher else title
        snippet = _plain(item.findtext("description", ""))
        # Search-feed descriptions usually repeat the headline and publisher.
        if snippet.replace(publisher, "").strip() in {"", headline}:
            snippet = ""
        if publisher.startswith(("https://", "http://")):
            publisher = (urlsplit(publisher).hostname or "").removeprefix("www.")
        text = headline + (f"\n\n{snippet}" if snippet else "")
        records.append({
            "url": link,
            "raw_text": text,
            "title": headline,
            "publisher": publisher,
            "publisher_url": publisher_url if publisher_url.startswith(("https://", "http://")) else "",
            "created_at": _published(item.findtext("pubDate", "")),
            "scraped_at": scraped_at,
            "source_type": "news_article",
            "platform": "google_news",
            "news_category": category,
            "news_categories": [category],
            "news_query": query,
            "news_edition": edition,
            "comments": [],
            "comment_count": 0,
            "news_assessment": assess_article(text),
        })
    return records


def fetch_feed(url: str, timeout: float = 20) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SenseMaker news monitor)",
                                    "Accept": "application/rss+xml, application/xml;q=0.9"})
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where())) as response:
            return response.read(5_000_000)
    except HTTPError as exc:
        if exc.code == 429:
            raise NewsError("Google News is rate limiting requests. Wait a few minutes and search again.") from None
        raise NewsError(f"Google News returned HTTP {exc.code}; try again later.") from None
    except (URLError, TimeoutError):
        raise NewsError("Google News could not be reached. Check the server connection and try again.") from None


def collect_news(
    searches: dict[str, str], *, window: str = "7d", editions: Iterable[str] = ("en",),
    limit: int = 50, fetch: Callable[[str], bytes] = fetch_feed,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Search each category in each edition; deduplicate articles found by several searches.

    A failed search becomes a ``batch_warning`` row so the other categories are kept.
    """
    jobs = [(category, query.strip(), edition) for category, query in searches.items() if query.strip()
            for edition in editions if edition in EDITIONS]
    by_url: dict[str, dict[str, Any]] = {}
    warnings = []
    for done, (category, query, edition) in enumerate(jobs, 1):
        url = search_url(query, window, edition)
        try:
            for record in parse_feed(fetch(url), category, query, edition, limit):
                existing = by_url.get(record["url"])
                if existing:
                    if category not in existing["news_categories"]:
                        existing["news_categories"].append(category)
                    continue
                record["target_url"] = browse_url(query, window, edition)
                by_url[record["url"]] = record
        except NewsError as exc:
            warnings.append({"url": browse_url(query, window, edition), "source_type": "batch_warning",
                             "target_url": f"{NEWS_CATEGORIES.get(category, {}).get('label', category)} · "
                                           f"{EDITIONS[edition]['label']}",
                             "raw_text": str(exc)})
        if on_progress:
            on_progress(done, len(jobs), query)
        if done < len(jobs):
            time.sleep(0.3)
    articles = sorted(by_url.values(), key=lambda r: r.get("created_at", ""), reverse=True)
    return articles + warnings


def risk_level(score: float) -> str:
    return next(label for threshold, label in RISK_LEVELS if score >= threshold)


def build_scan_snapshot(
    articles: list[dict[str, Any]], *, searches: dict[str, str], window: str, editions: list[str],
    previous: dict[str, Any] | None = None, scan_id: str | None = None, scanned_at: str | None = None,
) -> dict[str, Any] | None:
    """Aggregate one scan into a dated border-tension risk point.

    Risk = 100 × Σ tension level / (2 × max(linked articles, MIN_SAMPLE)), where escalation
    (clashes, shelling, casualties) is level 2, other tension reporting level 1, and coverage
    of ceasefires/talks lowers an article by one level.
    """
    articles = [a for a in articles if a.get("source_type") == "news_article"]
    if not articles:
        return None
    assessments = [a.get("news_assessment") or assess_article(a.get("raw_text", "")) for a in articles]
    linked = [x for x in assessments if x["thailand_cambodia"]]
    tension_points = sum(x["tension_level"] for x in linked)
    risk = round(100 * tension_points / (2 * max(len(linked), MIN_SAMPLE)), 1)
    boycott = sum(x["boycott"] for x in linked)
    previous_risk = previous.get("risk_score") if previous else None
    delta = round(risk - previous_risk, 1) if isinstance(previous_risk, (int, float)) else None
    category_counts = {key: 0 for key in NEWS_CATEGORIES}
    for article in articles:
        for category in article.get("news_categories") or [article.get("news_category")]:
            if category in category_counts:
                category_counts[category] += 1
    tones = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for x in assessments:
        tones[x["tone"]] += 1
    return {
        "scan_id": scan_id or uuid4().hex,
        "scanned_at": scanned_at or datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles),
        "linked_count": len(linked),
        "category_counts": category_counts,
        "boycott_count": boycott,
        "tension_count": sum(x["tension_level"] == 1 for x in linked),
        "escalation_count": sum(x["tension_level"] == 2 for x in linked),
        "deescalation_count": sum(x["deescalation"] for x in linked),
        "risk_score": risk,
        "risk_level": risk_level(risk),
        "risk_delta": delta,
        "rising": delta is not None and delta >= RISING_DELTA,
        "boycott_index": round(100 * boycott / max(len(linked), MIN_SAMPLE), 1),
        "tone_counts": tones,
        "searches": {k: v for k, v in searches.items() if v.strip()},
        "window": window,
        "editions": list(editions),
        "score_version": 1,
    }


def daily_flag_counts(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flagged articles per publication day, for the timeline inside a single scan."""
    counts: dict[tuple[str, str], int] = {}
    for article in articles:
        day = str(article.get("created_at", ""))[:10]
        assessment = article.get("news_assessment") or {}
        if not day:
            continue
        for flag in assessment.get("flags", []):
            if flag != "De-escalation":
                counts[(day, flag)] = counts.get((day, flag), 0) + 1
    return [{"day": day, "flag": flag, "articles": n} for (day, flag), n in sorted(counts.items())]
