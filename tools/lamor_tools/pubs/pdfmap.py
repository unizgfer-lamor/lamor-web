"""`lamor-pubs pdfmap`: attach PDFs from the old website's curated publication lists to CRORIS records.

The old site (lamor.fer.hr/lamor/publications/...) lists papers per year with links to PDFs under
/images/... . Each curated entry is matched to a CRORIS record in the database:

  1. bibIrbId   (old CROSBI link bib.irb.hr/prikazi-rad?rad=NNN)
  2. DOI        (doi.org links)
  3. contained  (normalised CRORIS title contained in the normalised citation text, year ±1)
  4. fuzzy      (difflib ratio >= 0.9 between normalised titles, year ±1)

Matched entries with a PDF become `{id, pdf}` items in src/data/publication-overrides.yaml.
Unmatched entries whose best fuzzy candidate is < 0.6 are added to src/data/publications-extra.yaml;
the grey zone in between is only listed in tools/publications/pdfmap-report.md for a human.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import yaml
from bs4 import BeautifulSoup, Tag

from lamor_tools.common import PUBLIC_DIR, HttpClient, dump_yaml, normalize_title, slugify
from lamor_tools.pubs import db
from lamor_tools.pubs.config import (
    DB_PATH,
    EXTRA_YAML,
    OLD_SITE_CACHE,
    OVERRIDES_YAML,
    PDFMAP_REPORT_PATH,
    Config,
    load_config,
)
from lamor_tools.pubs.export import ExportResult, build_export
from lamor_tools.pubs.fields import clean_title, is_lab_author, lab_keys, normalize_author

log = logging.getLogger(__name__)

OLD_SITE = "https://lamor.fer.hr"
PAGES = [
    "/lamor/publications/2025_present",
    "/lamor/publications/2020_-_present",
    "/lamor/publications/2015_-_present",
    "/lamor/publications/2010_-_2014",
]
FUZZY_MATCH = 0.9
FUZZY_GREY = 0.6
MIN_CONTAINED_LEN = 20  # characters of normalised title needed for the containment rule

OVERRIDES_HEADER = """\
# Corrections for generated publications (src/data/publications.json, from CRORIS) and for the
# hand-made ones in publications-extra.yaml. One list item per publication, matched by `id`
# (the CRORIS crosbiId as a number, or the string id of an extra entry). Every field given here
# replaces the generated value; leave out what should stay as generated.
#
#   id:        required. CRORIS id (number) or extra entry id (string)
#   hide:      true removes the publication from the website
#   category:  journal | conference
#   venue:     journal / conference name as it should be shown
#   title:     corrected title
#   pdf:       root-relative path of a PDF in public/, e.g. /images/50020777/Borrmann2014.pdf
#   note:      free text for maintainers (not shown on the site)
#   (year, authors, details, status, doi and url can be overridden the same way)
#
# `lamor-pubs pdfmap` adds or updates `pdf` values here and keeps all other keys and entries,
# but it rewrites the file: comments other than this header are not preserved.
"""

EXTRA_HEADER = """\
# Publications that are not (or not correctly) in CRORIS, shown on the website together with the
# generated list. Same fields as src/data/publications.json:
#
#   id:        unique string, e.g. legacy-2015-some-title (never a number)
#   category:  journal | conference
#   year:      number
#   title, venue, details: text
#   authors:   list of {name: "Surname, Name", lab: true|false}
#   status, doi, url, pdf: optional
#
# Entries with ids starting with legacy- were imported by `lamor-pubs pdfmap` from the old website's
# publication lists (papers it could not find in CRORIS). pdfmap only appends ids that are missing,
# so edits to existing entries are kept; comments other than this header are not preserved.
"""

JOURNAL_HINTS = re.compile(
    r"journal|transactions|letters|magazine|robotics and autonomous systems|\bsensors\b|automatika|"
    r"information fusion|ieee access|applied sciences|electronics\b|machines\b|\bremote sensing",
    re.I,
)
CONFERENCE_HINTS = re.compile(r"conference|symposium|workshop|congress|proceedings|convention|meeting|forum", re.I)


@dataclass
class Curated:
    page: str
    year: int
    text: str
    title: str
    authors: list[str]
    venue: str
    rest: str
    pdf: str | None = None
    bib_ids: list[int] = field(default_factory=list)
    dois: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    # matching results
    match_id: int | None = None
    method: str | None = None
    best_ratio: float = 0.0
    best_candidate: int | None = None


# ----------------------------------------------------------------------------- fetching


def page_slug(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def fetch_pages(refresh: bool = False, cache_dir: Path = OLD_SITE_CACHE) -> dict[str, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    http: HttpClient | None = None
    pages = {}
    for path in PAGES:
        cached = cache_dir / f"{page_slug(path)}.html"
        if cached.exists() and not refresh:
            pages[page_slug(path)] = cached.read_bytes().decode("utf-8", errors="replace")
            continue
        http = http or HttpClient(per_minute=60, retries=4, timeout=60)  # 1 request/second
        log.info("downloading %s%s", OLD_SITE, path)
        resp = http.get(OLD_SITE + path)
        resp.raise_for_status()
        cached.write_bytes(resp.content)  # raw bytes: the pages are UTF-8 but may not say so in the headers
        pages[page_slug(path)] = resp.content.decode("utf-8", errors="replace")
    return pages


# ----------------------------------------------------------------------------- parsing


def _text(el: Tag) -> str:
    for br in el.find_all("br"):
        br.replace_with(" ")
    return re.sub(r"\s+", " ", el.get_text("").replace("\xa0", " ")).strip()


def pdf_path(href: str) -> str | None:
    """Old-site PDF link -> root-relative, percent-encoded path ('/images/x/A b.pdf' -> '/images/x/A%20b.pdf')."""
    href = (href or "").strip()
    url = urlparse(href)
    if url.netloc and url.netloc.lower() not in ("lamor.fer.hr", "www.lamor.fer.hr"):
        return None
    path = url.path
    if not path.lower().endswith(".pdf"):
        return None
    if not path.startswith("/"):
        path = "/" + path
    return quote(unquote(path), safe="/-_.~()")


def _units(html: Tag) -> list[Tag]:
    """One element per curated entry: table cells, or paragraphs outside tables."""
    units: list[Tag] = []
    for el in html.find_all(["td", "p"]):
        if not _text(el):
            continue
        if el.name == "p" and el.find_parent("td") is not None:
            continue
        if el.name == "td":
            entries = [p for p in el.find_all("p") if "//" in p.get_text()]
            if len(entries) > 1:
                units.extend(entries)
                continue
        units.append(el)
    return units


def _title_element(unit: Tag) -> Tag | None:
    for a in unit.find_all("a", href=True):
        if re.search(r"bib\.irb\.hr", a["href"]) and len(a.get_text(strip=True)) > 12:
            if "," in a.get_text() and ";" in a.get_text():
                continue  # a link wrapped around the author list
            return a
    strongs = [s for s in unit.find_all(["strong", "b"]) if len(s.get_text(strip=True)) > 12]
    return max(strongs, key=lambda s: len(s.get_text(strip=True))) if strongs else None


def parse_unit(unit: Tag, year: int, page: str) -> Curated | None:
    links = [(a.get("href") or "", a.get_text(" ", strip=True)) for a in unit.find_all("a")]
    title_el = _title_element(unit)
    title_text = clean_title(_text(title_el)).strip(" .") if title_el else ""
    text = _text(unit)
    if not text or "//" not in text and not title_text:
        return None

    if title_text and title_text in text:
        before, after = text.split(title_text, 1)
    elif "//" in text:
        head, after = text.split("//", 1)
        # 'Surname, Name; Surname, Name. Title' -> split at the last '. ' that follows a name list
        m = re.match(r"^(.*?[^A-Z]\.)\s+(.+)$", head.strip())
        before, title_text = (m.group(1), m.group(2)) if m else ("", head)
        title_text = clean_title(title_text).strip(" .")
        after = "//" + after
    else:
        return None

    authors = [a.strip(" .") for a in re.split(r"\s*;\s*", before.strip(" .:")) if a.strip(" .")]
    after = re.sub(r"^[\s.]*//\s*", "", after.strip())

    venue = ""
    for em in unit.find_all(["em", "i"]):
        em_text = clean_title(_text(em)).strip(" ,.")
        if len(em_text) > 3 and em_text in after:
            venue = em_text
            break
    if not venue:
        m = re.match(r"^(.+?)(?:[.,]\s+(?=\(?\d)|\.\s*$|$|\s+\(\d{4}\)|,\s+\d)", after)
        venue = (m.group(1) if m else after).strip(" ,.")
    rest = after.split(venue, 1)[1] if venue and venue in after else ""
    rest = re.sub(r"\bpdf\b", "", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.;")
    rest = re.sub(r"\s+([,;.])", r"\1", rest)
    rest = re.sub(r"(\d)\s*-\s*(\d)", "\\1\u2013\\2", rest)

    entry = Curated(page=page, year=year, text=text, title=title_text, authors=authors, venue=venue, rest=rest)
    for href, _label in links:
        p = pdf_path(href)
        if p and not entry.pdf:
            entry.pdf = p
            continue
        m = re.search(r"bib\.irb\.hr/(?:prikazi-rad\?.*?rad=)?(\d+)", href)
        if m:
            entry.bib_ids.append(int(m.group(1)))
            continue
        doi = db.bare_doi(href) if "doi.org/" in href.lower() else None
        if doi:
            entry.dois.append(doi)
        elif href.startswith("http") and "lamor.fer.hr" not in href:
            entry.urls.append(href)
    for m in re.finditer(r"doi:\s*(10\.\d{4,9}/\S+)", text, flags=re.I):
        doi = db.bare_doi(m.group(1))
        if doi and doi not in entry.dois:
            entry.dois.append(doi)
    return entry


def parse_page(html_text: str, page: str) -> list[Curated]:
    soup = BeautifulSoup(html_text, "lxml")
    entries = []
    for module in soup.select("#cms_area_middle .cms_module.portlet_content"):
        title = module.select_one(".cms_module_title")
        body = module.select_one(".cms_module_html")
        if not title or not body or not re.fullmatch(r"\d{4}", title.get_text(strip=True)):
            continue
        year = int(title.get_text(strip=True))
        for unit in _units(body):
            entry = parse_unit(unit, year, page)
            if entry:
                entries.append(entry)
            else:
                log.debug("skipped unparsable entry on %s (%d): %s", page, year, _text(unit)[:100])
    return entries


# ----------------------------------------------------------------------------- matching


def _title_variants(row: dict[str, Any]) -> list[str]:
    raw = row["raw"]
    titles = [raw.get("naslov") or ""] + [n.get("naziv") or "" for n in raw.get("naslovi") or []]
    out = []
    for t in titles:
        norm = normalize_title(t)
        if norm and norm not in out:
            out.append(norm)
    return out


class Matcher:
    def __init__(self, rows: list[dict[str, Any]], exported: set[int]) -> None:
        self.rows = rows
        self.exported = exported
        self.by_bib: dict[int, list[dict[str, Any]]] = {}
        self.by_doi: dict[str, list[dict[str, Any]]] = {}
        self.variants = {r["id"]: _title_variants(r) for r in rows}
        for r in rows:
            if r.get("bib_irb_id"):
                self.by_bib.setdefault(r["bib_irb_id"], []).append(r)
            if r.get("doi"):
                self.by_doi.setdefault(r["doi"], []).append(r)

    def _prefer(self, cands: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
        if not cands:
            return None
        return max(cands, key=lambda r: (r["id"] in self.exported, -abs((r.get("year") or 0) - year), -r["id"]))

    def match(self, c: Curated) -> None:
        for bib in c.bib_ids:
            row = self._prefer(self.by_bib.get(bib, []), c.year)
            if row:
                c.match_id, c.method = row["id"], "bibIrbId"
                return
        for doi in c.dois:
            row = self._prefer(self.by_doi.get(doi.lower(), []), c.year)
            if row:
                c.match_id, c.method = row["id"], "doi"
                return
        near = [r for r in self.rows if r.get("year") is not None and abs(r["year"] - c.year) <= 1]
        text_norm = f" {normalize_title(c.text)} "
        contained = [
            (max(len(v) for v in self.variants[r["id"]] if f" {v} " in text_norm), r)
            for r in near
            if any(len(v) >= MIN_CONTAINED_LEN and f" {v} " in text_norm for v in self.variants[r["id"]])
        ]
        if contained:
            longest = max(n for n, _ in contained)
            row = self._prefer([r for n, r in contained if n == longest], c.year)
            c.match_id, c.method = row["id"], "title contained"
            return
        title_norm = normalize_title(c.title)
        best_ratio, best_row = 0.0, None
        best_near_ratio, best_near = 0.0, None
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(title_norm)
        for r in self.rows:
            is_near = r.get("year") is not None and abs(r["year"] - c.year) <= 1
            floor = min(best_ratio, best_near_ratio) if is_near else best_ratio
            for v in self.variants[r["id"]]:
                sm.set_seq1(v)
                if sm.real_quick_ratio() <= floor or sm.quick_ratio() <= floor:
                    continue  # upper bounds: cannot beat the best so far
                ratio = sm.ratio()
                if ratio > best_ratio:
                    best_ratio, best_row = ratio, r
                if is_near and ratio > best_near_ratio:
                    best_near_ratio, best_near = ratio, r
        c.best_ratio = round(best_ratio, 3)
        c.best_candidate = best_row["id"] if best_row else None
        if best_near is not None and best_near_ratio >= FUZZY_MATCH:
            c.match_id, c.method = best_near["id"], "fuzzy title"
            c.best_ratio, c.best_candidate = round(best_near_ratio, 3), best_near["id"]


# ----------------------------------------------------------------------------- YAML outputs


def load_yaml_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a YAML list; refusing to overwrite it")
    return [dict(item) for item in data if isinstance(item, dict) and "id" in item]


def merge_overrides(existing: list[dict[str, Any]], pdfs: dict[Any, str]) -> list[dict[str, Any]]:
    """Set `pdf` on the entries in `pdfs` (by id), keep every other key/entry; new ids appended sorted."""
    out = [dict(e) for e in existing]
    index = {str(e["id"]): e for e in out}
    for pub_id, pdf in pdfs.items():
        if str(pub_id) in index:
            index[str(pub_id)]["pdf"] = pdf
    new = sorted((k for k in pdfs if str(k) not in index), key=lambda k: (isinstance(k, str), str(k).zfill(12)))
    out += [{"id": k, "pdf": pdfs[k]} for k in new]
    return out


def merge_extras(existing: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append additions whose id is not present yet; existing entries are kept untouched."""
    out = [dict(e) for e in existing]
    ids = {str(e["id"]) for e in out}
    for a in additions:
        if str(a["id"]) not in ids:
            out.append(a)
            ids.add(str(a["id"]))
    return out


