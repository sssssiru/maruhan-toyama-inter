#!/usr/bin/env python3
"""みんレポ(min-repo.com)からマルハン富山インター店の台データを取得しCSV化する。

日付 / 機種名 / 台番号 / ゲーム数 / BB / RB (+差枚) を 1台1行の縦持ちで出力する。
取得済みの日付はスキップするので、再実行すると差分だけ追記される。

    python scrape.py                 # 全期間(未取得分のみ)
    python scrape.py --limit 3       # 新しい順に3日分だけ(動作確認用)
"""
import argparse
import csv
import os
import datetime as dt
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

TAG_URL = "https://min-repo.com/tag/%e3%83%9e%e3%83%ab%e3%83%8f%e3%83%b3%e5%af%8c%e5%b1%b1%e3%82%a4%e3%83%b3%e3%82%bf%e3%83%bc%e5%ba%97/"
FIELDS = ["date", "machine", "unit_no", "games", "bb", "rb", "diff_medals"]
DEFAULT_OUT = Path("data/maruhan_toyama_inter.csv")
CHALLENGE_MARK = "w_scd_n"  # ブラウザ確認(JS)ページの目印
DAY_URL_RE = re.compile(r"^https://min-repo\.com/\d+/?$")


def soup(html):
    return BeautifulSoup(html, "lxml")


def to_int(text):
    """'1,234' -> 1234, '-' や空 -> None"""
    t = text.strip().replace(",", "").replace("+", "")
    return int(t) if re.fullmatch(r"-?\d+", t) else None


# ---------- 一覧ページ ----------
def parse_tag_page(html):
    """タグ一覧ページ -> (日別記事URLのリスト, 総ページ数)"""
    s = soup(html)
    urls = []
    for a in s.select("div.table_wrap table td a[href]"):
        href = a["href"]
        if DAY_URL_RE.match(href) and href not in urls:
            urls.append(href)
    m = re.search(r"\d+\s*/\s*(\d+)", (s.select_one(".wp-pagenavi .pages") or s).get_text())
    pages = int(m.group(1)) if s.select_one(".wp-pagenavi .pages") and m else 1
    return urls, pages


# ---------- 日別ページ ----------
def parse_day(html):
    """日別ページ -> (date, [機種名...], {機種名: 1台機種の行})

    年は記事に載っていないので、公開日時(datePublished)から補う。
    """
    s = soup(html)
    h1 = s.find("h1")
    m = re.match(r"(\d+)/(\d+)", h1.get_text(strip=True)) if h1 else None
    if not m:
        raise ValueError("日別ページの見出しが見つかりません")
    month, day = int(m.group(1)), int(m.group(2))
    pub = re.search(r'"datePublished":"(\d{4})-(\d{2})-(\d{2})', html)
    pub_date = dt.date(*map(int, pub.groups()))
    date = dt.date(pub_date.year, month, day)
    if date > pub_date:  # 年またぎ(公開が1月・データが12月)
        date = dt.date(pub_date.year - 1, month, day)

    machines, singles = [], {}
    for tab in s.select("div.tab_content"):
        h2 = tab.find("h2")
        if not h2 or "末尾" in h2.get_text():
            continue
        for tr in tab.select("table.kishu tr"):
            a = tr.find("a")
            if not a:
                continue
            name = a.get_text(strip=True)
            if name not in machines:
                machines.append(name)
            tds = tr.find_all("td")
            if "num=" in a["href"] and len(tds) >= 4:  # 1台設置機種: 台番あり
                singles[name] = {
                    "unit_no": tds[1].get_text(strip=True),
                    "diff_medals": to_int(tds[2].get_text()),
                    "games": to_int(tds[3].get_text()),
                }
    return date, machines, singles


def kishu_url(day_url, machine):
    return f"{day_url.rstrip('/')}/?kishu={quote_plus(machine)}"


# ---------- 機種別ページ ----------
def parse_kishu(html, date, machine):
    """機種別ページ -> 台ごとの行(dict)のリスト。BB/RBの列があるテーブルを対象にする。"""
    rows = []
    for table in soup(html).select("table"):
        heads = [th.get_text(strip=True) for th in table.select("tr:first-child th")]
        if "台番" not in heads or "BB" not in heads:
            continue
        idx = {h: i for i, h in enumerate(heads)}
        for tr in table.select("tr")[1:]:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) != len(heads) or not tds[idx["台番"]].isdigit():  # 「平均」行を除く
                continue
            rows.append({
                "date": date.isoformat(),
                "machine": machine,
                "unit_no": tds[idx["台番"]],
                "games": to_int(tds[idx["G数"]]),
                "bb": to_int(tds[idx["BB"]]),
                "rb": to_int(tds[idx["RB"]]),
                "diff_medals": to_int(tds[idx["差枚"]]),
            })
        break
    return rows


