"""Tests for the old-site PDF mapping (lamor_tools.pubs.pdfmap)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from lamor_tools.pubs import db
from lamor_tools.pubs.pdfmap import (
    OVERRIDES_HEADER,
    Curated,
    Matcher,
    extra_entry,
    guess_category,
    load_yaml_list,
    merge_extras,
    merge_overrides,
    parse_page,
    pdf_path,
    write_yaml_list,
)
from lamor_tools.pubs.fields import lab_keys

FIXTURES = Path(__file__).parent / "fixtures" / "pubs"
KEYS = lab_keys(["Petrović, Ivan", "Marković, Ivan"])

PAGE = """
<html><body><div id="cms_area_middle">
 <div class="cms_module portlet_content"><div class="cms_module_title">2014</div>
  <div class="cms_module_html"><div class="text"><table><tbody>
   <tr><td>Borrmann, Dorit; Nüchter, Andreas; Petrović, Ivan. <a class="smedjilink"
     href="http://bib.irb.hr/prikazi-rad?&amp;rad=702915"><strong>A Mobile Robot based System for Fully Automated
     Thermal 3D Mapping</strong></a>. // <em>Advanced Engineering Informatics</em>. <strong>28</strong> (2014) , 4;
     425<strong>-</strong>440. <a href="/images/50020777/Borrmann2014.pdf"><u>pdf</u></a>
     <a href="http://dx.doi.org/10.1016/j.aei.2014.06.002"><img src="url.png"/></a></td></tr>
   <tr><td><p>Marković Ivan; Petrović, Ivan.<br/><strong>Some Workshop Paper on Robots</strong> //
     <em>Workshop on Things</em>, Zagreb, Croatia, 2014. <a href="https://lamor.fer.hr/images/x/My File (1).pdf">pdf</a></p></td></tr>
  </tbody></table></div></div>
 </div>
 <div class="cms_module portlet_content"><div class="cms_module_title">Not a year</div>
  <div class="cms_module_html"><p>Ignored, Entry. <strong>Ignored title here</strong> // nothing</p></div></div>
