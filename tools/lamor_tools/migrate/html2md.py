"""CKEditor HTML (old Quilt CMS) -> clean Markdown.

- ATX headings, "-" bullets, "**" strong, "*" em, backslash line breaks
- presentational attributes and wrappers (span/font/u, style/class/dir/id/data-*) are dropped
- empty / &nbsp;-only paragraphs are dropped, excess blank lines collapsed
- <iframe>, <video> and complex tables (colspan/rowspan or nested tables) are kept as raw HTML
- links and images are passed through optional callbacks (rewrite href / localize src)
"""

from __future__ import annotations

import re
from collections.abc import Callable

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import ATX, BACKSLASH, MarkdownConverter

RAW_ATTRS = {
    "src", "href", "width", "height", "alt", "title", "colspan", "rowspan", "allow",
    "allowfullscreen", "frameborder", "controls", "poster", "type", "loading",
}
DROP_TAGS = ("script", "style", "noscript", "meta", "link", "object", "embed", "form", "input", "button")
UNWRAP_TAGS = ("span", "font", "u", "o:p", "center", "small", "big", "section", "article")
RAW_TOKEN = "LAMORRAWHTML{}END"
RAW_TOKEN_RE = re.compile(r"LAMORRAWHTML(\d+)END")

HrefFn = Callable[[str], "str | None"]
SrcFn = Callable[[str, Tag], "str | None"]


class _Converter(MarkdownConverter):
    def __init__(self, min_heading: int = 1, **options):
        self.min_heading = min_heading
        super().__init__(**options)

    def convert_hN(self, n, el, text, parent_tags):  # noqa: N802 - markdownify naming
        return super().convert_hN(max(n, self.min_heading), el, text, parent_tags)

    def convert_a(self, el, text, parent_tags):
        el.attrs.pop("title", None)
        return super().convert_a(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags):
        if not el.get("src"):
            return ""
        return super().convert_img(el, text, parent_tags)


def _is_complex_table(table: Tag) -> bool:
    if table.find("table"):
        return True
    return any(cell.get("colspan", "1") not in ("", "1") or cell.get("rowspan", "1") not in ("", "1")
               for cell in table.find_all(["td", "th"]))


def _clean_raw(el: Tag) -> str:
    for node in [el, *el.find_all(True)]:
        node.attrs = {k: v for k, v in node.attrs.items() if k in RAW_ATTRS}
    for node in el.find_all(UNWRAP_TAGS):
        node.unwrap()
    for p in el.find_all("p"):
        if not p.get_text(strip=True) and not p.find(["img", "iframe", "video"]):
            p.decompose()
    html = str(el)
    html = re.sub(r"\n\s*\n+", "\n", html)
    return html.replace("\xa0", " ")


def _is_blank(el: Tag) -> bool:
    return not el.get_text(strip=True) and not el.find(["img", "iframe", "video", "table", "hr"])


