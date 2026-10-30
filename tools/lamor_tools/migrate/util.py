"""Small helpers shared by the migration modules."""

from __future__ import annotations

import re

from ..common import _Dumper


class BlockStr(str):
    """A string always dumped as a YAML literal block (`key: |`)."""


def _block_representer(dumper, value: BlockStr):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style="|")


_Dumper.add_representer(BlockStr, _block_representer)


def clean_text(text: str | None) -> str:
    """Collapse whitespace (incl. &nbsp; and zero-width spaces) and trim."""
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", text).strip()


def unique_slug(slug: str, used: set[str]) -> str:
    """Return `slug`, or `slug-2`, `slug-3`, ... whichever is not yet in `used`; records it."""
    candidate, n = slug, 2
    while candidate in used:
        candidate = f"{slug}-{n}"
        n += 1
    used.add(candidate)
    return candidate
