"""Download (and cache) the old LAMOR site pages so that parsers can run offline.

Cache layout under tools/.cache/old-site/:
    pages/<name>.html        one file per static page (see PAGES)
    news/list.html           archive listing with all news rows (page size 1000)
    news/items/<id>.html     one file per news item (`@=` id)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..common import CACHE_DIR, HttpClient

log = logging.getLogger("lamor_tools.migrate")

BASE = "https://lamor.fer.hr"
SITE_ROOT = f"{BASE}/lamor"
ARCHIVE_URL = f"{SITE_ROOT}?@=2zr4v"
ARCHIVE_FORM = "form#_v1page_page_size_88430"

OLD_SITE_CACHE = CACHE_DIR / "old-site"
PAGES_CACHE = OLD_SITE_CACHE / "pages"
NEWS_CACHE = OLD_SITE_CACHE / "news"
NEWS_LIST = NEWS_CACHE / "list.html"
NEWS_ITEMS = NEWS_CACHE / "items"

# cache name -> path below https://lamor.fer.hr/lamor
PAGES: dict[str, str] = {
    "home": "",
    "running_projects": "/research/running_projects",
    "equipment": "/research/equipment",
    "partners": "/research/partners",
    "courses": "/teaching/courses",
    "people_current": "/people/current",
    "people_alumni": "/people/alumni",
    "thesis": "/publications/thesis",
    "students_corner": "/students_corner",
    "contact": "/contact",
    "pubs_2025_present": "/publications/2025_present",
    "pubs_2020_present": "/publications/2020_-_present",
    "pubs_2015_present": "/publications/2015_-_present",
    "pubs_2010_2014": "/publications/2010_-_2014",
}

PUBLICATION_PAGES = ["pubs_2025_present", "pubs_2020_present", "pubs_2015_present", "pubs_2010_2014"]

# Archive-only ids, used only if the POST listing fails (homepage lists the other 226).
ARCHIVE_ONLY_IDS = "2xj2h 2xz9c 2yg0f 2yrln 2yrlo 2zlur 2zlus 30agt 30f1v 32qm6 33bji".split()

NEWS_ID_RE = re.compile(r"[?&]@=([0-9a-z]+)")


def page_url(name: str) -> str:
    return SITE_ROOT + PAGES[name]


def page_path(name: str) -> Path:
    return PAGES_CACHE / f"{name}.html"


def item_path(news_id: str) -> Path:
    return NEWS_ITEMS / f"{news_id}.html"


def read_page(name: str) -> str:
    path = page_path(name)
    if not path.exists():
        raise SystemExit(f"{path} is missing - run `lamor-migrate fetch` first")
    return path.read_text(encoding="utf-8")


class OldSite:
    """One requests session (keeps the CMS cookie) throttled to 1 request/second."""

    def __init__(self, client: HttpClient | None = None) -> None:
        self.http = client or HttpClient(per_minute=60)

    def get_text(self, url: str, **kwargs) -> str:
        resp = self.http.get(url, **kwargs)
        resp.raise_for_status()
        resp.encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else "utf-8"
        return resp.text

    # -- pages ---------------------------------------------------------------------------
    def fetch_pages(self, refresh: bool = False) -> None:
        PAGES_CACHE.mkdir(parents=True, exist_ok=True)
        for name in PAGES:
            path = page_path(name)
            if path.exists() and not refresh:
                continue
            log.info("fetch page %s", page_url(name))
            path.write_text(self.get_text(page_url(name)), encoding="utf-8")

    # -- news list -----------------------------------------------------------------------
    def fetch_news_list(self, refresh: bool = False) -> None:
        NEWS_CACHE.mkdir(parents=True, exist_ok=True)
        if NEWS_LIST.exists() and not refresh:
            return
        log.info("fetch news archive %s", ARCHIVE_URL)
        html = self.get_text(ARCHIVE_URL)
        form = BeautifulSoup(html, "lxml").select_one(ARCHIVE_FORM)
        if form is None or not form.get("action"):
            raise RuntimeError("archive page-size form not found")
        action = urljoin(ARCHIVE_URL, form["action"])
        resp = self.http.post(action, files={"set_limit": (None, "1000")}, headers={"Referer": ARCHIVE_URL})
        resp.raise_for_status()
        resp.encoding = "utf-8"
        NEWS_LIST.write_text(resp.text, encoding="utf-8")

    def fetch_news_items(self, ids: list[str], refresh: bool = False) -> None:
        NEWS_ITEMS.mkdir(parents=True, exist_ok=True)
        todo = [i for i in ids if refresh or not item_path(i).exists()]
        for n, news_id in enumerate(todo, 1):
            log.info("fetch news %d/%d %s", n, len(todo), news_id)
            item_path(news_id).write_text(self.get_text(f"{SITE_ROOT}?@={news_id}"), encoding="utf-8")


def list_ids_from_archive(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    ids: list[str] = []
    for a in soup.select("span.new_tab_link a.real_new_tab_link[href]"):
        m = NEWS_ID_RE.search(a["href"])
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def list_ids_from_homepage(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    ids: list[str] = []
    for art in soup.select("div.news_article"):
        for a in art.select("a[href]"):
            m = NEWS_ID_RE.search(a["href"])
            if m:
                if m.group(1) not in ids:
                    ids.append(m.group(1))
                break
    return ids


def fetch_all(refresh: bool = False) -> None:
    site = OldSite()
    site.fetch_pages(refresh=refresh)
    ids: list[str] = []
    try:
        site.fetch_news_list(refresh=refresh)
        ids = list_ids_from_archive(NEWS_LIST.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - fall back to the homepage listing
        log.warning("news archive POST failed (%s); falling back to homepage + known ids", exc)
    if len(ids) < 200:
        home_ids = list_ids_from_homepage(read_page("home"))
        ids = list(dict.fromkeys(ids + home_ids + ARCHIVE_ONLY_IDS))
    log.info("%d news ids", len(ids))
    site.fetch_news_items(ids, refresh=refresh)


def news_ids() -> list[str]:
    """Ids of all news items in the cache, in archive order (newest first)."""
    if NEWS_LIST.exists():
        ids = list_ids_from_archive(NEWS_LIST.read_text(encoding="utf-8"))
        if len(ids) >= 200:
            return ids
    else:
        ids = []
    home_ids = list_ids_from_homepage(read_page("home"))
    return list(dict.fromkeys(ids + home_ids + ARCHIVE_ONLY_IDS))
