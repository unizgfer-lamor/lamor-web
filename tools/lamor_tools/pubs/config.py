"""Paths and configuration for the publications pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lamor_tools.common import CACHE_DIR, DATA_DIR, TOOLS_DIR

PUBS_DIR = TOOLS_DIR / "publications"
CONFIG_PATH = PUBS_DIR / "config.yaml"
DB_PATH = PUBS_DIR / "lamor-pubs.sqlite"
REPORT_PATH = PUBS_DIR / "report.md"
PDFMAP_REPORT_PATH = PUBS_DIR / "pdfmap-report.md"
PUBLICATIONS_JSON = DATA_DIR / "publications.json"
OVERRIDES_YAML = DATA_DIR / "publication-overrides.yaml"
EXTRA_YAML = DATA_DIR / "publications-extra.yaml"
OLD_SITE_CACHE = CACHE_DIR / "old-site" / "publications"


@dataclass
class Researcher:
    id: int
    name: str


@dataclass
class Config:
    researchers: list[Researcher]
    lab_members: list[str] = field(default_factory=list)
    min_year: int = 2010
    author_role_ids: list[int] = field(default_factory=lambda: [905])
    include_status: list[str] = field(default_factory=lambda: ["objavljeno", "prihvaćeno"])
    categories: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    exclude_tip_contains: list[str] = field(default_factory=list)
    requests_per_minute: float = 90
    full_sync_after_days: int = 28
    venue_acronyms: list[str] = field(default_factory=list)
    venue_overrides: dict[str, str] = field(default_factory=dict)
    place_names: dict[str, str] = field(default_factory=dict)

    @property
    def researcher_ids(self) -> set[int]:
        return {r.id for r in self.researchers}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        data = dict(data)
        data["researchers"] = [Researcher(int(r["id"]), str(r["name"])) for r in data.get("researchers") or []]
        for key in ("lab_members", "venue_acronyms", "exclude_tip_contains"):
            data[key] = [str(x) for x in data.get(key) or []]
        for key in ("venue_overrides", "place_names"):
            data[key] = {str(k): str(v) for k, v in (data.get(key) or {}).items()}
        data["categories"] = {
            str(cat): {k: [str(x) for x in v or []] for k, v in (rules or {}).items()}
            for cat, rules in (data.get("categories") or {}).items()
        }
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: Path = CONFIG_PATH) -> Config:
    return Config.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
