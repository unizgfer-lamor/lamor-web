from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lamor_tools.migrate.news import local_datetime, parse_item, parse_pub_datetime

FIXTURES = Path(__file__).parent / "fixtures" / "migrate"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-21CEST09:49", "2026-10-21T09:49:00+02:00"),
        ("2016-03-11CET14:38", "2016-03-11T14:38:00+01:00"),
        ("2017-12-11CET9:05", "2017-12-11T09:05:00+01:00"),
        # no zone abbreviation -> Europe/Zagreb offset valid on that day
        ("2026-10-21T09:53", "2026-10-21T09:53:00+02:00"),
        ("2026-01-15T10:00", "2026-01-15T10:00:00+01:00"),
    ],
)
def test_parse_pub_datetime(value, expected):
    assert parse_pub_datetime(value).isoformat() == expected


def test_parse_pub_datetime_rejects_garbage():
    with pytest.raises(ValueError):
        parse_pub_datetime("yesterday")


def test_local_datetime_dst_switch():
    # last Sunday of March 2026 is the 29th: CET before, CEST after
    assert local_datetime(datetime(2026, 3, 28, 12, 0)).utcoffset() == timedelta(hours=1)
    assert local_datetime(datetime(2026, 3, 30, 12, 0)).utcoffset() == timedelta(hours=2)


def test_parse_item_fixture():
    item = parse_item((FIXTURES / "news_item.html").read_text(encoding="utf-8"), "33ulz")
    assert item.title == "LAMOR receives Croatian Science Foundation funding for the STAMP project"
    assert item.date == datetime(2026, 10, 21, 9, 49, tzinfo=timezone(timedelta(hours=2)))
    assert item.updated is None  # edited 4 minutes later -> not an update
    assert item.author == "Luka Petrović"
    assert item.image_src == "/_news/icons/ad4e5b67ae2b48f8891e0f009314a6d26943_icon.jpg"
    assert item.image_alt == item.title  # the CMS alt is a truncated title
    assert item.body_html is None
    assert "STAMP" in item.lead_html.get_text()
