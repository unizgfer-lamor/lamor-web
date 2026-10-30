"""Thin client for the public CRORIS APIs (CROSBI publications and events), HAL+JSON, no auth."""

from __future__ import annotations

import logging
import time
from typing import Any

from lamor_tools.common import HttpClient

log = logging.getLogger(__name__)

CROSBI_API = "https://www.croris.hr/crosbi-api"
EVENTS_API = "https://www.croris.hr/dogadanja-api"


class CrorisError(RuntimeError):
    pass


class CrorisClient:
    """All calls go through one throttled HttpClient (never parallel). 404 -> None."""

    def __init__(self, per_minute: float = 90, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(per_minute=per_minute, retries=5, timeout=60)
        self.http.session.headers["Accept"] = "application/hal+json, application/json"
        self.requests = 0

    def get_json(self, url: str) -> Any | None:
        self.requests += 1
        resp = self.http.get(url)
        if resp.status_code == 404:
            return None
        try:
            data = resp.json()
        except ValueError as exc:
            raise CrorisError(f"GET {url} -> {resp.status_code}, not JSON: {resp.text[:200]!r}") from exc
        if resp.status_code >= 400:
            raise CrorisError(f"GET {url} -> {resp.status_code}: {data}")
        if isinstance(data, dict) and data.get("type") == "RESOURCE_NOT_FOUND":
            return None
        return data

    def person(self, croris_id: int) -> dict[str, Any] | None:
        return self.get_json(f"{CROSBI_API}/osoba/{croris_id}")

    def publication(self, pub_id: int) -> dict[str, Any] | None:
        return self.get_json(f"{CROSBI_API}/publikacija/{pub_id}")

    def event(self, event_id: int) -> dict[str, Any] | None:
        return self.get_json(f"{EVENTS_API}/dogadanje/{event_id}")

    def _id_list(self, name: str, waits: tuple[float, ...] = (60, 120)) -> list[int]:
        """The change lists are throttled separately: HTTP 400 'try again in a few minutes' -> wait, retry."""
        for attempt in range(len(waits) + 1):
            try:
                data = self.get_json(f"{CROSBI_API}/publikacija/{name}") or {}
                break
            except CrorisError as exc:
                if attempt == len(waits) or "400" not in str(exc):
                    raise
                log.warning("%s temporarily unavailable, retrying in %.0fs", name, waits[attempt])
                time.sleep(waits[attempt])
        ids = (data.get("_embedded") or {}).get("publikacijeIds") or []
        return [int(i) for i in ids]

    def updated_ids(self) -> list[int]:
        """Publications updated in the last 30 days (rolling window, all of CRORIS)."""
        return self._id_list("updated-last-month")

    def created_ids(self) -> list[int]:
        return self._id_list("created-last-month")

    def deleted_ids(self) -> list[int]:
        return self._id_list("deleted-last-month")
