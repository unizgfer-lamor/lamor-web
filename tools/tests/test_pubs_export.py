"""Tests for classification, entry building and second-level dedup (lamor_tools.pubs.export) and the DB layer."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from lamor_tools.pubs import db
from lamor_tools.pubs.config import Config
from lamor_tools.pubs.export import build_entry, build_export, classify, dedup, dumps_entries
from lamor_tools.pubs.fields import lab_keys

FIXTURES = Path(__file__).parent / "fixtures" / "pubs"

CONFIG = Config.from_dict(
    {
        "researchers": [
            {"id": 14743, "name": "Petrović, Ivan"},
            {"id": 29108, "name": "Marković, Ivan"},
            {"id": 44396, "name": "Petrović, Luka"},
            {"id": 26770, "name": "Seder, Marija"},
        ],
        "lab_members": ["Peršić, Juraj"],
        "min_year": 2010,
        "author_role_ids": [905],
        "include_status": ["objavljeno", "prihvaćeno"],
        "categories": {
            "journal": {"vrsta": ["prilog u časopisu"], "exclude_tip": ["uvodnik", "prikaz, osvrt, kritika"]},
            "conference": {"vrsta": ["prilog sa skupa (u zborniku)"], "exclude_tip": []},
        },
        "exclude_tip_contains": ["sažetak"],
        "venue_acronyms": ["IEEE", "ECMR"],
        "venue_overrides": {"Advanced Robotics": "Advanced Robotics (Taylor & Francis)"},
        "place_names": {"Prag": "Prague"},
    }
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    for name in ("publication-journal-282380", "publication-journal-article-282305", "publication-conference-678831",
                 "publication-thesis-451329", "publication-unpublished-954023", "publication-abstract-900279"):
        assert db.upsert_publication(c, fixture(name)) == "new"
    db.upsert_event(c, 166921, fixture("event-166921"))
    yield c
    c.close()


def rows_by_id(conn) -> dict[int, dict]:
    return {r["id"]: r for r in db.load_publications(conn)}


# ----------------------------------------------------------------------------- DB


def test_upsert_is_idempotent_and_tracks_changes(conn):
    raw = fixture("publication-journal-282380")
    assert db.upsert_publication(conn, raw) == "same"
    changed = copy.deepcopy(raw)
    changed["volumen"] = "36"
    assert db.upsert_publication(conn, changed) == "changed"
    row = conn.execute("SELECT * FROM publication WHERE id = 282380").fetchone()
    assert row["content_hash"] == db.content_hash(changed)
    assert row["doi"] == "10.1080/01691864.2020.1819874"
    assert row["year"] == 2021 and row["bib_irb_id"] == 1076273
    people = conn.execute("SELECT person_id, role_id FROM publication_person WHERE pub_id = 282380").fetchall()
    assert {(p[0], p[1]) for p in people} >= {(14743, 905), (29108, 905), (44396, 905)}


def test_content_hash_is_key_order_independent():
    assert db.content_hash({"a": 1, "b": [1, 2]}) == db.content_hash({"b": [1, 2], "a": 1})


def test_migrations_set_user_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_events_linked(conn):
    rows = rows_by_id(conn)
    assert [e["id"] for e in rows[678831]["events"]] == [166921]
    assert rows[678831]["events"][0]["place"] == "Prag"


# ----------------------------------------------------------------------------- classification


def test_classify(conn):
    rows = rows_by_id(conn)
    assert classify(rows[282380], CONFIG)[0] == "journal"
    assert classify(rows[282305], CONFIG)[0] == "journal"
    assert classify(rows[678831], CONFIG)[0] == "conference"
    assert classify(rows[451329], CONFIG)[:2] == (None, "type (vrsta) not exported")
    assert classify(rows[954023], CONFIG)[:2] == (None, "type (vrsta) not exported")
    abstract = rows[900279]
    assert classify(abstract, CONFIG)[1].startswith("no lab researcher")  # real record, not ours
    as_ours = {**abstract, "people": [{"person_id": 44396, "role_id": 905, "ime": "Luka", "prezime": "Petrović"}]}
    assert classify(as_ours, CONFIG)[:2] == (None, "abstract (tip)")


def test_classify_role_status_year_and_flags(conn):
    row = rows_by_id(conn)[282380]
    mentor_only = {**row, "people": [{**p, "role_id": 907} for p in row["people"]]}
    assert classify(mentor_only, CONFIG)[1].startswith("no lab researcher as author")
    assert classify({**row, "status": "neobjavljeno"}, CONFIG)[:2] == (None, "status not exported")
    assert classify({**row, "status": None}, CONFIG)[0] == "journal"
    assert classify({**row, "status": "prihvaćeno"}, CONFIG)[0] == "journal"
    assert classify({**row, "year": 2009}, CONFIG)[1] == "before 2010"
    assert classify({**row, "deleted": 1}, CONFIG)[1] == "deleted in CRORIS"
    assert classify({**row, "orphaned": 1}, CONFIG)[1] == "no longer in any researcher's list"
    assert classify({**row, "tip": "uvodnik"}, CONFIG)[1] == "journal tip not exported"


# ----------------------------------------------------------------------------- entries


def test_build_entry_journal(conn):
    row = rows_by_id(conn)[282380]
    keys = lab_keys([r.name for r in CONFIG.researchers] + CONFIG.lab_members)
    entry = build_entry(row, "journal", CONFIG, keys)
    assert entry == {
        "id": 282380,
        "category": "journal",
        "year": 2021,
        "title": "Online Multi-Sensor Calibration Based on Moving Object Tracking",
        "authors": [
            {"name": "Peršić, Juraj", "lab": True},  # lab_members
            {"name": "Petrović, Luka", "lab": True},
            {"name": "Marković, Ivan", "lab": True},
            {"name": "Petrović, Ivan", "lab": True},
        ],
        "venue": "Advanced Robotics (Taylor & Francis)",  # re-cased, then venue_overrides
        "details": "vol. 35, no. 3-4, pp. 130–140",
        "doi": "10.1080/01691864.2020.1819874",
        "croris": 282380,
    }


def test_build_entry_conference(conn):
    row = rows_by_id(conn)[678831]
    entry = build_entry(row, "conference", CONFIG, lab_keys(["Seder, Marija"]))
    assert entry["venue"] == "ECMR 2019 – European Conference on Mobile Robots 2019"
    assert entry["details"] == "Prague, Czech Republic, 4.–6. September 2019, pp. 1–6"
    assert entry["doi"] == "10.1109/ecmr.2019.8870970"
    assert [a["lab"] for a in entry["authors"]] == [False, False, True, False]


def test_build_export_sorted_and_deterministic(conn):
    result = build_export(db.load_publications(conn), CONFIG)
    ids = [e["id"] for e in result.entries]
    assert ids == [282380, 282305, 678831]  # 2021 journal, 2020 journal, 2019 conference
    assert result.excluded[451329] == "type (vrsta) not exported"
    assert result.excluded[900279].startswith("no lab researcher")
    assert result.excluded[954023] == "type (vrsta) not exported"
    text = dumps_entries(result.entries)
    assert text.endswith("]\n") and "Peršić" in text
    assert text == dumps_entries(build_export(db.load_publications(conn), CONFIG).entries)


# ----------------------------------------------------------------------------- dedup


def _entry(pid, title="A Paper About Robots", year=2020, category="journal", doi=None):
    e = {"id": pid, "category": category, "year": year, "title": title, "authors": [], "venue": "", "details": ""}
    if doi:
        e["doi"] = doi
    return e


def _row(pid, status="objavljeno", doi=None, extra=0):
    raw = {"crosbiId": pid, **{f"f{i}": "x" for i in range(extra)}}
    return {"id": pid, "status": status, "doi": doi, "raw": raw}


def test_dedup_prefers_published_then_doi_then_fields_then_lowest_id():
    entries = [_entry(1), _entry(2), _entry(3), _entry(4)]
    rows = {1: _row(1, "prihvaćeno", "10.1000/a", extra=9), 2: _row(2, doi=None, extra=9),
            3: _row(3, doi="10.1000/a", extra=1), 4: _row(4, doi="10.1000/a", extra=1)}
    kept, merges, _ = dedup(entries, rows)
    assert [e["id"] for e in kept] == [3]
    assert merges[0]["kept"] == 3 and merges[0]["dropped"] == [1, 2, 4]

    rows[4] = _row(4, doi="10.1000/a", extra=5)
    kept, _, _ = dedup(entries, rows)
    assert [e["id"] for e in kept] == [4]


def test_dedup_by_doi_but_never_across_categories():
    entries = [
        _entry(10, "Fast SLAM in Large Spaces", doi="https://doi.org/10.1000/X"),
        _entry(11, "Fast SLAM in large spaces.", year=2021, doi="10.1000/x"),  # same DOI, other year: merged
        _entry(12, "Fast SLAM in Large Spaces", category="conference", doi="10.1000/x"),
    ]
    rows = {i: _row(i, doi="10.1000/x") for i in (10, 11, 12)}
    kept, merges, conflicts = dedup(entries, rows)
    assert sorted(e["id"] for e in kept) == [10, 12]
    assert merges == [{"kept": 10, "dropped": [11], "by": "doi", "title": "Fast SLAM in Large Spaces",
                       "year": 2020, "category": "journal"}]
    assert conflicts == []


def test_dedup_same_doi_different_titles_is_a_conflict():
    entries = [_entry(20, "Visual odometry for cars", doi="10.1000/book"),
               _entry(21, "Grasp planning with tactile sensing", doi="10.1000/book")]
    kept, merges, conflicts = dedup(entries, {i: _row(i, doi="10.1000/book") for i in (20, 21)})
    assert len(kept) == 2 and merges == []
    assert conflicts[0]["ids"] == [20, 21]
