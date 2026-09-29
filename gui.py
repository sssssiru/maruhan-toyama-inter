#!/usr/bin/env python3
"""scrape.py を操作するための簡易GUI。ダブルクリック(起動.bat)で使う。"""
import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

from scrape import DEFAULT_STORE

ROOT = Path(__file__).parent
SETTINGS = ROOT / "gui_settings.json"
WINDOWS = os.name == "nt"


def default_settings():
    return {
        "stores": DEFAULT_STORE,
        "keywords": "ジャグラー, ハナハナ",
        "since": (dt.date.today() - dt.timedelta(days=548)).isoformat(),
        "headed": True,
        "test": False,
    }


def split_words(text):
    """'ジャグラー, ハナハナ' / 改行区切り -> ['ジャグラー', 'ハナハナ']"""
    return [w for w in (t.strip() for t in text.replace("、", ",").replace("\n", ",").split(",")) if w]


def build_command(s):
    """設定 -> scrape.py を起動するコマンド(リスト)。不正な入力は ValueError。"""
    stores = split_words(s["stores"])
    if not stores:
        raise ValueError("店名を入力してください")
    since = s["since"].strip()
    if since:
        try:
            dt.date.fromisoformat(since)
        except ValueError:
            raise ValueError("開始日は 2025-01-01 の形式で入力してください") from None
    cmd = [sys.executable, "-u", str(ROOT / "scrape.py"), "--browser"]
    if s["headed"]:
        cmd.append("--headed")
    for store in stores:
        cmd += ["--store", store]
    for word in split_words(s["keywords"]):
        cmd += ["--machine", word]
    if since:
        cmd += ["--since", since]
    if s["test"]:
        cmd += ["--limit", "2"]
    return cmd


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        s = default_settings()
        try:
            s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

        root.title("台データ取得")
        root.geometry("760x560")
        f = tk.Frame(root, padx=12, pady=10)
        f.pack(fill="x")

        tk.Label(f, text="店名(複数なら改行で区切る)").grid(row=0, column=0, sticky="nw")
        self.stores = tk.Text(f, height=3, width=48)
        self.stores.insert("1.0", s["stores"])
        self.stores.grid(row=0, column=1, sticky="w", pady=3)

        tk.Label(f, text="機種(空欄なら全機種)").grid(row=1, column=0, sticky="w")
        self.keywords = tk.Entry(f, width=50)
        self.keywords.insert(0, s["keywords"])
        self.keywords.grid(row=1, column=1, sticky="w", pady=3)

        tk.Label(f, text="この日以降を取得").grid(row=2, column=0, sticky="w")
        self.since = tk.Entry(f, width=16)
        self.since.insert(0, s["since"])
        self.since.grid(row=2, column=1, sticky="w", pady=3)

        self.headed = tk.BooleanVar(value=s["headed"])
        self.test = tk.BooleanVar(value=s["test"])
        tk.Checkbutton(f, text="ブラウザ画面を表示する(通常はオン)", variable=self.headed).grid(row=3, column=1, sticky="w")
        tk.Checkbutton(f, text="テスト(新しい2日分だけ取得)", variable=self.test).grid(row=4, column=1, sticky="w")

        b = tk.Frame(root, padx=12)
        b.pack(fill="x")
        self.start_btn = tk.Button(b, text="開始", width=10, command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(b, text="停止", width=10, command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        tk.Button(b, text="保存フォルダを開く", command=self.open_data).pack(side="left")
        self.status = tk.Label(b, text="待機中", fg="gray")
        self.status.pack(side="right")

        self.log = scrolledtext.ScrolledText(root, height=18, state="disabled")
        self.log.pack(fill="both", expand=True, padx=12, pady=10)
        self.write("使い方: 内容を確認して「開始」を押してください。\n"
                   "実行中はブラウザを閉じず、PCをスリープさせないでください。\n"
                   "停止しても、次回は続きから再開します。\n\n")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.poll)

    # --- 設定 ---
    def settings(self):
        return {
            "stores": self.stores.get("1.0", "end").strip(),
            "keywords": self.keywords.get().strip(),
            "since": self.since.get().strip(),
            "headed": self.headed.get(),
            "test": self.test.get(),
        }

    def write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- 実行 ---
    def start(self):
        s = self.settings()
        try:
            cmd = build_command(s)
        except ValueError as e:
            messagebox.showwarning("入力を確認してください", str(e))
            return
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        flags = subprocess.CREATE_NO_WINDOW if WINDOWS else 0  # 黒い画面を出さない
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace", creationflags=flags)
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.configure(text="実行中…", fg="green")
        self.write("--- 開始 ---\n")

    def reader(self, proc):
        for line in proc.stdout:
            self.q.put(line)
        proc.wait()
        self.q.put(None)

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    self.finished()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(200, self.poll)

    def finished(self):
        code = self.proc.returncode if self.proc else None
        self.write(f"--- 終了 ---\n" if code == 0 else f"--- 停止しました(コード {code}) 再度「開始」で続きから再開できます ---\n")
        self.proc = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="待機中", fg="gray")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.write("停止しています…\n")
            if WINDOWS:  # ブラウザごとまとめて終了
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                self.proc.terminate()

    def open_data(self):
        d = ROOT / "data"
        d.mkdir(exist_ok=True)
        if WINDOWS:
            os.startfile(d)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(d)])

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("確認", "取得の途中です。停止して閉じますか?\n(次回は続きから再開できます)"):
                return
            self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
