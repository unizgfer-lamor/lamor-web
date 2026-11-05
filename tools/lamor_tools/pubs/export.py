"""`lamor-pubs export`: database -> src/data/publications.json + tools/publications/report.md (offline)."""

from __future__ import annotations

import difflib
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lamor_tools.common import ascii_fold, normalize_title
from lamor_tools.pubs import db
from lamor_tools.pubs.config import DB_PATH, PUBLICATIONS_JSON, REPORT_PATH, Config, load_config
from lamor_tools.pubs.fields import (
    best_url,
    bib_fields,
    choose_title,
    clean_venue,
    conference_details,
    conference_venue,
    is_lab_author,
    journal_details,
    lab_keys,
    normalize_author,
    parse_authors,
    recase_venue,
)

log = logging.getLogger(__name__)

CATEGORY_ORDER = {"journal": 0, "conference": 1}
DOI_TITLE_SIMILARITY = 0.8  # same DOI but clearly different titles -> not merged (shared proceedings DOI)
REPORT_LIST_LIMIT = 150


@dataclass
class ExportResult:
    entries: list[dict[str, Any]]
    excluded: dict[int, str]  # pub id -> reason (first failing rule)
    excluded_detail: dict[int, str]  # pub id -> detail (e.g. the vrsta / status)
    merges: list[dict[str, Any]]  # {kept: id, dropped: [ids], by: 'doi'|'title'}
    merged_into: dict[int, int]  # dropped id -> kept id
    doi_conflicts: list[dict[str, Any]] = field(default_factory=list)
    rows: dict[int, dict[str, Any]] = field(default_factory=dict)


# ----------------------------------------------------------------------------- classification


def classify(row: dict[str, Any], config: Config) -> tuple[str | None, str | None, str]:
    """-> (category, exclusion reason, detail). Exactly one of category / reason is set."""
    if row.get("deleted"):
        return None, "deleted in CRORIS", ""
    if row.get("orphaned"):
        return None, "no longer in any researcher's list", ""
    roles = set(config.author_role_ids)
    if not any(p["person_id"] in config.researcher_ids and p["role_id"] in roles for p in row["people"]):
        involved = sorted({p["role_id"] for p in row["people"] if p["person_id"] in config.researcher_ids})
        return None, "no lab researcher as author (mentor/editor/supervisor only)", ",".join(map(str, involved))
    year = row.get("year")
    if year is None or year < config.min_year:
        return None, f"before {config.min_year}", str(year)
    vrsta = (row.get("vrsta") or "").strip()
    tip = (row.get("tip") or "").strip()
    category = next((cat for cat, rules in config.categories.items() if vrsta in rules.get("vrsta", [])), None)
    if category is None:
        return None, "type (vrsta) not exported", vrsta or "(none)"
    if any(s.lower() in tip.lower() for s in config.exclude_tip_contains):
        return None, "abstract (tip)", tip
    if tip in config.categories[category].get("exclude_tip", []):
        return None, f"{category} tip not exported", tip
    status = (row.get("status") or "").strip()
    allowed_status = config.categories[category].get("include_status") or config.include_status
    if status and status not in allowed_status:
        return None, "status not exported", status
    return category, None, ""


# ----------------------------------------------------------------------------- entries


def build_entry(
    row: dict[str, Any], category: str, config: Config, keys: dict[tuple[str, str], str]
) -> dict[str, Any]:
    raw = row["raw"]
    names = [normalize_author(n, keys) for n in parse_authors(raw.get("autori"))]
    authors = [{"name": n, "lab": is_lab_author(n, keys)} for n in names]
    event = row["events"][0] if row.get("events") else None
    if category == "journal":
        venue_raw = clean_venue(raw.get("casopis") or raw.get("nadredenaPublikacija"))
        details = journal_details(raw)
    else:
        venue_raw = conference_venue(raw, event)
        details = conference_details(raw, event, config.place_names)
    venue = recase_venue(venue_raw, config.venue_acronyms)
    venue = config.venue_overrides.get(venue_raw, config.venue_overrides.get(venue, venue))

    entry: dict[str, Any] = {
        "id": row["id"],
        "category": category,
        "year": row["year"],
        "title": choose_title(raw),
        "authors": authors,
        "venue": venue,
        "details": details,
    }
    bib = bib_fields(raw, category, event, config.place_names)
    if bib:
        entry["bib"] = bib
    if (row.get("status") or "") == "prihvaćeno":
        entry["status"] = "accepted"
    doi = db.bare_doi(raw.get("doi"))
    if doi:
        entry["doi"] = doi
    url = best_url(raw)
    if url:
        entry["url"] = url
    entry["croris"] = row["id"]
    return entry