</div></body></html>
"""


def test_pdf_path_root_relative_and_encoded():
    assert pdf_path("/images/50020777/Borrmann2014.pdf") == "/images/50020777/Borrmann2014.pdf"
    assert pdf_path("https://lamor.fer.hr/images/50020776/Petkovic2018.pdf") == "/images/50020776/Petkovic2018.pdf"
    assert pdf_path("/images/x/My File (1).pdf") == "/images/x/My%20File%20(1).pdf"
    assert pdf_path("/images/x/My%20File.pdf") == "/images/x/My%20File.pdf"  # not double-encoded
    assert pdf_path("/images/x/Čolaković.PDF") == "/images/x/%C4%8Colakovi%C4%87.PDF"
    assert pdf_path("https://arxiv.org/pdf/1908.02963.pdf") is None
    assert pdf_path("https://ieeexplore.ieee.org/document/9733096") is None


def test_parse_page():
    entries = parse_page(PAGE, "2010_-_2014")
    assert len(entries) == 2
    first, second = entries
    assert first.year == 2014
    assert first.title == "A Mobile Robot based System for Fully Automated Thermal 3D Mapping"
    assert first.authors == ["Borrmann, Dorit", "Nüchter, Andreas", "Petrović, Ivan"]
    assert first.venue == "Advanced Engineering Informatics"
    assert first.rest == "28 (2014), 4; 425–440"
    assert first.pdf == "/images/50020777/Borrmann2014.pdf"
    assert first.bib_ids == [702915]
    assert first.dois == ["10.1016/j.aei.2014.06.002"]
    assert second.title == "Some Workshop Paper on Robots"
    assert second.authors == ["Marković Ivan", "Petrović, Ivan"]
    assert second.venue == "Workshop on Things"
    assert second.pdf == "/images/x/My%20File%20(1).pdf"


def test_extra_entry_and_category_guess():
    entries = parse_page(PAGE, "2010_-_2014")
    extra = extra_entry(entries[1], KEYS)
    assert extra["id"] == "legacy-2014-some-workshop-paper-on-robots"
    assert extra["category"] == "conference"
    assert extra["authors"] == [{"name": "Marković, Ivan", "lab": True}, {"name": "Petrović, Ivan", "lab": True}]
    assert extra["pdf"] == "/images/x/My%20File%20(1).pdf"
    assert extra["details"] == "Zagreb, Croatia, 2014"
    assert guess_category(entries[0]) == "conference"  # no journal hint in 'Advanced Engineering Informatics'
    journal = Curated(page="p", year=2015, text="", title="t", authors=[], venue="IEEE Transactions on X", rest="")
    assert guess_category(journal) == "journal"


def test_merge_overrides_preserves_other_keys():
    existing = [
        {"id": 2, "venue": "Hand-fixed venue", "pdf": "/images/old.pdf"},
        {"id": 1, "hide": True},
        {"id": "legacy-2012-foo", "note": "keep me"},
    ]
    merged = merge_overrides(existing, {2: "/images/new.pdf", 5: "/images/e.pdf", 3: "/images/c.pdf",
                                        "legacy-2012-foo": "/images/f.pdf"})
    assert merged == [
        {"id": 2, "venue": "Hand-fixed venue", "pdf": "/images/new.pdf"},
        {"id": 1, "hide": True},
        {"id": "legacy-2012-foo", "note": "keep me", "pdf": "/images/f.pdf"},
        {"id": 3, "pdf": "/images/c.pdf"},
        {"id": 5, "pdf": "/images/e.pdf"},
    ]
    assert existing[0]["pdf"] == "/images/old.pdf"  # input not mutated


def test_overrides_file_roundtrip_keeps_header_and_keys(tmp_path):
    path = tmp_path / "publication-overrides.yaml"
    path.write_text("# my comment\n- id: 7\n  hide: true\n- id: 8\n  title: Fixed title\n", encoding="utf-8")
    items = merge_overrides(load_yaml_list(path), {8: "/images/a b.pdf"})
    write_yaml_list(path, OVERRIDES_HEADER, items)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(OVERRIDES_HEADER)
    assert yaml.safe_load(text) == [{"id": 7, "hide": True}, {"id": 8, "title": "Fixed title", "pdf": "/images/a b.pdf"}]


def test_merge_extras_only_appends_missing_ids():
    existing = [{"id": "legacy-2011-a", "title": "Edited by hand"}]
    additions = [{"id": "legacy-2011-a", "title": "Generated"}, {"id": "legacy-2012-b", "title": "B"}]
    assert merge_extras(existing, additions) == [{"id": "legacy-2011-a", "title": "Edited by hand"},
                                                 {"id": "legacy-2012-b", "title": "B"}]


def _rows():
    conn = db.connect(":memory:")
    for name in ("publication-journal-282380", "publication-conference-678831", "publication-journal-article-282305"):
        db.upsert_publication(conn, json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))
    rows = db.load_publications(conn)
    conn.close()
    return rows


def _curated(title, year, text=None, **kw):
    return Curated(page="p", year=year, text=text or title, title=title, authors=[], venue="", rest="", **kw)


def test_matcher_methods():
    matcher = Matcher(_rows(), exported={282380, 678831})
    by_bib = _curated("Whatever", 2021, bib_ids=[1076273])
    matcher.match(by_bib)
    assert (by_bib.match_id, by_bib.method) == (282380, "bibIrbId")

    by_doi = _curated("Whatever", 2019, dois=["10.1109/ECMR.2019.8870970"])
    matcher.match(by_doi)
    assert (by_doi.match_id, by_doi.method) == (678831, "doi")

    contained = _curated("x", 2020, text="Petrović, L. Online multi-sensor calibration based on moving object "
                                         "tracking. // Advanced Robotics 35 (2021)")
    matcher.match(contained)
    assert (contained.match_id, contained.method) == (282380, "title contained")

    fuzzy = _curated("Stochastic Optimisation for Trajectory Planning with Heteroscedastic Gaussian Process", 2019)
    matcher.match(fuzzy)
    assert (fuzzy.match_id, fuzzy.method) == (678831, "fuzzy title")
    assert fuzzy.best_ratio >= 0.9

    wrong_year = _curated("Stochastic Optimization for Trajectory Planning with Heteroscedastic Gaussian Processes",
                          2015)
    matcher.match(wrong_year)
    assert wrong_year.match_id is None and wrong_year.best_ratio == 1.0  # grey zone for a human

    unrelated = _curated("Underwater acoustic modems for swarms of fish robots", 2019)
    matcher.match(unrelated)
    assert unrelated.match_id is None and unrelated.best_ratio < 0.6
