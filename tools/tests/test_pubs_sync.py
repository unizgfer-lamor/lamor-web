"""Tests for `lamor-pubs sync` against a fake CRORIS client (no network)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from lamor_tools.pubs import db
from lamor_tools.pubs.config import Config
from lamor_tools.pubs.sync import run_sync

FIXTURES = Path(__file__).parent / "fixtures" / "pubs"
CONFIG = Config.from_dict({"researchers": [{"id": 44396, "name": "Petrović, Luka"}], "requests_per_minute": 1000})


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self) -> None:
        self.pubs = {p["crosbiId"]: p for p in (fixture("publication-journal-282380"),
                                               fixture("publication-conference-678831"))}
        self.events = {166921: fixture("event-166921")}
        self.listed = [282380, 678831]
        self.updated: list[int] = []
        self.deleted: list[int] = []
        self.calls: list[str] = []
        self.requests = 0
        self.fail: set[int] = set()

    def person(self, croris_id):
        self.calls.append(f"person/{croris_id}")
        return {"crorisId": croris_id, "ime": "Luka", "prezime": "Petrović",
                "publikacijaList": [{"id": i} for i in self.listed]}

    def publication(self, pub_id):
        self.calls.append(f"publikacija/{pub_id}")
        if pub_id in self.fail:
            raise RuntimeError("boom")
        return copy.deepcopy(self.pubs.get(pub_id))

    def event(self, event_id):
        self.calls.append(f"dogadanje/{event_id}")
        return self.events.get(event_id)

    def updated_ids(self):
        return list(self.updated)

    def deleted_ids(self):
        return list(self.deleted)


def test_sync_incremental_flow(tmp_path):
    path = tmp_path / "pubs.sqlite"
    client = FakeClient()
    stats = run_sync(config=CONFIG, db_path=path, client=client)
    assert (stats.listed, stats.fetched, stats.new, stats.errors) == (2, 2, 2, 0)
    assert client.calls.count("dogadanje/166921") == 1
    conn = db.connect(path)
    run = conn.execute("SELECT * FROM sync_run").fetchone()
    assert run["mode"] == "full" and run["finished_at"]  # first run is always full
    first_changed = conn.execute("SELECT changed_at FROM publication WHERE id = 282380").fetchone()[0]
    conn.close()

    # Second run: incremental, nothing new, nothing updated -> no detail fetches at all.
    client.calls.clear()
    stats = run_sync(config=CONFIG, db_path=path, client=client)
    assert stats.fetched == 0 and not any(c.startswith("publikacija/") for c in client.calls)

    # Updated in CRORIS -> refetched; content change bumps changed_at; unchanged stays.
    client.pubs[282380]["volumen"] = "99"
    client.updated = [282380, 678831, 5]
    stats = run_sync(config=CONFIG, db_path=path, client=client)
    assert (stats.fetched, stats.changed) == (2, 1)
    conn = db.connect(path)
    assert conn.execute("SELECT changed_at FROM publication WHERE id = 282380").fetchone()[0] >= first_changed
    conn.close()

    # Dropped from the researcher list -> orphaned; back in the list -> not orphaned.
    client.updated = []
    client.listed = [282380]
    run_sync(config=CONFIG, db_path=path, client=client)
    conn = db.connect(path)
    assert conn.execute("SELECT orphaned FROM publication WHERE id = 678831").fetchone()[0] == 1
    conn.close()
    client.listed = [282380, 678831]
    client.deleted = [678831]  # on the deleted list but still listed and fetchable -> stays alive
    run_sync(config=CONFIG, db_path=path, client=client)
    conn = db.connect(path)
    assert tuple(conn.execute("SELECT orphaned, deleted FROM publication WHERE id = 678831").fetchone()) == (0, 0)
    conn.close()

    # Deleted and no longer listed -> deleted flag.
    client.listed = [282380]
    run_sync(config=CONFIG, db_path=path, client=client)
    conn = db.connect(path)
    assert tuple(conn.execute("SELECT orphaned, deleted FROM publication WHERE id = 678831").fetchone()) == (1, 1)
    conn.close()


def test_sync_errors_are_counted_and_dry_run_writes_nothing(tmp_path):
    path = tmp_path / "pubs.sqlite"
    client = FakeClient()
    client.fail = {678831}
    stats = run_sync(config=CONFIG, db_path=path, client=client)
    assert (stats.fetched, stats.errors) == (1, 1)
    conn = db.connect(path)
    assert db.known_publication_ids(conn) == {282380}
    runs_before = conn.execute("SELECT COUNT(*) FROM sync_run").fetchone()[0]
    conn.close()

    client.fail = set()
    stats = run_sync(config=CONFIG, db_path=path, client=client, dry_run=True)
    assert stats.new == 1  # the failed one is retried as new...
    conn = db.connect(path)
    assert db.known_publication_ids(conn) == {282380}  # ...but nothing was written
    assert conn.execute("SELECT COUNT(*) FROM sync_run").fetchone()[0] == runs_before
    conn.close()


def test_limit_runs_do_not_count_as_successful(tmp_path):
    path = tmp_path / "pubs.sqlite"
    run_sync(config=CONFIG, db_path=path, client=FakeClient(), limit=1)
    conn = db.connect(path)
    assert conn.execute("SELECT mode FROM sync_run").fetchone()[0] == "full+limit"
    assert db.last_successful_run(conn) is None
    conn.close()


def test_change_list_failure_is_not_fatal(tmp_path):
    path = tmp_path / "pubs.sqlite"
    client = FakeClient()
    run_sync(config=CONFIG, db_path=path, client=client)  # first (full) run succeeds

    def unavailable():
        raise RuntimeError("400 try again in a few minutes")

    client.updated_ids = unavailable
    client.listed = [282380, 678831, 282305]
    client.pubs[282305] = fixture("publication-journal-article-282305")
    stats = run_sync(config=CONFIG, db_path=path, client=client)
    assert (stats.new, stats.errors) == (1, 1)
    conn = db.connect(path)
    assert conn.execute("SELECT mode FROM sync_run ORDER BY id DESC").fetchone()[0] == "incremental+nolists"
    assert db.last_successful_run(conn)["mode"] == "full"
    conn.close()
