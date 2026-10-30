import pytest

from lamor_tools.migrate.links import (
    LinkRewriter,
    clean_external,
    decode_safelink,
    encode_path,
    map_page_path,
    strip_tracking,
    unwrap_redirect,
)

SAFELINK = (
    "https://eur02.safelinks.protection.outlook.com/?url=https%3A%2F%2Fkwnsfk27.r.eu-west-1.awstrack.me"
    "%2FL0%2Fhttps%3A%252F%252Fauthors.elsevier.com%252Fc%252F1nPmw3HdG3wi-H%2F1%2F0102019f4b0144f2-41d5"
    "%2FD89NzGRmDUhCla2JmvRzQOxsGnA%3D473&data=05%7C02%7Civan.markovic%40fer.hr%7C05acb&sdata=K4A770&reserved=0"
)


def test_decode_safelink():
    inner = decode_safelink(SAFELINK)
    assert inner.startswith("https://kwnsfk27.r.eu-west-1.awstrack.me/L0/https:%2F%2Fauthors.elsevier.com")


def test_safelink_and_awstrack_fully_unwrapped():
    assert clean_external(SAFELINK) == "https://authors.elsevier.com/c/1nPmw3HdG3wi-H"


def test_unwrap_redirect_leaves_normal_urls():
    assert unwrap_redirect("https://example.com/a/b") == "https://example.com/a/b"


def test_non_safelink_untouched():
    url = "https://www.fer.unizg.hr/en/news_insights/jobs?@=2ugq0#news_132565"
    assert decode_safelink(url) == url
    assert clean_external(url) == url


def test_strip_tracking():
    url = ("https://www.sciencedirect.com/science/article/abs/pii/S0921889026001910?DGCID=STMJ_220042"
           "&lid=jua9g5tkojjo&utm_campaign=STMJ&utm_medium=email&utm_source=braze")
    assert strip_tracking(url) == "https://www.sciencedirect.com/science/article/abs/pii/S0921889026001910"
    assert strip_tracking("https://example.com/?id=5&utm_source=x") == "https://example.com/?id=5"
    assert strip_tracking("https://youtube.com/watch?v=abc") == "https://youtube.com/watch?v=abc"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/lamor", "/"),
        ("/lamor/", "/"),
        ("/lamor/research/running_projects", "/research/projects/"),
        ("/lamor/research/equipment/", "/research/equipment/"),
        ("/lamor/research/partners", "/research/partners/"),
        ("/lamor/teaching/courses", "/teaching/"),
        ("/lamor/publications/2020_-_present", "/publications/"),
        ("/lamor/publications/thesis", "/publications/#theses"),
        ("/lamor/people/current", "/people/"),
        ("/lamor/people/alumni", "/people/alumni/"),
        ("/lamor/contact", "/contact/"),
        ("/lamor/students_corner", "/students-corner/"),
        ("/lamor/something/else", None),
    ],
)
def test_map_page_path(old, new):
    assert map_page_path(old) == new


def test_rewriter_internal_links():
    assets = []
    rw = LinkRewriter(news_slugs={"2wz34": "2016-11-18-workshop"}, asset=lambda p: assets.append(p) or encode_path(p))
    assert rw.rewrite("http://lamor.fer.hr/lamor/research/running_projects") == "/research/projects/"
    assert rw.rewrite("https://lamor.fer.hr/lamor/publications/2020_-_present?_v1=XYZ") == "/publications/"
    assert rw.rewrite("http://lamor.fer.hr") == "/"
    assert rw.rewrite("/lamor?@=2wz34#news_88430") == "/news/2016-11-18-workshop/"
    assert rw.rewrite("https://lamor.fer.hr/lamor?@=nope") is None  # unknown news item -> link dropped
    assert rw.rewrite("/lamor?_v1=abcdef") is None  # session-bound CMS link
    assert rw.rewrite("javascript:cms_mail('lamor','fer.hr','','')") == "mailto:lamor@fer.hr"
    assert rw.rewrite("/_news/125267/AIFORS WS (final).pdf") == "/_news/125267/AIFORS%20WS%20%28final%29.pdf"
    assert rw.rewrite("https://lamor.fer.hr/images/50036607/2022-popovic-rcim.pdf") == "/images/50036607/2022-popovic-rcim.pdf"
    assert assets == ["/_news/125267/AIFORS WS (final).pdf", "/images/50036607/2022-popovic-rcim.pdf"]
    # other FER sites stay external and untouched
    assert rw.rewrite("http://labust.fer.hr/") == "http://labust.fer.hr/"
    assert rw.rewrite("https://aifors.fer.hr") == "https://aifors.fer.hr"
    assert rw.rewrite("mailto:robotics@fer.hr") == "mailto:robotics@fer.hr"
    assert rw.rewrite("#") is None
    assert "/lamor?@=nope" in rw.unresolved[0]


def test_encode_path_idempotent():
    assert encode_path("/_news/1/a b.pdf") == "/_news/1/a%20b.pdf"
    assert encode_path("/_news/1/a%20b.pdf") == "/_news/1/a%20b.pdf"
    assert encode_path("/images/50018601/logo(1).png") == "/images/50018601/logo%281%29.png"
