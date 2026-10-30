from datetime import date

from lamor_tools.common import slugify
from lamor_tools.migrate.pages import (
    clean_person_name,
    parse_duration,
    profile_slug,
    split_heading,
    split_partners,
)
from lamor_tools.migrate.util import unique_slug


def test_unique_slug():
    used: set[str] = set()
    assert unique_slug("a", used) == "a"
    assert unique_slug("a", used) == "a-2"
    assert unique_slug("a", used) == "a-3"
    assert unique_slug("b", used) == "b"
    assert used == {"a", "a-2", "a-3", "b"}


def test_news_slug_shape():
    assert slugify("Prof. Ivan Petrović receives prestigious IFAC award") == "prof-ivan-petrovic-receives-prestigious-ifac-award"
    assert len(slugify("x " * 100)) <= 60


def test_clean_person_name():
    assert clean_person_name("Prof. Ivan Petrović, F.C.A.") == ("Ivan Petrović", ["Prof.", "F.C.A."])
    assert clean_person_name("Asst. Prof. Luka Petrović")[0] == "Luka Petrović"
    assert clean_person_name("Igor Cvišić, PhD")[0] == "Igor Cvišić"
    assert clean_person_name("Marta Čolaković-Bencerić") == ("Marta Čolaković-Bencerić", [])


def test_profile_slug():
    assert profile_slug("Ivan Petrović") == "ivan.petrovic"
    assert profile_slug("Đuro Žić") == "djuro.zic"


def test_split_heading():
    assert split_heading("EKO-KOMVOZ - Development of a vehicle") == ("EKO-KOMVOZ", "Development of a vehicle")
    assert split_heading("RAiSE – Robotics, AI, and Computer Science") == ("RAiSE", "Robotics, AI, and Computer Science")
    assert split_heading("DIH²- A Network of Robotics DIHs") == ("DIH²", "A Network of Robotics DIHs")
    assert split_heading("HORACE - Human-robot collaborative manipulation")[0] == "HORACE"
    assert split_heading("Visual Localization of Sports Cars") == (None, "Visual Localization of Sports Cars")


def test_parse_duration():
    assert parse_duration("01.01.2021 - 31.12.2026") == (date(2021, 1, 1), date(2026, 12, 31), None)
    assert parse_duration("01.07.2023. - 30.06.2025.") == (date(2023, 7, 1), date(2025, 6, 30), None)
    assert parse_duration("05.2020 - 05.2023.") == (None, None, "05.2020 - 05.2023")
    assert parse_duration("Academic year 2025/2026") == (None, None, "Academic year 2025/2026")


def test_split_partners():
    assert split_partners("Gideon Brothers d.o.o., Mobilisis d.o.o.") == ["Gideon Brothers d.o.o.", "Mobilisis d.o.o."]
    assert split_partners("Björn Hein, Karlsruhe Institute of Technology") == ["Björn Hein, Karlsruhe Institute of Technology"]
    assert split_partners("Jacobs University Bremen GmbH, Germany (coordinator Andreas Nüchter), University of Sarajevo") == [
        "Jacobs University Bremen GmbH, Germany (coordinator Andreas Nüchter)", "University of Sarajevo"]
    assert split_partners("University of Zagreb (LARICS, LABUST), Tallinn University") == [
        "University of Zagreb (LARICS, LABUST)", "Tallinn University"]
    assert split_partners("A University, Austria; B Institute, Slovenia;") == ["A University, Austria", "B Institute, Slovenia"]
    assert split_partners("X, Faculty of Electrical Engineering, Mechanical Engineering and Naval Architecture, Y") == [
        "X", "Faculty of Electrical Engineering, Mechanical Engineering and Naval Architecture", "Y"]