def write_yaml_list(path: Path, header: str, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dump_yaml(items) if items else "[]\n"
    path.write_text(header + "\n" + body, encoding="utf-8")


def guess_category(c: Curated) -> str:
    text = f"{c.venue} {c.rest}"
    if CONFERENCE_HINTS.search(c.venue):
        return "conference"
    if JOURNAL_HINTS.search(text):
        return "journal"
    return "conference"


def extra_entry(c: Curated, keys: dict[tuple[str, str], str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": f"legacy-{c.year}-{slugify(c.title, 40)}",
        "category": guess_category(c),
        "year": c.year,
        "title": c.title,
        "authors": [{"name": n, "lab": is_lab_author(n, keys)} for n in (normalize_author(a, keys) for a in c.authors)],
        "venue": c.venue,
        "details": "" if re.fullmatch(r"\(?\d{4}\)?", c.rest) else c.rest,  # a bare year repeats `year`
    }
    if c.dois:
        entry["doi"] = c.dois[0]
    if c.urls:
        entry["url"] = c.urls[0]
    if c.pdf:
        entry["pdf"] = c.pdf
    return entry


# ----------------------------------------------------------------------------- report


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_report(
    curated: list[Curated], result: ExportResult, exported: set[int], extras_added: list[dict[str, Any]],
    grey: list[Curated], unmatched_added: list[Curated], pdf_count: int, duplicates: dict[int, list[Curated]],
) -> str:
    rows = result.rows
    matched = [c for c in curated if c.match_id is not None]
    methods: dict[str, int] = {}
    for c in matched:
        methods[c.method or "?"] = methods.get(c.method or "?", 0) + 1
    out = ["# Old-site PDF mapping report", ""]
    out.append(f"- Generated: {datetime.now(UTC).replace(microsecond=0).isoformat()}")
    out.append(f"- Curated entries parsed from the old site: {len(curated)} "
               f"({sum(1 for c in curated if c.pdf)} with a PDF link)")
    out.append(f"- Matched to CRORIS: {len(matched)} — " + ", ".join(f"{k}: {v}" for k, v in sorted(methods.items())))
    out.append(f"- PDFs written to publication-overrides.yaml: {pdf_count}")
    out.append(f"- Not in CRORIS (best fuzzy ratio < {FUZZY_GREY}), added to publications-extra.yaml: "
               f"{len(unmatched_added)} ({len(extras_added)} new ids this run)")
    out.append(f"- Grey zone ({FUZZY_GREY}–{FUZZY_MATCH}), left for a human: {len(grey)}")
    not_exported = [c for c in matched if c.match_id not in exported]
    out.append(f"- Matched records that are not exported: {len(not_exported)}")

    if grey:
        out += ["", "## Grey zone — decide by hand", "",
                "Either add an override/extra entry, or ignore. Ratio = similarity of normalised titles.", "",
                "| Year | Old-site title | Best CRORIS candidate | Ratio | PDF |", "|---|---|---|---:|---|"]
        for c in grey:
            row = rows.get(c.best_candidate) if c.best_candidate else None
            cand = (f"[{row['id']}](https://www.croris.hr/crosbi/publikacija/{row['id']}) {row.get('year')} "
                    f"\"{_md(row.get('title'))}\" ({_md(result.excluded.get(row['id'], 'exported'))})") if row else "—"
            out.append(f"| {c.year} | {_md(c.title)} | {cand} | {c.best_ratio:.2f} | {_md(c.pdf or '')} |")

    if not_exported:
        out += ["", "## Matched but not exported", "",
                "The CRORIS record exists but the export rules drop it (the PDF override has no effect until "
                "the record is exported, or the paper is added to publications-extra.yaml).", "",
                "| Year | Old-site title | CRORIS id | Why not exported |", "|---|---|---|---|"]
        for c in not_exported:
            row = rows[c.match_id]
            why = result.excluded.get(c.match_id, "?")
            detail = result.excluded_detail.get(c.match_id, "")
            out.append(f"| {c.year} | {_md(c.title)} | [{c.match_id}](https://www.croris.hr/crosbi/publikacija/"
                       f"{c.match_id}) {row.get('vrsta')} / {row.get('tip')} | {_md(why)}"
                       f"{' (' + _md(detail) + ')' if detail else ''} |")

    if duplicates:
        out += ["", "## Several old-site entries matched the same CRORIS record", ""]
        for pid, cs in sorted(duplicates.items()):
            out.append(f"- {pid}: " + "; ".join(f"{c.year} \"{_md(c.title)}\"" for c in cs))

    out += ["", "## Not in CRORIS — added to publications-extra.yaml", ""]
    if unmatched_added:
        out += ["| Year | Title | Best ratio | PDF |", "|---|---|---:|---|"]
        out += [f"| {c.year} | {_md(c.title)} | {c.best_ratio:.2f} | {_md(c.pdf or '')} |" for c in unmatched_added]
    else:
        out.append("None.")

    out += ["", "## All matches", "", "| Year | Old-site title | CRORIS id | Method | Exported | PDF |",
            "|---|---|---|---|---|---|"]
    for c in matched:
        exp = "yes" if c.match_id in exported else "no"
        out.append(f"| {c.year} | {_md(c.title)} | {c.match_id} | {c.method} | {exp} | {_md(c.pdf or '')} |")
    out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------------------- main


def run_pdfmap(
    refresh: bool = False,
    config: Config | None = None,
    db_path: Path = DB_PATH,
    overrides_path: Path = OVERRIDES_YAML,
    extra_path: Path = EXTRA_YAML,
    report_path: Path = PDFMAP_REPORT_PATH,
) -> list[Curated]:
    config = config or load_config()
    if not Path(db_path).exists():
        raise SystemExit(f"{db_path} does not exist; run `lamor-pubs sync` first")
    conn = db.connect(db_path)
    rows = db.load_publications(conn)
    conn.close()
    result = build_export(rows, config)
    exported = {e["id"] for e in result.entries}

    curated: list[Curated] = []
    for slug, html_text in fetch_pages(refresh).items():
        page_entries = parse_page(html_text, slug)
        log.info("%s: %d entries", slug, len(page_entries))
        curated += page_entries

    matcher = Matcher(rows, exported)
    for c in curated:
        matcher.match(c)
        if c.match_id is not None and c.match_id in result.merged_into:
            c.match_id = result.merged_into[c.match_id]  # duplicate -> the record that is exported

    pdfs: dict[Any, str] = {}
    by_id: dict[int, list[Curated]] = {}
    for c in curated:
        if c.match_id is None:
            continue
        by_id.setdefault(c.match_id, []).append(c)
        if c.pdf and c.match_id not in pdfs:
            if (PUBLIC_DIR / unquote(c.pdf).lstrip("/")).exists():
                pdfs[c.match_id] = c.pdf
            else:
                log.warning("PDF not in public/ (not downloaded?), not attached: %s", c.pdf)
    duplicates = {k: v for k, v in by_id.items() if len(v) > 1}

    keys = lab_keys([r.name for r in config.researchers] + list(config.lab_members))
    unmatched = [c for c in curated if c.match_id is None]
    grey = [c for c in unmatched if c.best_ratio >= FUZZY_GREY]
    not_in_croris = [c for c in unmatched if c.best_ratio < FUZZY_GREY]
    additions = [extra_entry(c, keys) for c in not_in_croris]

    existing_overrides = load_yaml_list(overrides_path)
    existing_extras = load_yaml_list(extra_path)
    extras = merge_extras(existing_extras, additions)
    extra_ids = {str(e["id"]) for e in existing_extras}
    new_extras = [a for a in additions if str(a["id"]) not in extra_ids]
    write_yaml_list(overrides_path, OVERRIDES_HEADER, merge_overrides(existing_overrides, pdfs))
    write_yaml_list(extra_path, EXTRA_HEADER, extras)
    report_path.write_text(
        render_report(curated, result, exported, new_extras, grey, not_in_croris, len(pdfs), duplicates),
        encoding="utf-8",
    )
    methods: dict[str, int] = {}
    for c in curated:
        if c.method:
            methods[c.method] = methods.get(c.method, 0) + 1
    log.info("%d curated entries: %d matched (%s), %d PDFs -> overrides, %d grey zone, %d not in CRORIS -> extras",
             len(curated), sum(methods.values()), ", ".join(f"{k} {v}" for k, v in sorted(methods.items())),
             len(pdfs), len(grey), len(not_in_croris))
    log.info("wrote %s, %s and %s", overrides_path, extra_path, report_path)
    return curated
