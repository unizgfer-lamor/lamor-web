"""Old-site static pages (cached HTML) -> Markdown content collections + tools/reports/pages.md

    research/running_projects -> src/content/projects     research/equipment -> src/content/equipment
    research/partners         -> src/content/partners
    people/current + alumni   -> src/content/people       publications/thesis -> src/content/theses
    students_corner, home     -> src/content/pages        contact            -> report only

Courses are not migrated: src/content/courses comes from the FER web course pages instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..common import CACHE_DIR, CONTENT_DIR, PUBLIC_DIR, REPORTS_DIR, HttpClient, ascii_fold, slugify, write_markdown
from .assets import AssetStore, decode_data_uri
from .fetch import page_url, read_page
from .html2md import html_to_markdown, inline_text
from .links import OLD_BASE, OLD_HOSTS, LinkRewriter
from .util import clean_text, unique_slug

log = logging.getLogger("lamor_tools.migrate")

IMAGES = PUBLIC_DIR / "images"
SECTIONS = ("projects", "equipment", "partners", "people", "theses", "pages")
PROFILE_CACHE = CACHE_DIR / "old-site" / "profile-checks.json"

SUP_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
COUNTRIES = {
    "Austria", "Belgium", "Bosnia and Herzegovina", "Canada", "Croatia", "Czech Republic", "Czechia", "Denmark",
    "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland", "Israel", "Italy", "Japan", "Korea",
    "Latvia", "Lithuania", "Montenegro", "Netherlands", "North Macedonia", "Macedonia", "Norway", "Poland",
    "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "Turkey", "UK",
    "United Kingdom", "USA", "United States",
}
# Institution names that contain commas (protected before splitting partner lists on commas).
COMMA_NAMES = [
    "Faculty of Electrical Engineering, Mechanical Engineering and Naval Architecture",
    "Faculty of Electrical Engineering, Computer Science and Information Technology",
    "Czech Institute of Informatics, Robotics and Cybernetic",
    "Automatismos Y Sistemas De Transporte Interno, S.A.U.",
]
PERSON_RE = re.compile(r"^(?:(?:M\.Sc\.|Dr\.|Prof\.)\s+)?[A-ZČĆŽŠĐ][a-zäöüčćžšđéè]+ [A-ZČĆŽŠĐ][a-zäöüčćžšđéè]+$")
INSTITUTION_RE = re.compile(r"Universit|Institut|Faculty|College|School|Centre|Center|GmbH|d\.o\.o\.|Ltd|Fraunhofer", re.I)

# Which image of a project's logo row is the project logo (default: the first one).
LOGO_PICK = {"RAiSE": 1}  # RAiSE shows the CEEPUS programme logo first

# Unlinked sidebar logos identified by content (sha1 prefix of the decoded image) or file name.
SIDEBAR_SHA1_HINTS = {"750bdb8f": "AIFORS", "02087f63": "DRUMS", "360b251b": "RAiSE"}
SIDEBAR_NAME_HINTS = {"exceed-air": "EXCEED-AIR", "stamp": "STAMP", "sacre": "SACRE"}

# Projects shown in the "Current Projects" sidebar that have no entry on the projects page.
# Facts taken from the old-site news items cited in `source` (nothing invented).
SIDEBAR_ONLY_PROJECTS: dict[str, dict] = {
    "EXCEED-AIR": {
        "title": "EXCellence Initiative for European Empowerment and Development in AI-Driven Robotics",
        "funding": "Horizon Europe, HORIZON-WIDERA-2025-01-ACCESS-01-EEI, Coordination and Support Action (Total budget: 5 million EUR)",
        "duration": "5 years",
        "partners": [
            "Institut Jozef Stefan, Slovenia", "UNIZG-FSB, Croatia", "Kungliga Tekniska Högskolan, Sweden",
            "Technische Universität München, Germany", "Karlsruher Institut Für Technologie, Germany",
            "Università degli Studi Di Padova, Italy", "HAZU, Croatia", "Centar izvrsnosti MARBLE, Croatia",
            "European Fund Management Consulting, Estonia",
        ],
        "body": "A 5-year Horizon Europe European Excellence Initiative (EEI) project coordinated by UNIZG-FER. "
                "This Coordination and Support Action (CSA) project, financed under the "
                "HORIZON-WIDERA-2025-01-ACCESS-01-EEI program, will be implemented over 5 years with a budget "
                "of 5 million EUR.",
        "source": "news 2026-04-09 \"New Horizon Europe project! LAMOR to coordinate EXCEED-AIR\"",
    },
    "STAMP": {
        "title": "Stochastic integrated task and motion planning for human-robot collaboration",
        "funding": "Croatian Science Foundation, Installation Research Projects (UIP)",
        "duration": "3 years",
        "pi": "Luka Petrović",
        "website": "https://stamp.fer.hr/",
        "body": "Project STAMP aims to enable robot arms to safely and efficiently collaborate with humans in "
                "dynamic and uncertain environments by unifying high-level task planning and low-level motion "
                "planning within a single stochastic framework.",
        "source": "news 2026-10-21 \"LAMOR receives Croatian Science Foundation funding for the STAMP project\"",
    },
    "SACRE": {
        "title": "SACRE",
        "source": "sidebar logo only - no description anywhere on the old site",
    },
}
SIDEBAR_ONLY_ORDER = {"EXCEED-AIR": 2, "STAMP": 4, "SACRE": 6}  # before AIFORS (10), as in the sidebar

# Problems in the old-site content spotted while reviewing the migration (copied to the report).
CONTENT_OBSERVATIONS = [
    "projects/robocamp.md: the short description talks about tourism / smart mobile homes and the title equals "
    "DRUMS' title (\"Deep Tech & Robotics for Human-Centered Manufacturing Systems\") - probably copy/paste errors on the old page.",
    "projects/horace.md, drums.md: the funding cell links \"Weave\" / \"Erasmus\" to the HORIZON-EIC Pathfinder CORDIS page (wrong link, dropped - funding is plain text).",
    "equipment/hdl-32e.md: the old link pointed to the Nerian SP1 page while showing the Velodyne URL - the shown URL is used.",
    "equipment/dynamic-vision-sensor-dvs.md: the old link points to a Point Grey Grasshopper2 page, not to the DVS.",
    "equipment/guardian-witha-powerball-robot-arm.md: typo \"witha\" kept from the old page (also in the slug).",
    "people/edouard-ivanjko.md: affiliation typo \"University of Zageb\" kept; guests: \"University of Würtzburg\" (sic).",
    "people photos are the original PNGs from the old page (up to ~4 MB each for ~115x178 px display) - consider resizing.",
]


# -- generic helpers ------------------------------------------------------------------------
@dataclass
class Report:
    counts: list[tuple[str, int, str]] = field(default_factory=list)
    issues: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    notes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def issue(self, section: str, msg: str) -> None:
        self.issues[section].append(msg)
        log.warning("%s: %s", section, msg)


def content_blocks(html: str, area: str = "cms_area_middle") -> list[tuple[str, Tag]]:
    soup = BeautifulSoup(html, "lxml")
    blocks = []
    for b in soup.select(f"div#{area} div.cms_module.portlet_content"):
        t = b.select_one("div.cms_module_title")
        body = b.select_one("div.cms_module_html div.text") or b.select_one("div.cms_module_html")
        if body is not None:
            blocks.append((clean_text(t.get_text(" ")) if t else "", body))
    return blocks


def sup_to_unicode(el: Tag) -> None:
    for sup in el.find_all("sup"):
        text = sup.get_text()
        if text.isdigit():
            sup.replace_with(text.translate(SUP_DIGITS))


def image_sha1(src: str) -> str | None:
    if not src.startswith("data:"):
        return None
    try:
        return hashlib.sha1(decode_data_uri(src)[0]).hexdigest()
    except ValueError:
        return None


def ensure_scheme(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) and not url.startswith(("mailto:", "/")):
        url = "http://" + url
    return url


def cell_url(cell: Tag | None) -> str | None:
    """URL in a table cell: prefer visible full-URL text (hrefs are sometimes wrong), then href, then text."""
    if cell is None:
        return None
    text = clean_text(cell.get_text(" ")).split(" ")[0] if cell.get_text(strip=True) else ""
    a = cell.find("a", href=True)
    if text.lower().startswith(("http://", "https://")):
        return text
    if a is not None:
        return a["href"].strip()
    return ensure_scheme(text) if text and "." in text else None


def split_list(text: str) -> list[str]:
    """'A, B,  C,' -> ['A', 'B', 'C']"""
    return [clean_text(p).strip(" ,;.") for p in re.split(r"[,;\n]", text) if clean_text(p).strip(" ,;.")]


def _strip_period(text: str) -> str:
    """Drop a sentence-final period, but keep abbreviations such as 'd.o.o.' or 'S.A.U.'."""
    m = re.search(r"(\S+)\.$", text)
    if m and "." not in m.group(1) and len(m.group(1)) >= 4:
        return text[:-1].rstrip()
    return text


def split_partners(text: str) -> list[str]:
    """Split a partner list on ';' (if present) or on commas outside parentheses, keeping
    'Institution, Country' and 'Person, Institution' together."""
    text = _strip_period(clean_text(text).rstrip(" ;,"))
    if not text:
        return []
    protected = {}
    for i, name in enumerate(COMMA_NAMES):
        if name in text:
            key = f"\x00{i}\x00"
            protected[key] = name
            text = text.replace(name, key)
    sep = ";" if ";" in text else ","
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == sep and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    parts = [_strip_period(p.strip()) for p in parts if p.strip(" .")]
    merged: list[str] = []
    for p in parts:
        is_country = p.split(" (")[0].strip() in COUNTRIES
        is_person_then_institution = bool(merged) and PERSON_RE.match(merged[-1]) and INSTITUTION_RE.search(p)
        if merged and sep == "," and (is_country or is_person_then_institution):
            merged[-1] += ", " + p
        else:
            merged.append(p)
    for key, name in protected.items():
        merged = [m.replace(key, name) for m in merged]
    return merged


class Images:
    """Saves page images (data URIs, old-site files, hotlinks) under public/images/<section>/."""

    def __init__(self, store: AssetStore, report: Report) -> None:
        self.store = store
        self.report = report

    def save(self, src: str, stem: Path, section: str, what: str, keep_hotlink: bool = True) -> str | None:
        """Local URL of the saved image; for a failed external (non-old-site) download the original
        URL is kept when `keep_hotlink` (and reported)."""
        src = (src or "").strip()
        if not src:
            return None
        if src.startswith("data:"):
            return self.store.data_uri(src, stem)
        url = urljoin(OLD_BASE, src)
        new = self.store.remote_image(url, stem)
        if new is None:
            external = urlsplit(url).netloc.lower() not in OLD_HOSTS
            if external and keep_hotlink:
                self.report.issue(section, f"{what}: image download failed, kept the hotlink {url}")
                return url
            self.report.issue(section, f"{what}: image download failed: {url}")
        return new

    def first_ok(self, srcs: list[str], stem: Path, section: str, what: str) -> str | None:
        """Try each candidate; if none can be saved locally, keep the first external hotlink."""
        for src in srcs:
            new = self.save(src, stem, section, what, keep_hotlink=False)
            if new:
                return new
        for src in srcs:
            url = urljoin(OLD_BASE, src)
            if not src.startswith("data:") and urlsplit(url).netloc.lower() not in OLD_HOSTS:
                self.report.issue(section, f"{what}: no image could be downloaded, kept the hotlink {url}")
                return url
        return None


def localizer(images: Images, section: str, prefix: str, report: Report):
    """<img> callback for html_to_markdown: saves to public/images/<section>/<prefix>-<n>.<ext>."""
    counter: Counter = Counter()

    def localize(src: str, img: Tag) -> str | None:
        heading = img.find_previous(["h1", "h2", "h3", "h4"])
        base = slugify(heading.get_text(" ")) if heading is not None and section == "students" else prefix
        counter[base] += 1
        stem = IMAGES / section / f"{base}-{counter[base]}"
        absolute = urljoin(OLD_BASE, src)
        if src.startswith("data:") or urlsplit(absolute).netloc.lower() not in OLD_HOSTS:
            return images.save(src, stem, section, f"inline image in {prefix}")
        new = images.store.old_site_file(urlsplit(absolute).path)
        if new is None:
            report.issue(section, f"inline image in {prefix} not migrated: {src[:100]}")
        return new

    return localize


# -- projects -------------------------------------------------------------------------------
@dataclass
class Project:
    heading: str
    acronym: str | None
    title: str
    status: str
    order: int
    images: list[str] = field(default_factory=list)
    rows: dict[str, Tag] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    slug: str = ""
    logo: str | None = None
    sidebar: bool = False
    fixed: dict = field(default_factory=dict)  # sidebar-only projects: ready-made field values


def split_heading(heading: str) -> tuple[str | None, str]:
    m = re.match(r"^(.+?)\s*[-–]\s+(.+)$", heading)
    if m and len(m.group(1)) <= 25 and len(m.group(1).split()) <= 3:
        return m.group(1).strip(), m.group(2).strip()
    return None, heading


def classify_label(label: str) -> str | None:
    label = label.lower().strip(" :")
    if re.match(r"short\s+desc", label):
        return "description"
    if label.startswith("funding"):
        return "funding"
    if label.startswith("duration"):
        return "duration"
    if label in ("website", "web site", "web", "url"):
        return "website"
    if label.startswith("principal investigator") or label in ("pi", "coordinator"):
        return "pi"
    if label in ("researchers", "project team", "team", "research team", "researcher"):
        return "team"
    if label.startswith("partner"):
        return "partners"
    return None


DATE_RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\.?")


def parse_duration(text: str) -> tuple[date | None, date | None, str | None]:
    text = clean_text(text)
    m = DATE_RANGE_RE.search(text)
    if m:
        d1, m1, y1, d2, m2, y2 = map(int, m.groups())
        try:
            return date(y1, m1, d1), date(y2, m2, d2), None
        except ValueError:
            pass
    return None, None, (text.rstrip(".") or None)


def parse_projects(html: str, report: Report) -> list[Project]:
    projects: list[Project] = []
    for block_title, body in content_blocks(html):
        low = block_title.lower()
        status = "ongoing" if low.startswith("ongoing") else "past" if low.startswith("past") else None
        if status is None:
            continue
        current: Project | None = None
        for el in body.children:
            if not isinstance(el, Tag):
                continue
            if el.name in ("h2", "h3", "h4"):
                sup_to_unicode(el)
                heading = inline_text(el)
                if not heading:
                    continue
                acronym, title = split_heading(heading)
                current = Project(heading, acronym, title, status, order=(len(projects) + 1) * 10)
                projects.append(current)
            elif current is None:
                if el.get_text(strip=True):
                    report.issue("projects", f"text before the first project in '{block_title}': {inline_text(el)[:80]}")
            elif el.name == "table":
                _parse_project_table(el, current)
            else:
                current.images += [i["src"] for i in el.find_all("img") if i.get("src")]
    return projects


def _parse_project_table(table: Tag, p: Project) -> None:
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        imgs = [i["src"] for i in tr.find_all("img") if i.get("src")]
        if len(cells) == 1 or cells[0].get("colspan", "1") != "1":
            p.images += imgs
            if not imgs and tr.get_text(strip=True):
                p.unknown.append(f"single-cell row: {inline_text(tr)[:60]}")
            continue
        label = clean_text(cells[0].get_text(" "))
        value = cells[1]
        key = classify_label(label)
        if key is None and not label.strip() and "description" not in p.rows and value.get_text(strip=True):
            key = "description"
        if key is None:
            if label.strip() or value.get_text(strip=True):
                p.unknown.append(f"{label or '(empty label)'}: {inline_text(value)[:60]}")
            continue
        if key in p.rows:
            p.unknown.append(f"duplicate row '{label}'")
            continue
        p.rows[key] = value


@dataclass
class SidebarLogo:
    src: str
    href: str | None
    sha1: str | None
    name: str


def parse_sidebar(html: str) -> list[SidebarLogo]:
    logos = []
    for title, body in content_blocks(html, "cms_area_right"):
        if title.lower() != "current projects":
            continue
        for img in body.find_all("img"):
            src = img.get("src") or ""
            a = img.find_parent("a")
            name = "" if src.startswith("data:") else src.rsplit("/", 1)[-1].lower()
            logos.append(SidebarLogo(src, a["href"] if a and a.get("href") else None, image_sha1(src), name))
    return logos


def _norm_url(url: str) -> tuple[str, str]:
    parts = urlsplit(ensure_scheme(url))
    return parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/").lower()


def match_sidebar(logos: list[SidebarLogo], projects: list[Project], report: Report) -> dict[str, list[SidebarLogo]]:
    """Sidebar logos grouped by target: an ongoing project's slug, or 'acr:<ACRONYM>' for a hinted
    project that has no entry on the projects page. Matching order: identical image, link, file
    name hint, known-logo hint (sha1 prefix)."""
    ongoing = [p for p in projects if p.status == "ongoing"]
    by_sha: dict[str, Project] = {}
    by_src: dict[str, Project] = {}
    for p in ongoing:
        for src in p.images:
            sha = image_sha1(src)
            if sha:
                by_sha.setdefault(sha, p)
            else:
                by_src.setdefault(urljoin(OLD_BASE, src), p)
    websites = [(_norm_url(url), p) for p in ongoing if "website" in p.rows and (url := cell_url(p.rows["website"]))]

    def by_acronym(acr: str) -> str:
        hits = [p for p in ongoing if p.acronym == acr]
        return hits[0].slug if len(hits) == 1 else f"acr:{acr}"

    matched: dict[str, list[SidebarLogo]] = defaultdict(list)
    for logo in logos:
        target: str | None = None
        how = ""
        if logo.sha1 and logo.sha1 in by_sha:
            target, how = by_sha[logo.sha1].slug, "identical image"
        elif not logo.sha1 and urljoin(OLD_BASE, logo.src) in by_src:
            target, how = by_src[urljoin(OLD_BASE, logo.src)].slug, "same image file"
        if target is None and logo.href:
            host, path = _norm_url(logo.href)
            same_host = [(wp, p) for (wh, wp), p in websites if wh == host]
            exact = [p for wp, p in same_host if wp == path]
            if exact:
                target, how = exact[0].slug, "link"
            elif len(same_host) == 1:
                target, how = same_host[0][1].slug, "link host"
        if target is None:
            key = (logo.name + " " + (logo.href or "")).lower()
            for hint, acr in SIDEBAR_NAME_HINTS.items():
                if hint in key:
                    target, how = by_acronym(acr), "file name / link hint"
                    break
        if target is None and logo.sha1:
            for prefix, acr in SIDEBAR_SHA1_HINTS.items():
                if logo.sha1.startswith(prefix):
                    target, how = by_acronym(acr), "known logo"
                    break
        if target is None:
            report.issue("projects", f"sidebar logo not matched to a project: {logo.src[:60]} -> {logo.href}")
            continue
        matched[target].append(logo)
        label = logo.name or f"data:image ({(logo.sha1 or '')[:8]})"
        report.notes["sidebar"].append(f"{label}{' -> ' + logo.href if logo.href else ''}: `{target}` ({how})")
    return matched


def logo_from_website(store: AssetStore, website: str, acronym: str, stem: Path) -> str | None:
    """Find the project logo on its website (an <img> whose alt is the acronym, or a 'logo' file)."""
    if store.offline:
        return None
    try:
        resp = store.client().get(website, timeout=30)
    except Exception as exc:  # noqa: BLE001
        store.failed.append((website, type(exc).__name__))
        return None
    if resp.status_code != 200:
        store.failed.append((website, f"HTTP {resp.status_code}"))
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    candidates = [i for i in soup.find_all("img", src=True) if clean_text(i.get("alt", "")).lower() == acronym.lower()]
    candidates += [i for i in soup.find_all("img", src=True)
                   if acronym.lower() in i["src"].lower() and "logo" in i["src"].lower() and i not in candidates]
    for img in candidates:
        new = store.remote_image(urljoin(resp.url, img["src"]), stem)
        if new:
            return new
    return None


def build_projects(html: str, home_html: str, store: AssetStore, images: Images, rewriter: LinkRewriter,
                   report: Report) -> list[Project]:
    projects = parse_projects(html, report)
    # acronym fallback + slugs
    for p in projects:
        if not p.acronym:
            p.acronym = p.title
            report.notes["projects"].append(f"No acronym on the old site, `acronym` set to the full title: \"{p.title}\"")
    acr_count = Counter(p.acronym for p in projects)
    used: set[str] = set()
    for p in projects:
        base = slugify(p.acronym) if acr_count[p.acronym] == 1 else slugify(f"{p.acronym} {p.title}", max_len=40)
        p.slug = unique_slug(base, used)

    # sidebar
    logos = parse_sidebar(home_html)
    matched = match_sidebar(logos, projects, report)
    by_slug = {p.slug: p for p in projects}
    for target in list(matched):
        if not target.startswith("acr:"):
            continue
        acr = target[4:]
        info = SIDEBAR_ONLY_PROJECTS.get(acr)
        if info is None:
            report.issue("projects", f"sidebar logo for '{acr}' but no such ongoing project and no fallback data")
            continue
        p = Project(acr, acr, info["title"], "ongoing", order=SIDEBAR_ONLY_ORDER.get(acr, 5), fixed=info)
        p.slug = unique_slug(slugify(acr), used)
        projects.append(p)
        by_slug[p.slug] = p
        matched[p.slug] = matched.pop(target)
        report.notes["projects"].append(
            f"Created `{p.slug}` (sidebar project without an entry on the projects page) from: {info['source']}")
    for slug in matched:
        if slug in by_slug:
            by_slug[slug].sidebar = True

    for p in projects:
        stem = IMAGES / "projects" / p.slug
        own = list(p.images)
        if own:
            pick = LOGO_PICK.get(p.acronym, 0)
            ordered = own[pick:pick + 1] + [s for i, s in enumerate(own) if i != pick]
            p.logo = images.first_ok(ordered[:1], stem, "projects", f"{p.acronym} logo")
            if len(own) > 1:
                report.notes["projects"].append(f"`{p.slug}`: {len(own)} images in the logo row, used #{pick + 1}")
        if p.logo is None and p.slug in matched:
            for logo in matched[p.slug]:
                p.logo = images.save(logo.src, stem, "projects", f"{p.acronym} sidebar logo")
                if p.logo:
                    report.notes["projects"].append(f"`{p.slug}`: logo taken from the sidebar")
                    break
        if p.logo is None:
            website = p.fixed.get("website") or (cell_url(p.rows["website"]) if "website" in p.rows else None)
            if p.sidebar and website:
                p.logo = logo_from_website(store, website, p.acronym, stem)
                if p.logo:
                    report.notes["projects"].append(f"`{p.slug}`: logo downloaded from the project website {website}")
        if p.logo is None:
            why = "logo could not be downloaded" if (own or p.slug in matched) else "no logo on the old projects page"
            report.issue("projects", f"`{p.slug}`: {why}" + (" (sidebar project - manual fix needed)" if p.sidebar else ""))

    titles = Counter(p.title for p in projects)
    for title, n in titles.items():
        if n > 1:
            slugs = ", ".join(f"`{p.slug}`" for p in projects if p.title == title)
            report.issue("projects", f"same title used by {n} projects on the old page ({slugs}): \"{title}\"")
    for p in projects:
        write_project(p, images, rewriter, report)
    return projects


def write_project(p: Project, images: Images, rewriter: LinkRewriter, report: Report) -> None:
    fm: dict = {"acronym": p.acronym, "title": p.title, "status": p.status, "logo": p.logo}
    if p.fixed:
        start = end = None
        fm.update({k: p.fixed.get(k) for k in ("website", "funding")})
        duration = p.fixed.get("duration")
        pi = p.fixed.get("pi")
        team: list[str] = []
        partners = p.fixed.get("partners", [])
        body = p.fixed.get("body", "")
    else:
        r = p.rows
        fm["website"] = cell_url(r.get("website"))
        fm["funding"] = inline_text(r.get("funding")) or None
        start, end, duration = parse_duration(inline_text(r.get("duration"))) if "duration" in r else (None, None, None)
        pi = inline_text(r.get("pi")).strip(" ,") or None
        team = split_list(inline_text_lines(r.get("team"))) if "team" in r else []
        partners = split_partners(inline_text(r.get("partners"))) if "partners" in r else []
        body = html_to_markdown(r.get("description"), rewriter.rewrite, localizer(images, "projects", p.slug, report)) \
            if "description" in r else ""
        if not body:
            report.issue("projects", f"`{p.slug}`: no short description")
        for u in p.unknown:
            report.issue("projects", f"`{p.slug}`: unparsed row - {u}")
        if duration and "duration" in r:
            report.notes["projects"].append(f"`{p.slug}`: duration kept as text: \"{duration}\"")
    fm.update({"start": start, "end": end, "duration": duration, "pi": pi, "team": team, "partners": partners,
               "sidebar": True if p.sidebar else None, "order": p.order})
    write_markdown(CONTENT_DIR / "projects" / f"{p.slug}.md", fm, body)


def inline_text_lines(el: Tag | None) -> str:
    """Like inline_text but keeps <p>/<br> boundaries as newlines (list separators)."""
    if el is None:
        return ""
    frag = BeautifulSoup(f"<div>{el.decode_contents()}</div>", "lxml")
    for br in frag.find_all("br"):
        br.replace_with("\n")
    for p in frag.find_all(["p", "div", "li"]):
        p.insert_after("\n")
    return frag.get_text("").replace("\xa0", " ")


# -- equipment ------------------------------------------------------------------------------
def build_equipment(html: str, images: Images, rewriter: LinkRewriter, report: Report) -> Counter:
    counts: Counter = Counter()
    used: set[str] = set()
    order = 0
    for block_title, body in content_blocks(html):
        low = block_title.lower()
        kind = "robot" if "robot" in low else "sensor" if "sensor" in low else None
        if kind is None:
            continue
        pending: list[str] = []
        for el in body.children:
            if not isinstance(el, Tag):
                continue
            if el.name != "table":
                pending += [i["src"] for i in el.find_all("img") if i.get("src")]
                continue
            imgs, pending = pending, []
            rows: dict[str, Tag] = {}
            for tr in el.find_all("tr"):
                cells = tr.find_all(["th", "td"], recursive=False)
                tr_imgs = [i["src"] for i in tr.find_all("img") if i.get("src")]
                label = clean_text(cells[0].get_text(" ")).lower().strip(" :") if len(cells) >= 2 else ""
                if len(cells) < 2 or not label:
                    imgs += tr_imgs
                    continue
                rows[label] = cells[1]
            if "name" not in rows:
                report.issue("equipment", f"table without a Name row in '{block_title}'")
                continue
            sup_to_unicode(rows["name"])
            name = inline_text(rows["name"])
            slug = unique_slug(slugify(name), used)
            order += 10
            image = images.first_ok(imgs, IMAGES / "equipment" / slug, "equipment", name) if imgs else None
            if image is None:
                report.issue("equipment", f"`{slug}`: no image")
            elif imgs and len(imgs) > 1:
                report.notes["equipment"].append(f"`{slug}`: {len(imgs)} candidate images, saved the first that worked")
            link_cell = rows.get("link")
            link = cell_url(link_cell)
            extra_links: list[str] = []
            if link_cell is not None:
                a = link_cell.find("a", href=True)
                text_url = clean_text(link_cell.get_text(" ")).split(" ")[0]
                if a is not None and text_url.startswith("http") and _norm_url(a["href"]) != _norm_url(text_url):
                    report.notes["equipment"].append(
                        f"`{slug}`: link text {text_url} differs from its href {a['href']} - used the text")
                hrefs = [x["href"] for x in link_cell.find_all("a", href=True)]
                extra_links = [h for h in hrefs[1:] if h != link]
            body = html_to_markdown(rows.get("applications"), rewriter.rewrite,
                                    localizer(images, "equipment", slug, report))
            if extra_links:
                body += "\n\n" + "\n".join(f"- <{h}>" for h in extra_links)
            unknown = [k for k in rows if k not in ("name", "manufacturer", "applications", "link")]
            for k in unknown:
                report.issue("equipment", f"`{slug}`: unparsed row '{k}'")
            fm = {"name": name, "kind": kind, "image": image,
                  "manufacturer": inline_text(rows.get("manufacturer")) or None,
                  "link": link, "order": order}
            write_markdown(CONTENT_DIR / "equipment" / f"{slug}.md", fm, body)
            counts[kind] += 1
            report.notes["equipment"].append(f"{kind}: {name} (`{slug}`)")
    return counts


# -- partners -------------------------------------------------------------------------------
def build_partners(html: str, images: Images, report: Report) -> int:
    used: set[str] = set()
    n = 0
    for block_title, body in content_blocks(html):
        intro = [inline_text(p) for p in body.find_all("p", recursive=False) if inline_text(p)]
        if intro:
            report.notes["partners"].append(f"Intro text on the old page (not migrated): \"{' '.join(intro)}\"")
        for tr in body.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            texts = [inline_text(td) for td in tds if inline_text(td)]
            if not texts:
                continue
            text = texts[0]
            name, country = text, None
            if "," in text:
                head, tail = (s.strip() for s in text.rsplit(",", 1))
                if tail in COUNTRIES or len(tail.split()) <= 3:
                    name, country = head, tail
            else:
                m = re.search(r"\bof ([A-Z][a-z]+)$", text)
                if m and m.group(1) in COUNTRIES:
                    country = m.group(1)
            slug = unique_slug(slugify(name), used)
            n += 1
            srcs = [i["src"] for i in tr.find_all("img") if i.get("src")]
            logo = images.first_ok(srcs, IMAGES / "partners" / slug, "partners", name) if srcs else None
            if logo is None:
                report.issue("partners", f"`{slug}`: no logo")
            a = tr.find("a", href=True)
            fm = {"name": name, "country": country, "logo": logo, "website": a["href"] if a else None, "order": n * 10}
            write_markdown(CONTENT_DIR / "partners" / f"{slug}.md", fm)
    return n


# -- people ---------------------------------------------------------------------------------
NAME_PREFIX_RE = re.compile(r"^(?:(?:asst|assoc|assist|full)\.?\s+)?(?:prof|dr|doc)\.?(?:\s*sc\.)?\s+", re.I)
NAME_SUFFIX_RE = re.compile(r",?\s*\b(?:ph\.?\s?d\.?|f\.c\.a\.?|m\.\s?sc\.?|dipl\.\s?ing\.?|mag\.\s?ing\.?)\s*$", re.I)
PEOPLE_CATEGORIES = {"faculty": "faculty", "senior researchers": "senior", "phd students": "phd",
                     "project managers": "manager"}
ALUMNI_CATEGORIES = {"alumni": "alumni", "guests": "guest", "master students internships": "intern"}


def clean_person_name(raw: str) -> tuple[str, list[str]]:
    """'Prof. Ivan Petrović, F.C.A.' -> ('Ivan Petrović', ['Prof.', 'F.C.A.'])"""
    name, stripped = clean_text(raw), []
    while True:
        m = NAME_PREFIX_RE.match(name) or NAME_SUFFIX_RE.search(name)
        if not m:
            break
        stripped.append(m.group(0).strip(" ,"))
        name = (name[: m.start()] + name[m.end():]).strip(" ,")
    return name, stripped


def profile_slug(name: str) -> str:
    parts = ascii_fold(name).lower().split()
    return f"{re.sub(r'[^a-z-]', '', parts[0])}.{re.sub(r'[^a-z-]', '', parts[-1])}"


class ProfileChecker:
    """GET https://www.fer.unizg.hr/en/<first>.<last>; OK if 200 and the <title> contains the surname."""

    def __init__(self, offline: bool) -> None:
        self.offline = offline
        self.http: HttpClient | None = None
        self.cache: dict[str, list] = json.loads(PROFILE_CACHE.read_text()) if PROFILE_CACHE.exists() else {}

    def check(self, url: str, surname: str) -> tuple[bool | None, str]:
        if url in self.cache:
            ok, title = self.cache[url]
        elif self.offline:
            return None, "not verified (offline)"
        else:
            self.http = self.http or HttpClient(per_minute=30)
            try:
                resp = self.http.get(url, timeout=30, allow_redirects=True)
            except Exception as exc:  # noqa: BLE001
                return False, type(exc).__name__
            title = ""
            if resp.status_code == 200:
                t = BeautifulSoup(resp.text, "lxml").title
                title = clean_text(t.get_text()) if t else ""
            ok = resp.status_code == 200
            title = title if ok else f"HTTP {resp.status_code}"
            self.cache[url] = [ok, title]
            PROFILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_CACHE.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False))
        if not ok:
            return False, title
        if ascii_fold(surname).lower() in ascii_fold(title).lower():
            return True, title
        return False, f"title '{title}' does not contain '{surname}'"