def _rank(row: dict[str, Any]) -> tuple[int, int, int, int]:
    """Higher is better: published > accepted, has DOI, more non-empty fields, lower id."""
    status = row.get("status") or "objavljeno"
    status_rank = 2 if status == "objavljeno" else 1 if status == "prihvaćeno" else 0
    nonempty = sum(1 for v in row["raw"].values() if v not in (None, "", [], {}))
    return status_rank, 1 if row.get("doi") else 0, nonempty, -row["id"]


def dedup(
    entries: list[dict[str, Any]], rows: dict[int, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge CRORIS double entries: same DOI (with similar titles), or same (title, year, category).

    Never merges across categories. Returns (kept entries, merge groups, DOI conflicts).
    """
    parent = {e["id"]: e["id"] for e in entries}
    via: dict[int, str] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int, how: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
            via[a] = via[b] = how

    by_doi: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_title: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        doi = db.normalize_doi(e.get("doi"))
        if doi:
            by_doi[(doi, e["category"])].append(e)
        by_title[(normalize_title(e["title"]), e["year"], e["category"])].append(e)

    conflicts = []
    for (doi, _cat), group in sorted(by_doi.items()):
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ratio = difflib.SequenceMatcher(None, normalize_title(a["title"]), normalize_title(b["title"])).ratio()
                if ratio >= DOI_TITLE_SIMILARITY:
                    union(a["id"], b["id"], "doi")
                else:
                    conflicts.append({"doi": doi, "ids": [a["id"], b["id"]], "titles": [a["title"], b["title"]]})
    for (title, _year, _cat), group in sorted(by_title.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if not title:
            continue
        for other in group[1:]:
            union(group[0]["id"], other["id"], "title")

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[find(e["id"])].append(e)
    kept, merges = [], []
    for members in groups.values():
        best = max(members, key=lambda e: _rank(rows[e["id"]]))
        kept.append(best)
        if len(members) > 1:
            dropped = sorted(e["id"] for e in members if e is not best)
            how = "doi" if any(via.get(e["id"]) == "doi" for e in members) else "title"
            merges.append({"kept": best["id"], "dropped": dropped, "by": how, "title": best["title"],
                           "year": best["year"], "category": best["category"]})
    merges.sort(key=lambda m: (-m["year"], m["kept"]))
    return kept, merges, conflicts


def sort_key(e: dict[str, Any]) -> tuple[Any, ...]:
    first = e["authors"][0]["name"] if e.get("authors") else ""
    return (-int(e["year"]), CATEGORY_ORDER.get(e["category"], 9), ascii_fold(first).lower(),
            ascii_fold(e["title"]).lower(), str(e["id"]))


def build_export(rows: list[dict[str, Any]], config: Config) -> ExportResult:
    keys = lab_keys([r.name for r in config.researchers] + list(config.lab_members))
    excluded: dict[int, str] = {}
    detail: dict[int, str] = {}
    candidates = []
    for row in rows:
        category, reason, why = classify(row, config)
        if reason:
            excluded[row["id"]] = reason
            detail[row["id"]] = why
            continue
        candidates.append(build_entry(row, category, config, keys))
    by_id = {r["id"]: r for r in rows}
    kept, merges, conflicts = dedup(candidates, by_id)
    merged_into = {}
    for m in merges:
        for d in m["dropped"]:
            merged_into[d] = m["kept"]
            excluded[d] = "duplicate of another CRORIS record (merged)"
            detail[d] = str(m["kept"])
    kept.sort(key=sort_key)
    return ExportResult(kept, excluded, detail, merges, merged_into, conflicts, by_id)


# ----------------------------------------------------------------------------- output


def dumps_entries(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


def _load_previous(path: Path) -> dict[str, dict[str, Any]]:
    try:
        return {str(e["id"]): e for e in json.loads(path.read_text(encoding="utf-8"))}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {}


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _limited(lines: list[str], limit: int = REPORT_LIST_LIMIT) -> list[str]:
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"- … and {len(lines) - limit} more"]


def render_report(
    result: ExportResult, conn: sqlite3.Connection, previous: dict[str, dict[str, Any]], config: Config
) -> str:
    entries = result.entries
    total_db = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0]
    listed = conn.execute("SELECT COUNT(DISTINCT pub_id) FROM researcher_publication").fetchone()[0]
    deleted = conn.execute("SELECT COUNT(*) FROM publication WHERE deleted = 1").fetchone()[0]
    orphaned = conn.execute("SELECT COUNT(*) FROM publication WHERE orphaned = 1").fetchone()[0]
    last = conn.execute("SELECT * FROM sync_run WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()

    out = ["# Publications export report", ""]
    out.append(f"- Generated: {datetime.now(UTC).replace(microsecond=0).isoformat()}")
    if last:
        out.append(
            f"- Last sync: {last['finished_at']} ({last['mode']}; listed {last['listed']}, fetched {last['fetched']}, "
            f"new {last['new']}, changed {last['changed']}, deleted {last['deleted']}, errors {last['errors']})"
        )
    out.append(f"- Distinct CRORIS ids in the database: {total_db} (listed by researchers now: {listed}, "
               f"deleted: {deleted}, orphaned: {orphaned})")
    cats = Counter(e["category"] for e in entries)
    out.append(f"- Exported: **{len(entries)}** ({cats['journal']} journal, {cats['conference']} conference)")
    out += ["", "## Exported per year", "", "| Year | Journal | Conference | Total |", "|---|---:|---:|---:|"]
    per_year: dict[int, Counter[str]] = defaultdict(Counter)
    for e in entries:
        per_year[e["year"]][e["category"]] += 1
    for year in sorted(per_year, reverse=True):
        c = per_year[year]
        out.append(f"| {year} | {c['journal']} | {c['conference']} | {c['journal'] + c['conference']} |")
    out.append(f"| **all** | **{cats['journal']}** | **{cats['conference']}** | **{len(entries)}** |")

    out += ["", "## Not exported", "", "| Reason | Count | Breakdown |", "|---|---:|---|"]
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for pid, reason in result.excluded.items():
        reasons[reason][result.excluded_detail.get(pid, "")] += 1
    for reason, details in sorted(reasons.items(), key=lambda kv: -sum(kv[1].values())):
        breakdown = ", ".join(f"{_md(d)} ({n})" for d, n in details.most_common(12) if d)
        if reason.startswith("no lab researcher"):
            breakdown = ""
        if reason.startswith("duplicate"):
            breakdown = ""
        out.append(f"| {_md(reason)} | {sum(details.values())} | {breakdown} |")

    out += ["", f"## Duplicates merged ({len(result.merges)} groups)", ""]
    if result.merges:
        out.append("Kept record first; the others are CRORIS double entries of the same paper.")
        out.append("")
        lines = [
            f"- {m['year']} {m['category']}: kept [{m['kept']}](https://www.croris.hr/crosbi/publikacija/{m['kept']}) "
            f"\"{_md(m['title'])}\"; dropped {', '.join(str(d) for d in m['dropped'])} (by {m['by']})"
            for m in result.merges
        ]
        out += _limited(lines)
    else:
        out.append("None.")
    if result.doi_conflicts:
        out += ["", f"### Same DOI, different titles — not merged ({len(result.doi_conflicts)})", ""]
        out += _limited([
            f"- `{c['doi']}`: {c['ids'][0]} \"{_md(c['titles'][0])}\" vs {c['ids'][1]} \"{_md(c['titles'][1])}\""
            for c in result.doi_conflicts
        ])

    out += ["", "## Changes since the previous export", ""]
    if not previous:
        out.append(f"No previous export found; all {len(entries)} entries are new.")
    else:
        current = {str(e["id"]): e for e in entries}
        new = [current[k] for k in current if k not in previous]
        removed = [previous[k] for k in previous if k not in current]
        changed = []
        for k, e in current.items():
            if k in previous and previous[k] != e:
                fields = sorted(f for f in set(e) | set(previous[k]) if e.get(f) != previous[k].get(f))
                changed.append((e, fields))
        out.append(f"{len(new)} new, {len(changed)} changed, {len(removed)} removed.")
        for title, items in (("New", new), ("Removed", removed)):
            if items:
                out += ["", f"### {title} ({len(items)})", ""]
                out += _limited([f"- {e.get('year')} {e.get('category')} [{e['id']}]: {_md(e.get('title', ''))}"
                                 for e in sorted(items, key=sort_key)])
                if title == "Removed":
                    out.append("")
                    out += _limited([f"  - {e['id']}: {result.excluded.get(int(e['id']), 'not in database')}"
                                     for e in items if str(e["id"]).isdigit()])
        if changed:
            out += ["", f"### Changed ({len(changed)})", ""]
            out += _limited([f"- [{e['id']}] {_md(e['title'])}: {', '.join(f)}" for e, f in changed])
    out.append("")
    return "\n".join(out)


def run_export(
    config: Config | None = None,
    db_path: Path = DB_PATH,
    json_path: Path = PUBLICATIONS_JSON,
    report_path: Path = REPORT_PATH,
) -> ExportResult:
    config = config or load_config()
    if not Path(db_path).exists():
        raise SystemExit(f"{db_path} does not exist; run `lamor-pubs sync` first")
    conn = db.connect(db_path)
    rows = db.load_publications(conn)
    result = build_export(rows, config)
    previous = _load_previous(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(dumps_entries(result.entries), encoding="utf-8")
    report_path.write_text(render_report(result, conn, previous, config), encoding="utf-8")
    conn.close()
    cats = Counter(e["category"] for e in result.entries)
    log.info("exported %d publications (%d journal, %d conference) from %d records; %d duplicate groups merged",
             len(result.entries), cats["journal"], cats["conference"], len(rows), len(result.merges))
    log.info("wrote %s and %s", json_path, report_path)
    return result
