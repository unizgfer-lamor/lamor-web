"""URL helpers: classify and rewrite links found in old-site HTML.

- old page links (/lamor/...)     -> new site paths (PAGE_MAP)
- news links (/lamor?@=<id>)      -> /news/<slug>/ when the id is a migrated item, else dropped
- assets (/images, /_news, ...)   -> handed to an asset callback (download + new root-relative path)
- external links                  -> Outlook safelinks / awstrack redirects unwrapped, tracking params removed
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

OLD_BASE = "https://lamor.fer.hr/lamor"
OLD_HOSTS = {"lamor.fer.hr", "www.lamor.fer.hr"}

# old page path (no trailing slash, lowercase) -> new path
PAGE_MAP: dict[str, str] = {
    "/lamor": "/",
    "/lamor/research/running_projects": "/research/projects/",
    "/lamor/research/equipment": "/research/equipment/",
    "/lamor/research/partners": "/research/partners/",
    "/lamor/teaching/courses": "/teaching/",
    "/lamor/publications/thesis": "/publications/#theses",
    "/lamor/people/current": "/people/",
    "/lamor/people/alumni": "/people/alumni/",
    "/lamor/contact": "/contact/",
    "/lamor/students_corner": "/students-corner/",
}
PAGE_PREFIX_MAP: dict[str, str] = {
    "/lamor/publications": "/publications/",
}

ASSET_PREFIXES = ("/images/", "/_news/", "/_download/", "/upload/", "/_upload/")

TRACKING_PARAMS = {"dgcid", "lid", "fbclid", "gclid"}
NEWS_ID_RE = re.compile(r"(?:^|[?&])@=([0-9a-z]+)")
CMS_MAIL_RE = re.compile(r"javascript:\s*cms_mail\(\s*'([^']*)'\s*,\s*'([^']*)'")
AWSTRACK_RE = re.compile(r"awstrack\.me/L\d+/([^/]+)/")

# Characters left as-is when percent-encoding a root-relative path we write.
PATH_SAFE = "/-._~"


def encode_path(path: str) -> str:
    """'/_news/1/My file (1).pdf' -> '/_news/1/My%20file%20%281%29.pdf' (input may be encoded already)."""
    return quote(unquote(path), safe=PATH_SAFE)


def decode_safelink(url: str) -> str:
    """Unwrap Outlook 'safelinks' (possibly nested) to the original URL."""
    for _ in range(3):
        parts = urlsplit(url)
        if not parts.netloc.lower().endswith("safelinks.protection.outlook.com"):
            break
        target = dict(parse_qsl(parts.query)).get("url")
        if not target:
            break
        url = target
    return url


def unwrap_redirect(url: str) -> str:
    """Unwrap awstrack-style redirect URLs, only if the real URL is URL-encoded inside."""
    m = AWSTRACK_RE.search(url)
    if m:
        inner = unquote(m.group(1))
        if inner.startswith(("http://", "https://")):
            return inner
    return url


def strip_tracking(url: str) -> str:
    """Remove utm_* and a few well-known tracking params; keep everything else untouched."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)]
    if len(kept) == len(pairs):
        return url
    return urlunsplit(parts._replace(query=urlencode(kept, doseq=True, quote_via=quote)))


def clean_external(url: str) -> str:
    return strip_tracking(unwrap_redirect(decode_safelink(url)))


def map_page_path(path: str) -> str | None:
    """Map an old site page path (/lamor/...) to the new site path, or None if unknown."""
    p = unquote(path).rstrip("/").lower() or "/"
    if p in ("/", "/lamor", "/index.php"):
        return "/"
    if p in PAGE_MAP:
        return PAGE_MAP[p]
    for prefix, target in PAGE_PREFIX_MAP.items():
        if p.startswith(prefix + "/") or p == prefix:
            return target
    return None


@dataclass
class LinkRewriter:
    """Rewrites hrefs; collects statistics for the reports."""

    news_slugs: dict[str, str] = field(default_factory=dict)  # old @= id -> new news slug
    asset: Callable[[str], str | None] | None = None  # old local asset path -> new URL (or None if dead)
    base: str = OLD_BASE
    external_hosts: Counter = field(default_factory=Counter)
    unresolved: list[str] = field(default_factory=list)
    rewritten: Counter = field(default_factory=Counter)

    def is_local(self, url: str) -> bool:
        parts = urlsplit(urljoin(self.base, url))
        return parts.netloc.lower() in OLD_HOSTS

    def rewrite(self, href: str | None) -> str | None:
        """New href, or None when the link should be dropped (text kept)."""
        if not href:
            return None
        href = href.strip()
        if not href or href == "#":
            return None
        m = CMS_MAIL_RE.match(href)
        if m:
            self.rewritten["cms_mail"] += 1
            return f"mailto:{m.group(1)}@{m.group(2)}"
        low = href.lower()
        if low.startswith("javascript:"):
            self.unresolved.append(href)
            return None
        if low.startswith(("mailto:", "tel:")):
            return href
        if href.startswith("#"):
            return None  # in-page anchors of the old CMS are meaningless now
        url = clean_external(href)
        if url != href:
            self.rewritten["cleaned"] += 1
        absolute = urljoin(self.base, url)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            return url
        if parts.netloc.lower() not in OLD_HOSTS:
            self.external_hosts[parts.netloc.lower()] += 1
            return url
        return self._rewrite_local(parts, href)

    def _rewrite_local(self, parts, original: str) -> str | None:
        path = parts.path or "/"
        if path.startswith(ASSET_PREFIXES):
            new = self.asset(path) if self.asset else encode_path(path)
            if new is None:
                self.unresolved.append(original)
            else:
                self.rewritten["asset"] += 1
            return new
        m = NEWS_ID_RE.search(parts.query)
        if m:
            slug = self.news_slugs.get(m.group(1))
            if slug:
                self.rewritten["news"] += 1
                return f"/news/{slug}/"
            self.unresolved.append(original)
            return None
        new = map_page_path(path)
        if new == "/" and "_v1=" in parts.query:  # session-bound CMS action link on the home page
            new = None
        if new is None:
            self.unresolved.append(original)
            return None
        self.rewritten["page"] += 1
        return new + (f"#{parts.fragment}" if parts.fragment and not parts.fragment.startswith(("news_", "content_")) else "")
