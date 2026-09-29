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

MAX_FAILURES = 5  # 連続でこの日数失敗したら停止(サイトへの無駄なアクセスを避ける)
DEFAULT_STORE = "マルハン富山インター店"


def tag_url(store):
    """店名 -> みんレポのタグ一覧URL(店名はサイト上のタグ名と完全一致が必要)"""
    enc = re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), quote(store))
    return f"https://min-repo.com/tag/{enc}/"


# 差枚はサイト側で0固定(非公開)のため出力しない
FIELDS = ["date", "machine", "unit_no", "games", "bb", "rb"]
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
        self.headless = headless
        self._pw = sync_playwright().start()
        self._launch()

    def _launch(self):
        self.browser = self._pw.chromium.launch(headless=self.headless, executable_path=os.environ.get("CHROMIUM_PATH") or None)
        self.page = self.browser.new_page()

    def _alive(self):
        try:
            return self.browser.is_connected() and not self.page.is_closed()
        except Exception:  # noqa: BLE001
            return False

    def get(self, url, tries=3):
        for n in range(tries):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            html = ""
            try:
                if not self._alive():
                    print("  ブラウザが閉じていたので起動し直します", file=sys.stderr)
                    try:
                        self.browser.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._launch()
                resp = self.page.goto(url, timeout=45000, wait_until="domcontentloaded")
                if resp is not None and resp.status == 404:
                    return None
                # JS確認ページなら、確認が通った後にもう一度開き直す(最大4回)
                for _ in range(4):
                    for _ in range(7):  # 表示されればすぐ次へ(最大約2秒待つ)
                        self.page.wait_for_timeout(300)
                        html = self.page.content()
                        if "<h1" in html and CHALLENGE_MARK not in html:
                            return html
                    self.page.goto(url, timeout=45000, wait_until="domcontentloaded")
                raise RuntimeError("確認ページを通過できませんでした")
            except Exception as e:  # noqa: BLE001
                print(f"  retry {n + 1}/{tries}: {url} ({e})", file=sys.stderr)
                Path("debug").mkdir(exist_ok=True)
                name = urlparse(url).path.strip("/") or "top"
                try:
                    (Path("debug") / f"browser_{name}.html").write_text(html or self.page.content(), encoding="utf-8")
                except Exception:  # noqa: BLE001  (ブラウザが落ちていて保存できない場合)
                    pass
                time.sleep(2 ** (n + 1))
        raise RuntimeError(f"取得失敗: {url} (debug/ にHTMLを保存)")

    def close(self):
        self.browser.close()
        self._pw.stop()


def collect_day_urls(client, store):
    base = tag_url(store)
    html = client.get(base)
    if html is None:
        raise SystemExit(f"店舗が見つかりません: {store}\n(みんレポ上のタグ名と完全に一致する店名を指定してください: {base})")
    urls, pages = parse_tag_page(html)
    for p in range(2, pages + 1):
        html = client.get(f"{base}page/{p}/")
        if html is None:
            break
        urls += [u for u in parse_tag_page(html)[0] if u not in urls]
    return urls


class TooOld(Exception):
    """--since より古い日に到達した(一覧は新しい順なので、以降は不要)"""


def scrape_day(client, day_url, done=(), since=None, newest=None, keywords=()):
    html = client.get(day_url)
    try:
        date, machines, singles = parse_day(html)
    except ValueError:
        Path("debug").mkdir(exist_ok=True)
        dump = Path("debug") / f"{urlparse(day_url).path.strip('/')}.html"
        dump.write_text(html, encoding="utf-8")
        print(f"  想定外のページ: {day_url} -> {dump} に保存(スキップ)", file=sys.stderr)
        return None, []
    if since and date < since:
        raise TooOld
    if newest and date > newest:  # 集計途中の直近日は取らない
        return None, []
    if date.isoformat() in done:  # 取得済みの日は機種ページを取りに行かない
        return None, []
    if keywords:  # 機種名に含まれるキーワードで絞る(取得するリクエストも減る)
        machines = [m for m in machines if any(k in m for k in keywords)]
    rows = []
    for name in machines:
        page = client.get(kishu_url(day_url, name))
        got = parse_kishu(page, date, name) if page else []
        if not got and name in singles:  # 機種別ページが取れない1台機種は日別ページの値で代用(BB/RBなし)
            got = [{"date": date.isoformat(), "machine": name, "bb": None, "rb": None, **singles[name]}]
        rows += got
    return date, rows


