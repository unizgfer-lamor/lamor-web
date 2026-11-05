"""Unit tests for the CRORIS field formatting helpers (lamor_tools.pubs.fields / db helpers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lamor_tools.pubs import db
from lamor_tools.pubs.fields import (
    best_url,
    canonical_author,
    choose_title,
    conference_details,
    conference_venue,
    format_date_range,
    is_lab_author,
    journal_details,
    lab_keys,
    name_key,
    needs_recase,
    normalize_author,
    bib_fields,
    pages_part,
    parse_authors,
    recase_venue,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pubs"
ACRONYMS = ["IEEE", "IFAC", "ASME", "RSJ", "ICRA", "ECMR", "II", "IV"]


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- authors


def test_parse_authors_spaced_semicolons():
    raw = fixture("publication-journal-282380")
    assert raw["autori"] == "Peršić, Juraj ; Petrović, Luka ; Marković, Ivan ; Petrović, Ivan"
    assert parse_authors(raw["autori"]) == ["Peršić, Juraj", "Petrović, Luka", "Marković, Ivan", "Petrović, Ivan"]


def test_parse_authors_tight_semicolons_and_whitespace():
    raw = fixture("publication-unpublished-954023")
    authors = parse_authors(raw["autori"])
    assert len(authors) == 8
    assert authors[0] == "Štironja, Vlaho-Josip"
    assert authors[-1] == "Petrović, Ivan"
    assert parse_authors("  Doe,   John ;; ;Roe, Jane;  ") == ["Doe, John", "Roe, Jane"]
    assert parse_authors(None) == []


def test_name_key_and_lab_flags():
    assert name_key("Petrović, Ivan") == ("petrovic", "i")
    assert name_key("Petrovic, I.") == ("petrovic", "i")
    assert name_key("Ivan Petrović") == ("petrovic", "i")
    assert name_key("Đakulović, Marija") == ("djakulovic", "m")
    keys = lab_keys(["Petrović, Ivan", "Petrović, Luka", "Seder, Marija"])
    assert is_lab_author("Petrovic, Ivan", keys)
    assert is_lab_author("Petrović, L.", keys)
    assert not is_lab_author("Petrović, Nikola", keys)
    assert not is_lab_author("Marković, Ivan", keys)


def test_normalize_author_fixes_missing_commas_for_lab_names():
    keys = lab_keys(["Petrović, Ivan", "Marković, Ivan"])
    assert normalize_author("Marković Ivan", keys) == "Marković, Ivan"
    assert normalize_author("Ivan Petrovic", keys) == "Petrović, Ivan"
    assert normalize_author("Filip  Marić", keys) == "Filip Marić"
    assert normalize_author("Šamija, Hrvoje", keys) == "Šamija, Hrvoje"


def test_canonical_author_restores_diacritics_only_for_lab_names():
    keys = lab_keys(["Petrović, Ivan"])
    assert canonical_author("Petrovic, Ivan", keys) == "Petrović, Ivan"
    assert canonical_author("Petrović, I.", keys) == "Petrović, I."  # initials are left alone
    assert canonical_author("Maric, Filip", keys) == "Maric, Filip"


# ----------------------------------------------------------------------------- titles


def test_choose_title_prefers_english():
    raw = fixture("publication-thesis-451329")
    assert choose_title(raw) == "High-dimensional trajectory optimization for robot motion planning based on Gaussian processes"
    croatian = {
        "naslov": "Detekcija objekata u stvarnom vremenu korištenjem dubokih neuronskih mreža.",
        "naslovi": [
            {"cfLangCode": "hr", "naziv": "Detekcija objekata u stvarnom vremenu korištenjem dubokih neuronskih mreža",
             "original": True},
            {"cfLangCode": "en", "naziv": "Real-time object detection using deep neural networks. ", "original": False},
        ],
    }
    assert choose_title(croatian) == "Real-time object detection using deep neural networks"
    mislabeled = {"naslovi": [{"cfLangCode": "hr", "naziv": "Robot localization based on the Kalman filter",
                               "original": True}], "naslov": "x"}
    assert choose_title(mislabeled) == "Robot localization based on the Kalman filter"
    assert choose_title({"naslov": "  Only   naslov.  "}) == "Only naslov"


# ----------------------------------------------------------------------------- venues


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ieee transactions on robotics", "IEEE Transactions on Robotics"),
        ("Robotics and autonomous systems", "Robotics and Autonomous Systems"),
        ("IEEE robotics & automation letters", "IEEE Robotics & Automation Letters"),
        ("robotics and computer-integrated manufacturing", "Robotics and Computer-Integrated Manufacturing"),
        ("IEEE/ASME transactions on mechatronics", "IEEE/ASME Transactions on Mechatronics"),
        ("Machine vision and applications: an international journal",
         "Machine Vision and Applications: An International Journal"),
        ("Proceedings of the ii workshop on robots in the loop", "Proceedings of the II Workshop on Robots in the Loop"),
        # mixed case is left alone
        ("IEEE Transactions on Systems Man Cybernetics-Systems", "IEEE Transactions on Systems Man Cybernetics-Systems"),
        ("IFAC-PapersOnLine", "IFAC-PapersOnLine"),
        ("Sensors", "Sensors"),
        # Croatian names are not title-cased
        ("Automatika : časopis za automatiku", "Automatika : časopis za automatiku"),
    ],
)
def test_recase_venue(raw, expected):
    assert recase_venue(raw, ACRONYMS) == expected


def test_needs_recase():
    assert needs_recase("advanced robotics")
    assert needs_recase("Advanced robotics")
    assert not needs_recase("Advanced Robotics")
    assert not needs_recase("IEEE Access", ACRONYMS)


def test_conference_venue_from_event_and_fallbacks():
    raw = fixture("publication-conference-678831")
    event = db.event_fields(fixture("event-166921"))
    assert conference_venue(raw, event) == "ECMR 2019 – European Conference on Mobile Robots 2019"
    assert conference_venue(raw, {**event, "acronym": "ECMR"}) == "ECMR 2019 – European Conference on Mobile Robots 2019"
    assert conference_venue(raw, {**event, "acronym": "ECMR 2019"}) == (
        "ECMR 2019 – European Conference on Mobile Robots 2019")
    named = {**event, "name": "European Conference on Mobile Robots (ECMR) 2025", "acronym": "ECMR 2025"}
    assert conference_venue(raw, named) == "European Conference on Mobile Robots (ECMR) 2025"
    assert conference_venue(raw, {**event, "acronym": "EMR"}) == (
        "ECMR 2019 – European Conference on Mobile Robots 2019 (EMR)")
    assert conference_venue(raw, None) == "ECMR 2019 – European Conference on Mobile Robots 2019"  # skup name
    no_skup = {k: v for k, v in raw.items() if k != "skup"}
    assert conference_venue(no_skup, None) == "European Conference on Mobile Robots (ECMR)"


# ----------------------------------------------------------------------------- details


def test_journal_details_pages():
    assert journal_details(fixture("publication-journal-282380")) == "vol. 35, no. 3-4, pp. 130–140"


def test_journal_details_article_number():
    assert journal_details(fixture("publication-journal-article-282305")) == "vol. 133, article no. 103618"
    assert journal_details({"volumen": "162", "svescic": "September", "stranice": "str. 1-15"}) == "vol. 162, pp. 1–15"


def test_pages_part_variants():
    assert pages_part({"stranice": "str. 12-18"}) == "pp. 12–18"
    assert pages_part({"pocetnaStranica": "7", "zavrsnaStranica": "7"}) == "p. 7"
    assert pages_part({"stranice": "e1234"}) == "article no. e1234"
    assert pages_part({"stranice": "103618, 14"}) == "article no. 103618"
    assert pages_part({"stranice": "12"}) == "p. 12"
    assert pages_part({"stranice": "see online"}) == ""
    assert pages_part({"stranice": "S12-S18"}) == "pp. S12–S18"
    assert pages_part({}) == ""
    assert pages_part({"pocetnaStranica": "6 stranica"}) == ""  # a page count, not a page
    assert pages_part({"stranice": "x, 1", "brojRada": "x", "brojStranica": "1"}) == ""
    assert pages_part({"stranice": "2182, 8", "brojRada": "2182", "brojStranica": "8"}) == "article no. 2182"


def test_pages_part_article_number_as_first_page():
    # CRORIS: first page = article number, last page = page count
    raw = {"pocetnaStranica": "105617", "zavrsnaStranica": "1", "brojRada": "105617", "stranice": "str. 105617-1"}
    assert pages_part(raw) == "article no. 105617"


def test_bib_fields_journal():
    assert bib_fields(fixture("publication-journal-282380"), "journal", None, {}) == {
        "volume": "35",
        "number": "3-4",
        "pages": "130--140",
    }
    assert bib_fields(fixture("publication-journal-article-282305"), "journal", None, {}) == {
        "volume": "133",
        "pages": "103618",
    }


def test_bib_fields_conference():
    raw = {
        "nadredenaPublikacija": "IFAC 2026 World Congress Proceedings",
        "izdavac": "IFAC",
        "mjesto": "Busan",
        "pocetnaStranica": "1",
        "zavrsnaStranica": "6",
    }
    assert bib_fields(raw, "conference", None, {}) == {
        "booktitle": "IFAC 2026 World Congress Proceedings",
        "publisher": "IFAC",
        "address": "Busan",
        "pages": "1--6",
    }
    event = db.event_fields(fixture("event-166921"))
    assert bib_fields({}, "conference", event, {"Prag": "Prague"}) == {"address": "Prague, Czech Republic"}


def test_format_date_range():
    assert format_date_range("2019-09-04", "2019-09-06") == "4.–6. September 2019"
    assert format_date_range("2019-09-30", "2019-10-02") == "30. September–2. October 2019"
    assert format_date_range("2019-12-30", "2020-01-02") == "30. December 2019–2. January 2020"
    assert format_date_range("2019-09-04", None) == "4. September 2019"
    assert format_date_range(None, None) == ""
    assert format_date_range("2013-01-01", "2013-01-01") == ""  # CRORIS placeholder


def test_conference_details_with_event():
    raw = fixture("publication-conference-678831")
    event = db.event_fields(fixture("event-166921"))
    assert event["start_date"] == "2019-09-04" and event["country"] == "CZ"
    assert conference_details(raw, event, {"Prag": "Prague"}) == "Prague, Czech Republic, 4.–6. September 2019, pp. 1–6"
    assert conference_details(raw, None, {}) == "pp. 1–6"


# ----------------------------------------------------------------------------- DOI / URL


@pytest.mark.parametrize(
    ("raw", "bare", "norm"),
    [
        ("10.1109/TRO.2021.3061364", "10.1109/TRO.2021.3061364", "10.1109/tro.2021.3061364"),
        ("https://doi.org/10.1016/j.robot.2020.103618", "10.1016/j.robot.2020.103618", "10.1016/j.robot.2020.103618"),
        ("http://dx.doi.org/10.1016/j.inffus.2014.01.003.", "10.1016/j.inffus.2014.01.003",
         "10.1016/j.inffus.2014.01.003"),
        ("doi: 10.1080/01691864.2020.1819874 ", "10.1080/01691864.2020.1819874", "10.1080/01691864.2020.1819874"),
        ("not a doi", None, None),
        ("", None, None),
        (None, None, None),
    ],
)
def test_doi_normalization(raw, bare, norm):
    assert db.bare_doi(raw) == bare
    assert db.normalize_doi(raw) == norm


def test_best_url_prefers_open_access():
    raw = {"poveznice": [{"urlVrstaId": 991, "url": "https://closed"}, {"urlVrstaId": 990, "url": " https://open "}]}
    assert best_url(raw) == "https://open"
    assert best_url({"poveznice": [{"urlVrstaId": 991, "url": "https://closed"}]}) == "https://closed"
    assert best_url({}) is None
