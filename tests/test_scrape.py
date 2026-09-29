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


def test_tag_url():
    from scrape import tag_url
    assert tag_url("マルハン富山インター店") == "https://min-repo.com/tag/%e3%83%9e%e3%83%ab%e3%83%8f%e3%83%b3%e5%af%8c%e5%b1%b1%e3%82%a4%e3%83%b3%e3%82%bf%e3%83%bc%e5%ba%97/"
    assert tag_url("KYORAKU西店").startswith("https://min-repo.com/tag/KYORAKU%e8%a5%bf")


def test_out_path():
    from pathlib import Path
    from scrape import out_path, DEFAULT_OUT
    assert out_path("マルハン富山インター店", None) == DEFAULT_OUT
    assert out_path("A B/店", None) == Path("data/A_B_店.csv")
