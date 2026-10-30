from pathlib import Path

from lamor_tools.migrate.html2md import html_to_markdown, inline_text
from lamor_tools.migrate.links import LinkRewriter, encode_path
from lamor_tools.migrate.news import strip_repeated_lead

FIXTURES = Path(__file__).parent / "fixtures" / "migrate"


def convert(html: str, **kwargs) -> str:
    rw = LinkRewriter(news_slugs={"2wz34": "2016-11-18-workshop"}, asset=encode_path)
    return html_to_markdown(html, rw.rewrite, **kwargs)


def test_fixture_matches_expected():
    html = (FIXTURES / "html2md_input.html").read_text(encoding="utf-8")
    expected = (FIXTURES / "html2md_expected.md").read_text(encoding="utf-8").strip()
    assert convert(html, min_heading=2) == expected


def test_headings_bullets_and_attributes():
    md = convert('<h2 style="color:red">Title</h2><ul><li><span class="x">a</span></li><li>b</li></ul>')
    assert md == "## Title\n\n- a\n- b"


def test_empty_paragraphs_dropped():
    assert convert("<p>&nbsp;</p><p>Text</p><p> </p><p><br></p>") == "Text"


def test_simple_table_becomes_markdown_table():
    md = convert("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert "| A | B |" in md and "| 1 | 2 |" in md


def test_complex_table_kept_as_html():
    md = convert('<table style="x"><tr><td colspan="2" style="y">A</td></tr><tr><td>1</td><td>2</td></tr></table>')
    assert md.startswith("<table>") and 'colspan="2"' in md and "style" not in md


def test_video_and_iframe_kept():
    md = convert('<p><video controls src="/x.mp4" class="c"></video></p>')
    assert md == '<video controls="" src="/x.mp4"></video>'


def test_images_localized():
    md = html_to_markdown('<p><img src="data:image/png;base64,AAAA" alt="Logo"></p>', localize_src=lambda s, el: "/images/x.png")
    assert md == "![Logo](/images/x.png)"
    assert html_to_markdown('<p>a<img src="http://x/y.png"></p>', localize_src=lambda s, el: None) == "a"


def test_inline_text():
    from bs4 import BeautifulSoup

    td = BeautifulSoup("<td>H2020 (Total budget:\n<p>2.5 million EUR)</p></td>", "lxml").td
    assert inline_text(td) == "H2020 (Total budget: 2.5 million EUR)"


def test_strip_repeated_lead():
    lead = "First paragraph.\n\nSecond **one**."
    assert strip_repeated_lead(lead, lead + "\n\nMore text.") == "More text."
    assert strip_repeated_lead(lead, "First  paragraph.\n\nSecond **one**.\n\nMore.") == "More."
    assert strip_repeated_lead(lead, "Different body.") == "Different body."
