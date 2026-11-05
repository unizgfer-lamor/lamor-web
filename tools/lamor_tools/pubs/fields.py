"""Pure helpers that turn raw CRORIS fields into display values (no I/O, unit-tested)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from lamor_tools.common import ascii_fold

EN_DASH = "–"

# ----------------------------------------------------------------------------- authors


def parse_authors(autori: str | None) -> list[str]:
    """CRORIS `autori` ('A, B ; C, D' or 'A, B; C, D') -> ['A, B', 'C, D'] in order."""
    parts = re.split(r"\s*;\s*", autori or "")
    names = [re.sub(r"\s+", " ", p).strip(" ,") for p in parts]
    return [n for n in names if n]


def name_key(name: str) -> tuple[str, str] | None:
    """'Petrović, Ivan' / 'Ivan Petrović' / 'Petrović, I.' -> ('petrovic', 'i')."""
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name:
        return None
    if "," in name:
        surname, first = (x.strip() for x in name.split(",", 1))
    else:
        tokens = name.split(" ")
        surname, first = tokens[-1], " ".join(tokens[:-1])
    surname = re.sub(r"[^a-z0-9]+", " ", ascii_fold(surname).lower()).strip()
    first = re.sub(r"[^a-z]", "", ascii_fold(first).lower())
    if not surname:
        return None
    return surname, first[:1]


def lab_keys(names: Iterable[str]) -> dict[tuple[str, str], str]:
    """{('petrovic', 'i'): 'Petrović, Ivan', ...} for the configured researchers / lab members."""
    keys: dict[tuple[str, str], str] = {}
    for n in names:
        k = name_key(n)
        if k and k not in keys:
            keys[k] = re.sub(r"\s+", " ", n).strip()
    return keys


def is_lab_author(name: str, keys: dict[tuple[str, str], str] | set[tuple[str, str]]) -> bool:
    key = name_key(name)
    return key is not None and key in keys


def normalize_author(name: str, keys: dict[tuple[str, str], str]) -> str:
    """Fix lab names written without a comma ('Marković Ivan', 'Ivan Petrović') and restore diacritics."""
    name = re.sub(r"\s+", " ", name).strip()
    tokens = name.split(" ")
    if "," not in name and len(tokens) == 2:
        for surname, first in ((tokens[0], tokens[1]), (tokens[1], tokens[0])):
            candidate = f"{surname}, {first}"
            if is_lab_author(candidate, keys):
                return canonical_author(candidate, keys)
    return canonical_author(name, keys)


def canonical_author(name: str, keys: dict[tuple[str, str], str]) -> str:
    """Restore diacritics of lab names written without them ('Petrovic, Ivan' -> 'Petrović, Ivan')."""
    key = name_key(name)
    canonical = keys.get(key) if key else None
    if canonical and canonical != name and ascii_fold(canonical).lower() == ascii_fold(name).lower():
        return canonical
    return name


# ----------------------------------------------------------------------------- titles

_CROATIAN_LETTERS = re.compile(r"[čćđšžČĆĐŠŽ]")
_ENGLISH_WORDS = {
    "the", "of", "and", "for", "with", "using", "based", "in", "on", "an", "from", "by", "via",
    "towards", "toward", "through", "under", "into", "robot", "robots", "robotic", "control",
}


def clean_title(text: str | None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(\w)- (?=[A-Z])", r"\1-", text)  # 'Radar-LiDAR- Camera' -> 'Radar-LiDAR-Camera'
    return re.sub(r"[\s.]+$", "", text)


def is_englishish(text: str, lang: str | None = None) -> bool:
    if (lang or "").lower() == "en":
        return True
    if _CROATIAN_LETTERS.search(text):
        return False
    words = set(re.findall(r"[a-z]+", text.lower()))
    return bool(words & _ENGLISH_WORDS)


def choose_title(raw: dict[str, Any]) -> str:
    """Original title if it is English-ish, else the English translation, else `naslov`."""
    naslovi = [n for n in raw.get("naslovi") or [] if clean_title(n.get("naziv"))]
    original = next((n for n in naslovi if n.get("original")), None)
    if original and is_englishish(original["naziv"], original.get("cfLangCode")):
        return clean_title(original["naziv"])
    english = next((n for n in naslovi if (n.get("cfLangCode") or "").lower() == "en"), None)
    if english:
        return clean_title(english["naziv"])
    return clean_title(raw.get("naslov"))


# ----------------------------------------------------------------------------- venues

SMALL_WORDS = {"of", "and", "on", "in", "for", "the", "a", "an", "to", "with", "at", "by", "from", "via", "&"}
_CROATIAN_SMALL = {"i", "u", "za", "na", "od", "s", "sa", "o", "iz", "pri", "te", "ili"}


def needs_recase(text: str, acronyms: Iterable[str] = ()) -> bool:
    """True for all-lowercase or sentence-case strings ('Ieee transactions on robotics').

    Words that are known acronyms or fully upper-case ('IEEE robotics & automation letters') are
    ignored for the test, so a leading acronym does not make a sentence-case name "mixed case".
    """
    acr = {a.lower() for a in acronyms}
    words = re.findall(r"[^\W\d_]+", text)
    rest = [w for w in words if not ((len(w) > 1 and w.isupper()) or w.lower() in acr)]
    if not rest or not any(w.islower() for w in rest):
        return False
    first, others = rest[0], rest[1:]
    if words and first is not words[0] and not first.islower():
        return False  # first non-acronym word is capitalised but is not the first word: mixed case
    return (first.islower() or first[1:].islower() or len(first) == 1) and all(w.islower() for w in others)


def looks_croatian(text: str) -> bool:
    if _CROATIAN_LETTERS.search(text):
        return True
    words = re.findall(r"[^\W\d_]+", text.lower())
    return any(w in _CROATIAN_SMALL for w in words[1:])


def _case_word(word: str, first: bool, acronyms: dict[str, str]) -> str:
    m = re.match(r"^([^\w]*)(.*?)([^\w]*)$", word)
    lead, core, trail = m.groups() if m else ("", word, "")
    if not core:
        return word
    if core.lower() in acronyms:
        return lead + acronyms[core.lower()] + trail
    parts = re.split(r"([-/])", core)
    if len(parts) > 1:
        cased = [
            p if p in "-/" else _case_word(p, first and i == 0, acronyms) if p else p
            for i, p in enumerate(parts)
        ]
        return lead + "".join(cased) + trail
    if not first and core.lower() in SMALL_WORDS:
        return lead + core.lower() + trail
    return lead + core[:1].upper() + core[1:] + trail


def recase_venue(text: str, acronyms: Iterable[str]) -> str:
    """Title-case all-lowercase / sentence-case venue names; leave mixed-case ones alone."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or not needs_recase(text, acronyms) or looks_croatian(text):
        return text
    acr = {a.lower(): a for a in acronyms}
    out = []
    after_break = True
    for word in text.split(" "):
        out.append(_case_word(word, after_break, acr))
        after_break = word.endswith((":", EN_DASH, "—")) or word in ("-", EN_DASH)
    return " ".join(out)


