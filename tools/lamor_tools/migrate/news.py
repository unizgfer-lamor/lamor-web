"""Old-site news items (cached HTML) -> src/content/news/<YYYY-MM-DD>-<slug>.md + tools/reports/news.md"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from ..common import CONTENT_DIR, PUBLIC_DIR, REPORTS_DIR, normalize_title, slugify, write_markdown
from .assets import AssetStore
from .fetch import NEWS_ITEMS, NEWS_LIST, item_path, news_ids
from .html2md import html_to_markdown
from .links import NEWS_ID_RE, LinkRewriter
from .util import BlockStr, clean_text, unique_slug

log = logging.getLogger("lamor_tools.migrate")

ZAGREB = ZoneInfo("Europe/Zagreb")
NEWS_DIR = CONTENT_DIR / "news"
TZ_ABBR = {"CEST": timedelta(hours=2), "CET": timedelta(hours=1)}
PUB_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(CEST|CET|T)?\s*(\d{1,2}:\d{2})(?::(\d{2}))?")
ROW_RE = re.compile(
    r"Published\s+(\d{4}-\d{2}-\d{2})\s+at\s+(\d{1,2}:\d{2})\s*\(\s*(.*?)\s*\)"
    r"(?:\s*Edited:\s*(\d{4}-\d{2}-\d{2})\s+at\s+(\d{1,2}:\d{2})\s*\(\s*(.*?)\s*\))?",
    re.S,
)


# -- dates ----------------------------------------------------------------------------------
def parse_pub_datetime(value: str) -> datetime:
    """'2026-10-21CEST09:49' -> 2026-10-21T09:49:00+02:00 (CEST/CET honoured, else Europe/Zagreb)."""
    m = PUB_RE.search(value or "")
    if not m:
        raise ValueError(f"unparsable date {value!r}")
    day, abbr, hm, sec = m.groups()
    naive = datetime.fromisoformat(f"{day}T{int(hm.split(':')[0]):02d}:{hm.split(':')[1]}:{sec or '00'}")
    if abbr in TZ_ABBR:
        return naive.replace(tzinfo=timezone(TZ_ABBR[abbr]))
    return local_datetime(naive)


def local_datetime(naive: datetime) -> datetime:
    """Attach the Zagreb offset valid at that moment, as a fixed offset (stable isoformat)."""
    aware = naive.replace(tzinfo=ZAGREB)
    return naive.replace(tzinfo=timezone(aware.utcoffset()))


# -- parsing --------------------------------------------------------------------------------
@dataclass
class ListRow:
    news_id: str
    title: str
    published: str | None = None
    author: str | None = None
    edited: str | None = None


@dataclass
class NewsItem:
    news_id: str
    title: str
    date: datetime
    updated: datetime | None = None
    author: str | None = None
    image_src: str | None = None
    image_alt: str | None = None
    lead_html: Tag | None = None
    body_html: Tag | None = None
    warnings: list[str] = field(default_factory=list)
    slug: str = ""
    lead_md: str = ""
    body_md: str = ""
    duplicate_of: str | None = None

    @property
    def content_size(self) -> int:
        size = len(self.lead_html.get_text(strip=True)) if self.lead_html else 0
        size += len(self.body_html.get_text(strip=True)) if self.body_html else 0
        return size + (50 if self.image_src else 0)


def parse_list(html: str) -> dict[str, ListRow]:
    soup = BeautifulSoup(html, "lxml")
    rows: dict[str, ListRow] = {}
    for a in soup.select("span.new_tab_link a.real_new_tab_link[href]"):
        m = NEWS_ID_RE.search(a["href"])
        if not m:
            continue
        title = re.sub(r"^Read news:\s*", "", a.get("title", "")).strip()
        row = ListRow(m.group(1), clean_text(title))
        tr = a.find_parent("tr")
        if tr is not None:
            rm = ROW_RE.search(tr.get_text(" ", strip=True).replace("\xa0", " "))
            if rm:
                row.published = f"{rm.group(1)}T{rm.group(2)}"
                row.author = clean_text(rm.group(3))
                if rm.group(4):
                    row.edited = f"{rm.group(4)}T{rm.group(5)}"
        rows.setdefault(row.news_id, row)
    return rows


def parse_item(html: str, news_id: str, row: ListRow | None = None) -> NewsItem:
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("div.news_article.news_article_full")
    if art is None:
        raise ValueError("no div.news_article_full")
    warnings: list[str] = []

    # The link title attribute holds the exact title; h1 text has stray spaces around inline tags.
    link = art.select_one("div.news_title h1 a[title^='Read news:']") or art.select_one("a[title^='Read news:']")
    title = clean_text(re.sub(r"^Read news:\s*", "", link["title"])) if link else ""
    h1 = art.select_one("div.news_title h1")
    if not title and h1 is not None:
        title = clean_text(h1.get_text(""))
    if not title and row:
        title = row.title
    if not title:
        raise ValueError("no title")

    time_el = art.select_one("div.news_pub_date time[datetime]")
    if time_el is not None:
        date = parse_pub_datetime(time_el["datetime"])
    elif row and row.published:
        date = local_datetime(datetime.fromisoformat(row.published))
        warnings.append("published date taken from the archive list")
    else:
        raise ValueError("no published date")
    if row and row.published and date.strftime("%Y-%m-%dT%H:%M") != row.published:
        warnings.append(f"published {date.isoformat()} differs from archive list {row.published}")

    updated = None
    edit_el = art.select_one("div.news_edit_date time[datetime]")
    edit_value = edit_el["datetime"] if edit_el is not None else (row.edited if row else None)
    if edit_value:
        try:
            edited = parse_pub_datetime(edit_value)
            if edited - date > timedelta(days=1):
                updated = edited
        except ValueError:
            warnings.append(f"unparsable edit date {edit_value!r}")

    author_el = art.select_one("span.author_name")
    author = clean_text(author_el.get_text(" ")) if author_el else (row.author if row else None)

    img = art.select_one("img.news_lead_image[src]")
    image_src = img["src"] if img else None
    image_alt = None
    if img is not None:
        alt = clean_text(img.get("alt", ""))
        if alt.endswith("...") and title.startswith(alt[:-3].rstrip()):
            image_alt = title  # the CMS truncates the title into the alt text
        elif alt:
            image_alt = alt

    lead = art.select_one("div.news_lead")
    body = art.select_one("div.news_content")
    if body is not None and not body.get_text(strip=True) and not body.find(["img", "iframe", "video", "table"]):
        body = None
    return NewsItem(news_id, title, date, updated, author or None, image_src, image_alt, lead, body, warnings)


# -- conversion -----------------------------------------------------------------------------
def _paragraphs(md: str) -> list[str]:
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", md) if p.strip()]


def strip_repeated_lead(lead_md: str, body_md: str) -> str:
    """Drop the lead from the start of the body when the body repeats it verbatim."""
    if not lead_md or not body_md:
        return body_md
    if body_md.startswith(lead_md):
        return body_md[len(lead_md):].strip()
    lead_p, body_p = _paragraphs(lead_md), _paragraphs(body_md)
    if lead_p and body_p[: len(lead_p)] == lead_p:
        raw_parts = [p for p in re.split(r"\n\s*\n", body_md) if p.strip()]
        return "\n\n".join(raw_parts[len(lead_p):]).strip()
    return body_md


def news_filename_slug(item: NewsItem) -> str:
    return f"{item.date:%Y-%m-%d}-{slugify(item.title)}"


@dataclass
class NewsResult:
    items: list[NewsItem]
    dropped: list[NewsItem]
    same_title_diff_date: list[list[NewsItem]]
    errors: list[str]
    rewriter: LinkRewriter
    store: AssetStore
    removed_files: list[str]
    list_count: int


def migrate_news(offline: bool = False, store: AssetStore | None = None) -> NewsResult:
    store = store or AssetStore(offline=offline)
    ids = news_ids()
    rows = parse_list(NEWS_LIST.read_text(encoding="utf-8")) if NEWS_LIST.exists() else {}
    items: list[NewsItem] = []
    errors: list[str] = []
    for news_id in ids:
        path = item_path(news_id)
        if not path.exists():
            errors.append(f"`{news_id}`: not in cache ({NEWS_ITEMS})")
            continue
        try:
            items.append(parse_item(path.read_text(encoding="utf-8"), news_id, rows.get(news_id)))
        except ValueError as exc:
            errors.append(f"`{news_id}`: {exc}")

    # Duplicates: same normalized title on the same day -> keep the richer one.
    groups: dict[tuple[str, str], list[NewsItem]] = defaultdict(list)
    for it in items:
        groups[(f"{it.date:%Y-%m-%d}", normalize_title(it.title))].append(it)
    kept: list[NewsItem] = []
    dropped: list[NewsItem] = []
    for group in groups.values():
        group.sort(key=lambda i: (-i.content_size, i.news_id))
        kept.append(group[0])
        for dup in group[1:]:
            dup.duplicate_of = group[0].news_id
            dropped.append(dup)
            log.info("duplicate news %s (%s) dropped in favour of %s", dup.news_id, dup.title, group[0].news_id)
    by_title: dict[str, list[NewsItem]] = defaultdict(list)
    for it in kept:
        by_title[normalize_title(it.title)].append(it)
    same_title = [sorted(g, key=lambda i: i.date) for g in by_title.values() if len(g) > 1]

    # Slugs (oldest first gets the plain slug) and the id -> slug map for internal links.
    kept.sort(key=lambda i: (i.date, i.news_id))
    used: set[str] = set()
    for it in kept:
        it.slug = unique_slug(news_filename_slug(it), used)
    slug_of = {it.news_id: it.slug for it in kept}
    for dup in dropped:
        slug_of[dup.news_id] = slug_of[dup.duplicate_of]

    rewriter = LinkRewriter(news_slugs=slug_of, asset=store.old_site_file)
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    written: set[Path] = set()
    for it in kept:
        counter = {"n": 0}

        def localize(src: str, img: Tag, it=it, counter=counter) -> str | None:
            counter["n"] += 1
            stem = PUBLIC_DIR / "images" / "news" / f"{it.slug}-{counter['n']}"
            new = store.image(src, stem)
            if new is None:
                if src.startswith(("http://", "https://")) and not rewriter.is_local(src):
                    it.warnings.append(f"image download failed, kept the hotlink: {src[:120]}")
                    return src
                it.warnings.append(f"image not migrated: {src[:80]}")
            return new

        it.lead_md = html_to_markdown(it.lead_html, rewriter.rewrite, localize)
        it.body_md = strip_repeated_lead(it.lead_md, html_to_markdown(it.body_html, rewriter.rewrite, localize))
        image = None
        if it.image_src:
            image = store.image(it.image_src, PUBLIC_DIR / "_news" / "icons" / it.slug)
            if image is None:
                it.warnings.append(f"lead image not migrated: {it.image_src}")
        fm = {
            "title": it.title,
            "date": it.date,
            "updated": it.updated,
            "author": it.author,
            "image": image,
            "imageAlt": it.image_alt if image else None,
            "lead": BlockStr(it.lead_md) if it.lead_md else None,
        }
        path = NEWS_DIR / f"{it.slug}.md"
        write_markdown(path, fm, it.body_md)
        written.add(path)

    removed = []
    for old in NEWS_DIR.glob("*.md"):
        if old not in written:
            removed.append(old.name)
            old.unlink()
    result = NewsResult(kept, dropped, same_title, errors, rewriter, store, removed, len(ids))
    write_news_report(result)
    log.info("news: %d written, %d duplicates dropped, %d errors", len(kept), len(dropped), len(errors))
    return result


# -- report ---------------------------------------------------------------------------------
def write_news_report(r: NewsResult) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    kept = sorted(r.items, key=lambda i: i.date, reverse=True)
    no_image = [i for i in kept if not i.image_src]
    with_body = [i for i in kept if i.body_md]
    warn_items = [i for i in kept if i.warnings]
    lines = [
        "# News migration report",
        "",
        "Generated by `uv run lamor-migrate news` from the cached old site (tools/.cache/old-site/news).",
        "",
        "## Counts",
        "",
        f"- Items in the old-site archive list: {r.list_count} (expected 237)",
        f"- Parsed: {len(r.items) + len(r.dropped)}; parse errors: {len(r.errors)}",
        f"- Written to src/content/news: **{len(kept)}**",
        f"- Duplicates dropped (same title, same day): {len(r.dropped)}",
        f"- Date range: {min(i.date for i in kept):%Y-%m-%d} - {max(i.date for i in kept):%Y-%m-%d}" if kept else "- Date range: n/a",
        f"- Items with a full body (div.news_content): {len(with_body)}",
        f"- Items without a lead image: {len(no_image)}",
        f"- Items with `updated` (edited > 1 day after publishing): {sum(1 for i in kept if i.updated)}",
        "",
        "News URLs: a news file `src/content/news/<id>.md` is linked internally as `/news/<id>/`.",
        "",
    ]
    if r.removed_files:
        lines += ["Stale files removed from src/content/news: " + ", ".join(r.removed_files), ""]
    lines += ["## Duplicates dropped (same normalized title and same date)", ""]
    lines += [f"- `{d.news_id}` {d.date:%Y-%m-%d} \"{d.title}\" -> kept `{d.duplicate_of}`" for d in r.dropped] or ["- none"]
    lines += ["", "## Same title, different dates (both kept - check manually)", ""]
    for g in r.same_title_diff_date:
        lines.append(f"- \"{g[0].title}\": " + ", ".join(f"`{i.slug}`" for i in g))
    if not r.same_title_diff_date:
        lines.append("- none")
    lines += ["", "## Items without a lead image", ""]
    lines += [f"- `{i.slug}`" for i in no_image] or ["- none"]
    lines += ["", "## Items with a full body", ""]
    lines += [f"- `{i.slug}`" for i in with_body] or ["- none"]
    lines += ["", "## Parse errors", ""]
    lines += [f"- {e}" for e in r.errors] or ["- none"]
    lines += ["", "## Warnings", ""]
    for i in warn_items:
        for w in i.warnings:
            lines.append(f"- `{i.slug}`: {w}")
    if not warn_items:
        lines.append("- none")
    lines += ["", "## Links", ""]
    rw = r.rewriter
    lines.append(
        f"- Rewritten: {rw.rewritten.get('page', 0)} old page links, {rw.rewritten.get('news', 0)} links to other news, "
        f"{rw.rewritten.get('asset', 0)} asset links, {rw.rewritten.get('cms_mail', 0)} `cms_mail()` e-mail links, "
        f"{rw.rewritten.get('cleaned', 0)} external URLs cleaned (safelinks/redirects/tracking)"
    )
    lines += ["", "Links dropped (link text kept):", ""]
    lines += [f"- `{u}`" for u in sorted(set(rw.unresolved))] or ["- none"]
    lines += ["", "External hosts linked (count):", ""]
    lines += [f"- {h}: {n}" for h, n in sorted(rw.external_hosts.items(), key=lambda kv: (-kv[1], kv[0]))]
    lines += ["", "## Assets", ""]
    lines += r.store.summary_lines()
    if r.store.failed:
        lines += ["", "Failed downloads:", ""]
        lines += [f"- {u} ({why})" for u, why in r.store.failed]
    if r.store.dead:
        lines += ["", "Dead old-site links (not downloadable):", ""]
        lines += [f"- {u}" for u in sorted(set(r.store.dead))]
    (REPORTS_DIR / "news.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