def cell_lines(cell: Tag) -> list[str]:
    return [clean_text(x) for x in inline_text_lines(cell).split("\n") if clean_text(x)]


def build_people(html: str, images: Images, checker: ProfileChecker, used: set[str], report: Report) -> Counter:
    counts: Counter = Counter()
    entries = []
    for block_title, body in content_blocks(html):
        category = PEOPLE_CATEGORIES.get(block_title.lower())
        if category is None:
            report.issue("people", f"unknown block '{block_title}'")
            continue
        for tr in body.find_all("tr"):
            cells = tr.find_all("td", recursive=False)
            i = 0
            while i < len(cells):
                cell = cells[i]
                img = cell.find("img")
                if img is not None:
                    info = cells[i + 1] if i + 1 < len(cells) else None
                    i += 2
                else:
                    info = cell
                    i += 1
                lines = cell_lines(info) if info is not None else []
                if not lines:
                    if img is not None:
                        report.issue("people", f"photo without a name in '{block_title}'")
                    continue
                links = [a["href"] for c in (cell, info) if c is not None for a in c.find_all("a", href=True)]
                entries.append((category, img.get("src") if img is not None else None, lines, links))

    photo_count = Counter(image_sha1(src) or src for _, src, _, _ in entries if src)
    for n, (category, src, lines, links) in enumerate(entries, 1):
        # `name` is the string the old site displays (titles included); the plain name drives the
        # file slug and the generated profile URL.
        display_name = clean_text(lines[0]).strip(" ,")
        name, _ = clean_person_name(display_name)
        rest = lines[1:]
        group = next((l for l in rest if "group" in l.lower()), None)
        role = "; ".join(l for l in rest if l != group) or None
        slug = unique_slug(slugify(name), used)
        photo = None
        if src:
            key = image_sha1(src) or src
            if photo_count[key] > 1:
                report.notes["people"].append(f"`{slug}`: placeholder photo (same image used by {photo_count[key]} people) - photo omitted")
            else:
                photo = images.save(src, IMAGES / "people" / slug, "people", name)
        else:
            report.issue("people", f"`{slug}`: no photo")
        profile = None
        surname = name.split()[-1]
        if category == "faculty":
            candidate = f"https://www.fer.unizg.hr/en/{profile_slug(name)}"
            ok, why = checker.check(candidate, surname)
            if ok or ok is None:
                profile = candidate
                if ok is None:
                    report.notes["people"].append(f"`{slug}`: profile {candidate} {why}")
            else:
                report.issue("people", f"`{slug}`: generated profile URL {candidate} failed verification ({why}) - omitted")
        elif links:
            url = re.sub(r"^http://", "https://", links[0])
            last = url.rstrip("/").rsplit("/", 1)[-1]
            if "fer.unizg.hr" in url and ascii_fold(surname).lower().split("-")[0] not in last:
                report.issue("people", f"`{slug}`: existing profile link {url} does not look like theirs - omitted")
            else:
                ok, why = checker.check(url, surname) if "fer.unizg.hr" in url else (True, "")
                if ok is False:
                    report.issue("people", f"`{slug}`: existing profile link {url} failed verification ({why}) - omitted")
                else:
                    profile = url
        fm = {"name": display_name, "category": category, "role": role, "group": group, "photo": photo,
              "profile": profile, "order": n * 10}
        write_markdown(CONTENT_DIR / "people" / f"{slug}.md", fm)
        counts[category] += 1
    return counts