# ---------- HTTP ----------
class Client:
    def __init__(self, delay):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en;q=0.8",
        })
        self.last = 0.0

    def get(self, url, tries=4):
        for n in range(tries):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                if not r.text.strip():
                    raise RuntimeError("空のレスポンス(アクセス制限の可能性)")
                if CHALLENGE_MARK in r.text:
                    raise SystemExit("サイトのブラウザ確認画面が返されました。--browser を付けて実行してください。")
                return r.text
            except Exception as e:  # noqa: BLE001
                print(f"  retry {n + 1}/{tries}: {url} ({e})", file=sys.stderr)
                time.sleep(2 ** (n + 1))
        raise RuntimeError(f"取得失敗: {url}")


class BrowserClient:
    """実ブラウザ(Chromium)でページを開く。サイトのJS確認を通常のブラウザ同様に通過させる。"""

    def __init__(self, delay, headless=True):
        from playwright.sync_api import sync_playwright
        self.delay = delay
        self.last = 0.0
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless, executable_path=os.environ.get("CHROMIUM_PATH") or None)
        self.page = self.browser.new_page()

    def get(self, url, tries=3):
        for n in range(tries):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                resp = self.page.goto(url, timeout=45000)
                if resp is not None and resp.status == 404:
                    return None
                # 確認ページの場合はJSが自動でリロードするので、本物のページの<h1>を待つ
                self.page.wait_for_selector("h1", timeout=30000)
                return self.page.content()
            except Exception as e:  # noqa: BLE001
                print(f"  retry {n + 1}/{tries}: {url} ({e})", file=sys.stderr)
                time.sleep(2 ** (n + 1))
        raise RuntimeError(f"取得失敗: {url}")

    def close(self):
        self.browser.close()
        self._pw.stop()


def collect_day_urls(client):
    html = client.get(TAG_URL)
    urls, pages = parse_tag_page(html)
    for p in range(2, pages + 1):
        html = client.get(f"{TAG_URL}page/{p}/")
        if html is None:
            break
        urls += [u for u in parse_tag_page(html)[0] if u not in urls]
    return urls


def scrape_day(client, day_url, done=()):
    html = client.get(day_url)
    try:
        date, machines, singles = parse_day(html)
    except ValueError:
        Path("debug").mkdir(exist_ok=True)
        dump = Path("debug") / f"{urlparse(day_url).path.strip('/')}.html"
        dump.write_text(html, encoding="utf-8")
        print(f"  想定外のページ: {day_url} -> {dump} に保存(スキップ)", file=sys.stderr)
        return None, []
    if date.isoformat() in done:  # 取得済みの日は機種ページを取りに行かない
        return None, []
    rows = []
    for name in machines:
        page = client.get(kishu_url(day_url, name))
        got = parse_kishu(page, date, name) if page else []
        if not got and name in singles:  # 機種別ページが取れない1台機種は日別ページの値で代用(BB/RBなし)
            got = [{"date": date.isoformat(), "machine": name, "bb": None, "rb": None, **singles[name]}]
        rows += got
    return date, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔(秒)")
    ap.add_argument("--limit", type=int, help="取得する日数の上限(新しい順)")
    ap.add_argument("--browser", action="store_true", help="実ブラウザ(Playwright)で取得する")
    a = ap.parse_args()

    a.out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if a.out.exists():
        with a.out.open(encoding="utf-8-sig", newline="") as f:
            done = {r["date"] for r in csv.DictReader(f)}
    new_file = not a.out.exists()

    client = BrowserClient(a.delay) if a.browser else Client(a.delay)
    day_urls = collect_day_urls(client)
    print(f"{len(day_urls)}日分の記事を発見 / 取得済み {len(done)}日")

    try:
        _run(a, client, day_urls, done, new_file)
    finally:
        if hasattr(client, "close"):
            client.close()


def _run(a, client, day_urls, done, new_file):
    count = 0
    with a.out.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new_file:
            w.writeheader()
        for url in day_urls:
            if a.limit is not None and count >= a.limit:
                break
            date, rows = scrape_day(client, url, done)
            if date is None:
                continue
            w.writerows(rows)
            f.flush()
            count += 1
            print(f"{date} {len(rows)}台")


if __name__ == "__main__":
    main()
