"""Shared helpers for the LAMOR tooling: paths, a polite HTTP client, slugs and YAML output."""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
import yaml

log = logging.getLogger("lamor_tools")

TOOLS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TOOLS_DIR.parent
CACHE_DIR = TOOLS_DIR / ".cache"
CONTENT_DIR = REPO_ROOT / "src" / "content"
DATA_DIR = REPO_ROOT / "src" / "data"
PUBLIC_DIR = REPO_ROOT / "public"
REPORTS_DIR = TOOLS_DIR / "reports"

USER_AGENT = "LAMOR-website-tools/0.1 (+https://lamor.fer.hr; lamor@fer.hr)"


class RateLimiter:
    """Allows at most `per_minute` calls per minute, spaced evenly."""

    def __init__(self, per_minute: float) -> None:
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.interval


class HttpClient:
    """requests.Session with throttling and retry/backoff on 429, 5xx and network errors."""

    def __init__(self, per_minute: float = 60, retries: int = 5, timeout: float = 60) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.limiter = RateLimiter(per_minute)
        self.retries = retries
        self.timeout = timeout

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        delay = 2.0
        for attempt in range(1, self.retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.retries:
                    raise
                log.warning("%s %s failed (%s), retry %d in %.0fs", method, url, exc, attempt, delay)
            else:
                if resp.status_code != 429 and resp.status_code < 500:
                    return resp
                if attempt == self.retries:
                    resp.raise_for_status()
                log.warning("%s %s -> %d, retry %d in %.0fs", method, url, resp.status_code, attempt, delay)
            time.sleep(delay)
            delay = min(delay * 2, 120)
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)


# Croatian letters that NFKD does not reduce to ASCII on its own (đ) or that we want spelled out.
_TRANSLIT = str.maketrans({"đ": "dj", "Đ": "Dj", "ß": "ss", "æ": "ae", "Æ": "Ae", "ø": "o", "Ø": "O"})


def ascii_fold(text: str) -> str:
    text = text.translate(_TRANSLIT)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def slugify(text: str, max_len: int = 60) -> str:
    """'Prof. Ivan Petrović receives…' -> 'prof-ivan-petrovic-receives'; cut on a word boundary."""
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold(text).lower()).strip("-")
    if len(slug) > max_len:
        cut = slug[: max_len + 1]
        slug = cut[: cut.rfind("-")] if "-" in cut else slug[:max_len]
    return slug or "item"


def normalize_title(text: str) -> str:
    """Lowercase ASCII alphanumerics only, for duplicate detection and fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", " ", ascii_fold(text).lower()).strip()


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def _datetime_representer(dumper: yaml.SafeDumper, value: datetime) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", value.isoformat())


def _date_representer(dumper: yaml.SafeDumper, value: date) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", value.isoformat())


_Dumper.add_representer(str, _str_representer)
_Dumper.add_representer(datetime, _datetime_representer)
_Dumper.add_representer(date, _date_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str = "") -> None:
    """Write a Markdown file with YAML frontmatter, dropping None/empty values."""
    fm = {k: v for k, v in frontmatter.items() if v not in (None, "", [], {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\n{dump_yaml(fm)}---\n"
    body = body.strip()
    if body:
        text += f"\n{body}\n"
    path.write_text(text, encoding="utf-8")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