def build_alumni(html: str, used: set[str], report: Report) -> Counter:
    counts: Counter = Counter()
    n = 0
    for block_title, body in content_blocks(html):
        category = ALUMNI_CATEGORIES.get(block_title.lower())
        if category is None:
            report.issue("alumni", f"unknown block '{block_title}'")
            continue
        for td in body.find_all("td"):
            text = inline_text(td)
            if not text:
                continue
            name, _, affiliation = (s.strip() for s in text.partition(","))
            n += 1
            base = slugify(name)
            slug = unique_slug(f"{base}-{category}" if base in used else base, used)
            if slug != base:
                report.notes["alumni"].append(f"slug collision for {name}: wrote `{slug}`")
            write_markdown(CONTENT_DIR / "people" / f"{slug}.md",
                           {"name": name, "category": category, "affiliation": affiliation or None, "order": n * 10})
            counts[category] += 1
    return counts


# -- theses ---------------------------------------------------------------------------------
THESIS_RE = re.compile(r"^\s*(?P<surname>[^,]+),\s*(?P<given>[^.]+?)\.\s+(?P<rest>.*)$")
THESIS_TYPE_RE = re.compile(r"//\s*(?P<type>[^()]+?)\s*\((?P<year>\d{4})\)")


def build_theses(html: str, store: AssetStore, report: Report) -> int:
    n = 0
    used: set[str] = set()
    for block_title, body in content_blocks(html):
        if not re.fullmatch(r"\d{4}", block_title):
            continue
        for p in body.find_all("p"):
            text = inline_text(p)
            if not text:
                continue
            m = THESIS_RE.match(text)
            strong = p.find(["strong", "b"])
            if not m or strong is None:
                report.issue("theses", f"unparsed paragraph ({block_title}): {text[:100]}")
                continue
            author = f"{clean_text(m.group('given'))} {clean_text(m.group('surname'))}"
            title = inline_text(strong).strip(" .")
            tm = THESIS_TYPE_RE.search(text)
            year = int(tm.group("year")) if tm else int(block_title)
            ttype = clean_text(tm.group("type")).capitalize() if tm else "Doctoral thesis"
            pdf = url = None
            for a in p.find_all("a", href=True):
                href = urljoin(OLD_BASE, a["href"])
                parts = urlsplit(href)
                if parts.netloc.lower() in OLD_HOSTS:
                    if parts.path.startswith("/_download/"):
                        report.issue("theses", f"{author} ({year}): dead old-site link {a['href']} dropped")
                        continue
                    if parts.path.lower().endswith(".pdf"):
                        pdf = store.old_site_file(parts.path)
                        if pdf is None:
                            report.issue("theses", f"{author} ({year}): PDF download failed: {href}")
                else:
                    url = href
            if pdf is None and not any(a for a in p.find_all("a", href=True) if a["href"].lower().endswith(".pdf")):
                report.notes["theses"].append(f"{author} ({year}): no PDF linked on the old page")
            slug = unique_slug(f"{year}-{slugify(author)}", used)
            write_markdown(CONTENT_DIR / "theses" / f"{slug}.md",
                           {"title": title, "author": author, "year": year, "type": ttype, "pdf": pdf, "url": url})
            n += 1
    return n


