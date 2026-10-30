"""SQLite store for raw CRORIS records (tools/publications/lamor-pubs.sqlite).

The database keeps the raw JSON of every publication and event so that `export` can run offline
and be re-run with different rules. Schema changes go through MIGRATIONS (PRAGMA user_version).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lamor_tools.common import normalize_title

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE researcher (
        croris_id INTEGER PRIMARY KEY,
        name TEXT,
        last_listed_at TEXT
    );
    CREATE TABLE researcher_publication (
        researcher_id INTEGER NOT NULL,
        pub_id INTEGER NOT NULL,
        PRIMARY KEY (researcher_id, pub_id)
    );
    CREATE TABLE publication (
        id INTEGER PRIMARY KEY,
        bib_irb_id INTEGER,
        doi TEXT,
        title TEXT,
        title_norm TEXT,
        year INTEGER,
        vrsta TEXT,
        tip TEXT,
        status TEXT,
        raw_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        first_seen_at TEXT,
        fetched_at TEXT,
        changed_at TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        orphaned INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX publication_doi ON publication (doi);
    CREATE INDEX publication_title_year ON publication (title_norm, year);
    CREATE INDEX publication_bib_irb_id ON publication (bib_irb_id);
    CREATE TABLE publication_person (
        pub_id INTEGER NOT NULL,
        person_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        ime TEXT,
        prezime TEXT,
        PRIMARY KEY (pub_id, person_id, role_id)
    );
    CREATE INDEX publication_person_person ON publication_person (person_id, role_id);
    CREATE TABLE event (
        id INTEGER PRIMARY KEY,
        name TEXT,
        acronym TEXT,
        start_date TEXT,
        end_date TEXT,
        place TEXT,
        country TEXT,
        raw_json TEXT,
        fetched_at TEXT
    );
    CREATE TABLE publication_event (
        pub_id INTEGER NOT NULL,
        event_id INTEGER NOT NULL,
        PRIMARY KEY (pub_id, event_id)
    );
    CREATE TABLE sync_run (
        id INTEGER PRIMARY KEY,
        started_at TEXT,
        finished_at TEXT,
        mode TEXT,
        listed INTEGER,
        fetched INTEGER,
        new INTEGER,
        changed INTEGER,
        deleted INTEGER,
        errors INTEGER
    );
    """,
]


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")


def memory_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    """In-memory clone, used by --dry-run so that nothing reaches the file."""
    mem = sqlite3.connect(":memory:")
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    return mem


# ----------------------------------------------------------------------------- helpers


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def normalize_doi(doi: str | None) -> str | None:
    """'https://doi.org/10.1109/TRO.2021.3061364 ' -> '10.1109/tro.2021.3061364' (lowercase)."""
    bare = bare_doi(doi)
    return bare.lower() if bare else None


def bare_doi(doi: str | None) -> str | None:
    """Strip URL/'doi:' prefixes and whitespace; None unless it looks like a DOI (10.xxxx/...)."""
    if not doi:
        return None
    text = doi.strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?(?:www\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I).strip()
    match = re.search(r"10\.\d{4,9}/\S+", text)
    if not match:
        return None
    return match.group(0).rstrip(".,;")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def publication_people(raw: dict[str, Any]) -> list[dict[str, Any]]:
    osobe = ((raw.get("osobeResources") or {}).get("_embedded") or {}).get("osobe") or []
    people = []
    for o in osobe:
        pid = _int_or_none(o.get("crorisId"))
        role = _int_or_none((o.get("funkcija") or {}).get("id"))
        if pid is None or role is None:
            continue
        people.append({"person_id": pid, "role_id": role, "ime": o.get("ime"), "prezime": o.get("prezime")})
    return people


def publication_event_ids(raw: dict[str, Any]) -> list[int]:
    ids = []
    for s in raw.get("skup") or []:
        eid = _int_or_none(s.get("cfEventId"))
        if eid is None and s.get("href"):
            eid = _int_or_none(str(s["href"]).rstrip("/").rsplit("/", 1)[-1])
        if eid is not None and eid not in ids:
            ids.append(eid)
    return ids


# ----------------------------------------------------------------------------- writes


