"""Read public website pages without browser sessions, crawling or model credentials."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import certifi

MAX_LINKS = 10
MAX_BYTES = 2_000_000
MAX_TEXT = 60_000
SOCIAL_HOSTS = ("facebook.com", "fb.com", "fb.watch", "reddit.com", "redd.it", "x.com", "twitter.com",
                "tiktok.com", "instagram.com", "threads.net", "threads.com", "youtube.com", "youtu.be",
                "linkedin.com", "pinterest.com", "pin.it", "snapchat.com", "bsky.app")


class WebsiteError(ValueError):
    pass


def normalize_website_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        if (parts.scheme not in {"http", "https"} or not host or "." not in host
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80 if parts.scheme == "http" else 443}
                or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value):
            raise ValueError
        if any(host == domain or host.endswith("." + domain) for domain in SOCIAL_HOSTS):
            raise WebsiteError("Use a regular website link here. Social media links belong in their platform's collector.")
        if host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (not address.is_global or address.is_multicast):
            raise ValueError
        return urlunsplit((parts.scheme, host, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parts.query, safe="%/?@!$&'()*+,;=:-._~"), ""))
    except WebsiteError:
        raise
    except (ValueError, UnicodeError):
        raise WebsiteError("Enter a public HTTP or HTTPS website link using the standard web port.") from None


def parse_website_links(text: str) -> list[str]:
    urls = []
    for line, value in enumerate(text.splitlines(), 1):
        if value.strip():
            try:
                url = normalize_website_url(value)
            except WebsiteError as exc:
                raise WebsiteError(f"Line {line}: {exc}") from None
            if url not in urls:
                urls.append(url)
    if not urls:
        raise WebsiteError("Paste at least one website link, one per line.")
    if len(urls) > MAX_LINKS:
        raise WebsiteError(f"Collect at most {MAX_LINKS} unique website links per run.")
    return urls


def _public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast
                                for a in addresses):
            raise WebsiteError("This link resolves to a private or reserved network address and cannot be collected.")
        return addresses
    except OSError:
        raise WebsiteError("The website address could not be resolved. Check the link and try again.") from None


def _get_page(url: str, deadline: float) -> tuple[int, dict[str, str], bytes]:
    """Pin validated DNS addresses while retaining the original HTTP Host and TLS hostname.

    Redirects are followed by the caller after repeating all checks. No ambient
    cookies, authorization headers or proxy credentials are sent to user URLs.
    """
    parts = urlsplit(url)
    port = 443 if parts.scheme == "https" else 80
    addresses = _public_addresses(parts.hostname, port)

    def connect(address, timeout, source_address=None):
        for index, ip in enumerate(addresses):
            remaining = min(timeout, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError
            try:
                return socket.create_connection((ip, port), remaining, source_address)
            except OSError:
                if index == len(addresses) - 1:
                    raise

    remaining = min(15, deadline - time.monotonic())
    if remaining <= 0:
        raise WebsiteError("Collection reached its time limit. Try fewer links.")
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, timeout=remaining,
                                                context=ssl.create_default_context(cafile=certifi.where()))
    else:
        connection = http.client.HTTPConnection(parts.hostname, timeout=remaining)
    connection._create_connection = connect
    try:
        connection.request("GET", urlunsplit(("", "", parts.path or "/", parts.query, "")),
                           headers={"User-Agent": "CrisisRadar/1.0 (public website text collection)",
                                    "Accept": "text/html, application/xhtml+xml, text/plain", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status != 200:
            return response.status, headers, b""
        if headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
            raise WebsiteError("The website returned an unsupported compressed response.")
        length = headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BYTES:
            raise WebsiteError("The page exceeds the 2 MB collection limit.")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebsiteError("Collection reached its time limit. Try fewer links.")
            if connection.sock is not None:
                connection.sock.settimeout(min(15, remaining))
            chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise WebsiteError("The page exceeds the 2 MB collection limit.")
            chunks.append(chunk)
        return response.status, headers, b"".join(chunks)
    except (OSError, http.client.HTTPException):
        raise WebsiteError("The website could not be reached securely or did not respond in time. Try again later.") from None
    finally:
        connection.close()


class _PageText(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form", "svg"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    BLOCKS = {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "tr", "blockquote", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.chunks, self.main_chunks, self.title_chunks = [], [], [], []
        self.published = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (tag in self.SKIP or "hidden" in attrs or str(attrs.get("aria-hidden") or "").lower() == "true"
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style") or "", re.I)))
        if tag == "meta" and attrs.get("property") == "article:published_time":
            self.published = attrs.get("content") or ""
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if tag in self.BLOCKS:
            self.handle_data("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in self.BLOCKS:
            self.handle_data("\n")

    def handle_data(self, data):
        tags = {tag for tag, _ in self.stack}
        if "title" in tags:
            self.title_chunks.append(data)
        elif not any(hidden for _, hidden in self.stack) and "head" not in tags:
            self.chunks.append(data)
            if tags & {"main", "article"}:
                self.main_chunks.append(data)


def extract_page(payload: bytes, content_type: str) -> tuple[str, str, str, bool]:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in {"text/html", "application/xhtml+xml", "text/plain"}:
        raise WebsiteError("Only HTML and plain-text pages are supported. PDFs, media and other downloads are not collected.")
    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    try:
        text = payload.decode(charset.group(1) if charset else "utf-8", errors="replace")
    except LookupError:
        text = payload.decode("utf-8", errors="replace")
    title, published = "", ""
    if mime != "text/plain":
        parser = _PageText()
        parser.feed(text)
        parser.close()
        title = " ".join("".join(parser.title_chunks).split())[:500]
        main = "".join(parser.main_chunks).strip()
        text = main if main else "".join(parser.chunks)
        published = parser.published[:100]
    text = "\n".join(line for part in text.splitlines() if (line := " ".join(part.split())))
    if not text:
        raise WebsiteError("No readable page text was returned. The page may require JavaScript or sign-in.")
    if (title.lower().rstrip(". !") in {"just a moment", "access denied", "attention required | cloudflare", "verify you are human"}
            or "enable javascript and cookies to continue" in text.lower()):
        raise WebsiteError("The website returned a verification page. Open the source in your browser; it was not collected.")
    if title:
        text = title + "\n\n" + text
    return title, text[:MAX_TEXT], published, len(text) > MAX_TEXT


def collect_websites(urls: list[str], *, on_progress=None) -> list[dict]:
    urls = parse_website_links("\n".join(urls))
    records, seen = [], set()
    deadline = time.monotonic() + 90
    for index, original in enumerate(urls, 1):
        current = original
        try:
            if time.monotonic() >= deadline:
                raise WebsiteError("Collection reached its time limit. Remaining links were not attempted; completed pages were kept.")
            for hop in range(6):
                current = normalize_website_url(current)
                status, headers, payload = _get_page(current, deadline)
                if status not in {301, 302, 303, 307, 308}:
                    break
                if hop == 5 or not headers.get("location"):
                    raise WebsiteError("The page redirected too many times or returned an invalid redirect.")
                current = urljoin(current, headers["location"])
            if status != 200:
                raise WebsiteError(f"The website returned HTTP {status}. It may be unavailable or require sign-in or verification.")
            title, text, published, truncated = extract_page(payload, headers.get("content-type", ""))
            if current not in seen:
                seen.add(current)
                records.append({"url": current, "target_url": original, "raw_text": text, "title": title,
                                "publisher": urlsplit(current).hostname, "platform": "website", "source_type": "website_page",
                                "scraped_at": datetime.now(timezone.utc).isoformat(), "created_at": published,
                                "text_truncated": truncated, "comments": [], "comment_count": 0})
        except WebsiteError as exc:
            records.append({"url": original, "target_url": original, "source_type": "batch_warning", "raw_text": str(exc)})
            if time.monotonic() >= deadline:
                break
        finally:
            if on_progress:
                on_progress(index, len(urls), original)
    return records