# -- single pages ---------------------------------------------------------------------------
def build_students_corner(html: str, images: Images, rewriter: LinkRewriter, report: Report) -> bool:
    blocks = content_blocks(html)
    if not blocks:
        report.issue("pages", "students corner: no content block")
        return False
    parts = []
    for title, body in blocks:
        md = html_to_markdown(body, rewriter.rewrite, localizer(images, "students", "students-corner", report), min_heading=3)
        parts.append(f"## {title}\n\n{md}" if title else md)
    write_markdown(CONTENT_DIR / "pages" / "students-corner.md",
                   {"title": "Student's corner", "description": blocks[0][0] or None}, "\n\n".join(parts))
    return True


def build_home(html: str, rewriter: LinkRewriter, report: Report) -> bool:
    blocks = content_blocks(html)
    if not blocks:
        report.issue("pages", "home: no content block")
        return False
    title, body = blocks[0]
    md = html_to_markdown(body, rewriter.rewrite)
    write_markdown(CONTENT_DIR / "pages" / "home.md",
                   {"title": title or "Laboratory for Autonomous Systems and Mobile Robotics"}, md)
    return True


def parse_contact(html: str, rewriter: LinkRewriter) -> list[str]:
    out = []
    soup_blocks = content_blocks(html)
    for title, body in soup_blocks:
        iframe = body.find("iframe", src=True)
        if iframe is not None:
            out.append(f"- Map ({title}): {iframe['src']}")
        for tr in body.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) < 2:
                continue
            label = inline_text(cells[0]).strip(" :")
            if label.lower().startswith("address"):
                lines = [l for l in cell_lines(cells[1])]
                out.append(f"- {label}: " + " / ".join(lines))
            else:
                a = cells[1].find("a", href=True)
                href = rewriter.rewrite(a["href"]) if a else None
                value = inline_text(cells[1])
                out.append(f"- {label}: {value}" + (f" (`{href}`)" if href and href.replace('mailto:', '') != value else ""))
    return out