def upsert_publication(conn: sqlite3.Connection, raw: dict[str, Any], ts: str | None = None) -> str:
    """Store a publication detail record. Returns 'new', 'changed' or 'same'."""
    ts = ts or now_iso()
    pub_id = int(raw["crosbiId"])
    digest = content_hash(raw)
    row = conn.execute("SELECT content_hash FROM publication WHERE id = ?", (pub_id,)).fetchone()
    title = raw.get("naslov") or ""
    fields = {
        "bib_irb_id": _int_or_none(raw.get("bibIrbId")),
        "doi": normalize_doi(raw.get("doi")),
        "title": title,
        "title_norm": normalize_title(title),
        "year": _int_or_none(raw.get("godina")),
        "vrsta": raw.get("vrsta"),
        "tip": raw.get("tip"),
        "status": raw.get("status"),
        "raw_json": canonical_json(raw),
        "content_hash": digest,
    }
    if row is None:
        conn.execute(
            """INSERT INTO publication (id, bib_irb_id, doi, title, title_norm, year, vrsta, tip, status,
                   raw_json, content_hash, first_seen_at, fetched_at, changed_at, deleted, orphaned)
               VALUES (:id, :bib_irb_id, :doi, :title, :title_norm, :year, :vrsta, :tip, :status,
                   :raw_json, :content_hash, :ts, :ts, :ts, 0, 0)""",
            {"id": pub_id, "ts": ts, **fields},
        )
        result = "new"
    elif row["content_hash"] != digest:
        conn.execute(
            """UPDATE publication SET bib_irb_id = :bib_irb_id, doi = :doi, title = :title,
                   title_norm = :title_norm, year = :year, vrsta = :vrsta, tip = :tip, status = :status,
                   raw_json = :raw_json, content_hash = :content_hash, fetched_at = :ts, changed_at = :ts,
                   deleted = 0
               WHERE id = :id""",
            {"id": pub_id, "ts": ts, **fields},
        )
        result = "changed"
    else:
        conn.execute("UPDATE publication SET fetched_at = ?, deleted = 0 WHERE id = ?", (ts, pub_id))
        result = "same"

    conn.execute("DELETE FROM publication_person WHERE pub_id = ?", (pub_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO publication_person (pub_id, person_id, role_id, ime, prezime) VALUES (?, ?, ?, ?, ?)",
        [(pub_id, p["person_id"], p["role_id"], p["ime"], p["prezime"]) for p in publication_people(raw)],
    )
    conn.execute("DELETE FROM publication_event WHERE pub_id = ?", (pub_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO publication_event (pub_id, event_id) VALUES (?, ?)",
        [(pub_id, eid) for eid in publication_event_ids(raw)],
    )
    return result


def localized(value: Any) -> str | None:
    """CRORIS multilingual value (string, {naziv}, or list of {cfLangCode, naziv, original}) -> text."""
    if value is None or isinstance(value, str):
        return (value or "").strip() or None
    if isinstance(value, dict):
        return localized(value.get("naziv"))
    if isinstance(value, list):
        items = [v for v in value if (isinstance(v, str) and v.strip()) or (isinstance(v, dict) and v.get("naziv"))]
        if not items:
            return None
        chosen = next((v for v in items if isinstance(v, dict) and v.get("original")), None)
        chosen = chosen or next((v for v in items if isinstance(v, dict) and v.get("cfLangCode") == "en"), None)
        return localized(chosen or items[0])
    return str(value)


def event_fields(raw: dict[str, Any]) -> dict[str, Any]:
    name = localized(raw.get("naziv"))
    places = raw.get("mjestoOdrzavanja") or []
    place = country = None
    if places:
        first = places[0]
        place = ((first.get("mjesto") or {}).get("defaultNaziv")) or None
        drzava = first.get("drzava") or {}
        country = drzava.get("cfCountryCode") or drzava.get("cfName") or None
    return {
        "name": name,
        "acronym": localized(raw.get("akronim")),
        "start_date": _croris_date(raw.get("datumPocetka")),
        "end_date": _croris_date(raw.get("datumZavrsetka")),
        "place": place,
        "country": country,
    }


def _croris_date(text: Any) -> str | None:
    """'04.09.2024.' -> '2024-09-04'."""
    if not text:
        return None
    m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", str(text))
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def upsert_event(conn: sqlite3.Connection, event_id: int, raw: dict[str, Any], ts: str | None = None) -> None:
    ts = ts or now_iso()
    f = event_fields(raw)
    conn.execute(
        """INSERT INTO event (id, name, acronym, start_date, end_date, place, country, raw_json, fetched_at)
           VALUES (:id, :name, :acronym, :start_date, :end_date, :place, :country, :raw_json, :ts)
           ON CONFLICT (id) DO UPDATE SET name = :name, acronym = :acronym, start_date = :start_date,
               end_date = :end_date, place = :place, country = :country, raw_json = :raw_json, fetched_at = :ts""",
        {"id": event_id, "raw_json": canonical_json(raw), "ts": ts, **f},
    )


def set_researcher_list(conn: sqlite3.Connection, croris_id: int, name: str, pub_ids: set[int], ts: str) -> None:
    conn.execute(
        """INSERT INTO researcher (croris_id, name, last_listed_at) VALUES (?, ?, ?)
           ON CONFLICT (croris_id) DO UPDATE SET name = excluded.name, last_listed_at = excluded.last_listed_at""",
        (croris_id, name, ts),
    )
    conn.execute("DELETE FROM researcher_publication WHERE researcher_id = ?", (croris_id,))
    conn.executemany(
        "INSERT INTO researcher_publication (researcher_id, pub_id) VALUES (?, ?)",
        [(croris_id, pid) for pid in sorted(pub_ids)],
    )


# ----------------------------------------------------------------------------- reads


def known_publication_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM publication")}


def known_event_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT id FROM event WHERE raw_json IS NOT NULL")}


def last_successful_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Latest finished, unlimited run that was not swamped by errors (<= 10% of fetched details)."""
    return conn.execute(
        """SELECT * FROM sync_run WHERE finished_at IS NOT NULL AND mode NOT LIKE '%limit%'
               AND mode NOT LIKE '%nolists%' AND errors * 10 <= MAX(fetched, 10)
           ORDER BY finished_at DESC LIMIT 1"""
    ).fetchone()


def load_publications(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every publication row as a dict with parsed `raw`, `people` and `events`."""
    people: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute("SELECT * FROM publication_person ORDER BY pub_id, person_id, role_id"):
        people.setdefault(r["pub_id"], []).append(dict(r))
    events_by_id = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM event")}
    pub_events: dict[int, list[dict[str, Any]]] = {}
    for r in conn.execute("SELECT * FROM publication_event ORDER BY pub_id, event_id"):
        if r["event_id"] in events_by_id:
            pub_events.setdefault(r["pub_id"], []).append(events_by_id[r["event_id"]])
    rows = []
    for r in conn.execute("SELECT * FROM publication ORDER BY id"):
        row = dict(r)
        row["raw"] = json.loads(row.pop("raw_json"))
        row["people"] = people.get(row["id"], [])
        row["events"] = pub_events.get(row["id"], [])
        rows.append(row)
    return rows
