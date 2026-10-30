"""Sanity checks for the migrated content:

- YAML frontmatter parses; required fields, enums and basic types match src/content.config.ts
- every root-relative image/PDF/file path referenced in frontmatter or Markdown exists under public/
- every /news/<id>/ link points at an existing news file
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

from ..common import CONTENT_DIR, PUBLIC_DIR

COLLECTIONS = ("news", "projects", "people", "equipment", "partners", "courses", "theses", "pages")
STR, NUM, INT, DATE, BOOL, LIST, ANY = "str", "num", "int", "date", "bool", "list", "any"

# field -> (type, required, enum)
SCHEMAS: dict[str, dict[str, tuple[str, bool, tuple | None]]] = {
    "news": {"title": (STR, True, None), "date": (DATE, True, None), "updated": (DATE, False, None),
             "author": (STR, False, None), "image": (STR, False, None), "imageAlt": (STR, False, None),
             "lead": (STR, False, None), "attachments": (LIST, False, None), "draft": (BOOL, False, None)},
    "projects": {"acronym": (STR, True, None), "title": (STR, True, None),
                 "status": (STR, True, ("ongoing", "past")), "logo": (STR, False, None),
                 "website": (STR, False, None), "funding": (STR, False, None), "start": (DATE, False, None),
                 "end": (DATE, False, None), "duration": (STR, False, None), "pi": (STR, False, None),
                 "team": (LIST, False, None), "partners": (LIST, False, None), "sidebar": (BOOL, False, None),
                 "order": (NUM, False, None)},
    "people": {"name": (STR, True, None),
               "category": (STR, True, ("faculty", "senior", "phd", "manager", "alumni", "guest", "intern")),
               "role": (STR, False, None), "group": (STR, False, None), "photo": (STR, False, None),
               "profile": (STR, False, None), "email": (STR, False, None), "affiliation": (STR, False, None),
               "croris_id": (NUM, False, None), "order": (NUM, False, None)},
    "equipment": {"name": (STR, True, None), "kind": (STR, True, ("robot", "sensor")), "image": (STR, False, None),
                  "manufacturer": (STR, False, None), "link": (STR, False, None), "order": (NUM, False, None)},
    "partners": {"name": (STR, True, None), "country": (STR, False, None), "logo": (STR, False, None),
                 "website": (STR, False, None), "order": (NUM, False, None)},
    "courses": {"name": (STR, True, None), "name_hr": (STR, False, None),
                "level": (STR, True, ("undergraduate", "graduate", "phd")),
                "url": (STR, False, None), "url_hr": (STR, False, None), "order": (NUM, False, None)},
    "theses": {"title": (STR, True, None), "author": (STR, True, None), "year": (INT, True, None),
               "type": (STR, False, None), "supervisors": (LIST, False, None), "pdf": (STR, False, None),
               "url": (STR, False, None)},
    "pages": {"title": (STR, True, None), "description": (STR, False, None)},
}
PATH_FIELDS = ("image", "logo", "photo", "pdf")
FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
MD_LINK_RE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_LINK_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""")
FILE_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def _type_ok(value, kind: str) -> bool:
    if kind == STR:
        return isinstance(value, str)
    if kind == NUM:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == BOOL:
        return isinstance(value, bool)
    if kind == LIST:
        return isinstance(value, list)
    if kind == DATE:
        if isinstance(value, (date, datetime)):
            return True
        try:
            datetime.fromisoformat(str(value))
            return True
        except ValueError:
            return False
    return True


def check_file(path: Path) -> tuple[list[str], list[str], dict]:
    """Returns (errors, referenced root-relative URLs, frontmatter)."""
    collection = path.relative_to(CONTENT_DIR).parts[0]
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return ["no YAML frontmatter"], [], {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        return [f"YAML error: {exc}"], [], {}
    errors = []
    schema = SCHEMAS[collection]
    for key, (kind, required, enum) in schema.items():
        if key not in fm:
            if required:
                errors.append(f"missing required field '{key}'")
            continue
        value = fm[key]
        if not _type_ok(value, kind):
            errors.append(f"field '{key}' has wrong type ({type(value).__name__}, expected {kind})")
        if enum and value not in enum:
            errors.append(f"field '{key}' = {value!r} not in {enum}")
        if kind == LIST and isinstance(value, list) and key in ("team", "partners", "supervisors"):
            if not all(isinstance(v, str) and v for v in value):
                errors.append(f"field '{key}' must be a list of non-empty strings")
    for key in fm:
        if key not in schema:
            errors.append(f"unknown field '{key}' (would be stripped by Zod)")
    urls = [fm[k] for k in PATH_FIELDS if isinstance(fm.get(k), str)]
    for att in fm.get("attachments") or []:
        if isinstance(att, dict) and isinstance(att.get("file"), str):
            urls.append(att["file"])
    for chunk in (m.group(2), fm.get("lead") or ""):
        urls += MD_LINK_RE.findall(chunk)
        urls += HTML_LINK_RE.findall(chunk)
    return errors, urls, fm


def local_target(url: str) -> Path | None:
    """Root-relative file URL -> path under public/ (None for page links and external URLs)."""
    if not url.startswith("/") or url.startswith("//"):
        return None
    path = unquote(urlsplit(url).path)
    if path.startswith(("/images/", "/_news/", "/files/", "/design/")) or FILE_EXT_RE.search(path):
        return PUBLIC_DIR / path.lstrip("/")
    return None


def verify(verbose: bool = True) -> int:
    news_ids = {p.stem for p in (CONTENT_DIR / "news").glob("*.md")}
    problems: list[str] = []
    checked_files = 0
    checked_refs = 0
    encoded_issues = []
    for collection in COLLECTIONS:
        for path in sorted((CONTENT_DIR / collection).glob("**/*.md")):
            checked_files += 1
            rel = path.relative_to(CONTENT_DIR)
            errors, urls, _ = check_file(path)
            problems += [f"{rel}: {e}" for e in errors]
            for url in urls:
                if " " in url:
                    encoded_issues.append(f"{rel}: unencoded space in {url!r}")
                target = local_target(url)
                if target is not None:
                    checked_refs += 1
                    if not target.exists():
                        problems.append(f"{rel}: missing file public{unquote(urlsplit(url).path)}")
                elif url.startswith("/news/"):
                    checked_refs += 1
                    slug = url.strip("/").split("/")[1] if url.count("/") >= 2 else ""
                    if slug not in news_ids:
                        problems.append(f"{rel}: link to unknown news item {url}")
    problems += encoded_issues
    print(f"checked {checked_files} content files, {checked_refs} local references")
    for p in problems:
        print(f"  {p}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0
