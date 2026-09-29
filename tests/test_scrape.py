import datetime as dt
from pathlib import Path

from scrape import parse_day, parse_kishu, parse_tag_page, kishu_url

FX = Path(__file__).parent / "fixtures"
read = lambda n: (FX / n).read_text(encoding="utf-8")


def test_tag_page():
    urls, pages = parse_tag_page(read("tag_page1.html"))
    assert pages == 4
    assert urls[0] == "https://min-repo.com/3378289/"
    assert len(urls) > 100


def test_day():
    date, machines, singles = parse_day(read("day_2026-09-28.html"))
    assert date == dt.date(2026, 9, 28)
    assert "ネオアイムジャグラーEX" in machines
    assert singles["L邪神ちゃんドロップキック"] == {"unit_no": "414", "diff_medals": 0, "games": 632}
    assert len(singles) == 26


def test_kishu():
    rows = parse_kishu(read("kishu_neo_imjuggler_ex.html"), dt.date(2026, 9, 28), "ネオアイムジャグラーEX")
    assert len(rows) == 24
    r = {x["unit_no"]: x for x in rows}
    assert r["664"] == {"date": "2026-09-28", "machine": "ネオアイムジャグラーEX", "unit_no": "664",
                        "games": 821, "bb": 2, "rb": 4, "diff_medals": 0}


def test_kishu_url():
    assert kishu_url("https://min-repo.com/1/", "A B") == "https://min-repo.com/1/?kishu=A+B"