def clean_soup(
    root: Tag,
    rewrite_href: HrefFn | None = None,
    localize_src: SrcFn | None = None,
) -> list[str]:
    """Normalize a fragment in place; returns the list of raw HTML blocks referenced by tokens."""
    for c in root.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in root.find_all(DROP_TAGS):
        t.decompose()
    # Links
    for a in root.find_all("a"):
        href = a.get("href")
        new = rewrite_href(href) if (rewrite_href and href) else href
        if not new:
            a.unwrap()
            continue
        a.attrs = {"href": new}
    # Adjacent links to the same target ("<a>link</a><a>.</a>") -> one link
    for a in root.find_all("a"):
        if a.decomposed or a.parent is None:
            continue
        nxt = a.next_sibling
        while isinstance(nxt, Tag) and nxt.name == "a" and nxt.get("href") == a.get("href"):
            for child in list(nxt.contents):
                a.append(child.extract())
            nxt.decompose()
            nxt = a.next_sibling
    # Images
    for img in root.find_all("img"):
        src = img.get("src", "")
        new = localize_src(src, img) if localize_src else src
        if not new:
            img.decompose()
            continue
        alt = (img.get("alt") or "").strip()
        img.attrs = {"src": new, "alt": alt}
    for t in root.find_all(UNWRAP_TAGS):
        t.unwrap()
    # A <br> in the middle of a sentence (next text starts lowercase) is a copy/paste artefact.
    for br in root.find_all("br"):
        nxt = br.next_sibling
        while nxt is not None and isinstance(nxt, NavigableString) and not nxt.strip():
            nxt = nxt.next_sibling
        prev = br.previous_sibling
        prev_text = prev.get_text() if isinstance(prev, Tag) else str(prev or "")
        if (isinstance(nxt, NavigableString) and re.match(r"\s*[a-zčćšžđ(]", str(nxt))
                and re.search(r"[\w,]\s*$", prev_text)):
            br.replace_with(" ")
    # Emphasis around punctuation only ("2017<strong>.</strong>") breaks CommonMark flanking rules.
    for t in root.find_all(["strong", "b", "em", "i"]):
        if t.decomposed or t.parent is None or t.find(["img", "br"]):
            continue
        text = t.get_text()
        nxt = t.next_sibling
        if text and not re.search(r"\w", text) and not (isinstance(nxt, Tag) and nxt.name == t.name):
            t.unwrap()
    # Non-breaking spaces -> spaces, so &nbsp;-only paragraphs become empty.
    for s in root.find_all(string=True):
        if "\xa0" in s or "​" in s:
            s.replace_with(NavigableString(s.replace("\xa0", " ").replace("​", "")))
    for p in root.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "strong", "em", "b", "i"]):
        if p.decomposed or p.parent is None:
            continue
        if _is_blank(p):
            if p.name in ("strong", "em", "b", "i") and p.string:
                p.unwrap()  # keep the whitespace itself
            else:
                p.decompose()
    # Raw HTML blocks
    raw: list[str] = []
    for el in list(root.find_all(["iframe", "video", "table"])):
        if el.decomposed or el.parent is None or el.find_parent(["table", "video"]) is not None:
            continue
        if el.name == "table" and not _is_complex_table(el):
            continue
        token = RAW_TOKEN.format(len(raw))
        raw.append(_clean_raw(el))
        p = BeautifulSoup("", "lxml").new_tag("p")
        p.string = token
        el.replace_with(p)
    return raw


def html_to_markdown(
    html: str | Tag | None,
    rewrite_href: HrefFn | None = None,
    localize_src: SrcFn | None = None,
    min_heading: int = 1,
) -> str:
    """Convert an HTML fragment (string or bs4 Tag, which is copied) to Markdown."""
    if html is None:
        return ""
    if isinstance(html, Tag):
        # inner HTML only: a bare <td>/<th>/<li> would be dropped by the HTML parser
        html = f"<div>{html.decode_contents()}</div>"
    fragment = BeautifulSoup(html, "lxml")
    root = fragment.body or fragment
    raw = clean_soup(root, rewrite_href, localize_src)
    md = _Converter(
        min_heading=min_heading,
        heading_style=ATX,
        bullets="-",
        strong_em_symbol="*",
        newline_style=BACKSLASH,
        sup_symbol="<sup>",
        sub_symbol="<sub>",
        table_infer_header=True,
        keep_inline_images_in=["td", "th"],
        escape_misc=False,
    ).convert_soup(root)
    md = RAW_TOKEN_RE.sub(lambda m: raw[int(m.group(1))], md)
    return tidy_markdown(md)


def tidy_markdown(md: str) -> str:
    lines = [line.rstrip() for line in md.replace("\r\n", "\n").split("\n")]
    # collapse runs of spaces inside a line (keep indentation)
    lines = [re.sub(r"(?<=\S) {2,}", " ", line) for line in lines]
    md = "\n".join(lines)
    md = re.sub(r"\\\n(\s*\n)+", "\n\n", md)  # a line break right before a paragraph break
    md = re.sub(r"(^|\n)\\(\n|$)", r"\1\2", md)  # lone backslash lines
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"\*\*(\s*)\*\*", r"\1", md)  # empty strong
    md = re.sub(r"[ \t]+\\\n", "\\\n", md)  # spaces before a hard line break
    md = re.sub(r"\\+\s*$", "", md.strip())  # hard line break at the very end
    return md.strip()


def inline_text(el: Tag | None) -> str:
    """Plain text of an element with <br>/<p> boundaries as spaces and whitespace collapsed."""
    if el is None:
        return ""
    frag = BeautifulSoup(str(el), "lxml")
    for br in frag.find_all("br"):
        br.replace_with(" ")
    for p in frag.find_all(["p", "div", "li"]):
        p.insert_after(" ")
    text = frag.get_text("")
    return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("​", "")).strip()
