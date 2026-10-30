"""Download / decode assets into public/ and hand back root-relative, percent-encoded URLs.

Rules:
- an asset already on disk is never downloaded again (downloads go to <file>.part, then rename)
- old-site files (/images/<n>/..., /_news/...) keep their path below public/
- data: URIs are decoded (type sniffed from the bytes) and written under a caller-chosen name
- hotlinked external images are downloaded into public/images/<section>/
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests

from ..common import PUBLIC_DIR, HttpClient
from .links import OLD_HOSTS, encode_path

log = logging.getLogger("lamor_tools.migrate")

OLD_ORIGIN = "https://lamor.fer.hr"
DEAD_PREFIXES = ("/_download/repository/",)
FINAL_STATUSES = {401, 404, 410}
RETRY_ATTEMPTS = 3

MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"%PDF", "pdf"),
]
MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif",
            "image/webp": "webp", "image/svg+xml": "svg", "image/bmp": "bmp", "application/pdf": "pdf"}


def sniff_ext(data: bytes, mime: str | None = None) -> str:
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower()):
        return "svg"
    if mime:
        mime = mime.split(";")[0].strip().lower()
        if mime in MIME_EXT:
            return MIME_EXT[mime]
        guess = mimetypes.guess_extension(mime)
        if guess:
            return guess.lstrip(".")
    return "bin"


def decode_data_uri(uri: str) -> tuple[bytes, str]:
    """'data:image/png;base64,....' -> (bytes, 'png')."""
    m = re.match(r"data:([^;,]*)((?:;[^;,]*)*),(.*)", uri, re.S)
    if not m:
        raise ValueError("not a data URI")
    mime, params, payload = m.group(1), m.group(2), m.group(3)
    if ";base64" in params:
        payload = re.sub(r"\s+", "", payload)
        payload += "=" * (-len(payload) % 4)
        data = base64.b64decode(payload)
    else:
        data = unquote(payload).encode("latin-1")
    return data, sniff_ext(data, mime)


def url_for(dest: Path) -> str:
    """public/images/a b.png -> /images/a%20b.png"""
    return encode_path("/" + dest.relative_to(PUBLIC_DIR).as_posix())


@dataclass
class AssetStore:
    offline: bool = False
    http: HttpClient | None = None
    downloaded: list[tuple[str, int]] = field(default_factory=list)  # (dest, bytes)
    existing: set[str] = field(default_factory=set)
    written: list[tuple[str, int]] = field(default_factory=list)  # decoded data URIs
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)
    dead: list[str] = field(default_factory=list)
    _claimed: dict[str, str] = field(default_factory=dict)  # dest -> sha1 written this run

    def client(self) -> HttpClient:
        if self.http is None:
            self.http = HttpClient(per_minute=60)
        return self.http

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET with a few extra attempts (backoff 3s, 9s) on unexpected statuses that HttpClient
        does not retry itself; 401/404/410 are treated as final."""
        delay = 3.0
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            resp = self.client().get(url, **kwargs)
            if resp.status_code == 200 or resp.status_code in FINAL_STATUSES or attempt == RETRY_ATTEMPTS:
                return resp
            log.warning("GET %s -> %d, retry %d in %.0fs", url, resp.status_code, attempt, delay)
            resp.close()
            time.sleep(delay)
            delay *= 3
        raise RuntimeError("unreachable")

    @property
    def bytes_downloaded(self) -> int:
        return sum(n for _, n in self.downloaded)

    # -- low level ------------------------------------------------------------------------
    def _safe_dest(self, rel: str) -> Path:
        dest = (PUBLIC_DIR / rel.lstrip("/")).resolve()
        if PUBLIC_DIR.resolve() not in dest.parents:
            raise ValueError(f"unsafe asset path {rel!r}")
        return dest

    def download(self, url: str, dest: Path, expect_ext: str | None = None) -> bool:
        """Stream `url` to `dest` unless it already exists. Returns True if the file is there."""
        if dest.exists() and dest.stat().st_size > 0:
            self.existing.add(str(dest))
            return True
        if self.offline:
            self.failed.append((url, "offline"))
            return False
        part = dest.with_name(dest.name + ".part")
        try:
            resp = self._get(url, stream=True, timeout=120)
        except requests.RequestException as exc:
            self.failed.append((url, f"{type(exc).__name__}"))
            return False
        with resp:
            if resp.status_code != 200:
                self.failed.append((url, f"HTTP {resp.status_code}"))
                return False
            ctype = resp.headers.get("Content-Type", "")
            if expect_ext in ("png", "jpg", "gif", "webp", "svg") and "text/html" in ctype:
                self.failed.append((url, f"not an image ({ctype})"))
                return False
            size = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with part.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=256 * 1024):
                        fh.write(chunk)
                        size += len(chunk)
            except requests.RequestException as exc:
                part.unlink(missing_ok=True)
                self.failed.append((url, f"{type(exc).__name__} during download"))
                return False
        part.replace(dest)
        self.downloaded.append((str(dest), size))
        log.info("downloaded %s (%d bytes)", dest.relative_to(PUBLIC_DIR), size)
        return True

    def fetch_bytes(self, url: str) -> tuple[bytes, str] | None:
        """GET a small resource into memory (for hotlinked images whose type we need to sniff)."""
        if self.offline:
            self.failed.append((url, "offline"))
            return None
        try:
            resp = self._get(url, timeout=60)
        except requests.RequestException as exc:
            self.failed.append((url, type(exc).__name__))
            return None
        if resp.status_code != 200:
            self.failed.append((url, f"HTTP {resp.status_code}"))
            return None
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype:
            self.failed.append((url, f"not an image ({ctype})"))
            return None
        return resp.content, ctype

    def write_bytes(self, dest: Path, data: bytes, downloaded_from: str | None = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(data).hexdigest()
        key = str(dest)
        if key in self._claimed and self._claimed[key] != digest:
            raise ValueError(f"two different files want {dest}")
        self._claimed[key] = digest
        if dest.exists() and dest.read_bytes() == data:
            self.existing.add(key)
            return
        dest.write_bytes(data)
        if downloaded_from:
            self.downloaded.append((key, len(data)))
        else:
            self.written.append((key, len(data)))

    # -- high level -----------------------------------------------------------------------
    def old_site_file(self, path: str) -> str | None:
        """'/images/50018601/logo(1).png' -> download to public/images/... ; returns encoded URL."""
        decoded = unquote(urlsplit(path).path)
        if decoded.startswith(DEAD_PREFIXES):
            self.dead.append(decoded)
            return None
        dest = self._safe_dest(decoded)
        if self.offline:
            if dest.exists():
                self.existing.add(str(dest))
            return url_for(dest)
        ok = self.download(OLD_ORIGIN + quote(decoded, safe="/-._~()!$&'*+,;=:@"), dest)
        return url_for(dest) if ok else None

    def data_uri(self, uri: str, dest_stem: Path) -> str | None:
        """Decode a data: URI to <dest_stem>.<ext> (ext sniffed). Returns the URL."""
        try:
            data, ext = decode_data_uri(uri)
        except (ValueError, binascii.Error) as exc:
            self.failed.append((uri[:40] + "...", f"bad data URI: {exc}"))
            return None
        dest = dest_stem.with_name(f"{dest_stem.name}.{ext}")
        self.write_bytes(dest, data)
        return url_for(dest)

    def remote_image(self, url: str, dest_stem: Path) -> str | None:
        """Download an external (hotlinked) image to <dest_stem>.<ext>; reuse any existing match."""
        for existing in sorted(dest_stem.parent.glob(dest_stem.name + ".*")) if dest_stem.parent.exists() else []:
            if existing.suffix != ".part" and existing.stem == dest_stem.name:
                self.existing.add(str(existing))
                return url_for(existing)
        got = self.fetch_bytes(url)
        if got is None:
            return None
        data, ctype = got
        ext = sniff_ext(data, ctype)
        if ext in ("bin", "pdf"):
            self.failed.append((url, f"unexpected content ({ctype})"))
            return None
        dest = dest_stem.with_name(f"{dest_stem.name}.{ext}")
        self.write_bytes(dest, data, downloaded_from=url)
        log.info("downloaded %s (%d bytes)", dest.relative_to(PUBLIC_DIR), len(data))
        return url_for(dest)

    def image(self, src: str, dest_stem: Path, base: str = "https://lamor.fer.hr/lamor") -> str | None:
        """Localize any <img src>: data URI, old-site path, or external URL."""
        src = (src or "").strip()
        if not src:
            return None
        if src.startswith("data:"):
            return self.data_uri(src, dest_stem)
        absolute = urljoin(base, src)
        parts = urlsplit(absolute)
        if parts.netloc.lower() in OLD_HOSTS:
            return self.old_site_file(parts.path)
        return self.remote_image(absolute, dest_stem)

    def summary_lines(self) -> list[str]:
        files = {d for d, _ in self.downloaded} | {d for d, _ in self.written} | self.existing
        on_disk = sum(Path(f).stat().st_size for f in files if Path(f).exists())
        lines = [
            f"- Assets used by this step (all on disk now): {len(files)} files, {on_disk:,} bytes",
            f"- Files downloaded this run: {len(self.downloaded)} ({self.bytes_downloaded:,} bytes)",
            f"- Files decoded from data: URIs this run: {len(self.written)} ({sum(n for _, n in self.written):,} bytes)",
            f"- Files already on disk (skipped): {len(self.existing)}",
            f"- Failed downloads: {len(self.failed)}",
        ]
        return lines
