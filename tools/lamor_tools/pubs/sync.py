"""`lamor-pubs sync`: download raw CRORIS records into the SQLite database (network only).

Incremental by default: list every researcher, fetch details of ids we have never seen plus known
ids that CRORIS reports as updated in the last 30 days. `--full` (or a last successful run older
than `full_sync_after_days`) refetches every listed publication and every referenced event.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lamor_tools.pubs import db
from lamor_tools.pubs.config import DB_PATH, Config, load_config
from lamor_tools.pubs.croris import CrorisClient

log = logging.getLogger(__name__)

COMMIT_EVERY = 25


@dataclass
class SyncStats:
    listed: int = 0
    fetched: int = 0
    new: int = 0
    changed: int = 0
    deleted: int = 0
    errors: int = 0
    events_fetched: int = 0


def _needs_full(conn: sqlite3.Connection, max_age_days: int) -> tuple[bool, str]:
    last = db.last_successful_run(conn)
    if last is None:
        return True, "no previous successful run"
    finished = datetime.fromisoformat(last["finished_at"])
    if datetime.now(UTC) - finished > timedelta(days=max_age_days):
        return True, f"last successful run {last['finished_at']} is older than {max_age_days} days"
    return False, ""


def run_sync(
    *,
    full: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    config: Config | None = None,
    db_path: Path = DB_PATH,
    client: CrorisClient | None = None,
) -> SyncStats:
    config = config or load_config()
    file_conn = db.connect(db_path)
    conn = db.memory_copy(file_conn) if dry_run else file_conn
    if dry_run:
        file_conn.close()
        log.info("dry run: working on an in-memory copy, nothing will be written")
    client = client or CrorisClient(per_minute=config.requests_per_minute)
    stats = SyncStats()

    if not full:
        full, why = _needs_full(conn, config.full_sync_after_days)
        if full:
            log.info("switching to a full sync: %s", why)
    mode = ("full" if full else "incremental") + ("+limit" if limit is not None else "")
    started = db.now_iso()
    run_id = conn.execute("INSERT INTO sync_run (started_at, mode) VALUES (?, ?)", (started, mode)).lastrowid
    conn.commit()
    t0 = time.monotonic()

    # 1. researcher lists -> union of publication ids (a listing failure is fatal: we would mis-orphan)
    listed: set[int] = set()
    for r in config.researchers:
        person = client.person(r.id)
        if person is None:
            raise RuntimeError(f"CRORIS person {r.id} ({r.name}) not found")
        ids = {int(p["id"]) for p in person.get("publikacijaList") or [] if p.get("id") is not None}
        name = f"{person.get('prezime', '')}, {person.get('ime', '')}".strip(", ") or r.name
        db.set_researcher_list(conn, r.id, name, ids, db.now_iso())
        log.info("researcher %s (%d): %d publications listed", name, r.id, len(ids))
        listed |= ids
    stats.listed = len(listed)
    conn.commit()
    log.info("%d distinct publication ids listed", len(listed))

    # 2. rolling change lists (throttled separately by CRORIS; a failure is logged, not fatal)
    known = db.known_publication_ids(conn)
    updated: set[int] = set()
    deleted_list: set[int] = set()
    try:
        updated = set(client.updated_ids()) & known
        deleted_list = set(client.deleted_ids()) & known
        log.info("CRORIS change lists: %d of our known ids updated, %d deleted in the last 30 days",
                 len(updated), len(deleted_list))
    except Exception as exc:
        stats.errors += 1
        if not full:
            mode += "+nolists"  # updates may have been missed: does not count as a successful run
        log.error("could not fetch the CRORIS change lists (%s); only new ids are fetched this time", exc)

    # 3. what to fetch: new ids first, then updated, then (full) everything else listed
    new_ids = sorted(listed - known)
    if full:
        rest = sorted((listed & known) - updated)
        to_fetch = new_ids + sorted(updated & listed) + rest
    else:
        to_fetch = new_ids + sorted(updated & listed)
    # Deleted-list ids still in a researcher list are refetched to see whether they really are gone.
    to_fetch += sorted((deleted_list & listed) - set(to_fetch))
    if limit is not None:
        to_fetch = to_fetch[:limit]
    log.info("fetching %d publication details (%d new)%s", len(to_fetch), len(new_ids),
             f" [limit {limit}]" if limit is not None else "")

    # 4. fetch details
    event_ids_seen: set[int] = set()
    known_events = db.known_event_ids(conn)
    events_to_fetch: list[int] = []
    for n, pub_id in enumerate(to_fetch, start=1):
        try:
            raw = client.publication(pub_id)
            ts = db.now_iso()
            if raw is None:
                log.warning("publication %d: 404, marking deleted", pub_id)
                if conn.execute("UPDATE publication SET deleted = 1 WHERE id = ?", (pub_id,)).rowcount:
                    stats.deleted += 1
                continue
            if int(raw.get("crosbiId", -1)) != pub_id:
                raise ValueError(f"crosbiId {raw.get('crosbiId')!r} does not match requested id")
            result = db.upsert_publication(conn, raw, ts)
            stats.fetched += 1
            if result == "new":
                stats.new += 1
            elif result == "changed":
                stats.changed += 1
            log.debug("publication %d: %s (%s)", pub_id, result, raw.get("naslov", "")[:70])
            for eid in db.publication_event_ids(raw):
                if eid in event_ids_seen:
                    continue
                event_ids_seen.add(eid)
                if full or eid not in known_events:
                    events_to_fetch.append(eid)
        except Exception as exc:  # one bad record must not stop the run
            stats.errors += 1
            log.error("publication %d failed: %s", pub_id, exc)
        if n % COMMIT_EVERY == 0:
            conn.commit()
        if n % 50 == 0:
            elapsed = time.monotonic() - t0
            log.info("  %d/%d details fetched (%.0fs elapsed)", n, len(to_fetch), elapsed)
    conn.commit()

    # Events referenced by stored publications that were never fetched (e.g. after an interruption).
    if limit is None:
        missing = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT event_id FROM publication_event WHERE event_id NOT IN "
                "(SELECT id FROM event WHERE raw_json IS NOT NULL)"
            )
        }
        events_to_fetch += sorted(missing - set(events_to_fetch))

    # 5. events, each once per run
    if events_to_fetch:
        log.info("fetching %d events", len(events_to_fetch))
    for n, eid in enumerate(events_to_fetch, start=1):
        try:
            raw = client.event(eid)
            if raw is None:
                log.warning("event %d: 404", eid)
                continue
            db.upsert_event(conn, eid, raw)
            stats.events_fetched += 1
        except Exception as exc:
            stats.errors += 1
            log.error("event %d failed: %s", eid, exc)
        if n % COMMIT_EVERY == 0:
            conn.commit()
    conn.commit()

    # 6. orphaned / deleted flags (based on the complete researcher listing)
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS listed_ids (id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM listed_ids")
    conn.executemany("INSERT INTO listed_ids (id) VALUES (?)", [(i,) for i in listed])
    orphaned = conn.execute(
        "UPDATE publication SET orphaned = 1 WHERE orphaned = 0 AND id NOT IN (SELECT id FROM listed_ids)"
    ).rowcount
    revived = conn.execute(
        "UPDATE publication SET orphaned = 0 WHERE orphaned = 1 AND id IN (SELECT id FROM listed_ids)"
    ).rowcount
    # An id on CRORIS' deleted list that no researcher lists any more is gone; one that is still listed
    # (and was refetched above without a 404) stays alive.
    for pub_id in sorted(deleted_list - listed):
        if conn.execute("UPDATE publication SET deleted = 1 WHERE id = ? AND deleted = 0", (pub_id,)).rowcount:
            stats.deleted += 1
    conn.execute("DROP TABLE listed_ids")
    if orphaned or revived:
        log.info("%d publications newly orphaned, %d back in a researcher list", orphaned, revived)

    conn.execute(
        """UPDATE sync_run SET finished_at = ?, mode = ?, listed = ?, fetched = ?, new = ?, changed = ?,
               deleted = ?, errors = ? WHERE id = ?""",
        (db.now_iso(), mode, stats.listed, stats.fetched, stats.new, stats.changed, stats.deleted, stats.errors,
         run_id),
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0]
    conn.close()
    log.info(
        "%s sync done in %.0fs (%d requests): listed %d, fetched %d (new %d, changed %d), deleted %d, "
        "events %d, errors %d; %d publications in the database%s",
        mode, time.monotonic() - t0, client.requests, stats.listed, stats.fetched, stats.new, stats.changed,
        stats.deleted, stats.events_fetched, stats.errors, total, " (dry run, discarded)" if dry_run else "",
    )
    return stats