# -- main -----------------------------------------------------------------------------------
def clean_output_dirs() -> list[str]:
    removed = []
    for section in SECTIONS:
        d = CONTENT_DIR / section
        for f in d.glob("*.md") if d.exists() else []:
            removed.append(f"{section}/{f.name}")
            f.unlink()
    return removed


def migrate_pages(offline: bool = False, store: AssetStore | None = None) -> Report:
    store = store or AssetStore(offline=offline)
    report = Report()
    images = Images(store, report)
    rewriter = LinkRewriter(asset=store.old_site_file)
    previous = set(clean_output_dirs())
    home = read_page("home")

    projects = build_projects(read_page("running_projects"), home, store, images, rewriter, report)
    pc = Counter(p.status for p in projects if not p.fixed)
    report.counts.append(("Projects - ongoing (projects page)", pc["ongoing"], "8"))
    report.counts.append(("Projects - past", pc["past"], "22"))
    report.counts.append(("Projects - created from the sidebar", sum(1 for p in projects if p.fixed), "-"))
    report.counts.append(("Projects with sidebar: true", sum(1 for p in projects if p.sidebar), "-"))

    ec = build_equipment(read_page("equipment"), images, rewriter, report)
    report.counts.append(("Equipment - robots", ec["robot"], "6"))
    report.counts.append(("Equipment - sensors", ec["sensor"], "13"))
    report.counts.append(("Partners", build_partners(read_page("partners"), images, report), "19"))

    used: set[str] = set()
    checker = ProfileChecker(offline)
    peo = build_people(read_page("people_current"), images, checker, used, report)
    for cat in ("faculty", "senior", "phd", "manager"):
        report.counts.append((f"People - {cat}", peo[cat], "-"))
    alu = build_alumni(read_page("people_alumni"), used, report)
    for cat, exp in (("alumni", "24"), ("guest", "6"), ("intern", "6")):
        report.counts.append((f"People - {cat}", alu[cat], exp))
    report.counts.append(("Theses", build_theses(read_page("thesis"), store, report), "12"))
    report.counts.append(("Pages - students-corner", int(build_students_corner(read_page("students_corner"), images, rewriter, report)), "1"))
    report.counts.append(("Pages - home", int(build_home(home, rewriter, report)), "1"))
    contact = parse_contact(read_page("contact"), rewriter)

    now = {f"{s}/{f.name}" for s in SECTIONS for f in (CONTENT_DIR / s).glob("*.md")}
    stale = sorted(previous - now)
    write_pages_report(report, store, rewriter, contact, stale)
    return report