def out_path(store, out, keywords=()):
    """出力先。機種を絞った場合は別ファイルにする(取得済み日付の判定が全機種版と混ざらないように)"""
    if out:
        return out
    base = DEFAULT_OUT.stem if store == DEFAULT_STORE else re.sub(r'[\\/:*?"<>|\s]', "_", store)
    if keywords:
        base += "_" + "_".join(keywords)
    return DEFAULT_OUT.parent / (base + ".csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="append", help=f"店名(複数指定可・既定: {DEFAULT_STORE})")
    ap.add_argument("--machine", action="append", help="機種名に含まれる語で絞る(複数指定可。例: --machine ジャグラー --machine ハナハナ)")
    ap.add_argument("--out", type=Path, help="出力CSV(店舗1つのときのみ。既定は data/<店名>.csv)")
    ap.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔(秒)")
    ap.add_argument("--limit", type=int, help="店舗ごとに取得する日数の上限(新しい順)")
    ap.add_argument("--headed", action="store_true", help="ブラウザ画面を表示して実行(--browserと併用)")
    ap.add_argument("--since", type=dt.date.fromisoformat, help="この日付(YYYY-MM-DD)以降だけ取得")
    ap.add_argument("--skip-recent", type=int, default=2, help="直近N日は集計途中の可能性があるため取得しない(既定2)")
    ap.add_argument("--browser", action="store_true", help="実ブラウザ(Playwright)で取得する")
    a = ap.parse_args()
    stores = a.store or [DEFAULT_STORE]
    if a.out and len(stores) > 1:
        ap.error("--out は店舗を1つだけ指定したときに使えます")

    client = BrowserClient(a.delay, headless=not a.headed) if a.browser else Client(a.delay)
    try:
        for store in stores:  # 店舗は1つずつ順番に(並列にしない)
            print(f"=== {store} ===")
            path = out_path(store, a.out, a.machine)
            path.parent.mkdir(parents=True, exist_ok=True)
            done = set()
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as f:
                    done = {r["date"] for r in csv.DictReader(f)}
            day_urls = collect_day_urls(client, store)
            print(f"{len(day_urls)}日分の記事を発見 / 取得済み {len(done)}日 -> {path}")
            _run(a, client, day_urls, done, not path.exists(), path)
    except KeyboardInterrupt:
        print("\n中断しました。再実行すれば取得済みの日をスキップして続きから再開します。")
    finally:
        if hasattr(client, "close"):
            client.close()


def _run(a, client, day_urls, done, new_file, path):
    newest = dt.date.today() - dt.timedelta(days=a.skip_recent)
    count = 0
    failures = 0
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for url in day_urls:
            if a.limit is not None and count >= a.limit:
                break
            try:
                date, rows = scrape_day(client, url, done, a.since, newest, a.machine)
                failures = 0
            except TooOld:
                break
            except RuntimeError as e:  # この日は諦めて次へ(再実行すれば取り直す)
                failures += 1
                print(f"  スキップ: {url} ({e})", file=sys.stderr)
                if failures >= MAX_FAILURES:
                    raise SystemExit(f"{MAX_FAILURES}日連続で失敗したため停止します。しばらく置いてから再実行してください。")
                continue
            if date is None:
                continue
            w.writerows(rows)
            f.flush()
            count += 1
            print(f"{date} {len(rows)}台")


if __name__ == "__main__":
    main()