def clean_venue(text: str | None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s+([,:;])", r"\1", text)
    text = re.sub(r"(\w)- (?=[A-Z])", r"\1-", text)  # 'Alpe- Adria' -> 'Alpe-Adria'
    return text.rstrip(" .,;")


# ----------------------------------------------------------------------------- details

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _clean_num(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().strip(".,;")


_PAGE_TOKEN = re.compile(r"[A-Za-z]{0,2}\d+")


def page_range(raw: dict[str, Any]) -> tuple[str, str, str]:
    """-> ('pages', first, last) / ('article', number, '') / ('', '', '') from the page fields.

    For a single page, first == last.
    """
    start, end = _clean_num(raw.get("pocetnaStranica")), _clean_num(raw.get("zavrsnaStranica"))
    article = _clean_num(raw.get("brojRada"))
    # '6 stranica' / '12 pages' are page counts, 'x' is a placeholder: not page numbers
    start = start if _PAGE_TOKEN.fullmatch(start) else ""
    end = end if _PAGE_TOKEN.fullmatch(end) else ""
    article = article if re.search(r"\d", article) else ""
    if start and end and start.isdigit() and end.isdigit() and int(end) < int(start):
        # '105617' – '1': an article number entered as the first page, the last page is a page count
        return "article", article or start, ""
    if start and end:
        return "pages", start, end
    if article:
        return "article", article, ""
    if start:
        return "pages", start, start
    pages = re.sub(r"^(?:str|pp?|pages?)\.?\s*", "", _clean_num(raw.get("stranice")), flags=re.I)
    m = re.fullmatch(r"([A-Za-z]?\d+)\s*[-–—]+\s*([A-Za-z]?\d+)", pages)
    if m:
        return "pages", m.group(1), m.group(2)
    if re.fullmatch(r"\d+", pages):
        return "pages", pages, pages
    m = re.fullmatch(r"([A-Za-z]{0,2}\d+)(?:,\s*\d+)?", pages)  # 'e1234' or '103618, 14' (article, page count)
    if m:
        return "article", m.group(1), ""
    return "", "", ""


def pages_part(raw: dict[str, Any]) -> str:
    """'pp. 130–140' / 'p. 5' / 'article no. 103618' / '' from the page fields."""
    kind, a, b = page_range(raw)
    if kind == "article":
        return f"article no. {a}"
    if kind == "pages":
        return f"p. {a}" if a == b else f"pp. {a}{EN_DASH}{b}"
    return ""


def _issue(raw: dict[str, Any]) -> str:
    issue = _clean_num(raw.get("svescic"))
    return issue if re.search(r"\d", issue) and issue != _clean_num(raw.get("brojRada")) else ""


def journal_details(raw: dict[str, Any]) -> str:
    parts = []
    vol = _clean_num(raw.get("volumen"))
    issue = _issue(raw)
    if vol:
        parts.append(f"vol. {vol}")
    if issue:
        parts.append(f"no. {issue}")
    pages = pages_part(raw)
    if pages:
        parts.append(pages)
    return ", ".join(parts)


def format_date_range(start: str | None, end: str | None) -> str:
    """ISO dates -> '4.–6. September 2019' / '30. September–2. October 2019' / '4. September 2019'."""
    if not start:
        return ""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end) if end else s
    if e < s:
        e = s
    if s == e and (s.month, s.day) == (1, 1):
        return ""  # CRORIS placeholder for an unknown date
    if s == e:
        return f"{s.day}. {MONTHS[s.month - 1]} {s.year}"
    if (s.year, s.month) == (e.year, e.month):
        return f"{s.day}.{EN_DASH}{e.day}. {MONTHS[s.month - 1]} {s.year}"
    if s.year == e.year:
        return f"{s.day}. {MONTHS[s.month - 1]}{EN_DASH}{e.day}. {MONTHS[e.month - 1]} {s.year}"
    return f"{s.day}. {MONTHS[s.month - 1]} {s.year}{EN_DASH}{e.day}. {MONTHS[e.month - 1]} {e.year}"


def event_place(event: dict[str, Any], place_names: dict[str, str]) -> str:
    city = (event.get("place") or "").strip()
    city = place_names.get(city, city)
    city = re.sub(r"^(.+?)\s*\(([A-Z]{2})\)$", r"\1, \2", city)  # 'Las Vegas (NV)' -> 'Las Vegas, NV'
    country = (event.get("country") or "").strip()
    country = COUNTRY_NAMES.get(country.upper(), place_names.get(country, country)) if country else ""
    if city and country and city.lower() == country.lower():
        country = ""
    return ", ".join(x for x in (city, country) if x)


def conference_details(raw: dict[str, Any], event: dict[str, Any] | None, place_names: dict[str, str]) -> str:
    parts = []
    if event:
        place = event_place(event, place_names)
        if place:
            parts.append(place)
        when = format_date_range(event.get("start_date"), event.get("end_date"))
        if when:
            parts.append(when)
    pages = pages_part(raw)
    if pages:
        parts.append(pages)
    return ", ".join(parts)


def bib_fields(
    raw: dict[str, Any], category: str, event: dict[str, Any] | None, place_names: dict[str, str]
) -> dict[str, str]:
    """Structured BibTeX fields that the display `details` string merges together.

    journal: volume, number, pages; conference: booktitle (proceedings), publisher, address.
    Pages use BibTeX's '--' range; an article number goes into `pages` as well (Elsevier style).
    Empty values are left out.
    """
    kind, a, b = page_range(raw)
    pages = f"{a}--{b}" if kind == "pages" and a != b else a
    fields: dict[str, str] = {}
    if category == "journal":
        fields["volume"] = _clean_num(raw.get("volumen"))
        fields["number"] = _issue(raw)
    else:
        fields["booktitle"] = clean_venue(raw.get("nadredenaPublikacija"))
        fields["publisher"] = clean_venue(raw.get("izdavac"))
        fields["address"] = event_place(event, place_names) if event else clean_venue(raw.get("mjesto"))
    fields["pages"] = pages
    return {k: v for k, v in fields.items() if v}


def _acronym_in(acronym: str, name: str) -> bool:
    """'ECMR 2025' counts as contained in 'European Conference on Mobile Robots (ECMR) 2025'."""
    if acronym.lower() in name.lower():
        return True
    core = re.sub(r"[\W\d_]+", "", acronym)
    words = {re.sub(r"[\W\d_]+", "", w) for w in name.split()}
    return bool(core) and core.lower() in {w.lower() for w in words}


def conference_venue(raw: dict[str, Any], event: dict[str, Any] | None) -> str:
    """Event name (+ acronym if missing), else the skup name, else the proceedings title."""
    name = clean_venue((event or {}).get("name"))
    acronym = clean_venue((event or {}).get("acronym"))
    if not name:
        skup = raw.get("skup") or []
        name = clean_venue(skup[0].get("naziv")) if skup else ""
    if name and acronym and not _acronym_in(acronym, name):
        name = f"{name} ({acronym})"
    return name or clean_venue(raw.get("nadredenaPublikacija"))


def best_url(raw: dict[str, Any]) -> str | None:
    links = [p for p in raw.get("poveznice") or [] if (p.get("url") or "").strip()]
    oa = next((p for p in links if p.get("urlVrstaId") == 990), None)
    chosen = oa or (links[0] if links else None)
    return chosen["url"].strip() if chosen else None


COUNTRY_NAMES: dict[str, str] = dict(
    item.split(":", 1)
    for item in (
        "AD:Andorra|AE:United Arab Emirates|AF:Afghanistan|AL:Albania|AM:Armenia|AO:Angola|AR:Argentina|"
        "AT:Austria|AU:Australia|AZ:Azerbaijan|BA:Bosnia and Herzegovina|BB:Barbados|BD:Bangladesh|"
        "BE:Belgium|BF:Burkina Faso|BG:Bulgaria|BH:Bahrain|BI:Burundi|BJ:Benin|BN:Brunei|BO:Bolivia|"
        "BR:Brazil|BS:Bahamas|BT:Bhutan|BW:Botswana|BY:Belarus|BZ:Belize|CA:Canada|CD:DR Congo|"
        "CF:Central African Republic|CG:Congo|CH:Switzerland|CI:Côte d'Ivoire|CL:Chile|CM:Cameroon|"
        "CN:China|CO:Colombia|CR:Costa Rica|CU:Cuba|CV:Cape Verde|CY:Cyprus|CZ:Czech Republic|DE:Germany|"
        "DJ:Djibouti|DK:Denmark|DM:Dominica|DO:Dominican Republic|DZ:Algeria|EC:Ecuador|EE:Estonia|"
        "EG:Egypt|ER:Eritrea|ES:Spain|ET:Ethiopia|FI:Finland|FJ:Fiji|FR:France|GA:Gabon|GB:United Kingdom|"
        "GD:Grenada|GE:Georgia|GH:Ghana|GM:Gambia|GN:Guinea|GQ:Equatorial Guinea|GR:Greece|GT:Guatemala|"
        "GW:Guinea-Bissau|GY:Guyana|HK:Hong Kong|HN:Honduras|HR:Croatia|HT:Haiti|HU:Hungary|ID:Indonesia|"
        "IE:Ireland|IL:Israel|IN:India|IQ:Iraq|IR:Iran|IS:Iceland|IT:Italy|JM:Jamaica|JO:Jordan|JP:Japan|"
        "KE:Kenya|KG:Kyrgyzstan|KH:Cambodia|KP:North Korea|KR:South Korea|KW:Kuwait|KZ:Kazakhstan|LA:Laos|"
        "LB:Lebanon|LI:Liechtenstein|LK:Sri Lanka|LR:Liberia|LS:Lesotho|LT:Lithuania|LU:Luxembourg|"
        "LV:Latvia|LY:Libya|MA:Morocco|MC:Monaco|MD:Moldova|ME:Montenegro|MG:Madagascar|MK:North Macedonia|"
        "ML:Mali|MM:Myanmar|MN:Mongolia|MO:Macau|MR:Mauritania|MT:Malta|MU:Mauritius|MV:Maldives|"
        "MW:Malawi|MX:Mexico|MY:Malaysia|MZ:Mozambique|NA:Namibia|NE:Niger|NG:Nigeria|NI:Nicaragua|"
        "NL:Netherlands|NO:Norway|NP:Nepal|NZ:New Zealand|OM:Oman|PA:Panama|PE:Peru|PG:Papua New Guinea|"
        "PH:Philippines|PK:Pakistan|PL:Poland|PR:Puerto Rico|PS:Palestine|PT:Portugal|PY:Paraguay|"
        "QA:Qatar|RO:Romania|RS:Serbia|RU:Russia|RW:Rwanda|SA:Saudi Arabia|SC:Seychelles|SD:Sudan|"
        "SE:Sweden|SG:Singapore|SI:Slovenia|SK:Slovakia|SL:Sierra Leone|SM:San Marino|SN:Senegal|"
        "SO:Somalia|SR:Suriname|SV:El Salvador|SY:Syria|TD:Chad|TG:Togo|TH:Thailand|TJ:Tajikistan|"
        "TM:Turkmenistan|TN:Tunisia|TR:Turkey|TT:Trinidad and Tobago|TW:Taiwan|TZ:Tanzania|UA:Ukraine|"
        "UG:Uganda|US:USA|UY:Uruguay|UZ:Uzbekistan|VA:Vatican City|VE:Venezuela|VN:Vietnam|XK:Kosovo|"
        "YE:Yemen|ZA:South Africa|ZM:Zambia|ZW:Zimbabwe"
    ).split("|")
)