def write_pages_report(report: Report, store: AssetStore, rewriter: LinkRewriter, contact: list[str], stale: list[str]) -> None:
    lines = [
        "# Pages migration report",
        "",
        "Generated by `uv run lamor-migrate pages` from the cached old-site pages (tools/.cache/old-site/pages).",
        "",
        "## Counts",
        "",
        "| Section | Migrated | Expected |",
        "| --- | ---: | ---: |",
    ]
    lines += [f"| {label} | {n} | {exp} |" for label, n, exp in report.counts]
    lines.append("")
    if stale:
        lines += ["Files from a previous run that were not regenerated (deleted): " + ", ".join(stale), ""]
    lines += ["## Contact page (no content file - details for src/data/site.yaml)", ""] + (contact or ["- not found"])
    lines += ["", "## Needs attention", ""]
    if not any(report.issues.values()):
        lines.append("- nothing")
    for section, msgs in report.issues.items():
        lines.append(f"### {section}")
        lines.append("")
        lines += [f"- {m}" for m in msgs]
        lines.append("")
    lines += ["## Old-site content problems (manual review)", ""] + [f"- {o}" for o in CONTENT_OBSERVATIONS] + [""]
    lines += ["## Notes", ""]
    for section, msgs in report.notes.items():
        lines.append(f"### {section}")
        lines.append("")
        lines += [f"- {m}" for m in msgs]
        lines.append("")
    lines += ["## Links", ""]
    lines += [f"- Rewritten internal links: {dict(rewriter.rewritten)}"]
    if rewriter.unresolved:
        lines += ["- Dropped links (text kept):"] + [f"  - `{u}`" for u in sorted(set(rewriter.unresolved))]
    lines += ["", "## Assets", ""]
    lines += store.summary_lines()
    if store.failed:
        lines += ["", "Failed downloads:", ""] + [f"- {u} ({why})" for u, why in store.failed]
    if store.dead:
        lines += ["", "Dead old-site links:", ""] + [f"- {u}" for u in sorted(set(store.dead))]
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "pages.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
